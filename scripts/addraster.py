#! /usr/bin/env python3
import logging
import sys
import click
import os.path
import numpy as np
import rasterio
import tables as hdf
from affine import Affine

log = logging.getLogger(__name__)



@click.command()
@click.option('--output', type=click.Path(exists=False), required=1)
@click.option('--verbose', help="Log everything", default=False)
@click.argument('geotiffs', nargs=-1)
def main(output, verbose, geotiffs):
    """ Add one or more geotiffs to an hdf5 file stack (that may exist)

        The HDF5 file has the following datasets:

            - Raster: (original image data)

            - Latitude: (vector or matrix of pixel latitudes)

            - Longitude: (vector or matrix of pixel longitudes)

            - Label: (label or descriptions of bands)

    """

    # setup logging
    if verbose is True:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    # validate every input with respect to the first file
    with rasterio.open(geotiffs[0]) as raster:
        longitudes, latitudes = lonlat_pixel_centres(raster)
    
    all_valid = True
    for filename in geotiffs[1:]:
        log.info("processing {}".format(filename))
        valid = validate_file(filename, longitudes, latitudes)
        all_valid = valid and all_valid
    if not all_valid:
        sys.exit(-1)

    # Find out the total dimensionality of the new matrix
    ndims = 0
    for filename in geotiffs:
        with rasterio.open(filename) as f:
            #f.count is the number of bands of the image
            ndims += f.count

    h5file = hdf.open_file(output, mode='w')

    # Now that we know the images are all the same size/shape, we use the first
    # one to calculate the appropriate latitudes/longitudes
    with rasterio.open(geotiffs[0]) as raster:
        longs, lats = lonlat_pixel_centres(raster)
        h5file.create_array("/", "Longitude", obj=longs)
        h5file.create_array("/", "Latitude", obj=lats)
        raster_shape = (longs.shape[0], lats.shape[0], ndims)
        h5file.create_carray("/", "Raster", atom=hdf.Float64Atom(),
                shape=raster_shape)
        h5file.create_array("/", "Labels", atom=hdf.StringAtom(itemsize=100),
                shape=(ndims,))

    #Write the actual data into the hdf5 file
    # note we keep note of the current band index we're writing into
    idx = 0
    for f in geotiffs:
        idx = write_to_hdf5(f, h5file, idx)

def write_to_hdf5(raster, h5file, idx):
    log.info("Writing {} into hdf5 array".format(raster))
    with rasterio.open(raster) as f:
        I = f.read()
        nanvals = f.get_nodatavals()
        ndims = f.count

    # Permute layers to be less like a standard image and more like a
    # matrix i.e. (band, lon, lat) -> (lon, lat, band)
    I = (I.transpose([2, 1, 0]))[:, ::-1]

    # build channel labels
    basename = os.path.basename(raster).split(".")[-2]
    print("basename: " + basename)
    channel_labels = np.array([basename + "_band_" + str(i+1) 
            for i in range(I.shape[2])], dtype='S')

    # Mask out NaN vals if they exist
    if nanvals is not None:
        for v in nanvals:
            if v is not None:
                I[I == v] = np.nan

    # Now write the hdf5
    h5file.root.Raster[:,:,idx:idx+ndims] = I
    h5file.root.Labels[idx:idx+ndims] = channel_labels
    return idx + ndims

def lonlat_pixel_centres(raster):

    # Get affine transform for pixel centres
    # https://en.wikipedia.org/wiki/Transformation_matrix#Affine_transformations
    T1 = raster.affine * Affine.translation(0.5, 0.5)

    # No shearing or rotation allowed!!
    if not ((T1[1] == 0) and (T1[3] == 0)):
        log.critical("transform to pixel coordinates has rotation "
                     "or shear")
        sys.exit(-1)

    # compute the tiffs lat/lons
    f_lons = T1[2] + np.arange(raster.width) * T1[0]
    f_lats = T1[5] + np.arange(raster.height) * T1[4]
    return f_lons, f_lats


def validate_file(filename, longitudes, latitudes):
    xres = longitudes.shape[0]
    yres = latitudes.shape[0]
    xbounds = (longitudes[0], longitudes[-1])
    ybounds = (latitudes[0], latitudes[-1])
    all_valid = True
    with rasterio.open(filename) as f:

        if not f.width == xres:
            log.critical("input image width does not match hdf5")
            all_valid = False
        if not f.height == yres:
            log.critical("input image height does not match hdf5")
            all_valid = False

        f_lons, f_lats = lonlat_pixel_centres(f)

        if not xbounds == (f_lons[0], f_lons[-1]):
            log.critical("image x-bounds do not match hdf5")
            all_valid = False
        if not ybounds == (f_lats[0], f_lats[-1]):
            log.critical("image y-bounds do not match hdf5")
            all_valid = False
        if not np.all(longitudes == f_lons):
            log.critical("longitudes pixel values do not match hdf5")
            all_valid = False
        if not np.all(latitudes == f_lats):
            log.critical("latitudes pixel values do not match hdf5")
            all_valid = False
    return all_valid


if __name__ == "__main__":
    main()
