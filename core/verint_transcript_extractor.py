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
        project_selected = page.evaluate("""
            () => {
                const label = Array.from(document.querySelectorAll('*')).find(el => {
                    const txt = (el.textContent || '').trim();
                    return (txt === 'Proyecto:' || txt === 'Project:') && el.offsetWidth > 0;
                });
                const projectInput = label ? (label.parentElement.querySelector('input') || label.closest('.x-container, .x-field').querySelector('input')) : null;
                
                if (!projectInput) return { success: false, error: "Input de Proyecto no encontrado" };
                if ((projectInput.value || '').trim() === 'Televentas') {
                    return { success: true, alreadySelected: true };
                }
                
                const triggerId = projectInput.id.replace('-inputEl', '-trigger-picker');
                const trigger = document.getElementById(triggerId);
                if (trigger) {
                    trigger.click();
                } else {
                    projectInput.click();
                }
                return { success: true, clicked: true };
            }
        """)

        page.wait_for_timeout(800)
        # Seleccionar la opción Televentas en la lista desplegable abierta
        try:
            option = page.locator('.x-boundlist-item:has-text("Televentas")').first
            if option.is_visible(timeout=3000):
                option.click()
                logger.info("¡Opción 'Televentas' seleccionada desde la lista desplegable!")
            else:
                # Fallback ExtJS directo sin fireEvent corrupto
                page.evaluate("""
                    () => {
                        if (window.Ext && window.Ext.ComponentQuery) {
                            const combos = Ext.ComponentQuery.query('combo, combobox');
                            for (let c of combos) {
                                const label = (c.fieldLabel || c.name || (c.el ? c.el.dom.innerText : '') || '').toLowerCase();
                                if (label.includes('proyecto') || label.includes('project')) {
                                    c.setValue('Televentas');
                                }
                            }
                        }
                    }
                """)
        except Exception as ex_opt:
            logger.warning(f"Aviso al hacer clic en opción de lista: {ex_opt}")

        logger.info("Proyecto 'Televentas' asignado. Esperando que finalice la carga de Verint...")
        page.wait_for_timeout(2000)
        try:
            page.wait_for_selector('.x-mask', state='detached', timeout=15000)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Aviso al seleccionar proyecto Televentas: {e}")

    return p, browser, context, page


def extract_single_transcript_in_session(page, call_id: str, metadata: dict = None, output_dir: str = "."):
    """
    Busca, abre y extrae la transcripción de UNA llamada en la sesión de Verint previamente abierta.
    """
    if metadata is None:
        metadata = {}

    fecha_yyyymmdd = metadata.get('fecha') or metadata.get('fecha_modificacion') or metadata.get('fecha_venta') or datetime.datetime.now().strftime("%Y%m%d")
    dni = metadata.get('dni') or metadata.get('num_documento') or '00000000'
    reg_ejecutivo = metadata.get('ejecutivo') or metadata.get('usuario') or 'B00000'

    output_filename = f"{fecha_yyyymmdd}_{dni}_{reg_ejecutivo}_TC_{call_id}.txt"
    txt_path = os.path.join(output_dir, output_filename)

    # Calcular rango de fechas (desde el día 1 del mes de la llamada hasta hoy)
    if fecha_yyyymmdd and len(str(fecha_yyyymmdd)) == 8:
        f_str = str(fecha_yyyymmdd)
        desde_str = f"01/{f_str[4:6]}/{f_str[:4]}"
    else:
        past = datetime.datetime.now() - datetime.timedelta(days=45)
        desde_str = past.strftime("01/%m/%Y")
    hasta_str = datetime.datetime.now().strftime("%d/%m/%Y")

    logger.info(f"=== INICIANDO PASOS DE FILTRADO PARA CALL ID: {call_id} (Rango Fechas: {desde_str} - {hasta_str}) ===")

    # Cerrar cualquier vista de detalle abierta (speech_Player) y regresar a la vista de filtros (Analizar interacciones / speech_Listen)
    try:
        page.evaluate("""
            () => {
                const navBtn = Array.from(document.querySelectorAll('a, span, div, button')).find(el => {
                    const txt = (el.innerText || el.textContent || '').trim();
                    return txt === 'Analizar interacciones' && el.offsetWidth > 0;
                });
                if (navBtn) navBtn.click();

                const closeBtns = Array.from(document.querySelectorAll('.x-tool-close, div[class*="close"]')).filter(b => b.offsetWidth > 0);
                for (let b of closeBtns) b.click();
            }
        """)
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
    except Exception:
        pass

    # 1. Asegurar que el filtro del conmutador esté visible desglosando la barra lateral 'Mi conjunto'
    logger.info("[PASO 1/5] Haciendo clic en la pestaña 'Mi conjunto...'...")
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
    page.wait_for_timeout(300)

    # 2. Desplegar acordeón Conmutadores si está colapsado
    logger.info("[PASO 2/5] Desplegando sección de acordeón 'Conmutadores'...")
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
    page.wait_for_timeout(300)

    # 3. Configurar rango de fechas (Entre desde_str y hasta_str)
    logger.info(f"[PASO 3/5] Aplicando rango de fechas ({desde_str} a {hasta_str})...")
    page.evaluate("""
        ([desde, hasta]) => {
            const safeFire = (el, type) => {
                if (!el) return;
                let evt;
                if (typeof Event === 'function') {
                    try { evt = new Event(type, { bubbles: true, cancelable: true }); } catch(e) {}
                }
                if (!evt) {
                    try {
                        evt = document.createEvent('HTMLEvents');
                        evt.initEvent(type, true, true);
                    } catch(e) {}
                }
                if (evt) el.dispatchEvent(evt);
            };

            if (window.Ext && window.Ext.ComponentQuery) {
                const radios = Ext.ComponentQuery.query('radiofield, radio');
                for (let r of radios) {
                    const label = (r.boxLabel || r.fieldLabel || (r.el ? r.el.dom.innerText : '') || '').toLowerCase();
                    if (label.includes('entre') || label.includes('between')) {
                        r.setValue(true);
                        if (r.fireEvent) try { r.fireEvent('change', r, true); } catch(e) {}
                    }
                }
                const dateFields = Ext.ComponentQuery.query('datefield');
                if (dateFields.length >= 2) {
                    try {
                        dateFields[0].setValue(desde);
                        dateFields[1].setValue(hasta);
                        if (dateFields[0].fireEvent) dateFields[0].fireEvent('change', dateFields[0], desde);
                        if (dateFields[1].fireEvent) dateFields[1].fireEvent('change', dateFields[1], hasta);
                    } catch(e) {}
                }
            }
        }
    """, [desde_str, hasta_str])
    page.wait_for_timeout(300)

    # 4. Asignar ID de llamada al filtro del conmutador
    logger.info(f"[PASO 4/5] Escribiendo Call ID en campo Conmutador: {call_id}...")
    field_set = page.evaluate("""
        (targetId) => {
            const safeFire = (el, type) => {
                if (!el) return;
                let evt;
                if (typeof Event === 'function') {
                    try { evt = new Event(type, { bubbles: true, cancelable: true }); } catch(e) {}
                }
                if (!evt) {
                    try {
                        evt = document.createEvent('HTMLEvents');
                        evt.initEvent(type, true, true);
                    } catch(e) {}
                }
                if (evt) el.dispatchEvent(evt);
            };

            if (window.Ext && window.Ext.ComponentQuery) {
                const fields = Ext.ComponentQuery.query('textfield');
                for (let f of fields) {
                    const label = (f.fieldLabel || f.name || f.emptyText || (f.el ? f.el.dom.innerText : '') || '').toLowerCase();
                    const containerText = (f.up('.x-panel, .x-container') ? f.up('.x-panel, .x-container').title || f.up('.x-panel, .x-container').el.dom.innerText : '').toLowerCase();
                    if (label.includes('conmutador') || label.includes('id de llamada') || label.includes('switch') || containerText.includes('conmutadores')) {
                        f.setValue(targetId);
                        if (f.fireEvent) {
                            try { f.fireEvent('change', f, targetId); } catch(e) {}
                            try { f.fireEvent('keyup', f, { getKey: () => 13 }); } catch(e) {}
                        }
                        return true;
                    }
                }
            }

            // Fallback en DOM puro
            const inputs = Array.from(document.querySelectorAll('input')).filter(i => i.offsetWidth > 0);
            for (let i of inputs) {
                const containerText = (i.closest('.x-panel, .x-container, .x-field') ? i.closest('.x-panel, .x-container, .x-field').innerText : '').toLowerCase();
                if (containerText.includes('conmutador') || containerText.includes('switch') || containerText.includes('id de llamada')) {
                    i.value = targetId;
                    safeFire(i, 'input');
                    safeFire(i, 'change');
                    return true;
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

    page.wait_for_timeout(300)

    # 5. Presionar el botón Aplicar
    logger.info("[PASO 5/5] Presionando botón 'Aplicar'...")
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

    # 6. Esperar a que la máscara de carga ('Cargando...', .x-mask) desaparezca por completo
    logger.info("Esperando que finalice la carga de resultados ('Cargando...')...")
    try:
        page.wait_for_timeout(1000)
        page.wait_for_function("""
            () => {
                const masks = Array.from(document.querySelectorAll('.x-mask, .x-mask-msg')).filter(m => {
                    return m.offsetWidth > 0 && m.offsetHeight > 0;
                });
                return masks.length === 0;
            }
        """, timeout=15000)
        page.wait_for_timeout(1000)
    except Exception:
        page.wait_for_timeout(3000)

    # Localizar la fila resultado dentro de la grilla principal #grdContacts y abrir la interacción
    logger.info("Abriendo la interacción en la grilla #grdContacts...")
    opened = False
    try:
        row_loc = page.locator('table[data-recordindex="0"], tr.x-grid-row, .x-grid-item').first
        row_loc.wait_for(state="visible", timeout=8000)
        row_loc.dblclick()
        opened = True
        logger.info("¡Doble clic ejecutado con éxito en la fila de la grilla!")
    except Exception as ex_row:
        logger.warning(f"Intento de dblclick Playwright sin force: {ex_row}")

    if not opened:
        open_script = """
            () => {
                const row = document.querySelector('table[data-recordindex="0"]') || document.querySelector('.x-grid-row');
                if (row) {
                    const evt = new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window });
                    row.dispatchEvent(evt);
                    return true;
                }
                const ctiData = document.querySelector('.SA_CTIData');
                if (ctiData) {
                    ctiData.click();
                    return true;
                }
                return false;
            }
        """
        try:
            opened = page.evaluate(open_script)
        except Exception:
            pass
        if not opened:
            for f in page.frames:
                try:
                    if f.evaluate(open_script):
                        opened = True
                        break
                except Exception:
                    pass

    # Polling dinámico: Esperar hasta 15 segundos a que aparezcan las líneas del diálogo
    logger.info("Extrayendo diálogo con sondeo dinámico de renderizado...")
    extract_script = """
        () => {
            const rows = Array.from(document.querySelectorAll('tr')).filter(tr => {
                return tr.querySelector('.interactionTranscriptionSPSTimeFormatter') !== null;
            });

            const structuredLines = [];

            for (let tr of rows) {
                const timeEl = tr.querySelector('.interactionTranscriptionSPSTimeFormatter');
                const timestamp = timeEl ? (timeEl.innerText || timeEl.textContent || '').trim() : '';

                const isAgent = !!tr.querySelector('.transcriptionSpeakerAgent');
                const isCustomer = !!tr.querySelector('.transcriptionSpeakerCustomer');
                const speaker = isAgent ? 'Asesor' : (isCustomer ? 'Cliente' : 'Hablante');

                const wordEls = Array.from(tr.querySelectorAll('.transcript'));
                let text = '';
                if (wordEls.length > 0) {
                    text = wordEls.map(w => w.innerText || w.textContent || '').join('').replace(/\\s+/g, ' ').trim();
                } else {
                    const tds = tr.querySelectorAll('td');
                    if (tds.length >= 3) {
                        text = (tds[2].innerText || tds[2].textContent || '').replace(/\\s+/g, ' ').trim();
                    }
                }

                if (timestamp && text) {
                    structuredLines.push(`${speaker} [${timestamp}]: ${text}`);
                }
            }

            return structuredLines.length > 0 ? structuredLines : null;
        }
    """

    transcript_lines = []
    t_start = time.time()

    while time.time() - t_start < 15:
        try:
            res = page.evaluate(extract_script)
            if res and len(res) > 0:
                transcript_lines = res
                break
        except Exception:
            pass

        for f in page.frames:
            try:
                res = f.evaluate(extract_script)
                if res and len(res) > 0:
                    transcript_lines = res
                    break
            except Exception:
                pass

        if transcript_lines:
            break

        page.wait_for_timeout(500)

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
    Función de entrada individual para la prueba de 1 sola llamada.
    Si headless=False, en caso de error NO cierra el navegador para permitir inspección manual.
    """
    p, browser, context, page = initialize_verint_session(headless=headless)
    try:
        return extract_single_transcript_in_session(page, call_id, metadata, output_dir)
    except Exception as e:
        if not headless:
            logger.warning(f"Ocurrió un error en modo visible (headless=False). El navegador permanecerá abierto para inspección manual.")
        else:
            browser.close()
            p.stop()
        raise e
    else:
        if headless:
            browser.close()
            p.stop()


def extract_all_transcripts_batch(periodo: Optional[str] = None, headless: bool = True, output_dir: Optional[str] = None) -> List[str]:
    """
    Función de PRODUCCIÓN: Obtiene la lista de llamadas pendientes desde Teradata (SUB_EQUIPO='TC')
    y extrae todas las transcripciones reutilizando 1 sola sesión de Verint.
    """
    calls = get_pending_calls_from_teradata(periodo=periodo)
    if not calls:
        logger.info("No se encontraron llamadas pendientes en Teradata para el periodo indicado.")
        return []

    logger.info(f"Iniciando extracción masiva para {len(calls)} llamadas pendientes...")
    p, browser, context, page = initialize_verint_session(headless=headless)
    exported_files = []
    try:
        for idx, item in enumerate(calls, 1):
            call_id = item['call_id']
            meta = item['metadata']
            logger.info(f"[{idx}/{len(calls)}] Procesando extracción de llamada: {call_id}...")
            try:
                txt_file = extract_single_transcript_in_session(page, call_id, metadata=meta, output_dir=output_dir)
                exported_files.append(txt_file)
            except Exception as e:
                logger.error(f"Fallo en la extracción de la llamada {call_id}: {e}")
        return exported_files
    finally:
        browser.close()
        p.stop()


if __name__ == "__main__":
    test_id = "9766a115-eb53-4a5f-b918-cc6673f69e7d"
    extract_transcript_by_call_id(test_id, headless=False)
