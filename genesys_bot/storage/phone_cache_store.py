import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List

from genesys_bot.config import TELEFONOS_CACHE_FILE
from genesys_bot.logger import get_logger

logger = get_logger("PhoneCacheStore")


class PhoneCacheStore:
    def __init__(self, cache_file: Path = TELEFONOS_CACHE_FILE):
        self.cache_file = Path(cache_file)

    def cargar(self) -> Dict[str, List[str]]:
        if not self.cache_file.exists():
            return {}
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error leyendo caché de teléfonos ({self.cache_file}): {e}")
            return {}

    def guardar(self, cache: Dict[str, List[str]]) -> None:
        target_dir = self.cache_file.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            with tempfile.NamedTemporaryFile("w", dir=str(target_dir), delete=False, encoding="utf-8") as tf:
                json.dump(cache, tf, indent=2, ensure_ascii=False)
                temp_path = tf.name

            os.replace(temp_path, str(self.cache_file))
        except Exception as e:
            logger.error(f"Error guardando caché de teléfonos atómicamente: {e}")
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.remove(temp_path)
