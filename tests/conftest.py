from __future__ import division

import pytest
import numpy as np
import shapefile as shp
import rasterio
from affine import Affine
import time
import signal
import subprocess

timg = np.reshape(np.arange(1, 17), (4, 4))


@pytest.fixture
def masked_array():
    x = np.ma.masked_array(data=[[0., 1.], [2., 3.], [4., 5.]],
                           mask=[[True, False], [False, True], [False, False]],
                           fill_value=1e23)
    return x


@pytest.fixture
def int_masked_array():
    x = np.ma.masked_array(data=[[1, 1], [2, 3], [4, 5], [3, 3]],
                           mask=[[True, False], [False, True], [False, False],
                                 [False, False]],
                           fill_value=-9999, dtype=int)
    return x


@pytest.fixture
def make_patch_31():
    pwidth = 1
    pstride = 1

    # Test output patches, patch centres
    tpatch = np.array([[[1, 2, 3],
                        [5, 6, 7],
                        [9, 10, 11]],
                       [[2, 3, 4],
                        [6, 7, 8],
                        [10, 11, 12]],
                       [[5, 6, 7],
                        [9, 10, 11],
                        [13, 14, 15]],
                       [[6, 7, 8],
                        [10, 11, 12],
                        [14, 15, 16]]])

    tx = np.array([1, 1, 2, 2])
    ty = np.array([1, 2, 1, 2])

    return timg, pwidth, pstride, tpatch, tx, ty


@pytest.fixture
def make_patch_11():
    pwidth = 0
    pstride = 1

    # Test output patches, patch centres
    tpatch = np.array([[timg.flatten()]]).T

    tx, ty = [g.flatten() for g in np.meshgrid(np.arange(3), np.arange(3))]

    return timg, pwidth, pstride, tpatch, tx, ty


@pytest.fixture
def make_patch_12():
    pwidth = 0
    pstride = 2

    # Test output patches, patch centres
    tpatch = np.array([[[1]],
                       [[3]],
                       [[9]],
                       [[11]]])

    tx = np.array([0, 0, 2, 2])
    ty = np.array([0, 2, 0, 2])

    return timg, pwidth, pstride, tpatch, tx, ty


@pytest.fixture
def make_points():
    pwidth = 1
    points = np.array([[1, 1], [2, 1], [2, 2]])

    tpatch = np.array([[[1, 2, 3],
                        [5, 6, 7],
                        [9, 10, 11]],
                       [[5, 6, 7],
                        [9, 10, 11],
                        [13, 14, 15]],
                       [[6, 7, 8],
                        [10, 11, 12],
                        [14, 15, 16]]])

    return timg, pwidth, points, tpatch


@pytest.fixture(params=[make_patch_31, make_patch_11, make_patch_12])
def make_multi_patch(request):
    return request.param()


@pytest.fixture
def make_ipcluster4(request):
    return make_ipcluster(request, 4)


@pytest.fixture
def make_ipcluster1(request):
    return make_ipcluster(request, 1)


def make_ipcluster(request, n):
    proc = subprocess.Popen(["ipcluster", "start", "--n=" + str(n)])
    time.sleep(10)

    def fin():
        # Shutdown engines
        proc.send_signal(signal.SIGINT)
        proc.wait()

    request.addfinalizer(fin)
    return proc


@pytest.fixture
def make_raster():

    res_x = 100
    res_y = 50
    x_range = (50, 80)
    y_range = (-40, -30)

    pix_x = (x_range[1] - x_range[0]) / res_x
    pix_y = (y_range[1] - y_range[0]) / res_y

    A = Affine(pix_x, 0, x_range[0],
                   0, -pix_y, y_range[1])

    lons = np.array([(x, 0) * A for x in np.arange(res_x)])[:, 0]
    lats = np.array([(0, y) * A for y in np.arange(res_y)])[:, 1]

    x_bound = (x_range[0], x_range[1] + pix_x)
    y_bound = (y_range[0] - pix_y, y_range[1])

    return (res_x, res_y), x_bound, y_bound, lons, lats, A


@pytest.fixture(scope='session')
def make_gtiff(tmpdir_factory):
    ftif = str(tmpdir_factory.mktemp('tif').join('test.tif').realpath())
    # Create grid
    res, x_bound, y_bound, lons, lats, Ao = make_raster()
    # Generate data for geotiff
    Lons, Lats = np.meshgrid(lons, lats)

    # Write geotiff
    profile = {'driver': "GTiff",
               'width': len(lons),
               'height': len(lats),
               'count': 2,
               'dtype': rasterio.float64,
               'transform': Ao,
               'crs': {'proj': 'longlat',
                       'ellps': 'WGS84',
                       'datum': 'WGS84',
                       'nodefs': True
                       }
               }

    with rasterio.open(ftif, 'w', **profile) as f:
        f.write(np.array([Lons, Lats]))
    return ftif


@pytest.fixture(scope='session', params=["allchunks", "somechunks"])
def make_shp(tmpdir_factory, request):

    # File names for test shapefile and test geotiff
    fshp = str(tmpdir_factory.mktemp('shapes').join('test.shp').realpath())

    if request.param == "allchunks":
        output_filenames = ["fpatch.part{}of4.hdf5".format(i)
                            for i in range(4)]
    else:
        output_filenames = ["fpatch.part{}of2.hdf5".format(i)
                            for i in range(2)]

    # Create grid
    res, x_bound, y_bound, lons, lats, Ao = make_raster()

    # Generate data for shapefile
    nsamples = 100
    ntargets = 10
    dlon = lons[np.random.randint(0, high=len(lons), size=nsamples)]
    if request.param == "allchunks":
        dlat = lats[np.random.randint(0, high=len(lats), size=nsamples)]
    else:
        dlat = lats[np.random.randint(3 / 8 * len(lats),
                                      high=5 / 8 * len(lats), size=nsamples)]
    fields = [str(i) for i in range(ntargets)] + ["lon", "lat"]
    vals = np.ones((nsamples, ntargets)) * np.arange(ntargets)
    vals = np.hstack((vals, np.array([dlon, dlat]).T))

    # write shapefile
    w = shp.Writer(shp.POINT)
    w.autoBalance = 1

    # points
    for p in zip(dlon, dlat):
        w.point(*p)

    # fields
    for f in fields:
        w.field(f, 'N', 16, 6)

    # records
    for v in vals:
        vdict = dict(zip(fields, v))
        w.record(**vdict)

    w.save(fshp)

    return fshp, output_filenames


@pytest.fixture(scope='session')
def make_fakedata(tmpdir_factory):

    mod_dir = str(tmpdir_factory.mktemp('models').realpath())

    w = np.array([1., 2.])
    x = np.atleast_2d(np.arange(-50, 50)).T
    X = np.hstack((np.ones((100, 1)), x))
    y = X.dot(w) + np.random.randn(100) / 1000

    return X, y, w, mod_dir
