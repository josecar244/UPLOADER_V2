import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from genesys_bot.config import NO_ENCONTRADOS_FILE, TRACKING_FILE
from genesys_bot.logger import get_logger
from genesys_bot.models import EstadoRegistro, RegistroTracking, SolicitudAudio

logger = get_logger("TrackingStore")


class TrackingStore:
    def __init__(self, tracking_file: Path = TRACKING_FILE, csv_file: Path = NO_ENCONTRADOS_FILE):
        self.tracking_file = Path(tracking_file)
        self.csv_file = Path(csv_file)

    def cargar(self) -> Dict[str, RegistroTracking]:
        if not self.tracking_file.exists():
            return {}
        try:
            with open(self.tracking_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {clave: RegistroTracking.from_dict(val) for clave, val in data.items()}
        except Exception as e:
            logger.error(f"Error cargando tracking JSON ({self.tracking_file}): {e}")
            return {}

    def guardar(self, tracking: Dict[str, RegistroTracking]) -> None:
        data = {clave: reg.to_dict() for clave, reg in tracking.items()}
        target_dir = self.tracking_file.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        # Escritura atómica vía archivo temporal
        try:
            with tempfile.NamedTemporaryFile("w", dir=str(target_dir), delete=False, encoding="utf-8") as tf:
                json.dump(data, tf, indent=2, ensure_ascii=False)
                temp_path = tf.name

            os.replace(temp_path, str(self.tracking_file))
        except Exception as e:
            logger.error(f"Error guardando tracking atómicamente: {e}")
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.remove(temp_path)

    def marcar_como_procesado(self, reg_ev: str, dni: str, estado: EstadoRegistro) -> None:
        tracking = self.cargar()
        clave = f"{reg_ev}|{dni}"
        tracking[clave] = RegistroTracking(
            reg_ev=reg_ev,
            dni=dni,
            estado=estado,
            timestamp=datetime.now().isoformat(),
        )
        self.guardar(tracking)

    def filtrar_no_procesados(self, solicitudes: List[SolicitudAudio]) -> List[SolicitudAudio]:
        tracking = self.cargar()
        filtrados = []
        for sol in solicitudes:
            if sol.clave_unica in tracking:
                prev = tracking[sol.clave_unica]
                logger.info(f"[SKIP] {sol.reg_ev} | DNI {sol.dni} ya procesado ({prev.estado.value})")
            else:
                filtrados.append(sol)
        return filtrados

    def registrar_no_encontrado(self, reg_ev: str, dni: str) -> None:
        try:
            self.csv_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.csv_file, "a", encoding="utf-8") as f:
                f.write(f"{reg_ev},{dni}\n")
        except Exception as e:
            logger.error(f"Error registrando no encontrado en CSV ({self.csv_file}): {e}")
