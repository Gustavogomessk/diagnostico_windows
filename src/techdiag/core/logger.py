import logging
from logging.handlers import RotatingFileHandler

from .config import data_dir


def setup_logging() -> logging.Logger:
    log = logging.getLogger("techdiag")
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)
    logs = data_dir() / "logs"
    logs.mkdir(exist_ok=True)
    fh = RotatingFileHandler(logs / "techdiag.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(fh)
    return log
