from dataclasses import dataclass
from multiprocessing import cpu_count
from typing import Dict, Union

import dask.dataframe as dd
import numpy as np
import pandas as pd
from dask.diagnostics import ProgressBar

from umdalib.logger import logger

NPARTITIONS = cpu_count() * 5


def read_as_ddf(
    filetype: str, filename: str, key: str = None, computed=False, use_dask=False
):
    logger.info(f"Reading {filename} as {filetype} using dask: {use_dask}")

    if not filetype:
        filetype = filename.split(".")[-1]
        logger.info(f"{filetype=}")

    df_fn = None
    if use_dask:
        df_fn = dd
        logger.info(f"Using Dask: {df_fn=}")
    else:
        df_fn = pd
        logger.info(f"Using Pandas: {df_fn=}")

    ddf: Union[dd.DataFrame, pd.DataFrame] = None

    if filetype == "smi":
        data = np.loadtxt(filename, dtype=str, ndmin=2)
        if data[0][0].lower() == "smiles":
            data = data[1:]

        ddf = pd.DataFrame(data, columns=["SMILES"])
        if use_dask:
            ddf = dd.from_pandas(ddf)

        logger.info(f"Columns in the DataFrame: {ddf.columns.tolist()}")
    elif filetype == "csv":
        ddf = df_fn.read_csv(filename)
    elif filetype == "parquet":
        ddf = df_fn.read_parquet(filename)
    elif filetype == "hdf":
        if not key:
            raise ValueError("Key is required for HDF files")
        ddf = df_fn.read_hdf(filename, key)
    elif filetype == "json":
        ddf = df_fn.read_json(filename)
    else:
        raise ValueError(f"Unknown filetype: {filetype}")

    if computed and use_dask:
        with ProgressBar():
            ddf = ddf.compute()

    logger.info(f"{type(ddf)=}")
    return ddf


@dataclass
class Args:
    filename: str
    filetype: str
    key: str
    rows: Dict[str, Union[int, str]]
    use_dask: bool


def main(args: Args):
    logger.info(f"Reading {args.filename} as {args.filetype}")
    logger.info(f"Using Dask: {args.use_dask}")

    ddf = read_as_ddf(args.filetype, args.filename, args.key, use_dask=args.use_dask)
    logger.info(f"{type(ddf)=}")

    shape = ddf.shape[0]
    if args.use_dask:
        shape = shape.compute()
    logger.info(f"read_data file: Shape: {shape}")

    data = {
        "columns": ddf.columns.values.tolist(),
    }

    count = int(args.rows["value"])

    with ProgressBar():
        if args.rows["where"] == "head":
            nrows = ddf.head(count).fillna("")
        elif args.rows["where"] == "tail":
            nrows = ddf.tail(count).fillna("")
        data["nrows"] = nrows.to_dict(orient="records")
        data["shape"] = shape
        data["index_name"] = ddf.index.name

    logger.info(f"{type(data)=}")

    return data
