import datetime
import decimal
import pathlib
import tempfile
import types
from multiprocessing import cpu_count
from os import environ
from pathlib import Path as pt
from platform import system

import joblib
import numpy as np
from gensim.models import word2vec
from loguru import logger
from psutil import virtual_memory
import json

RAM_IN_GB = virtual_memory().total / 1024**3
NPARTITIONS = cpu_count() * 5
BUNDLE_IDENTIFIER = "com.umdaui.dev"


class Paths:
    def get_app_log_dir(self):
        # Linux: Resolves to ${configDir}/${bundleIdentifier}/logs.
        # macOS: Resolves to ${homeDir}/Library/Logs/{bundleIdentifier}
        # Windows: Resolves to ${configDir}/${bundleIdentifier}/logs.

        if system() == "Linux":
            return pt(environ["HOME"]) / ".config" / BUNDLE_IDENTIFIER / "logs"
        elif system() == "Darwin":
            return pt(environ["HOME"]) / "Library" / "Logs" / BUNDLE_IDENTIFIER
        elif system() == "Windows":
            return pt(environ["APPDATA"]) / BUNDLE_IDENTIFIER / "logs"
        else:
            raise NotImplementedError(f"Unknown system: {system()}")

    def get_temp_dir(self):
        return pt(tempfile.gettempdir()) / BUNDLE_IDENTIFIER

    @property
    def app_log_dir(self):
        return self.get_app_log_dir()

    @property
    def temp_dir(self):
        return self.get_temp_dir()


logfile = Paths().app_log_dir / "umdapy_server.log"
logger.info(f"Logging to {logfile}")

logger.add(
    logfile,
    rotation="10 MB",
    compression="zip",
)

logger.info(f"loaded joblib: {joblib.__version__}")


def load_model(filepath: str, use_joblib: bool = False):
    logger.info(f"Loading model from {filepath}")
    if not pt(filepath).exists():
        logger.error(f"Model file not found: {filepath}")
        raise FileNotFoundError(f"Model file not found: {filepath}")
    logger.info(f"Model loaded from {filepath}")

    if use_joblib:
        return joblib.load(filepath)
    return word2vec.Word2Vec.load(str(filepath))


def convert_to_json_compatible(obj):
    if isinstance(obj, dict):
        return {key: convert_to_json_compatible(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_json_compatible(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_to_json_compatible(item) for item in obj)
    elif isinstance(obj, set):
        return list(convert_to_json_compatible(item) for item in obj)
    elif isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    elif isinstance(obj, decimal.Decimal):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.generic):
        return obj.item()
    elif isinstance(obj, pathlib.Path):
        return str(obj)
    elif isinstance(obj, types.FunctionType):
        return f"<function {obj.__name__}>"
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    else:
        # General handling for class objects
        if hasattr(obj, "__dict__"):
            return {
                "__class__": obj.__class__.__name__,
                "__module__": obj.__class__.__module__,
                "attributes": convert_to_json_compatible(obj.__dict__),
            }
        else:
            return str(obj)


def safe_json_dump(
    obj: dict, filename: str | pt, overwrite=True, create_dir: bool = True
):
    if not isinstance(obj, dict):
        raise ValueError(f"Expected a dictionary, got {type(obj)}")

    if not isinstance(filename, (str, pt)):
        raise ValueError(f"Expected a string or Path, got {type(filename)}")

    if isinstance(filename, str):
        filename = pt(filename)

    if filename.suffix != ".json":
        filename = filename.with_suffix(".json")

    if filename.exists():
        if overwrite:
            filename.unlink()
            logger.warning(f"Deleted existing file: {filename} to overwrite")
        else:
            logger.error(f"File already exists: {filename}")
            raise FileExistsError(f"File already exists: {filename}")

    if not filename.parent.exists() and create_dir:
        logger.warning(f"Creating directory: {filename.parent}")
        filename.parent.mkdir(parents=True)

    try:
        logger.info(f"Saving to {filename}")
        with open(filename, "w") as f:
            json.dump(convert_to_json_compatible(obj), f, indent=4)
            logger.success(f"{filename.name} saved successfully to {filename.parent}")
    except Exception as e:
        logger.error(f"Error saving to {filename}: {e}")
        raise e
