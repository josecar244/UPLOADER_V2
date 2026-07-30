import argparse
import os
import sys
import time
import logging
from typing import List, Dict, Any, Optional

# Asegurar importación de módulos del proyecto
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.verint_transcript_extractor import (
    initialize_verint_session,
    extract_single_transcript_in_session,
    get_pending_calls_from_teradata
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("BatchVerintExtractor")

def run_batch_extraction(
    call_items: Optional[List[Dict[str, Any]]] = None,
    periodo: Optional[str] = None,
    headless: bool = True,
    output_dir: str = "./transcripciones"
):
    """
    Ejecuta un barrido masivo de transcripciones reutilizando UNA SOLA SESIÓN de Verint.
    
    :param call_items: Lista opcional de diccionarios con {'call_id': '...', 'metadata': {...}}.
                        Si es None, se consulta Teradata automáticamente.
    :param periodo: Periodo en formato YYYYMM para filtrar en Teradata (ej: '202607'). Si es None, toma el mes actual.
    :param headless: True para ejecución en segundo plano (producción), False para ver la ventana.
    :param output_dir: Carpeta donde guardar los archivos .txt
    """
    if call_items is None:
        logger.info(f"No se proporcionó lista de llamadas. Consultando Teradata para PERIODO={periodo or 'Actual'}...")
        call_items = get_pending_calls_from_teradata(periodo=periodo)

    if not call_items:
        logger.warning("No se encontraron llamadas para procesar.")
        return

    os.makedirs(output_dir, exist_ok=True)
    logger.info(f"=== Iniciando Barrido Masivo de {len(call_items)} llamadas en Verint (Headless={headless}) ===")
    
    # 1. Iniciar sesión y cargar Speech Analytics una única vez
    playwright, browser, context, page = initialize_verint_session(headless=headless)
    
    successful_count = 0
    failed_count = 0
    failed_calls = []

    try:
        # 2. Recorrer la lista de llamadas 1 por 1 dentro de la misma sesión abierta
        for idx, item in enumerate(call_items, 1):
            call_id = item.get('call_id')
            metadata = item.get('metadata', {})
            
            # Omitir si la llamada ya fue procesada y existe su archivo .txt
            existing_files = [f for f in os.listdir(output_dir) if f.endswith(f"{call_id}.txt")] if os.path.exists(output_dir) else []
            if existing_files:
                logger.info(f"[SKIP {idx}/{len(call_items)}] La llamada {call_id} ya fue procesada anteriormente ({existing_files[0]}). Omitiendo...")
                successful_count += 1
                continue

            logger.info(f"\n--- Processing [{idx}/{len(call_items)}] | Call ID: {call_id} ---")
            
            try:
                txt_path = extract_single_transcript_in_session(
                    page=page,
                    call_id=call_id,
                    metadata=metadata,
                    output_dir=output_dir
                )
                if txt_path and os.path.exists(txt_path):
                    logger.info(f"[SUCCESS {idx}/{len(call_items)}] Guardado: {txt_path}")
                    successful_count += 1
                else:
                    logger.error(f"[FAILED {idx}/{len(call_items)}] No se pudo exportar TXT para {call_id}")
                    failed_count += 1
                    failed_calls.append(call_id)
            except Exception as e:
                logger.error(f"[ERROR {idx}/{len(call_items)}] Error procesando {call_id}: {e}")
                failed_count += 1
                failed_calls.append(call_id)
            
            # Pequeña pausa de estabilización entre llamadas
            time.sleep(2)

    finally:
        logger.info("\n=== RESUMEN FINAL DEL BARRIDO ===")
        logger.info(f"Total procesadas: {len(call_items)}")
        logger.info(f"Exitosas (.txt): {successful_count}")
        logger.info(f"Fallidas: {failed_count}")
        if failed_calls:
            logger.info(f"Llamadas no procesadas: {failed_calls}")
            
        browser.close()
        playwright.stop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Barrido Masivo de Transcripciones Verint (Producción)")
    parser.add_argument("--periodo", type=str, default=None, help="Periodo YYYYMM (por defecto: mes actual)")
    parser.add_argument("--output-dir", type=str, default="./transcripciones", help="Directorio de destino de los archivos .txt")
    parser.add_argument("--visible", action="store_true", help="Si se especifica, abre la ventana del navegador (desactiva headless)")

    args = parser.parse_args()

    run_batch_extraction(
        call_items=None,  # Consulta Teradata automáticamente
        periodo=args.periodo,
        headless=not args.visible,
        output_dir=args.output_dir
    )


