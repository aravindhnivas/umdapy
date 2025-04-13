from dataclasses import dataclass, fields
import json
from typing import Literal
import joblib
import numpy as np
from sklearn.decomposition import PCA, KernelPCA, FactorAnalysis
from sklearn.pipeline import Pipeline
import umap
from sklearn.manifold import TSNE, Isomap, SpectralEmbedding
from sklearn.preprocessing import StandardScaler
import phate
import trimap
from umdalib.logger import logger
from pathlib import Path as pt


@dataclass
class Args:
    params: dict
    vector_file: str
    dr_savefile: str
    embedder_loc: str
    method: Literal[
        "PCA",
        "UMAP",
        "t-SNE",
        "KernelPCA",
        "PHATE",
        "ISOMAP",
        "LaplacianEigenmaps",
        "TriMap",
        "FactorAnalysis",
    ] = "PCA"
    embedder_name: str = "mol2vec"
    scaling: bool = True
    save_diagnostics: bool = True
    diagnostics_file: str = "dr_diagnostics.json"


def parge_args(args_dict: dict) -> Args:
    # Build kwargs using defaults where necessary
    kwargs = {}
    for field in fields(Args):
        if field.name in args_dict:
            kwargs[field.name] = args_dict[field.name]
        elif field.default is not field.default_factory:  # default value exists
            kwargs[field.name] = field.default
        elif field.default_factory is not None:  # default factory (rare case)
            kwargs[field.name] = field.default_factory()
        else:
            raise ValueError(f"Missing required field: {field.name}")
    return Args(**kwargs)


def save_diagnostics_to_json(
    method: str, reducer, reduced: np.ndarray, diagnostics_file: str
):
    diagnostics = {
        "method": method,
        "reduced_shape": list(reduced.shape),
    }

    if method in ["PCA", "FactorAnalysis"]:
        if hasattr(reducer, "explained_variance_ratio_"):
            diagnostics["explained_variance_ratio"] = (
                reducer.explained_variance_ratio_.tolist()
            )
            diagnostics["cumulative_variance"] = np.cumsum(
                reducer.explained_variance_ratio_
            ).tolist()
    elif method in ["KernelPCA"]:
        if hasattr(reducer, "lambdas_"):
            diagnostics["kernel_eigenvalues"] = reducer.lambdas_.tolist()
    elif method in ["t-SNE", "UMAP", "PHATE", "ISOMAP", "LaplacianEigenmaps", "TriMap"]:
        # Generic notes
        diagnostics["info"] = (
            f"{method} does not provide variance info. Saved 2D shape only."
        )

    with open(diagnostics_file, "w") as f:
        json.dump(diagnostics, f, indent=4)
    logger.info(f"Saved diagnostics to {diagnostics_file}")


def main(args: Args):
    logger.info("Starting dimensionality reduction pipeline")
    args = parge_args(args.__dict__)
    # logger.info(json.dumps(args.__dict__, indent=4))

    logger.info(f"Loading embeddings from {args.vector_file}")

    # return

    # Load data
    X: np.ndarray = np.load(args.vector_file, allow_pickle=True)
    logger.info(f"{X.shape=}")

    logger.info(f"Applying {args.method} with parameters: {args.params}")

    # Apply dimensionality reduction
    if args.method == "PCA":
        reducer = PCA(**args.params)
    elif args.method == "UMAP":
        reducer = umap.UMAP(**args.params)
    elif args.method == "t-SNE":
        reducer = TSNE(**args.params)
    elif args.method == "KernelPCA":
        reducer = KernelPCA(**args.params)
    elif args.method == "PHATE":
        reducer = phate.PHATE(**args.params)
    elif args.method == "ISOMAP":
        reducer = Isomap(**args.params)
    elif args.method == "LaplacianEigenmaps":
        reducer = SpectralEmbedding(**args.params)
    elif args.method == "TriMap":
        reducer = trimap.TRIMAP(**args.params)
    elif args.method == "FactorAnalysis":
        reducer = FactorAnalysis(**args.params)
    else:
        logger.error(f"Unsupported method: {args.method}")
        raise ValueError(f"Unsupported method: {args.method}")

    steps = []
    if args.scaling:
        steps.append(("scaler", StandardScaler()))

    # Add the DR method
    steps.append(("reducer", reducer))
    pipeline = Pipeline(steps)

    reduced = pipeline.fit_transform(X)
    logger.info(f"{reduced.shape=}")

    np.save(args.dr_savefile, reduced)
    logger.info(f"Saved reduced data to {args.dr_savefile}")

    dr_savefile = pt(args.dr_savefile)
    # save_loc = dr_savefile.parent / args.method.lower()

    pipeline_loc = dr_savefile.parent / "dr_pipelines"
    pipeline_loc.mkdir(parents=True, exist_ok=True)

    joblib.dump(pipeline, pipeline_loc / f"{dr_savefile.stem}.joblib")
    logger.info("Saved DR pipeline to dr_pipeline.joblib")

    save_diagnostics_file = (
        dr_savefile.parent / "dr_diagnostics" / f"{dr_savefile.stem}.diagnostics.json"
    )

    save_diagnostics_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving diagnostics to {save_diagnostics_file}")
    if args.save_diagnostics:
        save_diagnostics_to_json(args.method, reducer, reduced, save_diagnostics_file)
