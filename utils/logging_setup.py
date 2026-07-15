import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "bot.log"

LOG_MAX_BYTES = 5_000_000
LOG_BACKUP_COUNT = 5


def setup_logging(level: int = logging.INFO) -> None:
    LOG_DIR.mkdir(exist_ok=True)

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    if not any(isinstance(h, RotatingFileHandler) and h.baseFilename == str(LOG_FILE.resolve())
               for h in root.handlers):
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
