import logging
import sys
from pathlib import Path


def get_logger(name: str = "GenesysBot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # Formato uniforme
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Handler a consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Handler opcional a archivo de log
    try:
        log_dir = Path(__file__).parent / "logs"
        log_dir.mkdir(exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "bot.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception:
        pass

    return logger
