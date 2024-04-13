import sys
import umdapy
from multiprocessing import cpu_count
from umdapy.utils import NPARTITIONS, RAM_IN_GB


def main(args=""):
    version_info = sys.version_info
    version = f"{version_info.major}.{version_info.minor}.{version_info.micro}"
    return {
        "python": version,
        "umdapy": umdapy.__version__,
        "cpu_count": cpu_count(),
        "ram": RAM_IN_GB,
        "npartitions": NPARTITIONS,
    }
