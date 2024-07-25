try:
    from umdalib.utils import logger
except ImportError:
    from loguru import logger

from dataclasses import dataclass
from typing import Dict, Union, TypedDict

import numpy as np
from pathlib import Path as pt
from datetime import datetime

# for processing
# from sklearn.preprocessing import StandardScaler
# from sklearn.metrics.pairwise import cosine_distances, euclidean_distances
from sklearn import metrics

# models
from sklearn.linear_model import LinearRegression
from sklearn.linear_model import Ridge
from sklearn.svm import SVR
from sklearn.neighbors import KNeighborsRegressor
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.gaussian_process import GaussianProcessRegressor, kernels

# Cross-validation and others
# from sklearn.model_selection import KFold, GridSearchCV, ShuffleSplit
from sklearn.model_selection import (
    KFold,
    train_test_split,
    GridSearchCV,
    cross_val_score,
)

# for saving models
from joblib import dump
from sklearn.utils import resample

from umdalib.training.read_data import read_as_ddf
from dask.diagnostics import ProgressBar

import json

# import dask.dataframe as dd

# models_dict
models = {
    "linear_regression": LinearRegression,
    "ridge": Ridge,
    "svr": SVR,
    "knn": KNeighborsRegressor,
    "rfr": RandomForestRegressor,
    "gbr": GradientBoostingRegressor,
    "gpr": GaussianProcessRegressor,
}

random_state_supported_models = ["rfr", "gbr", "gpr"]

seed = 42
# rng = np.random.default_rng(seed)
rng = np.random.RandomState(seed)


class TrainingFile(TypedDict):
    filename: str
    filetype: str
    key: str


@dataclass
class Args:
    model: str
    test_size: float
    bootstrap: bool
    bootstrap_nsamples: int
    parameters: Dict[str, Union[str, int, None]]
    fine_tuned_hyperparameters: Dict[str, Union[str, int, float, None]]
    fine_tune_model: bool
    pre_trained_file: str
    kfold_nsamples: int
    training_file: TrainingFile
    training_column_name_y: str
    npartitions: int
    vectors_file: str
    noise_scale: float


def main(args: Args):
    logger.info(f"Training {args.model} model")
    logger.info(f"{args.training_file['filename']}")
    # logger.info("testing...")
    # return
    pre_trained_file = pt(args.pre_trained_file)
    # check and add .pkl extension
    if pre_trained_file.suffix != ".pkl":
        pre_trained_file = pre_trained_file.with_suffix(".pkl")

    estimator = None
    grid_search = None

    # load data

    # load vectors

    X = np.load(args.vectors_file, allow_pickle=True)
    invalid_indices = [i for i, arr in enumerate(X) if np.any(arr == 0)]
    valid_mask = np.ones(len(X), dtype=bool)  # Initially, mark all as valid
    valid_mask[invalid_indices] = False  # Mark invalid indices as False
    X = X[valid_mask]  # Keep only the rows that are marked as True in the valid_mask

    # reshaping the array
    # n_samples = X.shape[0]
    # n_features = X[0].shape[1]
    # logger.info(f"Before reshaping data: {X[0].shape=}")
    # X = np.vstack(X).reshape(n_samples, n_features)
    # logger.info(f"After reshaping data: {X[0].shape=}")

    # load training data from file
    ddf = read_as_ddf(
        args.training_file["filetype"],
        args.training_file["filename"],
        args.training_file["key"],
    )
    ddf = ddf.repartition(npartitions=args.npartitions)
    y: np.ndarray = None
    with ProgressBar():
        y = ddf[args.training_column_name_y].compute()

    y = y.to_numpy()
    y = np.log10(y)
    y = y[valid_mask]
    logger.info(f"{y[:5]=}, {type(y)=}")

    # bootstrap data
    if args.bootstrap:
        args.bootstrap_nsamples = int(args.bootstrap_nsamples)
        X, y = resample(X, y, n_samples=args.bootstrap_nsamples, random_state=rng)

        # adding noise to the y values
        y += rng.normal(0, float(args.noise_scale), y.shape)

    # stack the arrays (n_samples, n_features)
    if len(X.shape) == 1:
        X = np.vstack(X)

    logger.info(f"{X[0].shape=}\n{y[0]=}")
    logger.info(f"Loaded data: {X.shape=}, {y.shape=}")

    # split data
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=float(args.test_size), random_state=rng
    )

    if args.model in random_state_supported_models:
        args.parameters["random_state"] = rng

    if args.fine_tune_model:

        logger.info("Fine-tuning model")

        opts = {
            k: v
            for k, v in args.parameters.items()
            if k not in args.fine_tuned_hyperparameters.keys()
        }
        initial_estimator = models[args.model](**opts)

        logger.info("Running grid search")
        # Grid-search
        kfold = KFold(n_splits=int(args.kfold_nsamples), shuffle=True, random_state=rng)
        grid_search = GridSearchCV(
            initial_estimator, args.fine_tuned_hyperparameters, cv=kfold
        )
        logger.info("Fitting grid search")

        # run grid search
        grid_search.fit(X_train, y_train)
        estimator = grid_search.best_estimator_

        logger.info("Grid search complete")
        logger.info(f"Best score: {grid_search.best_score_}")
        logger.info(f"Best parameters: {grid_search.best_params_}")

        # save grid search
        current_time = datetime.now().strftime("%Y%m%d-%H%M%S")
        grid_savefile = pre_trained_file.with_name(
            f"{current_time}_{pre_trained_file.name}_grid_search"
        )
        dump(grid_search, grid_savefile)
        logger.info(f"Grid search saved to {grid_savefile}")

    else:
        estimator = models[args.model](**args.parameters)

    # Cross-validation
    # scores = cross_val_score(estimator, X, y, cv=4)
    # logger.info(f"CV Scores: Mean={scores.mean():.2f}, Std={scores.std():.2f}")

    # train model
    if not args.fine_tune_model:
        logger.info("Training model")
        estimator.fit(X_train, y_train)

    y_pred: np.ndarray = estimator.predict(X_test)

    logger.info("Evaluating model")
    # evaluate model
    r2 = metrics.r2_score(y_test, y_pred)
    mse = metrics.mean_squared_error(y_test, y_pred)
    rmse = np.sqrt(mse)
    mae = metrics.mean_absolute_error(y_test, y_pred)

    logger.info(f"{y_test[:5]=}, {y_pred[:5]=}")
    logger.info(f"R2: {r2:.2f}, MSE: {mse:.2f}, MAE: {mae:.2f}")

    # save model
    logger.info(f"Saving model to {pre_trained_file}")
    current_time = datetime.now().strftime("%Y%m%d-%H%M%S")
    savefile = pre_trained_file.with_name(f"{current_time}_{pre_trained_file.name}")
    dump(estimator, savefile)

    results = {
        "r2": f"{r2:.2f}",
        "mse": f"{mse:.2f}",
        "rmse": f"{rmse:.2f}",
        "mae": f"{mae:.2f}",
        "y_true": y_test.tolist(),
        "y_pred": y_pred.tolist(),
    }

    if args.fine_tune_model:
        results["best_params"] = grid_search.best_params_
        results["best_score"] = f"{grid_search.best_score_:.2f}"

        logger.info(grid_search.cv_results_)
        # serialize cv_results

        logger.info("Serializing cv_results")
        cv_results = {}
        for key, value in grid_search.cv_results_.items():
            if isinstance(value, np.ndarray) or isinstance(value, np.ma.MaskedArray):
                cv_results[key] = value.tolist()
            else:
                cv_results[key] = value
        results["cv_results"] = cv_results

    logger.info(f"{results=}")
    logger.info("Training complete")

    # saving results to file

    with open(
        pre_trained_file.with_suffix(".json"),
        "w",
    ) as f:
        json.dump(results, f, indent=4)

    return results
