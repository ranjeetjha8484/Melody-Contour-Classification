""" Functions to run full experiment """
import contour_classification.contour_utils as cc
import contour_classification.experiment_utils as eu
import contour_classification.mv_gaussian as mv
import contour_classification.clf_utils as cu
import contour_classification.generate_melody as gm

import pandas as pd
import numpy as np
import random
import json
import os

from sklearn.externals import joblib


def run_experiments(mel_type, outdir):

    if not os.path.exists(outdir):
        os.mkdir(outdir)

    # Compute Overlap with Annotation
    with open('melody_trackids.json', 'r') as fhandle:
        track_list = json.load(fhandle)
    track_list = track_list['tracks']

    dset_contour_dict, dset_annot_dict = \
        eu.compute_all_overlaps(track_list, meltype=mel_type)

    mdb_files, splitter = eu.create_splits(test_size=0.15)

    split_num = 1

    for train, test in splitter:

        print "="*80
        print "Processing split number %s" % split_num
        print "="*80

        outdir2 = os.path.join(outdir, 'splitnum_%s' % split_num)
        if not os.path.exists(outdir2):
            os.mkdir(outdir2)
        outdir2 = os.path.join(outdir2)

        split_num = split_num + 1

        random.shuffle(train)
        n_train = len(train) - (len(test)/2)
        train_tracks = mdb_files[train[:n_train]]
        valid_tracks = mdb_files[train[n_train:]]
        test_tracks = mdb_files[test]

        train_contour_dict = {k: dset_contour_dict[k] for k in train_tracks}
        valid_contour_dict = {k: dset_contour_dict[k] for k in valid_tracks}
        test_contour_dict = {k: dset_contour_dict[k] for k in test_tracks}

        train_annot_dict = {k: dset_annot_dict[k] for k in train_tracks}
        valid_annot_dict = {k: dset_annot_dict[k] for k in valid_tracks}
        test_annot_dict = {k: dset_annot_dict[k] for k in test_tracks}

        olap_stats, _ = eu.olap_stats(train_contour_dict)

        fpath = os.path.join(outdir2, 'olap_stats.csv')
        olap_stats.to_csv(fpath)

        for olap_thresh in np.arange(0, 1, 0.1):
            print '='*40
            print "overlap threshold = %s" % olap_thresh
            print '='*40

            outdir3 = os.path.join(outdir2, 'olap_%s' % olap_thresh)
            if not os.path.exists(outdir3):
                os.mkdir(outdir3)
            outdir3 = os.path.join(outdir3)

            print "computing labels"
            x_train, y_train, x_valid, y_valid, \
            x_test, y_test, test_contour_dict = \
                compute_labels(train_contour_dict, valid_contour_dict, \
                               test_contour_dict, olap_thresh)

            print "scoring with multivariate gaussian"
            multivariate_gaussian(x_train, y_train, x_test, y_test, outdir3)

            print "training and scoring classifier"
            clf, best_thresh = classifier(x_train, y_train, x_valid, y_valid,
                                          x_test, y_test, outdir3)

            print "computing melody output"
            melody_output(clf, best_thresh, test_contour_dict, test_annot_dict,
                          outdir3)


def compute_labels(train_contour_dict, valid_contour_dict, \
                   test_contour_dict, olap_thresh):
    """
    """
    # Compute Labels using Overlap Threshold
    train_contour_dict, valid_contour_dict, test_contour_dict = \
        eu.label_all_contours(train_contour_dict, valid_contour_dict, \
                              test_contour_dict, olap_thresh=olap_thresh)

    x_train, y_train = cc.pd_to_sklearn(train_contour_dict)
    x_valid, y_valid = cc.pd_to_sklearn(valid_contour_dict)
    x_test, y_test = cc.pd_to_sklearn(test_contour_dict)

    return x_train, y_train, x_valid, y_valid, x_test, y_test, test_contour_dict



def multivariate_gaussian(x_train, y_train, x_test, y_test, outdir):
    # Score with Multivariate Gaussian

    # Transform data using boxcox transform, and fit multivariate gaussians.
    x_train_boxcox, x_test_boxcox = mv.transform_features(x_train, x_test)
    rv_pos, rv_neg = mv.fit_gaussians(x_train_boxcox, y_train)

    # Compute melodiness scores on train and test set
    m_train, m_test = mv.compute_all_melodiness(x_train_boxcox, x_test_boxcox,
                                                rv_pos, rv_neg)

    # Compute various metrics based on melodiness scores.
    melodiness_scores = mv.melodiness_metrics(m_train, m_test, y_train, y_test)
    best_thresh, max_fscore = eu.get_best_threshold(y_test, m_test)


    melodiness_scores = pd.DataFrame.from_dict(melodiness_scores)
    fpath = os.path.join(outdir, 'melodiness_scores.csv')
    melodiness_scores.to_csv(fpath)

    print "Melodiness best thresh = %s" % best_thresh
    print "Melodiness max f1 score = %s" % max_fscore
    print "overall melodiness scores:"
    print melodiness_scores


def classifier(x_train, y_train, x_valid, y_valid, x_test, y_test, outdir):
    """ Train Classifier
    """

    # Cross Validation
    best_depth, _ = cu.cross_val_sweep(x_train, y_train)
    print "Classifier best depth = %s" % best_depth

    # Training
    clf = cu.train_clf(x_train, y_train, best_depth)

    # Predict and Score
    p_train, p_valid, p_test = cu.clf_predictions(x_train, x_valid, x_test, clf)
    clf_scores = cu.clf_metrics(p_train, p_test, y_train, y_test)
    print "Classifier scores:"
    print clf_scores

    # Get threshold that maximizes F1 score
    best_thresh, max_fscore = eu.get_best_threshold(y_valid, p_valid)

    clf_scores['best_thresh'] = best_thresh
    clf_scores['max_fscore'] = max_fscore

    clf_scores = pd.DataFrame.from_dict(clf_scores)
    fpath = os.path.join(outdir, 'classifier_scores.csv')
    clf_scores.to_csv(fpath)

    clf_fpath = os.path.join(outdir, 'rf_clf.pkl')
    joblib.dump(clf, clf_fpath)

    print "Classifier best threshold = %s" % best_thresh
    print "Classifier maximum f1 score = %s" % max_fscore

    return clf, best_thresh


def melody_output(clf, best_thresh, test_contour_dict, test_annot_dict, outdir):
    """ Generate Melody Output
    """

    # Add predicted melody probabilites to test set contour data
    for key in test_contour_dict.keys():
        test_contour_dict[key] = eu.contour_probs(clf, test_contour_dict[key])

    meldir = os.path.join(outdir, 'melody_output')
    if not os.path.exists(meldir):
        os.mkdir(meldir)
    meldir = os.path.join(meldir)

    # Generate melody output using predictions
    mel_output_dict = {}
    for key in test_contour_dict.keys():
        mel_output_dict[key] = gm.melody_from_clf(test_contour_dict[key],
                                                  prob_thresh=best_thresh)
        fpath = os.path.join(meldir, "%s_pred.csv")
        mel_output_dict[key].to_csv(fpath, header=False, index=True)

    # Score Melody Output
    mel_scores = gm.score_melodies(mel_output_dict, test_annot_dict)

    overall_scores = \
        pd.DataFrame(columns=['VR', 'VFA', 'RPA', 'RCA', 'OA'],
                     index=mel_scores.keys())
    overall_scores['VR'] = \
        [mel_scores[key]['Voicing Recall'] for key in mel_scores.keys()]
    overall_scores['VFA'] = \
        [mel_scores[key]['Voicing False Alarm'] for key in mel_scores.keys()]
    overall_scores['RPA'] = \
        [mel_scores[key]['Raw Pitch Accuracy'] for key in mel_scores.keys()]
    overall_scores['RCA'] = \
        [mel_scores[key]['Raw Chroma Accuracy'] for key in mel_scores.keys()]
    overall_scores['OA'] = \
        [mel_scores[key]['Overall Accuracy'] for key in mel_scores.keys()]

    scores_fpath = os.path.join(outdir, "all_mel_scores.csv")
    overall_scores.to_csv(scores_fpath)

    score_summary = os.path.join(outdir, "mel_score_summary.csv")
    overall_scores.describe().to_csv(score_summary)
