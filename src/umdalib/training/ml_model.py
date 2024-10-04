try:
    from umdalib.utils import logger
except ImportError:
    from loguru import logger

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path as pt
from time import perf_counter
from typing import Dict, Tuple, TypedDict, Union

import numpy as np
import optuna
import pandas as pd
import shap
from dask.diagnostics import ProgressBar
from joblib import dump, parallel_config
from scipy.optimize import curve_fit
from sklearn import metrics
from sklearn.gaussian_process import kernels
from sklearn.model_selection import (
    KFold,
    cross_validate,
    learning_curve,
    train_test_split,
)
from sklearn.utils import resample
from tqdm import tqdm

from umdalib.training.read_data import read_as_ddf
from umdalib.training.utils import Yscalers, get_transformed_data
from umdalib.utils import Paths

from .ml_utils.optuna_grids import get_optuna_objective
from .ml_utils.utils import (
    grid_search_dict,
    kernels_dict,
    models_dict,
    n_jobs_keyword_available_models,
)

tqdm.pandas()


def linear(x, m, c):
    return m * x + c


random_state_supported_models = ["rfr", "gbr", "gpr"]
rng = None


def optuna_optimize(
    model_name: str,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    optuna_n_trials: int = 100,
):
    save_loc = Paths().app_log_dir / "optuna"
    if not save_loc.exists():
        save_loc.mkdir(parents=True)

    logfile = save_loc / "storage.db"
    # if logfile.exists():
    #     logfile.unlink()
    storage = f"sqlite:///{str(logfile)}"

    logger.info(f"Using {storage} for storage")

    study = optuna.create_study(
        pruner=optuna.pruners.MedianPruner(n_warmup_steps=5),
        direction="minimize",
        study_name=model_name,
        storage=storage,
        # load_if_exists=True,
    )

    objective_func = get_optuna_objective(model_name)

    def objective(trial: optuna.Trial):
        return objective_func(trial, X_train, y_train, X_test, y_test)

    study.optimize(objective, n_trials=optuna_n_trials)

    logger.info("Number of finished trials:", len(study.trials))
    logger.info("Best trial:")
    trial = study.best_trial

    logger.info("  Value: ", trial.value)
    logger.info("  Params: ")
    for key, value in trial.params.items():
        logger.info("    {}: {}".format(key, value))

    best_params = study.best_params
    best_model = models_dict[model_name](**best_params)

    best_model.fit(X_train, y_train)

    return best_model, best_params


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
    fine_tuned_values: Dict[str, Union[str, int, float, None]]
    pre_trained_file: str
    cv_fold: int
    cross_validation: bool
    training_file: TrainingFile
    training_column_name_y: str
    npartitions: int
    vectors_file: str
    noise_percentage: float
    ytransformation: str
    yscaling: str
    embedding: str
    pca: bool
    save_pretrained_model: bool
    fine_tune_model: bool
    grid_search_method: str
    grid_search_parameters: Dict[str, int]
    parallel_computation: bool
    n_jobs: int
    parallel_computation_backend: str
    use_dask: bool
    skip_invalid_y_values: bool
    inverse_scaling: bool
    inverse_transform: bool
    learning_curve_train_sizes: list[float] | None
    analyse_shapley_values: bool
    optuna_n_trials: int


def augment_data(
    X: np.ndarray, y: np.ndarray, n_samples: int, noise_percentage: float
) -> Tuple[np.ndarray, np.ndarray]:
    """
    augment_data a small dataset to create a larger training set.

    :X: Feature matrix
    :y: Target vector
    :n_samples: Number of samples in the bootstrapped dataset
    :noise_percentage: Scale of Gaussian noise to add to y
    :return: Bootstrapped X and y
    """

    logger.info(f"Augmenting data with {n_samples} samples")
    X_boot, y_boot = resample(X, y, n_samples=n_samples, replace=True, random_state=rng)

    logger.info(f"Adding noise percentage: {noise_percentage}")
    noise_scale = (noise_percentage / 100) * np.abs(y_boot)

    y_boot += np.random.normal(0, noise_scale)

    return X_boot, y_boot


def make_custom_kernels(kernel_dict: Dict[str, Dict[str, str]]) -> kernels.Kernel:
    constants_kernels = None
    other_kernels = None

    for kernel_key in kernel_dict.keys():
        kernel_params = kernel_dict[kernel_key]

        for kernel_params_key, kernel_params_value in kernel_params.items():
            if "," in kernel_params_value:
                kernel_params[kernel_params_key] = tuple(
                    float(x) for x in kernel_params_value.split(",")
                )
            elif kernel_params_value != "fixed":
                kernel_params[kernel_params_key] = float(kernel_params_value)
        logger.info(f"{kernel_key=}, {kernel_params=}")

        if kernel_key == "Constant":
            constants_kernels = kernels_dict[kernel_key](**kernel_params)
        else:
            if other_kernels is None:
                other_kernels = kernels_dict[kernel_key](**kernel_params)
            else:
                other_kernels += kernels_dict[kernel_key](**kernel_params)

    kernel = constants_kernels * other_kernels
    return kernel


def get_stats(estimator, X_true: np.ndarray, y_true: np.ndarray):
    y_pred: np.ndarray = estimator.predict(X_true)

    if inverse_scaling and yscaler:
        logger.info("Inverse transforming Y-data")
        y_pred = yscaler.inverse_transform(y_pred.reshape(-1, 1)).flatten()
        y_true = yscaler.inverse_transform(y_true.reshape(-1, 1)).flatten()

    if inverse_transform and ytransformation:
        logger.info("Inverse transforming Y-data")
        if y_transformer:
            logger.info(
                f"Using Yeo-Johnson inverse transformation using {y_transformer=}"
            )
            y_pred = y_transformer.inverse_transform(y_pred.reshape(-1, 1)).flatten()
            y_true = y_transformer.inverse_transform(y_true.reshape(-1, 1)).flatten()
        else:
            logger.info("Using other inverse transformation")
            y_true = get_transformed_data(
                y_true,
                ytransformation,
                inverse=True,
                lambda_param=boxcox_lambda_param,
            )
            y_pred = get_transformed_data(
                y_pred,
                ytransformation,
                inverse=True,
                lambda_param=boxcox_lambda_param,
            )

    logger.info("Evaluating model")
    r2 = metrics.r2_score(y_true, y_pred)
    mse = metrics.mean_squared_error(y_true, y_pred)
    rmse = np.sqrt(mse)
    mae = metrics.mean_absolute_error(y_true, y_pred)

    logger.info(f"R2: {r2:.2f}, MSE: {mse:.2f}, RMSE: {rmse:.2f}, MAE: {mae:.2f}")

    pop, _ = curve_fit(linear, y_true, y_pred)
    y_linear_fit = linear(y_true, *pop)
    y_linear_fit = np.array(y_linear_fit, dtype=float)

    return r2, mse, rmse, mae, y_true, y_pred, y_linear_fit


def compute_cv(cv_fold: int, estimator, X: np.ndarray, y: np.ndarray):
    scoring = {
        "r2": "r2",
        "mse": "neg_mean_squared_error",
        "mae": "neg_mean_absolute_error",
    }

    logger.info("Cross-validating model")

    cv_fold = int(cv_fold)
    Kfold = KFold(n_splits=cv_fold, shuffle=True)

    cv_results = cross_validate(
        estimator, X, y, cv=Kfold, scoring=scoring, return_train_score=True
    )
    logger.info(f"{cv_results=}")

    cv_scores = {}
    for t in ["test", "train"]:
        cv_scores[t] = {}
        for k in ["r2", "mse", "mae"]:
            scores = np.array(cv_results[f"{t}_{k}"])
            if k in ["mse", "mae"]:
                # Negate because sklearn returns negative MSE and MAE
                scores = -scores

            avg = np.mean(scores)
            std = np.std(scores, ddof=1)  # Use ddof=1 for sample standard deviation

            if k == "mse":
                rmse_scores = np.sqrt(scores)
                rmse_avg = np.mean(rmse_scores)
                rmse_std = np.std(rmse_scores, ddof=1)

                cv_scores[t]["rmse"] = {
                    "mean": rmse_avg,
                    "std": rmse_std,
                    "ci_lower": rmse_avg - 1.96 * rmse_std,
                    "ci_upper": rmse_avg + 1.96 * rmse_std,
                    "scores": rmse_scores.tolist(),
                }

            cv_scores[t][k] = {
                "mean": avg,
                "std": std,
                "ci_lower": avg - 1.96 * std,
                "ci_upper": avg + 1.96 * std,
                "scores": scores.tolist(),
            }

    logger.info(f"{cv_scores=}")

    nfold_cv_scores = {f"{cv_fold}": cv_scores}

    cv_scores_savefile = pre_trained_loc / f"{pre_trained_file.stem}.cv_scores.json"

    if cv_scores_savefile.exists():
        read_cv_scores = {}
        with open(cv_scores_savefile, "r") as f:
            read_cv_scores = json.load(f)
            read_cv_scores.update(nfold_cv_scores)

        nfold_cv_scores = read_cv_scores

    # Save to JSON file
    with open(cv_scores_savefile, "w") as f:
        json.dump(nfold_cv_scores, f, indent=4)

    logger.info(f"Data saved to {cv_scores_savefile}")

    return cv_scores


def learn_curve(
    estimator,
    X: np.ndarray,
    y: np.ndarray,
    sizes: list[float] = None,
    n_jobs: int = -2,
    cv=5,
):
    logger.info("Learning curve")
    train_sizes, train_scores, test_scores = learning_curve(
        estimator,
        X,
        y,
        train_sizes=np.linspace(*sizes),
        cv=cv,
        scoring="r2",
        n_jobs=n_jobs,
    )

    learning_curve_data = {}
    for train_size, cv_train_scores, cv_test_scores in zip(
        train_sizes, train_scores, test_scores
    ):
        test_mean = np.mean(cv_test_scores)
        test_std = np.std(cv_test_scores, ddof=1)
        train_mean = np.mean(cv_train_scores)
        train_std = np.std(cv_train_scores, ddof=1)

        learning_curve_data[f"{train_size}"] = {
            "test": {
                "mean": f"{test_mean:.2f}",
                "std": f"{test_std :.2f}",
                "scores": cv_test_scores.tolist(),
            },
            "train": {
                "mean": f"{train_mean:.2f}",
                "std": f"{train_std:.2f}",
                "scores": cv_train_scores.tolist(),
            },
        }

        logger.info(f"{train_size} samples were used to train the model")
        logger.info(f"The average train accuracy is {train_mean:.2f}")
        logger.info(f"The average test accuracy is {test_mean:.2f}")

    learning_curve_savefile = (
        pre_trained_loc / f"{pre_trained_file.stem}.learning_curve.json"
    )
    # Save to JSON file
    with open(learning_curve_savefile, "w") as f:
        json.dump(learning_curve_data, f, indent=4)

    logger.info(f"Data saved to {learning_curve_savefile}")

    return


def analyse_shap_values(estimator, X: np.ndarray):
    logger.info("Analyzing SHAP values")

    explainer = shap.Explainer(estimator, X)
    shap_values = explainer(X)

    # shap.summary_plot(shap_values, X, plot_type="bar")
    # shap.summary_plot(shap_values, X)

    # Convert SHAP values to a numpy array
    shap_values_array = shap_values.values

    # Calculate mean absolute SHAP values for each feature
    mean_abs_shap = np.abs(shap_values_array).mean(axis=0)
    feature_names = [f"Feature {i}" for i in range(X.shape[1])]
    # Create a dictionary with all necessary data
    data = {
        "feature_names": explainer.feature_names or feature_names,
        "shap_values": shap_values_array.tolist(),
        "feature_values": X.tolist(),
        "mean_abs_shap": mean_abs_shap.tolist(),
    }

    # log data shapes
    logger.info(f"{shap_values_array.shape=}, {mean_abs_shap.shape=}")

    shapely_savefile = pre_trained_loc / f"{pre_trained_file.stem}.shapely.json"
    # Save to JSON file
    with open(shapely_savefile, "w") as f:
        json.dump(data, f, indent=4)

    logger.info(f"Data saved to {shapely_savefile}")

    return


pre_trained_file: pt = None
pre_trained_loc: pt = None
current_model_name: str = None
timeStamp: str = None


def fine_tune_estimator(args: Args, X_train: np.ndarray, y_train: np.ndarray):
    logger.info("Fine-tuning model")
    opts = {
        k: v
        for k, v in args.parameters.items()
        if k not in args.fine_tuned_values.keys()
    }

    if args.parallel_computation and args.model in n_jobs_keyword_available_models:
        opts["n_jobs"] = n_jobs

    initial_estimator = models_dict[args.model](**opts)

    logger.info("Running grid search")
    # Grid-search
    GridCV = grid_search_dict[args.grid_search_method]["function"]
    GridCV_parameters = {}
    for param in grid_search_dict[args.grid_search_method]["parameters"]:
        if param in args.grid_search_parameters:
            GridCV_parameters[param] = args.grid_search_parameters[param]

    if args.parallel_computation:
        GridCV_parameters["n_jobs"] = n_jobs

    logger.info(f"{GridCV=}, {GridCV_parameters=}")

    grid_search = GridCV(
        initial_estimator,
        args.fine_tuned_values,
        cv=int(args.cv_fold),
        **GridCV_parameters,
    )
    logger.info("Fitting grid search")

    # run grid search
    grid_search.fit(X_train, y_train)
    best_model = grid_search.best_estimator_

    logger.info("Grid search complete")
    logger.info(f"Best score: {grid_search.best_score_}")
    logger.info(f"Best parameters: {grid_search.best_params_}")

    # save grid search
    if args.save_pretrained_model:
        grid_savefile = pre_trained_file.with_name(
            f"{pre_trained_file.stem}_grid_search"
        ).with_suffix(".pkl")
        dump(grid_search, grid_savefile)

        df = pd.DataFrame(grid_search.cv_results_)
        df = df.sort_values(by="rank_test_score")
        df.to_csv(grid_savefile.with_suffix(".csv"))

        logger.info(f"Grid search saved to {grid_savefile}")

    return best_model, grid_search.best_params_


def save_parameters(suffix: str, parameters: Dict[str, Union[str, int, float, None]]):
    parameters_file = pre_trained_file.with_suffix(suffix)
    with open(parameters_file, "w") as f:
        parameters_dict = {
            "values": parameters,
            "model": current_model_name,
            "timestamp": timeStamp,
        }
        json.dump(parameters_dict, f, indent=4)
        logger.info(f"Model parameters saved to {parameters_file.name}")


def compute(args: Args, X: np.ndarray, y: np.ndarray):
    global pre_trained_file, pre_trained_loc, current_model_name, timeStamp

    current_model_name = args.model

    start_time = perf_counter()

    estimator = None
    # grid_search = None

    pre_trained_file = pt(args.pre_trained_file.strip()).with_suffix(".pkl")
    pre_trained_loc = pre_trained_file.parent
    if not pre_trained_loc.exists():
        pre_trained_loc.mkdir(parents=True)

    arguments_file = pre_trained_loc / f"{pre_trained_file.stem}.arguments.json"

    with open(arguments_file, "w") as f:
        json.dump(args.__dict__, f, indent=4)
        logger.info(f"Arguments saved to {arguments_file}")

    # bootstrap data
    if args.bootstrap:
        logger.info("Bootstrapping data")
        X, y = augment_data(
            X,
            y,
            n_samples=int(args.bootstrap_nsamples),
            noise_percentage=float(args.noise_percentage),
        )

    # stack the arrays (n_samples, n_features)
    if len(X.shape) == 1:
        logger.info("Reshaping X")
        X = np.vstack(X)

    logger.info(f"{X[0].shape=}\n{y[0]=}")
    logger.info(f"Loaded data: {X.shape=}, {y.shape=}")

    test_size = float(args.test_size)
    y_copy = y.copy()
    if test_size > 0:
        # split data
        logger.info("Splitting data for training and testing")
        X_train, X_test, y_train, y_test = train_test_split(
            X,
            y_copy,
            test_size=test_size,
            shuffle=True,
            # random_state=rng
        )
    else:
        X_train, X_test, y_train, y_test = X, X, y_copy, y_copy

    logger.info(f"{X_train.shape=}, {X_test.shape=}")
    logger.info(f"{y_train.shape=}, {y_test.shape=}")

    if (
        args.model in random_state_supported_models
        and "random_state" not in args.parameters
        and rng is not None
    ):
        args.parameters["random_state"] = rng

    kernel = None
    if args.model == "gpr":
        logger.info("Using Gaussian Process Regressor with custom kernel")

        if "kernel" in args.parameters and args.parameters["kernel"]:
            kernel = make_custom_kernels(args.parameters["kernel"])
            args.parameters.pop("kernel", None)

    if args.model == "catboost":
        args.parameters["train_dir"] = str(Paths().app_log_dir / "catboost_info")
        logger.info(f"catboost_info: {args.parameters['train_dir']=}")

    logger.info(f"{models_dict[args.model]=}")

    if args.fine_tune_model:
        if args.grid_search_method == "Optuna":
            logger.info("Optimizing hyperparameters using Optuna")
            estimator, best_params = optuna_optimize(
                args.model,
                X_train,
                y_train,
                X_test,
                y_test,
                optuna_n_trials=int(args.optuna_n_trials),
            )
        else:
            logger.info("Fine-tuning model using traditional grid search")
            estimator, best_params = fine_tune_estimator(args, X_train, y_train)
    else:
        logger.info("Training model without fine-tuning")
        if args.parallel_computation and args.model in n_jobs_keyword_available_models:
            args.parameters["n_jobs"] = n_jobs
        if args.model == "gpr" and kernel is not None:
            estimator = models_dict[args.model](kernel, **args.parameters)
        else:
            estimator = models_dict[args.model](**args.parameters)

    if args.learning_curve_train_sizes is not None and args.cross_validation:
        learn_curve(
            estimator,
            X,
            y,
            sizes=args.learning_curve_train_sizes,
            n_jobs=n_jobs,
            cv=int(args.cv_fold),
        )

    if not args.fine_tune_model:
        logger.info("Training model")
        estimator.fit(X_train, y_train)
        logger.info("Training complete")
    else:
        logger.info("Using best estimator from grid search")

    if args.save_pretrained_model:
        logger.info(f"Saving model to {pre_trained_file}")
        dump((estimator, yscaler), pre_trained_file)
        logger.success("Trained model saved")

    if args.analyse_shapley_values:
        analyse_shap_values(estimator, X)

    logger.info(f"Saving model to {pre_trained_file}")
    timeStamp = datetime.now().strftime("%m/%d/%Y, %I:%M:%S %p")

    trained_params = estimator.get_params()
    if args.model == "catboost":
        logger.info(f"{estimator.get_all_params()=}")
        trained_params = trained_params | estimator.get_all_params()

    # if args.save_pretrained_model:
    save_parameters(".parameters.user.json", args.parameters)
    save_parameters(".parameters.trained.json", trained_params)

    test_stats = get_stats(estimator, X_test, y_test)
    train_stats = get_stats(estimator, X_train, y_train)

    if inverse_scaling and yscaler:
        y = yscaler.inverse_transform(y.reshape(-1, 1)).flatten()
    if inverse_transform and ytransformation:
        if y_transformer:
            y = y_transformer.inverse_transform(y.reshape(-1, 1)).flatten()
        else:
            y = get_transformed_data(
                y, ytransformation, inverse=True, lambda_param=boxcox_lambda_param
            )

    results = {
        "data_shapes": {
            "X": X.shape,
            "y": y.shape,
            "X_test": X_test.shape,
            "y_test": y_test.shape,
            "X_train": X_train.shape,
            "y_train": y_train.shape,
        },
        "test_stats": {
            "r2": test_stats[0],
            "mse": test_stats[1],
            "rmse": test_stats[2],
            "mae": test_stats[3],
        },
        "train_stats": {
            "r2": train_stats[0],
            "mse": train_stats[1],
            "rmse": train_stats[2],
            "mae": train_stats[3],
        },
    }

    results["bootstrap"] = args.bootstrap
    if args.bootstrap:
        results["bootstrap_nsamples"] = args.bootstrap_nsamples
        results["noise_percentage"] = args.noise_percentage

    # if args.save_pretrained_model:
    with open(f"{pre_trained_file.with_suffix('.dat.json')}", "w") as f:
        json.dump(
            {
                "test": {
                    "y_true": test_stats[4].tolist(),
                    "y_pred": test_stats[5].tolist(),
                    "y_linear_fit": test_stats[6].tolist(),
                },
                "train": {
                    "y_true": train_stats[4].tolist(),
                    "y_pred": train_stats[5].tolist(),
                    "y_linear_fit": train_stats[6].tolist(),
                },
            },
            f,
            indent=4,
        )

    # Additional validation step
    results["cross_validation"] = args.cross_validation

    if args.cross_validation and not args.fine_tune_model and test_size > 0:
        results["cv_fold"] = int(args.cv_fold)
        cv_scores = compute_cv(int(args.cv_fold), estimator, X, y)
        results["cv_scores"] = cv_scores

    if args.fine_tune_model:
        results["best_params"] = best_params
        best_params_savefile = pre_trained_file.with_suffix(
            f".{args.grid_search_method}.best_params.json"
        )

        best_params_contents = {
            "values": best_params,
            "model": args.model,
            "timestamp": timeStamp,
            "cv_fold": args.cv_fold,
            "grid_search_method": args.grid_search_method,
        }

        with open(
            best_params_savefile,
            "w",
        ) as f:
            json.dump(best_params_contents, f, indent=4)
            logger.info(f"Results saved to {best_params_savefile}")

    results["timestamp"] = timeStamp

    end_time = perf_counter()
    logger.info(f"Training completed in {(end_time - start_time):.2f} s")
    results["time"] = f"{(end_time - start_time):.2f} s"

    with open(
        pre_trained_file.with_suffix(".results.json"),
        "w",
    ) as f:
        json.dump(results, f, indent=4)
        logger.info(f"Results saved to {pre_trained_file.with_suffix('.results.json')}")

    return results


def convert_to_float(value: Union[str, float]) -> float:
    try:
        return float(value)
    except ValueError:
        if isinstance(value, str) and "-" in value:
            parts = value.split("-")
            if len(parts) == 2 and parts[0] and parts[1]:
                try:
                    return (float(parts[0]) + float(parts[1])) / 2
                except ValueError:
                    pass
        if skip_invalid_y_values:
            return np.nan
        raise


n_jobs = None
backend = "threading"
skip_invalid_y_values = False
ytransformation: str = None
y_transformer = None
yscaling: str = "StandardScaler"
yscaler = None
boxcox_lambda_param = None

inverse_scaling = True
inverse_transform = True


def main(args: Args):
    if args.model not in models_dict:
        raise ValueError(f"{args.model} not implemented in yet!")

    global \
        n_jobs, \
        backend, \
        skip_invalid_y_values, \
        ytransformation, \
        yscaling, \
        yscaler, \
        boxcox_lambda_param, \
        inverse_scaling, \
        inverse_transform, \
        y_transformer

    ytransformation = args.ytransformation
    yscaling = args.yscaling
    inverse_scaling = args.inverse_scaling
    inverse_transform = args.inverse_transform

    skip_invalid_y_values = args.skip_invalid_y_values
    if args.parallel_computation:
        n_jobs = int(args.n_jobs)
        backend = args.parallel_computation_backend

    logger.info(f"Training {args.model} model")
    logger.info(f"{args.training_file['filename']}")

    X = np.load(args.vectors_file, allow_pickle=True)
    X = np.array(X, dtype=float)

    logger.info(f"{X.shape=}, {X.dtype=}")

    # load training data from file
    ddf = read_as_ddf(
        args.training_file["filetype"],
        args.training_file["filename"],
        args.training_file["key"],
        use_dask=args.use_dask,
    )

    y = None
    if args.use_dask:
        ddf = ddf.repartition(npartitions=args.npartitions)
        with ProgressBar():
            y = ddf[args.training_column_name_y].compute()
    else:
        y = ddf[args.training_column_name_y]
    logger.info(f"{type(y)=}")

    if not isinstance(y, pd.Series):
        y = pd.Series(y)

    logger.info("Apply the conversion function to handle strings like 188.0 - 189.0")
    y = y.apply(convert_to_float)

    # Keep track of valid indices
    valid_y_indices = y.notna()
    y = y[valid_y_indices]
    X = X[valid_y_indices]
    logger.info(f"{X.shape=} after removing invalid y")

    y = y.values

    invalid_embedding_indices = [i for i, arr in enumerate(X) if np.all(arr == 0)]

    # Initially, mark all as valid
    valid_embedding_mask = np.ones(len(X), dtype=bool)
    # Then, mark invalid indices as False
    valid_embedding_mask[invalid_embedding_indices] = False

    X = X[
        valid_embedding_mask
    ]  # Keep only the rows that are marked as True in the valid_embedding_mask
    y = y[valid_embedding_mask]

    logger.info(f"{X.shape=} after removing invalid X i.e., all zeros")

    y_transformer = None

    if ytransformation:
        if ytransformation == "boxcox":
            logger.info("Applying boxcox transformation")
            y, boxcox_lambda_param = get_transformed_data(
                y, ytransformation, get_other_params=True
            )
            logger.info(f"{boxcox_lambda_param=}")
        elif ytransformation == "yeo_johnson":
            logger.info("Applying yeo-johnson transformation")
            y, y_transformer = get_transformed_data(
                y, ytransformation, get_other_params=True
            )
            logger.info(f"{y_transformer=}")
        else:
            logger.info(f"Applying {ytransformation} transformation")
            y = get_transformed_data(y, ytransformation)
    else:
        logger.warning("No transformation applied")

    yscaler = None
    if yscaling:
        yscaler = Yscalers[yscaling]()
        y = yscaler.fit_transform(y.reshape(-1, 1)).flatten()
    else:
        logger.warning("No scaling applied")

    logger.info(f"{y[:5]=}, {type(y)=}")

    results = None
    if args.parallel_computation:
        with parallel_config(backend, n_jobs=n_jobs):
            logger.info(f"Using {n_jobs} jobs with {backend} backend")
            results = compute(args, X, y)
    else:
        logger.info("Running in serial mode")
        results = compute(args, X, y)

    return results
