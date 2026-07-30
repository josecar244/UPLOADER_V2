import logging
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"


class SensitiveDataFilter(logging.Filter):
    """Filtra y enmascara valores de contraseñas de las llamadas de logging."""
    def filter(self, record):
        if isinstance(record.msg, str):
            import os
            for key in ["TERADATA_PASSWORD", "PASSWORD_INSIGHT", "VERINT_PASS"]:
                secret = os.getenv(key)
                if secret and len(secret) > 2:
                    record.msg = record.msg.replace(secret, "***MASKED***")
            if record.args and isinstance(record.args, tuple):
                new_args = []
                for arg in record.args:
                    if isinstance(arg, str):
                        for key in ["TERADATA_PASSWORD", "PASSWORD_INSIGHT", "VERINT_PASS"]:
                            secret = os.getenv(key)
                            if secret and len(secret) > 2:
                                arg = arg.replace(secret, "***MASKED***")
                    new_args.append(arg)
                record.args = tuple(new_args)
        return True


def setup_logging(
    name: str = "",
    level: int = logging.INFO,
    log_prefix: str = "plantilla",
    log_dir: Path | None = None,
) -> logging.Logger:
    target_dir = Path(log_dir) if log_dir is not None else LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%d%m%Y_%H%M%S")
    log_file = target_dir / f"{log_prefix}_{timestamp}.log"

    logger = logging.getLogger(name)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    sensitive_filter = SensitiveDataFilter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sensitive_filter)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(sensitive_filter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger


logger = setup_logging()
