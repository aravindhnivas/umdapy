from dataclasses import dataclass
import json
import pandas as pd
from umdalib.training.read_data import read_as_ddf
from umdalib.utils import logger
from collections import Counter
import multiprocessing
from pathlib import Path as pt
from typing import Optional

# Constants for column names
COLUMN_ATOMS = "'No. of atoms'"
COLUMN_ELEMENTS = "Elements"
COLUMN_IS_AROMATIC = "IsAromatic"
COLUMN_IS_NON_CYCLIC = "IsNonCyclic"
COLUMN_IS_CYCLIC_NON_AROMATIC = "IsCyclicNonAromatic"


def apply_filters_to_df(
    x: pd.Series,
    min_atomic_number: Optional[int],
    max_atomic_number: Optional[int],
    filter_elements: list[str],
    filter_structures: list[str],
) -> Optional[pd.Series]:

    # Filter based on atomic number
    if min_atomic_number is not None and x[COLUMN_ATOMS] < min_atomic_number:
        return None
    if max_atomic_number is not None and x[COLUMN_ATOMS] > max_atomic_number:
        return None

    # Filter based on elements
    if filter_elements:
        if any(key in x[COLUMN_ELEMENTS] for key in filter_elements):
            return None

    # Filter based on structures
    if filter_structures:
        if "aromatic" in filter_structures and x[COLUMN_IS_AROMATIC]:
            return None
        if "non-cyclic" in filter_structures and x[COLUMN_IS_NON_CYCLIC]:
            return None
        if (
            "cyclic non-aromatic" in filter_structures
            and x[COLUMN_IS_CYCLIC_NON_AROMATIC]
        ):
            return None

    return x


@dataclass
class Args:
    analysis_file: str
    min_atomic_number: int
    max_atomic_number: int
    size_count_threshold: int
    elemental_count_threshold: int
    filter_elements: list[str]
    filter_structures: list[str]


def main(args: Args):
    analysis_file = pt(args.analysis_file)
    df = pd.read_csv(analysis_file)
    df["ElementCategories"] = df["ElementCategories"].apply(
        lambda x: Counter(json.loads(x))
    )
    df["Elements"] = df["Elements"].apply(lambda x: Counter(json.loads(x)))

    final_df: pd.DataFrame = df.apply(
        apply_filters_to_df,
        axis=1,
        args=(
            args.min_atomic_number,
            args.max_atomic_number,
            args.filter_elements,
            args.filter_structures,
        ),
    )
    final_df = final_df.dropna()  # Drop rows that were filtered out
    logger.info("Filtered DataFrame length: %d", len(final_df))
    filtered_file_path = analysis_file.parent / "filtered_" / analysis_file.name
    final_df.to_csv(filtered_file_path)

    return {"filtered_file": analysis_file.parent / "filtered_" / analysis_file.name}
