import os
import re
import sys
import time
import json
import logging
import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from docx import Document
import teradatasql

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("verint_transcript_extractor")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_config():
    config_path = os.path.join(BASE_DIR, "config", "config.json")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Archivo de configuración no encontrado: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_teradata_config():
    load_dotenv()
    env_user = os.getenv("TERADATA_USER_SELECT") or os.getenv("TERADATA_USER")
    env_password = os.getenv("TERADATA_PASSWORD_SELECT") or os.getenv("TERADATA_PASSWORD")
    env_host = os.getenv("TERADATA_HOST") or "IBKTD"
    env_logmech = os.getenv("TERADATA_LOGMECH_SELECT") or os.getenv("TERADATA_LOGMECH") or "LDAP"

    if not env_user or not env_password:
        raise ValueError("Faltan credenciales de Teradata en el archivo .env (TERADATA_USER / TERADATA_PASSWORD)")

    return {
        "teradata_user": env_user,
        "teradata_password": env_password,
        "teradata_host": env_host,
        "teradata_logmech": env_logmech
    }

def get_interaction_metadata_from_teradata(call_id):
    """
    Queries Teradata for FECHA_VENTA, DNI, and REG_EJECUTIVO for a given CONID (call_id).
    Raises RuntimeError if no record is found or connection fails.
    """
    logger.info(f"Consultando metadatos en Teradata para CONID={call_id}...")
    td_config = load_teradata_config()

    query = f"""
        SELECT TOP 1 FECHA_VENTA, FECHA_MODIFICADO, DNI, REG_EJECUTIVO
        FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_PURE_CLOUD
        WHERE CONID = '{call_id}'
    """

    try:
        with teradatasql.connect(
            host=td_config["teradata_host"],
            user=td_config["teradata_user"],
            password=td_config["teradata_password"],
            logmech=td_config["teradata_logmech"]
        ) as con:
            with con.cursor() as cur:
                cur.execute(query)
                row = cur.fetchone()
                if not row:
                    raise RuntimeError(f"Teradata no devolvió ningún registro para CONID='{call_id}' en DLAB_GEC.M_EXP_CALIDAD_DETALLE_PURE_CLOUD")
                
                fecha_raw = row[0] or row[1]
                if not fecha_raw:
                    raise ValueError(f"FECHA_VENTA / FECHA_MODIFICADO es nula para CONID='{call_id}'")
                
                dni = str(row[2]).strip()
                reg_ejec = str(row[3]).strip()

                if isinstance(fecha_raw, (datetime.datetime, datetime.date)):
                    fecha_str = fecha_raw.strftime("%Y%m%d")
                else:
                    fecha_str = str(fecha_raw)[:10].replace("-", "")

                logger.info(f"Metadatos Teradata -> Fecha: {fecha_str}, DNI: {dni}, Ejecutivo: {reg_ejec}")
                return fecha_str, dni, reg_ejec

    except Exception as e:
        logger.error(f"Fallo al consultar Teradata: {e}")
        raise RuntimeError(f"Error consultando metadatos en Teradata para CONID='{call_id}': {e}") from e

def get_pending_calls_from_teradata(periodo: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Consulta Teradata para obtener todos los CONID y metadatos de ejecutivos de televentas (SUB_EQUIPO = 'TC').
    Si no se especifica periodo, calcula dinámicamente el periodo actual YYYYMM.
    """
    if not periodo:
        periodo = datetime.datetime.now().strftime("%Y%m")

    logger.info(f"Consultando llamadas masivas en Teradata para PERIODO={periodo} (SUB_EQUIPO='TC')...")
    td_config = load_teradata_config()

    query = f"""
        SELECT DISTINCT 
            A.CONID, 
            A.FECHA_VENTA, 
            A.FECHA_MODIFICADO, 
            A.DNI, 
            A.REG_EJECUTIVO,
            A.REG_EVALUADOR
        FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_PURE_CLOUD A
        INNER JOIN DLAB_GEC.M_EXP_TELEVENTAS_EJECUTIVOS B
         ON A.REG_EJECUTIVO = B.REG_EJECUTIVO
         AND B.PERIODO = '{periodo}'
        WHERE B.SUB_EQUIPO = 'TC'
    """

    call_items = []
    try:
        with teradatasql.connect(
            host=td_config["teradata_host"],
            user=td_config["teradata_user"],
            password=td_config["teradata_password"],
            logmech=td_config["teradata_logmech"]
        ) as con:
            with con.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall()
                logger.info(f"Teradata devolvió {len(rows)} llamadas coincidentes.")

                for row in rows:
                    conid = str(row[0]).strip() if row[0] else None
                    if not conid:
                        continue

                    fecha_raw = row[1] or row[2]
                    if isinstance(fecha_raw, (datetime.datetime, datetime.date)):
                        fecha_str = fecha_raw.strftime("%Y%m%d")
                    elif fecha_raw:
                        fecha_str = str(fecha_raw)[:10].replace("-", "")
                    else:
                        fecha_str = datetime.datetime.now().strftime("%Y%m%d")

                    dni = str(row[3]).strip() if row[3] else "00000000"
                    reg_ejec = str(row[4]).strip() if row[4] else "B00000"
                    reg_eval = str(row[5]).strip() if row[5] else ""

                    call_items.append({
                        'call_id': conid,
                        'metadata': {
                            'fecha': fecha_str,
                            'dni': dni,
                            'ejecutivo': reg_ejec,
                            'evaluador': reg_eval
                        }
                    })

        return call_items

    except Exception as e:
        logger.error(f"Error al obtener llamadas de Teradata: {e}")
        raise RuntimeError(f"Error en consulta masiva Teradata (PERIODO={periodo}): {e}") from e


def extract_transcript_by_call_id(call_id, headless=False, output_dir=None, metadata=None):
    """
    Extracts interaction transcript from Verint for a given call_id.
    Raises Exception on any failure.
    """
    config = load_config()
    verint_settings = config.get("verint_settings", {})
    verint_url = verint_settings.get("verint_url", "https://wfo.mt5.verintcloudservices.com/wfo/control/signin")
    
    username = os.environ.get("VERINT_USER")
    password = os.environ.get("VERINT_PASS")
    if not username or not password:
        raise ValueError("VERINT_USER o VERINT_PASS no están configuradas en el archivo .env")

    if not output_dir:
        output_dir = BASE_DIR
    os.makedirs(output_dir, exist_ok=True)

def initialize_verint_session(headless: bool = False):
    """
    Inicia el navegador Chromium, realiza el login en Verint y navega a Speech Analytics (Proyecto Televentas).
    Devuelve los objetos (p, browser, context, page) para reutilizarlos en barridos masivos.
    """
    verint_url = os.getenv("VERINT_URL", "https://wfo.mt5.verintcloudservices.com/wfo/control/signin")
    username = os.getenv("VERINT_USER")
    password = os.getenv("VERINT_PASS")

    if not username or not password:
        raise ValueError("Faltan credenciales de Verint en el archivo .env (VERINT_USER / VERINT_PASS)")


    p = sync_playwright().start()
    logger.info("Lanzando navegador Chromium...")
    browser = p.chromium.launch(headless=headless)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    # Login
    logger.info(f"Navegando a: {verint_url}")
    page.goto(verint_url)
    page.wait_for_load_state("domcontentloaded")

    if "signin" in page.url or page.query_selector("#username"):
        logger.info("Enviando credenciales de inicio de sesión...")
        page.fill("#username", username)
        page.press("#username", "Enter")
        
        page.wait_for_selector("#password", timeout=15000)
        page.fill("#password", password)
        page.press("#password", "Enter")
        page.wait_for_load_state("domcontentloaded")

    logger.info("Sesión iniciada correctamente.")

    # Navegar a Speech Analytics
    interactions_url = "https://wfo.mt5.verintcloudservices.com/wfo/ui/#wsm%5Bws%5D=speech_Listen"
    logger.info("Cargando Speech Analytics (esperando que el indicador de carga de Verint desaparezca)...")
    page.goto(interactions_url)
    
    # Esperar explícitamente a que el spinner de Verint desaparezca y la grilla ExtJS esté 100% lista
    try:
        page.wait_for_function("""
            () => {
                return window.Ext && 
                       Ext.ComponentQuery && 
                       Ext.ComponentQuery.query('gridpanel').length > 0;
            }
        """, timeout=60000)
        logger.info("¡Interfaz de Verint detectada! Esperando a que el botón 'Proyecto' de la barra lateral sea interactivo...")
        # Esperar visibilidad del botón 'Proyecto' en la barra lateral
        proj_btn = page.locator('span.x-btn-inner:has-text("Proyecto"), .x-btn:has-text("Proyecto"), span:has-text("Proyecto")').first
        proj_btn.wait_for(state="visible", timeout=45000)
        proj_btn.click()
        logger.info("Panel de Proyecto desplegado.")
        page.wait_for_timeout(1500)

        logger.info("Seleccionando 'Televentas' en el combo desplegable de Proyecto...")
        # 1. Intentar vía interacción directa de Playwright sobre el input/trigger del combo
        try:
            proj_input = page.locator('.x-field:has-text("Proyecto:"), .x-field:has-text("Project:")').locator('input').first
            if proj_input.is_visible():
                proj_input.click()
                page.wait_for_timeout(500)
                # Hacer clic en la flecha desplegable si existe
                trigger = page.locator('.x-field:has-text("Proyecto:") .x-form-trigger, .x-field:has-text("Project:") .x-form-trigger').first
                if trigger.is_visible():
                    trigger.click()
                    page.wait_for_timeout(500)
                
                # Seleccionar la opción Televentas en la lista desplegada
                option = page.locator('.x-boundlist-item:has-text("Televentas")').first
                if option.is_visible():
                    option.click()
                    logger.info("¡Opción 'Televentas' seleccionada desde la lista desplegable!")
        except Exception as ex_loc:
            logger.warning(f"Intento directo por locator: {ex_loc}")

        # 2. Asignación robusta vía JS ExtJS sobre todos los combos y campos de texto
        page.evaluate("""
            () => {
                if (window.Ext && window.Ext.ComponentQuery) {
                    const fields = Ext.ComponentQuery.query('combo, combobox, textfield');
                    for (let f of fields) {
                        const label = (f.fieldLabel || f.name || (f.el ? f.el.dom.innerText : '') || '').toLowerCase();
                        const val = String(f.getValue() || '');
                        if (label.includes('proyecto') || label.includes('project') || val.includes('Banca Comercial')) {
                            f.setValue('Televentas');
                            if (f.fireEvent) {
                                f.fireEvent('select', f, f.findRecordByValue ? f.findRecordByValue('Televentas') : 'Televentas');
                                f.fireEvent('change', f, 'Televentas');
                            }
                        }
                    }
                }
                
                // Fallback en DOM puro
                const labelEl = Array.from(document.querySelectorAll('*')).find(el => {
                    const txt = (el.textContent || '').trim();
                    return (txt === 'Proyecto:' || txt === 'Project:') && el.offsetWidth > 0;
                });
                const inputEl = labelEl ? (labelEl.parentElement.querySelector('input') || labelEl.closest('.x-container, .x-field').querySelector('input')) : null;
                if (inputEl) {
                    inputEl.value = 'Televentas';
                    inputEl.dispatchEvent(new Event('input', { bubbles: true }));
                    inputEl.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }
        """)
        logger.info("Proyecto 'Televentas' asignado.")
    except Exception as e:
        logger.warning(f"Aviso al seleccionar proyecto Televentas: {e}")

    return p, browser, context, page


def extract_single_transcript_in_session(page, call_id: str, metadata: dict = None, output_dir: str = "."):
    """
    Busca, abre y extrae la transcripción de UNA llamada en la sesión de Verint previamente abierta.
    """
    if metadata is None:
        metadata = {}

    fecha_yyyymmdd = metadata.get('fecha') or metadata.get('fecha_modificacion') or metadata.get('fecha_venta') or '20260716'
    dni = metadata.get('dni') or metadata.get('num_documento') or '00000000'
    reg_ejecutivo = metadata.get('ejecutivo') or metadata.get('usuario') or 'B00000'

    output_filename = f"{fecha_yyyymmdd}_{dni}_{reg_ejecutivo}_TC_{call_id}.txt"
    txt_path = os.path.join(output_dir, output_filename)

    # Cerrar cualquier ventana de interacción abierta anteriormente
    try:
        page.evaluate("""
            () => {
                const closeBtns = Array.from(document.querySelectorAll('.x-tool-close, div[class*="close"]')).filter(b => b.offsetWidth > 0);
                for (let b of closeBtns) b.click();
            }
        """)
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception:
        pass

    # 1. Asegurar que el filtro del conmutador esté visible desglosando la barra lateral
    logger.info("Esperando que el botón 'Mi conjunto...' esté visible...")
    dataset_btn = page.locator('span.x-btn-inner:has-text("Mi conjunto"), .x-btn:has-text("Mi conjunto"), span:has-text("Mi conjunto")').first
    try:
        dataset_btn.wait_for(state="visible", timeout=15000)
        dataset_btn.click()
    except Exception:
        page.evaluate("""
            () => {
                const btn = Array.from(document.querySelectorAll('*')).find(el => {
                    const txt = (el.innerText || el.textContent || '').trim();
                    return (txt === 'Mi conjunto...' || txt === 'My Data Set...' || txt.includes('Mi conjunto')) && el.offsetWidth > 0;
                });
                if (btn) btn.click();
            }
        """)

    # Desplegar acordeón Conmutadores si está colapsado
    page.evaluate("""
        () => {
            const headers = Array.from(document.querySelectorAll('.x-panel-header, .x-accordion-hd, [id^="panel-"] .x-panel-header'));
            const target = headers.find(h => (h.textContent || '').includes('Conmutadores') || (h.textContent || '').includes('Switches'));
            if (target) {
                const panel = target.closest('.x-panel');
                if (panel && panel.classList.contains('x-panel-collapsed')) {
                    target.click();
                }
            }
        }
    """)

    # Limpiar filtros de fecha
    page.evaluate("""
        () => {
            if (window.Ext && window.Ext.ComponentQuery) {
                const dateFields = Ext.ComponentQuery.query('datefield');
                for (let df of dateFields) {
                    try { df.setValue(null); } catch(e) {}
                }
            }
            const dateInputs = Array.from(document.querySelectorAll('input')).filter(input => {
                return (input.id.includes('date') || input.name === 'startDate') && input.offsetWidth > 0;
            });
            for (let input of dateInputs) {
                input.value = '';
            }
        }
    """)

    # Asignar ID de llamada al filtro del conmutador
    logger.info(f"Asignando ID de llamada al filtro del conmutador: {call_id}")
    field_set = page.evaluate("""
        (targetId) => {
            if (window.Ext && window.Ext.ComponentQuery) {
                const fields = Ext.ComponentQuery.query('textfield');
                for (let f of fields) {
                    const label = (f.fieldLabel || f.name || (f.el ? f.el.dom.innerText : '') || '').toLowerCase();
                    if (label.includes('conmutador') || label.includes('id de llamada') || label.includes('switch')) {
                        f.setValue(targetId);
                        if (f.fireEvent) {
                            f.fireEvent('change', f, targetId);
                            f.fireEvent('keyup', f, { getKey: () => 13 });
                        }
                        return true;
                    }
                }
            }
            return false;
        }
    """, call_id)

    try:
        input_loc = page.locator('.x-field:has-text("conmutador"), .x-field:has-text("ID de llamada")').locator('input').first
        if input_loc.is_visible():
            input_loc.click(force=True)
            input_loc.fill(call_id)
            input_loc.press("Tab")
    except Exception:
        pass

    # 3. Presionar el botón Aplicar usando la clase única de Verint (.verint-facad-blue-button)
    logger.info("Esperando que el botón 'Aplicar' esté listo y visible...")
    try:
        aplicar_btn = page.locator("a.verint-facad-blue-button, .x-btn:has-text('Aplicar')").first
        aplicar_btn.wait_for(state="visible", timeout=15000)
        aplicar_btn.click(force=True)
        logger.info("¡Botón 'Aplicar' presionado con éxito!")
    except Exception as e:
        logger.warning(f"Ejecutando activación JS de Aplicar...")
        page.evaluate("""
            () => {
                const btn = document.querySelector('a.verint-facad-blue-button') || 
                            Array.from(document.querySelectorAll('.x-btn, span.x-btn-inner, a')).find(b => {
                                const txt = (b.textContent || b.innerText || '').trim().toLowerCase();
                                return (txt === 'aplicar' || txt === 'apply') && b.offsetWidth > 0;
                            });
                if (btn) {
                    const target = btn.closest('.x-btn') || btn;
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    target.click();
                }
            }
        """)
        logger.info("¡Botón 'Aplicar' presionado con éxito por Playwright!")
    except Exception as e:
        logger.warning(f"Ejecutando activación JS de Aplicar...")
        page.evaluate("""
            () => {
                const btn = document.querySelector('a.verint-facad-blue-button') || 
                            Array.from(document.querySelectorAll('.x-btn, span.x-btn-inner, a')).find(b => {
                                const txt = (b.textContent || b.innerText || '').trim().toLowerCase();
                                return (txt === 'aplicar' || txt === 'apply') && b.offsetWidth > 0;
                            });
                if (btn) {
                    const target = btn.closest('.x-btn') || btn;
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    target.click();
                }
            }
        """)

    # 4. Esperar que Verint Cloud refresque la grilla con el resultado filtrado (6 segundos)
    logger.info("Esperando refresco de la grilla de resultados (6 segundos)...")
    page.wait_for_timeout(6000)

    # Localizar la fila resultado dentro de la grilla principal #grdContacts y abrir la interacción
    logger.info("Abriendo la interacción en #grdContacts...")
    page.evaluate("""
        () => {
            if (window.Ext && window.Ext.ComponentQuery) {
                const grids = Ext.ComponentQuery.query('gridpanel#grdContacts, gridpanel');
                for (let g of grids) {
                    const store = g.getStore();
                    if (store && store.getCount() > 0) {
                        const view = g.getView();
                        const record = store.getAt(0);
                        const node = view ? view.getNode(0) : null;
                        if (view && record && node) {
                            g.fireEvent('itemdblclick', view, record, node, 0);
                            return true;
                        }
                    }
                }
            }

            const gridEl = document.getElementById('grdContacts') || document.querySelector('.x-grid');
            if (gridEl) {
                const row = gridEl.querySelector('.x-grid-row, table.x-grid-item');
                if (row) {
                    const cell = row.querySelector('.x-grid-cell') || row;
                    const evt = new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window });
                    cell.dispatchEvent(evt);
                    return true;
                }
            }
            return false;
        }
    """)

    # Esperar la carga de la ventana emergente y renderizado del diálogo (#InteractionTranscriptionMain)
    logger.info("Esperando que Verint cargue el reproductor y renderice las líneas de diálogo (12 segundos)...")
    page.wait_for_timeout(12000)

    # Scroll suave dinámico en el panel de transcripción para cargar oraciones extensas
    page.evaluate("""
        () => {
            const mainEl = document.getElementById('InteractionTranscriptionMain');
            if (mainEl) {
                mainEl.scrollTop = mainEl.scrollHeight;
            }
        }
    """)
    page.wait_for_timeout(1000)

    # Extraer el diálogo estructurado con Asesor vs Cliente desde #InteractionTranscriptionMain
    logger.info("Extrayendo el diálogo clasificado (Asesor vs Cliente) desde #InteractionTranscriptionMain...")
    transcript_lines = page.evaluate("""
        () => {
            const mainEl = document.getElementById('InteractionTranscriptionMain') 
                || document.querySelector('div[id*="InteractionTranscription"]')
                || document.body;

            const rows = Array.from(mainEl.querySelectorAll('tr')).filter(tr => {
                return tr.querySelector('.interactionTranscriptionSPSTimeFormatter') && tr.offsetWidth > 0;
            });

            const structuredLines = [];

            for (let tr of rows) {
                const timeEl = tr.querySelector('.interactionTranscriptionSPSTimeFormatter');
                const timestamp = timeEl ? timeEl.innerText.trim() : '';

                const isAgent = !!tr.querySelector('.transcriptionSpeakerAgent');
                const isCustomer = !!tr.querySelector('.transcriptionSpeakerCustomer');
                const speaker = isAgent ? 'Asesor' : (isCustomer ? 'Cliente' : 'Hablante');

                const wordEls = Array.from(tr.querySelectorAll('.transcript'));
                let text = '';
                if (wordEls.length > 0) {
                    text = wordEls.map(w => w.innerText).join('').replace(/\\s+/g, ' ').trim();
                } else {
                    const tds = tr.querySelectorAll('td');
                    if (tds.length >= 3) {
                        text = tds[2].innerText.replace(/\\s+/g, ' ').trim();
                    }
                }

                if (timestamp && text) {
                    structuredLines.push(`${speaker} [${timestamp}]: ${text}`);
                }
            }

            if (structuredLines.length > 0) return structuredLines;

            const timeEls = Array.from(mainEl.querySelectorAll('.interactionTranscriptionSPSTimeFormatter'));
            for (let tSpan of timeEls) {
                const tr = tSpan.closest('tr');
                if (tr) {
                    const isAgent = !!tr.querySelector('.transcriptionSpeakerAgent');
                    const speaker = isAgent ? 'Asesor' : 'Cliente';
                    const timestamp = tSpan.innerText.trim();
                    const text = (tr.innerText || '').replace(timestamp, '').replace(/\\n+/g, ' ').trim();
                    if (text.length > 2) {
                        structuredLines.push(`${speaker} [${timestamp}]: ${text}`);
                    }
                }
            }

            return structuredLines;
        }
    """)

    logger.info(f"¡Diálogo estructurado capturado con éxito! Total de intervenciones: {len(transcript_lines)}")

    if not transcript_lines:
        raise RuntimeError(f"No se encontró el texto del diálogo de la llamada {call_id} en la interfaz de Verint")

    # Filtrar encabezados y metadatos no deseados de la interfaz con REGENEX dinámico
    ui_ignore_keywords = [
        'Revisión de interacciones', 'Término buscado', 'Término etiquetado', 'Categoría',
        'Proyecto', 'Resumen de IA', 'Descripción', 'Hora local', 'Duración', 'Estado',
        'RESULTADOS DE BÚSQUEDA', 'Mi conjunt', 'ACCIONES ADICIONALES', 'Exportar datos',
        'Comparar dos conjuntos', 'Búsquedas guardadas', 'Restablecer workspace', 'Presione Intro',
        'Saltar para actualizar', 'Enviar mensaje', '@intercorp', 'SPEECH ANALYTICS',
        'DETECTAR', 'ANALIZAR', 'INFORME', 'DISEÑO', 'SINTONIZACIÓN', 'Categorías',
        'Contexto', 'Gráficos', 'Causa raíz', 'Interacciones', 'SUGERENCIAS ADICIONALES',
        'OPERADORES', 'Empleados', 'Dinámica de interacción', 'Conmutadores', 'Página actual',
        'Opciones de búsqueda', 'Principales 1.000 por relevancia', 'Relevancia', 'Empleado',
        'Hora local de inicio', 'Estado de redacción', 'Palabras clave encontradas',
        'Finalizada correctamente', 'Seleccionar todo', 'Borrar todo', 'Analizar interacciones',
        'Velocidad de reproducción', 'Leyenda:', 'Sentimiento:', 'Encontrado',
        'No hay indicadores visuales', 'La forma de onda no está disponible',
        'Banco Internacional Peru', 'Datos personalizados', 'Mis datos', 'Contactos', 'Aplicar', 'Borrar',
        'TLV - Ventas', 'TLV - Exp', 'TLV_', 'TRSV_', 'CD_', 'EC_', 'PP_', 'VR- Sentiment', 'TC_GC_', 'TRVS_', 'SEG_'
    ]

    re_duration_counter = re.compile(r'^\d{2}:\d{2}\s*/\s*\d{2}:\d{2}')
    re_tab_header = re.compile(r'^\d{2}:\d{2}\s+Transcripci[oó]n', re.IGNORECASE)

    clean_lines = []
    for line in transcript_lines:
        line_str = line.strip()
        if len(line_str) < 3:
            continue
        if line_str.lower() in ['transcripción', 'resumen de ia', 'transcripcion']:
            continue
        if re_duration_counter.search(line_str) or re_tab_header.search(line_str):
            continue
        if any(kw in line_str for kw in ui_ignore_keywords):
            continue
        clean_lines.append(line_str)

    if not clean_lines:
        raise RuntimeError(f"Se abrió la interacción pero no se encontró texto de diálogo real para la llamada {call_id}")

    # Construir archivo de texto plano .txt (optimizado para ingesta de Speech Agent en AWS)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"ID DE LLAMADA: {call_id}\n")
        f.write(f"DNI CLIENTE: {dni}\n")
        f.write(f"EJECUTIVO: {reg_ejecutivo}\n")
        f.write(f"FECHA: {fecha_yyyymmdd}\n")
        f.write("=" * 50 + "\n\n")
        f.write("TRANSCRIPCIÓN:\n")
        for line in clean_lines:
            f.write(f"{line}\n")

    logger.info(f"[OK] Transcripción exportada correctamente a: {txt_path}")

    # Cerrar la ventana emergente de la interacción al terminar
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

    return txt_path


def extract_transcript_by_call_id(call_id: str, headless: bool = False, metadata: dict = None, output_dir: str = ".") -> str:
    """
    Función de entrada individual para la prueba de 1 sola llamada (compatible con test_verint_transcript.py).
    """
    p, browser, context, page = initialize_verint_session(headless=headless)
    try:
        return extract_single_transcript_in_session(page, call_id, metadata, output_dir)
    finally:
        browser.close()
        p.stop()


if __name__ == "__main__":
    test_id = "9766a115-eb53-4a5f-b918-cc6673f69e7d"
    extract_transcript_by_call_id(test_id, headless=False)
