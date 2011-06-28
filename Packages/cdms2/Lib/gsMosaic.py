#/usr/bin/env python

"""
A file-like object to access mosaic and time aggregated data
$Id: gsFile.py 1728 2011-02-04 21:26:11Z dkindig $
"""

import os
import sys
sys.path.append("/home/kindig/software/projects/cdat/lib/python2.7/cdms2/")
from re import search, sub
from ctypes import c_float, c_char_p, c_int, CDLL, byref, POINTER
import cdms2
from numpy import zeros, float64, asarray, unique, reshape
from pycf import libCFConfig, __path__
from cdms2.hgrid import AbstractCurveGrid, TransientCurveGrid
from cdms2.coord import TransientAxis2D, TransientVirtualAxis
from error import CDMSError

LIBCFDIR  = __path__[0] + "/libcf"
libCF  = libCFConfig

def open(uri, mode = 'r'):
    """
    Open mosaic file
    @param mosaicfile mosaic file
    @param mode valid cdms2 open file mode
    @param inCdmsFile Mosaic file cdms2 object
    """

    outMosaicFile = GsMosaic(uri, mode)
    return outMosaicFile

def getSlab(str):
    """
    From a string return a tuple of slice objects
    @param str input string in the format "1:2 7:-1" for instance
    @return slice tuple, eg (slice(1, 2, 1), slice(7, -1, -1))
    """
    res = []
    # remove extra spaces
    str = sub(r'\s+', ' ', str)
    # remove leading/trailing spaces
    str = sub(r'^\s+', '', str)
    str = sub(r'\s+$', '', str)
    for index_range in str.split(libCF.CF_INDEX_SEPARATOR):
        m = search(r'([\-\d]+):([\-\d]+)', index_range)
        if m:
            step = 1
            startIndex = int(m.group(1))
            endIndex = int(m.group(2))
            if endIndex < startIndex: step = -1
            slc = slice(startIndex, endIndex, step)
            res.append(slc)
    return tuple(res)

class GsMosaic:
    """
    Define a mosaic.
    """

    def __init__(self, uri, mode = 'r'):
        """
        Constructor
        @param uri Filename with path
        @param mode read/write. Currently only read is supported
        """

        self.id      = uri
        self.mode    = mode
        self.uri     = uri
        self._status = 'Open'

        self.mosaicId_t = c_int(-1)
        self.lib = None
        for sosuffix in '.so', '.dylib', '.dll', '.a':
            self.lib = CDLL(LIBCFDIR + sosuffix)
            if self.lib:
                break

        libcfdll = self.lib

        self.file_type           = ""
        self.contact_map         = {}
        self.tile_contacts       = {}
        self.tile_contacts_compl = {}
        self.coordinate_names    = []
        tile_names               = []

        status = libcfdll.nccf_def_mosaic_from_file(uri, "", byref(self.mosaicId_t))

        if status != 0:
            print "ERROR: File %s doesn't exist or is not a valid mosaic file" % \
                mosaicfile
            print "error code: ", status
            return

        # Get some sizes
        ngrids         = c_int(-1)
        ndims          = c_int(-1)
        ncontacts      = c_int(-1)
        libcfdll.nccf_inq_mosaic_ndims(self.mosaicId_t, byref(ndims))
        libcfdll.nccf_inq_mosaic_ngrids(self.mosaicId_t, byref(ngrids))
        libcfdll.nccf_inq_mosaic_ncontacts(self.mosaicId_t, byref(ncontacts))

        # Build the character arrays
        separator_t = libCF.CF_TILE_SEPARATOR
        contact_map_t  = c_char_p(" " * (libCF.NC_MAX_NAME+1))
        tile_contact_t = c_char_p(" " * (libCF.NC_MAX_NAME+1))
        tile_name_t    = c_char_p(" " * (libCF.NC_MAX_NAME+1))
        coord_t = (c_char_p * ndims.value)()
        for iDim in range(ndims.value):
            coord_t[iDim] = " " * (libCF.NC_MAX_NAME+1)

        # Get the coordinate names for the grids
        libcfdll.nccf_inq_mosaic_coordnames(self.mosaicId_t, coord_t)

        for iCrd in range(len(coord_t)):
            self.coordinate_names.append(coord_t[iCrd])

        # Get the contact map information
        for iContact in range(ncontacts.value):
            status = libcfdll.nccf_inq_mosaic_contactmap(self.mosaicId_t, \
                                                       iContact, contact_map_t)
            status = libcfdll.nccf_inq_mosaic_tilecontact(self.mosaicId_t, \
                                                        iContact, tile_contact_t)

            tN1, tN2             = tile_contact_t.value.split(separator_t)
            tileName1, tileName2 = tN1.strip(), tN2.strip()
            s1, s2               = contact_map_t.value.split(separator_t)

            # slice objects
            slab1 = getSlab(s1.strip())
            slab2 = getSlab(s2.strip())

            # Create the tile contact dictionary. Non symmetric.
            if not self.tile_contacts.has_key(tileName1):
                self.tile_contacts[tileName1] = {}
            # The complement to tile_contacts
            if not self.tile_contacts_compl.has_key(tileName2):
                self.tile_contacts_compl[tileName2] = {}

            # Attach the contact map (slab) between the tiles
            self.tile_contacts[tileName1][tileName2] = (slab1, slab2)
            self.tile_contacts_compl[tileName2][tileName1] = (slab2, slab1)

    def getContactMap(self):
        return self.tile_contacts

    def getTileNames(self):
        return self.tile_names

    def getCellCenteredSlab(self, slab1, slab2):
        # adjust for cell centers
        newslabs = []
        for slab in slab1, slab2:
            newslab = []
            for sl in slab:
                b = sl.start
                e = sl.stop + 1
                newsl = slice(max(b-1, 0), max(e-1, -1), sl.step)
                newslab.append(newsl)
            newslabs.append(tuple(newslab))
        slab1, slab2 = newslabs
    
        return (slab1, slab2)
    
    def getSeamGrids(self, coordData):
        result = []
        for tn1 in self.tile_contacts.keys():
            for tn2 in self.tile_contacts[tn1].keys():
                # Get the seam data
                result.append(self.getSeamData(tn1, tn2, coordData))

                # Get the triangle data. Need to find the three cells 
                # comprising a corner.
                if tn2 in self.tile_contacts.keys():
                    t1n = self.tile_contacts[tn1].keys() 
                    t2n = self.tile_contacts[tn2].keys() 

                    # Look for a tile in the main list. Now compare the adjacent
                    # tiles to 1 and 2 until there is match. Now we have tile 3
                    for tn3 in t1n:
                        if tn3 in t1n and tn3 in t2n:
                            cornerIndex = self.getCornerData(tn1, tn2, tn3)
                            def getCornerInfo(data, cornerindex):
                                lon = data.getLongitude()
                                lat = data.getLatitude()
                                c1 = data[cornerindex]
                                n1 = lon[cornerindex]
                                t1 = lat[cornerindex]
                                lonatts = {'units':lon.units, 
                                           'standard_name':lon.standard_name}
                                latatts = {'units':lat.units, 
                                           'standard_name':lat.standard_name}

                                return c1, n1, t1, lonatts, latatts

                            def popCorner(d1, d2, d3, dtype):
                                corner = zeros((2, 2), dtype = dtype)
                                if 'data' in dir(d1):
                                    corner[0, 0] = d1.data
                                    corner[0, 1] = d2.data
                                    corner[1, 1] = d3.data
                                    corner[1, 0] = d3.data
                                else:
                                    corner[0, 0] = d1
                                    corner[0, 1] = d2
                                    corner[1, 1] = d3
                                    corner[1, 0] = d3
                                return corner

                            c1, n1, t1, lonAtts, latAtts = \
                                        getCornerInfo(coordData[tn1], cornerIndex[0])
                            c2, n2, t2, lonAtts, latAtts = \
                                getCornerInfo(coordData[tn2], cornerIndex[1])
                            c3, n3, t3, lonAtts, latAtts = \
                                getCornerInfo(coordData[tn3], cornerIndex[2])

                            # Make the triangle a degenerate square.
                            dtype = coordData[tn1].dtype
                            lon_dtype = coordData[tn1].getLongitude().dtype
                            lat_dtype = coordData[tn1].getLatitude().dtype
                            corner = popCorner(c1, c2, c3, dtype)
                            lon    = popCorner(n1, n2, n3, lon_dtype)
                            lat    = popCorner(t1, t2, t3, lat_dtype)
                            gridid = 'corner_%d_%d_%d' % (coordData[tn1].gridIndex, \
                                                           coordData[tn2].gridIndex, 
                                                           coordData[tn3].gridIndex)
                            gridAtts = {'lon':lonAtts, 'lat':latAtts, 'gridid':gridid}
                            cornerGrid = self.createSeamGrid(lon, lat, gridAtts)

                            cornerTV = cdms2.createVariable(corner, 
                                             axes = cornerGrid.getAxisList(), 
                                             grid = cornerGrid, 
                                             attributes = coordData[tn1].attributes, 
                                             id = gridid)
                    
                                
        return (result, cornerTV)

    def getCornerData(self, tileName1, tileName2, tileName3):
        
        # Get the slabs and account for cell centers
        s1, s2 = self.tile_contacts[tileName1][tileName2]
        s3, s4 = self.tile_contacts[tileName1][tileName3]
        s5, s6 = self.tile_contacts[tileName2][tileName3]
        s1, s2 = self.getCellCenteredSlab(s1, s2)
        s3, s4 = self.getCellCenteredSlab(s3, s4)
        s5, s6 = self.getCellCenteredSlab(s5, s6)

        # Get the index for the corners for each tile at the contact point.
        c1, c2 = self.getContactCornerIndex(s1, s2)
        c3, c4 = self.getContactCornerIndex(s3, s4)
        c5, c6 = self.getContactCornerIndex(s5, s6)

        # Set the tuple containing the corner indices in j, i order.
        if c1 == 0 and c3 == 1: pair1 = (s1[c1].start, s3[c3].start)
        if c1 == 1 and c3 == 0: pair1 = (s3[c3].start, s1[c1].start)
        if c2 == 0 and c5 == 1: pair2 = (s2[c2].start, s5[c5].start)
        if c2 == 1 and c5 == 0: pair2 = (s5[c5].start, s2[c2].start)
        if c4 == 0 and c6 == 1: pair3 = (s4[c4].start, s6[c6].start)
        if c4 == 1 and c6 == 0: pair3 = (s6[c6].start, s4[c4].start)

        return (pair1, pair2, pair3)

    def getContactCornerIndex(self, slab1, slab2):
        c1 = -1
        c2 = -1
        for index in range(2):
            aa = slab1[index].start - slab1[index].stop == -1
            bb = slab2[index].start - slab2[index].stop == -1
            if slab1[index].start - slab1[index].stop == -1:
                c1 = index
            if slab2[index].start - slab2[index].stop == -1:
                c2 = index
        return (c1, c2)

    def createCornerGrid(self, x, y, attrs):
        """
        Return the coordinate data associated with variable.
        @param x longitude coordinate
        @param y latitude coordinate
        @return attrs Attributes for eash plus the gridid
        """
        pass
        
    def createSeamGrid(self, x, y, attrs):
        """
        Return the coordinate data associated with variable.
        @param x longitude coordinate
        @param y latitude coordinate
        @return attrs Attributes for eash plus the gridid
        """
        from re import search
        LONSTR = 'lon'
        LATSTR = 'lat'


        # Get the dimensions
        xdim = x.shape
        ydim = y.shape

        if xdim != ydim: 
            raise CDMSError, "Dimension of coordinates grids don't match"

        nj = xdim[0]
        ni = xdim[1]

        # Define the axes, verifying the lon and lat grids
        jaxis = TransientVirtualAxis("j",nj)
        iaxis = TransientVirtualAxis("i",ni)

        if search(LONSTR, attrs['lon']['standard_name']): lon = x
        if search(LONSTR, attrs['lat']['standard_name']): lon = y
        if search(LATSTR, attrs['lon']['standard_name']): lat = x
        if search(LATSTR, attrs['lat']['standard_name']): lat = y

        lataxis = TransientAxis2D(lat, 
                       axes=(jaxis, iaxis), 
                       attributes=attrs['lat'], 
                       id=attrs['lat']['standard_name'])
        lonaxis = TransientAxis2D(lon, 
                       axes=(jaxis, iaxis), 
                       attributes=attrs['lon'], 
                       id=attrs['lon']['standard_name'])

        # Define the combined grid
        grid = TransientCurveGrid(lataxis, lonaxis, id=attrs['gridid'])
        return grid

    def getSeamData(self, tileName, otherTileName, inputData):
        """
        @param tileName Name for the first tile
        @param otherTileName Name for the other tile
        @param inputData Dictionary containing lon-lat names and their flat
                          coordinate data values
        @return newData Grid of data on slice
        """

        slab1, slab2 = self.tile_contacts[tileName][otherTileName]
    
        # Convert to cell centered slabs
        slab1, slab2 = self.getCellCenteredSlab(slab1, slab2)
        d1 = inputData[tileName]
        l1 = d1.getLongitude()
        t1 = d1.getLatitude()

        def createNewVar(data, slab):
            dat = data[slab]
            lon = data.getLongitude()
            lon = lon[slab]
            lat = data.getLatitude()
            lat = lat[slab]
            return (dat, lon, lat)

        data1, lon1, lat1 = createNewVar(inputData[tileName], slab1)
        data2, lon2, lat2 = createNewVar(inputData[otherTileName], slab2)
        
        # Remove dimensions of size 1.
        shape = []
        for d in data1.shape:
            if d != 1: shape.append(d)

        newshape = tuple(shape + [2])
        shape = tuple(shape)
        newVar = zeros(newshape, data1.dtype)
        newLon = zeros(newshape, lon1.dtype)
        newLat = zeros(newshape, lat1.dtype)
        newVar[:, 0] = reshape(data1, shape)
        newVar[:, 1] = reshape(data2, shape)
        newLon[:, 0] = reshape(lon1[:], shape)
        newLon[:, 1] = reshape(lon2, shape)
        newLat[:, 0] = reshape(lat1[:], shape)
        newLat[:, 1] = reshape(lat2, shape)
        gridid = 'seam_tile%d_tile%d' % (data1.gridIndex, data2.gridIndex)
        gridAtts = {'lon':{'units':l1.units, 'standard_name':l1.standard_name}, \
                    'lat':{'units':t1.units, 'standard_name':t1.standard_name}, \
                    'gridid':gridid}
        seamGrid = self.createSeamGrid(newLon, newLat, gridAtts)

        dataAtts = {'gridid': gridid}
        newData = cdms2.createVariable(newVar, 
                         axes = seamGrid.getAxisList(), 
                         grid = seamGrid, 
                         attributes = d1.attributes, 
                         id = dataAtts['gridid'])

        return newData

    
    def getCoordinateNames(self):
        return self.coordinate_names

    def __repr__(self):
        res = "<Mosaic: '%s',  URI: '%s', mode: '%s', status: '%s' >" % \
            ( self.id, self.uri, self.mode, self._status)
        return res

    # Not sure yet if these are needed.
    def __get_item__(self):
        pass

    def __call__(self):
        pass

    # Write? Only if self.mode is 'w'
    def __set_item__(self):
        if self.mode == 'w':
            # self.lib.nccf_put_mosaic( ncid, self.mosaicId_t )
            pass
        else:
            print 'Mode set to %s' % self.mode

    def __del__(self):
        self.lib.nccf_free_mosaic(self.mosaicId_t)

#############################################################################

def test():
    import os.path
    from sys import exit

    from optparse import OptionParser

    cfdir = '/home/kindig/projects/libcf/libcf/build/examples/'
    mfile = cfdir + 'ex2_mosaic.nc'
    usage = """
    Full path to mosaic file.
    e.g. python gsMosaic.py -f /home/kindig/projects/libcf/libcf/build/examples/ex2_mosaic.nc
    """
    parser = OptionParser()
    parser.add_option("-f", "--file", dest="mfile",
                  help="full path to mosaic file")

    options, args = parser.parse_args()
    if not options.mfile:
        print usage
        exit(1)
    # Use the libcf examples directory.
    if not os.path.exists(mfile):
        print "File '%s' does not exist. Check path"
        return

    m = open(mfile)

    print "\nCoordinate Names"
    for c in m.coordinate_names: print c

    print "\nTile Contacts"
    for t in m.tile_contacts: print "%s -> %s" % (t, m.tile_contacts[t])
    print "\nTile Contacts Complement"
    for t in m.tile_contacts_compl: print "%s -> %s" % (t, m.tile_contacts_compl[t])
    print

if __name__ == "__main__": test()