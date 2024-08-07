from umdalib.utils import load_model
from umdalib.utils import logger
from dataclasses import dataclass
from time import perf_counter
from typing import Callable, Literal
import dask.dataframe as dd
from dask.diagnostics import ProgressBar
from dask import array as da
import pytorch_lightning as pl

logger.info(f"pytorch_lightning version: {pl.__version__}")
# from astrochem_embedding import VICGAE

from pathlib import Path as pt
import numpy as np
from rdkit import Chem
from mol2vec import features
from umdalib.training.read_data import read_as_ddf


import warnings

# suppress `torch.load` with `weights_only=False` warning
warnings.filterwarnings("ignore", category=UserWarning)


@dataclass
class Args:
    filename: str
    filetype: str
    key: str
    npartitions: int
    mol2vec_dim: int
    PCA_dim: int
    df_column: str
    embedding: Literal["VICGAE", "mol2vec"]
    pretrained_model_location: str
    test_mode: bool
    test_smiles: str
    PCA_pipeline_location: str
    embedd_savefile: str


def VICGAE2vec(smi: str, model):
    global invalid_smiles
    smi = str(smi).replace("\xa0", "")
    if smi == "nan":
        return None
    try:
        return model.embed_smiles(smi).numpy().reshape(-1)
    except:
        invalid_smiles.append(smi)
        return np.zeros(32)


invalid_smiles = []


def mol2vec(smi: str, model, radius=1) -> list[np.ndarray]:
    """
    Given a model, convert a SMILES string into the corresponding
    NumPy vector.
    """

    global invalid_smiles
    smi = str(smi).replace("\xa0", "")

    if smi == "nan":
        return None

    # Molecule from SMILES will break on "bad" SMILES; this tries
    # to get around sanitization (which takes a while) if it can
    try:
        mol = Chem.MolFromSmiles(smi, sanitize=False)
        if not mol:
            if not isinstance(smi, str):
                return None
            invalid_smiles.append(str(smi))

        mol.UpdatePropertyCache(strict=False)
        Chem.GetSymmSSSR(mol)
        # generate a sentence from rdkit molecule
        sentence = features.mol2alt_sentence(mol, radius)
        # generate vector embedding from sentence and model
        vector = features.sentences2vec([sentence], model)

        return vector.reshape(-1)

    except:
        if smi not in invalid_smiles and isinstance(smi, str):
            invalid_smiles.append(smi)

        # return np.zeros((1, model.vector_size))
        return np.zeros(model.vector_size)


smi_to_vec_dict: dict[str, Callable] = {
    "VICGAE": VICGAE2vec,
    "mol2vec": mol2vec,
}

embedding: str = "mol2vec"
PCA_pipeline_location: str = None


def get_smi_to_vec_after_pca(smi: str, model):

    fn = smi_to_vec_dict[embedding]
    vector = fn(smi, model)

    pipeline_model = load_model(PCA_pipeline_location, use_joblib=True)
    for step in pipeline_model.steps:
        vector = step[1].transform(vector)

    return vector


def get_smi_to_vec(embedder, pretrained_file, pca_file=None):

    global embedding, PCA_pipeline_location
    embedding = embedder

    if embedding == "mol2vec":
        model = load_model(pretrained_file)
        logger.info(f"Loaded mol2vec model with {model.vector_size} dimensions")
    elif embedding == "VICGAE":
        model = load_model(pretrained_file, use_joblib=True)
        logger.info(f"Loaded VICGAE model")

    PCA_pipeline_location = pca_file
    smi_to_vector = None
    if pca_file:
        logger.info(f"Using PCA pipeline from {pca_file}")
        smi_to_vector = get_smi_to_vec_after_pca
    else:
        smi_to_vector = smi_to_vec_dict[embedding]

    logger.warning(f"{smi_to_vector=}")
    if not callable(smi_to_vector):
        raise ValueError(f"Unknown embedding model: {embedding}")

    return smi_to_vector, model


def main(args: Args):

    logger.info(f"{args=}")

    global invalid_smiles, embedding, PCA_pipeline_location
    invalid_smiles = []

    smi_to_vector, model = get_smi_to_vec(
        args.embedding, args.pretrained_model_location, args.PCA_pipeline_location
    )

    if args.test_mode:
        logger.info(f"Testing with {args.test_smiles}")
        vec: np.ndarray = smi_to_vector(args.test_smiles, model)
        if PCA_pipeline_location:
            if isinstance(vec, da.Array):
                vec = vec.compute()
        logger.info(f"{vec.shape=}\n")
        return {
            "test_mode": {
                "embedded_vector": vec.tolist() if vec is not None else None,
            }
        }

    fullfile = pt(args.filename)
    # location = fullfile.parent
    logger.info(f"Reading {fullfile} as {args.filetype}")

    ddf = read_as_ddf(args.filetype, args.filename, args.key)

    logger.info(f"{args.npartitions=}")
    ddf = ddf.repartition(npartitions=args.npartitions)

    vectors = None
    logger.info(f"Using {args.embedding} for embedding")

    logger.info(f"Using {smi_to_vector} for embedding")
    if not callable(smi_to_vector):
        raise ValueError(f"Unknown embedding model: {args.embedding}")

    vectors: dd = ddf[args.df_column].apply(
        smi_to_vector, args=(model,), meta=(None, np.float32)
    )

    if vectors is None:
        raise ValueError(f"Unknown embedding model: {args.embedding}")

    embedd_savefile = fullfile.with_name(args.embedd_savefile).with_suffix(".npy")
    logger.info(f"{embedd_savefile=}")
    logger.info(f"Begin computing embeddings for {fullfile.stem}...")
    time = perf_counter()

    start_time = perf_counter()
    computed_time = None

    with ProgressBar():
        vec_computed = vectors.compute()
        logger.info(f"{vec_computed.shape=}\n{vec_computed[0]=}")
        vec_computed = np.vstack(
            vec_computed
        )  # stack the arrays (n_samples, n_features)

        computed_time = f"{(perf_counter() - start_time):.2f} s"
        np.save(embedd_savefile, vec_computed)

    logger.info(f"{vec_computed.shape=}")

    logger.info(
        f"Embeddings computed in {(perf_counter() - time):.2f} s and saved to {embedd_savefile.name}"
    )

    # \xa0 is a non-breaking space in Latin1 (ISO 8859-1), also known as NBSP in Unicode. It's a character that prevents an automatic line break at its position. In HTML, it's often used to create multiple spaces that are visible.
    invalid_smiles = [
        smiles.replace("\xa0", "").strip()
        for smiles in invalid_smiles
        # if isinstance(smiles, str)
    ]
    invalid_smiles_filename = fullfile.with_name(
        f"[INVALID_entries]_{args.embedd_savefile}"
    ).with_suffix(".txt")
    if len(invalid_smiles) > 0:
        with open(invalid_smiles_filename, "w") as f:
            for smiles in invalid_smiles:
                f.write(smiles + "\n")

    return {
        "file_mode": {
            "name": embedd_savefile.name,
            "shape": vec_computed.shape[0],
            "invalid_smiles": len(invalid_smiles),
            "invalid_smiles_file": str(invalid_smiles_filename),
            "saved_file": str(embedd_savefile),
            "computed_time": computed_time,
        }
    }
