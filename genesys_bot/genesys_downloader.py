"""
Módulo de entrada retrocompatible para Genesys Bot.
Redirige la ejecución al orquestador modular principal (main.py).
"""
import sys
from pathlib import Path

_BOT_DIR = Path(__file__).parent.resolve()
_PROJECT_ROOT = _BOT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from genesys_bot.main import main

if __name__ == "__main__":
    main()
