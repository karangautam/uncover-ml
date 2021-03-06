"""
Run the uncoverml pipeline for clustering, supervised learning and prediction.

.. program-output:: uncoverml --help
"""

import logging
import pickle
import resource
from os.path import isfile, splitext, exists
import warnings

import click
import numpy as np
import matplotlib
matplotlib.use('Agg')

import uncoverml as ls
import uncoverml.cluster
import uncoverml.config
import uncoverml.features
import uncoverml.geoio
import uncoverml.learn
import uncoverml.mllog
import uncoverml.mpiops
import uncoverml.predict
import uncoverml.validate
import uncoverml.targets
from uncoverml.transforms import StandardiseTransform
# from uncoverml.mllog import warn_with_traceback


log = logging.getLogger(__name__)
# warnings.showwarning = warn_with_traceback
warnings.filterwarnings(action='ignore', category=FutureWarning)
warnings.filterwarnings(action='ignore', category=DeprecationWarning)


@click.group()
@click.option('-v', '--verbosity',
              type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR']),
              default='INFO', help='Level of logging')
def cli(verbosity):
    ls.mllog.configure(verbosity)


def run_crossval(x_all, targets_all, config):
    crossval_results = ls.validate.local_crossval(x_all,
                                                  targets_all, config)
    ls.mpiops.run_once(ls.geoio.export_crossval, crossval_results, config)


@cli.command()
@click.argument('pipeline_file')
@click.option('-p', '--partitions', type=int, default=1,
              help='divide each node\'s data into this many partitions')
def learn(pipeline_file, partitions):
    config = ls.config.Config(pipeline_file)
    targets_all, x_all = _load_data(config, partitions)

    if config.cross_validate:
        run_crossval(x_all, targets_all, config)

    log.info("Learning full {} model".format(config.algorithm))
    model = ls.learn.local_learn_model(x_all, targets_all, config)
    ls.mpiops.run_once(ls.geoio.export_model, model, config)
    log.info("Finished! Total mem = {:.1f} GB".format(_total_gb()))


def _load_data(config, partitions):
    if config.pickle_load:
        x_all = pickle.load(open(config.pickled_covariates, 'rb'))
        targets_all = pickle.load(open(config.pickled_targets, 'rb'))
        if config.cubist or config.multicubist:
            config.algorithm_args['feature_type'] = \
                pickle.load(open(config.featurevec, 'rb'))
        log.warning('Using  pickled targets and covariates. Make sure you have'
                    ' not changed targets file and/or covariates.')
    else:
        config.n_subchunks = partitions
        if config.n_subchunks > 1:
            log.info("Memory constraint forcing {} iterations "
                     "through data".format(config.n_subchunks))
        else:
            log.info("Using memory aggressively: "
                     "dividing all data between nodes")

        # Make the targets
        if config.train_data_pk and exists(config.train_data_pk):
            log.info('Reusing pickled training data')
            image_chunk_sets, transform_sets, targets = \
                pickle.load(open(config.train_data_pk, 'rb'))
        else:
            log.info('Intersecting targets as pickled train data was not '
                     'available')
            targets = ls.geoio.load_targets(shapefile=config.target_file,
                                            targetfield=config.target_property)
            # Get the image chunks and their associated transforms
            image_chunk_sets = ls.geoio.image_feature_sets(targets, config)
            transform_sets = [k.transform_set for k in config.feature_sets]

        if config.rawcovariates:
            log.info('Saving raw data before any processing')
            ls.features.save_intersected_features(image_chunk_sets,
                                                  transform_sets, config)

        if config.train_data_pk:
            pickle.dump([image_chunk_sets, transform_sets, targets],
                        open(config.train_data_pk, 'wb'))

        if config.rank_features:
            measures, features, scores = ls.validate.local_rank_features(
                image_chunk_sets,
                transform_sets,
                targets,
                config)
            ls.mpiops.run_once(ls.geoio.export_feature_ranks, measures,
                               features, scores, config)

        # need to add cubist cols to config.algorithm_args
        # keep: bool array corresponding to rows that are retained
        features, keep = ls.features.transform_features(image_chunk_sets,
                                                        transform_sets,
                                                        config.final_transform,
                                                        config)
        # learn the model
        # local models need all data
        x_all = ls.features.gather_features(features[keep], node=0)

        # We're doing local models at the moment
        targets_all = ls.targets.gather_targets(targets, keep, config, node=0)

        if config.pickle and ls.mpiops.chunk_index == 0:
            if hasattr(config, 'pickled_covariates'):
                pickle.dump(x_all, open(config.pickled_covariates, 'wb'))
            if hasattr(config, 'pickled_targets'):
                pickle.dump(targets_all, open(config.pickled_targets, 'wb'))

    return targets_all, x_all


@cli.command()
@click.argument('pipeline_file')
@click.option('-s', '--subsample_fraction', type=float, default=1.0,
              help='only use this fraction of the data for learning classes')
def cluster(pipeline_file, subsample_fraction):
    config = ls.config.Config(pipeline_file)

    for f in config.feature_sets:
        if not f.transform_set.global_transforms:
            raise ValueError('Standardise transform must be used for kmeans')
        for t in f.transform_set.global_transforms:
            if not isinstance(t, StandardiseTransform):
                raise ValueError('Only standardise transform is '
                                 'allowed for kmeans')

    config.subsample_fraction = subsample_fraction
    if config.subsample_fraction < 1:
        log.info("Memory contstraint: using {:2.2f}%"
                 " of pixels".format(config.subsample_fraction * 100))
    else:
        log.info("Using memory aggressively: dividing all data between nodes")

    if config.semi_supervised:
        semisupervised(config)
    else:
        unsupervised(config)
    log.info("Finished! Total mem = {:.1f} GB".format(_total_gb()))


def semisupervised(config):

    # make sure we're clear that we're clustering
    config.algorithm = config.clustering_algorithm
    config.cubist = False
    # Get the taregts
    targets = ls.geoio.load_targets(shapefile=config.class_file,
                                    targetfield=config.class_property)

    # Get the image chunks and their associated transforms
    image_chunk_sets = ls.geoio.semisupervised_feature_sets(targets, config)
    transform_sets = [k.transform_set for k in config.feature_sets]

    features, _ = ls.features.transform_features(image_chunk_sets,
                                                 transform_sets,
                                                 config.final_transform,
                                                 config)
    features, classes = ls.features.remove_missing(features, targets)
    indices = np.arange(classes.shape[0], dtype=int)

    config.n_classes = ls.cluster.compute_n_classes(classes, config)
    model = ls.cluster.KMeans(config.n_classes, config.oversample_factor)
    log.info("Clustering image")
    model.learn(features, indices, classes)
    ls.mpiops.run_once(ls.geoio.export_cluster_model, model, config)


def unsupervised(config):
    # make sure we're clear that we're clustering
    config.algorithm = config.clustering_algorithm
    config.cubist = False
    # Get the image chunks and their associated transforms
    image_chunk_sets = ls.geoio.unsupervised_feature_sets(config)
    transform_sets = [k.transform_set for k in config.feature_sets]

    features, _ = ls.features.transform_features(image_chunk_sets,
                                                 transform_sets,
                                                 config.final_transform,
                                                 config)

    features, _ = ls.features.remove_missing(features)
    model = ls.cluster.KMeans(config.n_classes, config.oversample_factor)
    log.info("Clustering image")
    model.learn(features)
    ls.mpiops.run_once(ls.geoio.export_cluster_model, model, config)


@cli.command()
@click.argument('model_or_cluster_file')
@click.option('-p', '--partitions', type=int, default=1,
              help='divide each node\'s data into this many partitions')
@click.option('-m', '--mask', type=str, default='',
              help='mask file used to limit prediction area')
@click.option('-r', '--retain', type=int, default=None,
              help='mask values where to predict')
def predict(model_or_cluster_file, partitions, mask, retain):

    with open(model_or_cluster_file, 'rb') as f:
        state_dict = pickle.load(f)

    model = state_dict["model"]
    config = state_dict["config"]
    config.cluster = True if splitext(model_or_cluster_file)[1] == '.cluster' \
        else False
    config.mask = mask if mask else config.mask
    if config.mask:
        config.retain = retain if retain else config.retain

        if not isfile(config.mask):
            config.mask = ''
            log.info('A mask was provided, but the file does not exist on '
                     'disc or is not a file.')

    config.n_subchunks = partitions
    if config.n_subchunks > 1:
        log.info("Memory contstraint forcing {} iterations "
                 "through data".format(config.n_subchunks))
    else:
        log.info("Using memory aggressively: dividing all data between nodes")

    image_shape, image_bbox, image_crs = ls.geoio.get_image_spec(model, config)

    outfile_tif = config.name + "_" + config.algorithm
    predict_tags = model.get_predict_tags()
    if not config.outbands:
        config.outbands = len(predict_tags)

    image_out = ls.geoio.ImageWriter(image_shape, image_bbox, image_crs,
                                     outfile_tif,
                                     config.n_subchunks, config.output_dir,
                                     band_tags=predict_tags[
                                               0: min(len(predict_tags),
                                                      config.outbands)],
                                     **config.geotif_options)

    for i in range(config.n_subchunks):
        log.info("starting to render partition {}".format(i+1))
        ls.predict.render_partition(model, i, image_out, config)

    # explicitly close output rasters
    image_out.close()

    if config.cluster and config.cluster_analysis:
        if ls.mpiops.chunk_index == 0:
            ls.predict.final_cluster_analysis(config.n_classes,
                                              config.n_subchunks)

    # ls.predict.final_cluster_analysis(config.n_classes,
    #                                   config.n_subchunks)

    if config.thumbnails:
        image_out.output_thumbnails(config.thumbnails)
    log.info("Finished! Total mem = {:.1f} GB".format(_total_gb()))


def _total_gb():
    # given in KB so convert
    my_usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**2)
    # total_usage = mpiops.comm.reduce(my_usage, root=0)
    total_usage = ls.mpiops.comm.allreduce(my_usage)
    return total_usage
