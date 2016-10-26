import geopandas as gpd
import pandas as pd
import pandas.core.algorithms as algos
import numpy as np
from shapely.geometry import Polygon
import logging
import click


BIN = 'bin'
GEOMETRY = 'geometry'
log = logging.getLogger(__name__)


@click.group()
def cli():
    logging.basicConfig(level=logging.INFO)


def filter_fields(fields_to_keep, input_shapefile):
    gdf = gpd.read_file(input_shapefile)
    fields_to_keep = [GEOMETRY] + list(fields_to_keep)  # add geometry
    original_fields = gdf.columns
    for f in fields_to_keep:
        if f not in original_fields:
            raise RuntimeError("field '{}' must exist in shapefile".format(f))
    gdf_out = gdf[fields_to_keep]
    return gdf_out


def strip_shapefile(input_shapefile, output_shapefile, *fields_to_keep):
    """
    Parameters
    ----------
    input_shapefile
    output_shapefile
    args features to keep in the output shapefile
    Returns
    -------
    """

    gdf_out = filter_fields(fields_to_keep, input_shapefile)
    gdf_out.to_file(output_shapefile)


def resample_shapefile(input_shapefile, output_shapefile, target_field,
                       bins=10, *fields_to_keep, replace=True,
                       output_samples=None):
    """
    Parameters
    ----------
    input_shapefile
    output_shapefile
    target_field: str, target field for sampling
    bins: number of bins for sampling
    fields_to_keep: list of strings to store in the output shapefile
    replace: bool, whether to sample with replacement or not
    output_samples: number of samples in the output shpfile
    Returns
    -------

    """
    gdf_out = filter_fields(fields_to_keep, input_shapefile)

    # the idea is stolen from pandas.qcut
    # pd.qcut does not work for cases when it result in non-unique bin edges
    target = gdf_out[target_field].values
    bin_edges = algos.quantile(
        np.unique(target), np.linspace(0, 1, bins+1))
    result = pd.tools.tile._bins_to_cuts(target, bin_edges,
                                         labels=False,
                                         include_lowest=True)
    # add to output df for sampling
    gdf_out[BIN] = result

    dfs_to_concat = []
    total_samples = output_samples if output_samples else gdf_out.shape[0]
    samples_per_bin = total_samples // bins

    gb = gdf_out.groupby(BIN)
    for b, gr in gb:
        dfs_to_concat.append(gr.sample(n=samples_per_bin, replace=replace))

    final_df = pd.concat(dfs_to_concat)
    final_df.sort_index(inplace=True)
    final_df.drop(BIN, axis=1).to_file(output_shapefile)


def resample_shapefile_spatially(input_shapefile,
                                 output_shapefile,
                                 rows=10,
                                 cols=10,
                                 *fields_to_keep,
                                 replace=True,
                                 output_samples=None):
    """
    Parameters
    ----------
    input_shapefile
    output_shapefile
    rows: number of bins in y
    cols: number of bins in x
    fields_to_keep: list of strings to store in the output shapefile
    replace: bool, whether to sample with replacement or not
    output_samples: number of samples in the output shpfile
    Returns
    -------

    """
    gdf_out = filter_fields(fields_to_keep, input_shapefile)

    minx, miny, maxx, maxy = gdf_out[GEOMETRY].total_bounds
    x_grid = np.linspace(minx, maxx, num=cols+1)
    y_grid = np.linspace(miny, maxy, num=rows+1)

    polygons = []
    for xs, xe in zip(x_grid[:-1], x_grid[1:]):
        for ys, ye in zip(y_grid[:-1], y_grid[1:]):
            polygons.append(Polygon([(xs, ys), (xs, ye), (xe, ye), (xe, ys)]))

    df_to_concat = []

    total_samples = output_samples if output_samples else gdf_out.shape[0]
    samples_per_group = total_samples // len(polygons)

    for p in polygons:
        df = gdf_out[gdf_out[GEOMETRY].within(p)]
        # should probably discard if df.shape[0] < 10% of samples_per_group
        if df.shape[0]:
            df_to_concat.append(df.sample(n=samples_per_group, replace=replace))
        else:
            log.info('{} does not contain any sample'.format(p))

    final_df = pd.concat(df_to_concat)
    final_df.to_file(output_shapefile)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    resample_shapefile(
        '/g/data/ge3/covariates/Sites/AWAGs/AWAGs2million.shp',
        '/g/data/ge3/covariates/Sites/AWAGs/uranium_1mil_from_AWAGs2million.shp',
        'uranium',
        1000,
        *['uranium'],
        replace=True,
        output_samples=100000
    )

