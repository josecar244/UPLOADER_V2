import os
import sys
import time
import logging

# Asegurar importe de dependencias del proyecto
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.verint_transcript_extractor import extract_transcript_by_call_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("TestSingleCall")

if __name__ == "__main__":
    # ID de prueba proporcionado por el usuario
    TEST_CALL_ID = "86698221-dbb3-459d-a0e4-ae1190cca509"
    OUTPUT_DIR = "./transcripciones_prueba"
    
    logger.info(f"=== INICIANDO PRUEBA VISIBLE DE EXTRACCIÓN EN VERINT ===")
    logger.info(f"Call ID a procesar: {TEST_CALL_ID}")
    logger.info("Se abrirá la ventana del navegador (headless=False) para que puedas visualizar la interacción.")
    
    try:
        txt_path = extract_transcript_by_call_id(
            call_id=TEST_CALL_ID,
            headless=False,
            output_dir=OUTPUT_DIR
        )
        logger.info(f"¡ÉXITO! Transcripción guardada correctamente en: {os.path.abspath(txt_path)}")
    except Exception as e:
        logger.error(f"Error durante la prueba de extracción: {e}")
        print("\n" + "="*70)
        print(" [PAUSA DE INSPECCIÓN MANUAL]")
        print(" El navegador Chrome se encuentra ABIERTO en tu pantalla con los filtros aplicados.")
        print(" Revisa la pantalla de Verint para ver qué ocurrió.")
        print(" Presiona la tecla ENTER en esta consola cuando desees cerrar el navegador...")
        print("="*70)
        input()

