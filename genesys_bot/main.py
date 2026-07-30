import sys
from pathlib import Path

# Garantizar que el directorio raíz del proyecto esté en PYTHONPATH
_BOT_DIR = Path(__file__).parent.resolve()
_PROJECT_ROOT = _BOT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from genesys_bot.logger import get_logger
from genesys_bot.services.genesys_browser import GenesysBrowserAutomation
from genesys_bot.services.outlook_service import OutlookService
from genesys_bot.services.teradata_service import TeradataService

logger = get_logger("MainOrchestrator")


def main():
    logger.info("=== Iniciando automatización Genesys Bot & Outlook ===")

    # 1. Extracción desde Outlook Desktop
    try:
        outlook_service = OutlookService(asunto_filtro="Solicitud de audio")
        solicitudes = outlook_service.obtener_solicitudes(solo_ultimo=True)
    except Exception as e:
        logger.error(f"Fallo al conectar o extraer solicitudes de Outlook: {e}")
        solicitudes = []

    if not solicitudes:
        logger.warning("No se obtuvieron solicitudes de Outlook. Proceso cancelado.")
        return

    logger.info(f"Se encontraron {len(solicitudes)} solicitud(es) iniciales.")
    for idx, s in enumerate(solicitudes, 1):
        logger.info(f"  [{idx}] Promotor={s.reg_ev} | DNI={s.dni} | Archivo={s.nombre_archivo}")

    # 2. Enriquecer registros con teléfonos desde Teradata / Caché local
    teradata_service = TeradataService()
    solicitudes_listas = teradata_service.enriquecer_solicitudes(solicitudes)

    if not solicitudes_listas:
        logger.warning("Ninguna solicitud cuenta con teléfono de gestión. Finalizando proceso.")
        return

    # 3. Automatización de descargas en Genesys Cloud UI vía Chrome CDP
    automation = GenesysBrowserAutomation()
    automation.ejecutar_descargas(solicitudes_listas)

    logger.info("=== Ejecución completada exitosamente ===")


if __name__ == "__main__":
    main()
