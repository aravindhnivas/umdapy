import sys
import json
import warnings
from importlib import import_module
from umdalib.utils import logger, Paths

log_dir = Paths().app_log_dir


class MyClass(object):
    @logger.catch
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


if __name__ == "__main__":
    pyfile = sys.argv[1]
    args = None
    if len(sys.argv) > 2:
        args = json.loads(sys.argv[2])

    logger.info(f"{args=}")
    logger.info(f"{pyfile=}\n")
    args = MyClass(**args)

    with warnings.catch_warnings(record=True) as warn:
        pyfunction = import_module(f"umdalib.{pyfile}")
        if args:
            result = pyfunction.main(args)
            if result:
                logger.success(f"{result=}")
                with open(log_dir / f"{pyfile}.json", "w") as f:
                    json.dump(result, f, indent=4)
                    logger.success(f"Result saved to {log_dir / f'{pyfile}.json'}")
        else:
            pyfunction.main()
