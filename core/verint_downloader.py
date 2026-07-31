import os
import re
import sys
import time
import datetime
import logging
from pathlib import Path
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import csv
import teradatasql

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
_base_logger = logging.getLogger("verint_downloader")

import threading

class ProgressCallbackLogger:
    def debug(self, msg, *args, **kwargs):
        self.base_logger.info(msg, *args, **kwargs)

    def __init__(self, base_logger):
        self.base_logger = base_logger
        self.local_state = threading.local()

    @property
    def callback(self):
        return getattr(self.local_state, 'callback', None)

    @callback.setter
    def callback(self, value):
        self.local_state.callback = value

    def info(self, msg, *args, **kwargs):
        self.base_logger.info(msg, *args, **kwargs)
        if self.callback:
            try:
                formatted_msg = msg % args if args else msg
                self.callback(str(formatted_msg))
            except Exception:
                pass

    def warning(self, msg, *args, **kwargs):
        self.base_logger.warning(msg, *args, **kwargs)
        if self.callback:
            try:
                formatted_msg = msg % args if args else msg
                self.callback(f"⚠️ {formatted_msg}")
            except Exception:
                pass

    def error(self, msg, *args, **kwargs):
        self.base_logger.error(msg, *args, **kwargs)
        if self.callback:
            try:
                formatted_msg = msg % args if args else msg
                self.callback(f"❌ {formatted_msg}")
            except Exception:
                pass

    def exception(self, msg, *args, **kwargs):
        self.base_logger.exception(msg, *args, **kwargs)
        if self.callback:
            try:
                formatted_msg = msg % args if args else msg
                self.callback(f"❌ {formatted_msg}")
            except Exception:
                pass

logger = ProgressCallbackLogger(_base_logger)

# Base directory points to the root of the workspace (one level above core/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_env():
    """Loads environment variables from a local .env file."""
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        logger.info(f"Loading environment variables from: {env_path}")
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip().strip("'").strip('"')

def load_config():
    """Loads settings from config/config.json."""
    config_path = os.path.join(BASE_DIR, "config", "config.json")
    import json
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_date_range(period=None):
    """Calculates the 15th of the previous month to today or period date."""
    now = datetime.datetime.now()
    if period:
        year = int(period[:4])
        month = int(period[4:])
        
        # If it is the current month, use today's date for hasta
        if year == now.year and month == now.month:
            current_date = now
        else:
            # Last day of that month
            if month == 12:
                next_month = datetime.datetime(year + 1, 1, 1)
            else:
                next_month = datetime.datetime(year, month + 1, 1)
            current_date = next_month - datetime.timedelta(days=1)
    else:
        current_date = now

    first_day_curr = current_date.replace(day=1)
    last_day_prev = first_day_curr - datetime.timedelta(days=1)
    prev_month_15 = last_day_prev.replace(day=15)
    
    desde = prev_month_15.strftime("%d/%m/%Y")
    
    if current_date > now:
        hasta = now.strftime("%d/%m/%Y")
    else:
        hasta = current_date.strftime("%d/%m/%Y")
        
    return desde, hasta

def load_teradata_config():
    """
    Loads Teradata credentials from environment variables.
    """
    load_env()
    load_dotenv()

    env_user = os.getenv("TERADATA_USER")
    env_password = os.getenv("TERADATA_PASSWORD")
    env_host = os.getenv("TERADATA_HOST")
    env_logmech = os.getenv("TERADATA_LOGMECH")

    if not env_user or not env_password:
        raise ValueError(
            "Faltan credenciales de Teradata. "
            "Verifica TERADATA_USER y TERADATA_PASSWORD en el archivo .env"
        )

    return {
        "teradata_user": env_user,
        "teradata_password": env_password,
        "teradata_host": env_host or "IBKTD",
        "teradata_logmech": env_logmech or "LDAP"
    }


def generate_ev_csv_from_teradata(period):
    """
    Executes Teradata SELECT and creates EV_yyyymm.csv in the script directory.
    """
    workspace_dir = BASE_DIR
    output_filename = f"EV_{period}.csv"
    output_path = os.path.join(workspace_dir, output_filename)

    td_config = load_teradata_config()

    query = """
        SELECT DISTINCT REG_EJECUTIVO
        FROM DLAB_GEC.M_EXP_TELEVENTAS_EJECUTIVOS
    """

    logger.info("Connecting to Teradata to generate EV CSV...")
    logger.info(f"Output CSV will be created at: {output_path}")

    rows = []

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

    except Exception as e:
        logger.error(f"Error querying Teradata: {e}")
        raise

    if not rows:
        raise ValueError(
            "El SELECT de Teradata no devolvió registros para REG_EJECUTIVO."
        )

    with open(output_path, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["REG_EJECUTIVO"])

        for row in rows:
            writer.writerow([row[0]])

    logger.info(f"Generated CSV successfully: {output_path}")
    logger.info(f"Total executives exported: {len(rows)}")

    return output_path

def find_input_csv(period):
    """
    Finds the local input CSV file for agent IDs.
    If EV_yyyymm.csv does not exist, generates it from Teradata.
    """
    workspace_dir = BASE_DIR
    expected_filename = f"EV_{period}.csv"
    expected_path = os.path.join(workspace_dir, expected_filename)

    if os.path.exists(expected_path):
        logger.info(f"Found exact input CSV file: {expected_path}")
        return expected_path

    logger.warning(f"Exact CSV file {expected_filename} not found in workspace.")
    logger.info("Generating EV CSV from Teradata...")

    try:
        generated_path = generate_ev_csv_from_teradata(period)

        if os.path.exists(generated_path):
            logger.info(f"Using generated input CSV file: {generated_path}")
            return generated_path

    except Exception as e:
        logger.error(f"Could not generate EV CSV from Teradata: {e}")
        raise

    raise FileNotFoundError(
        f"No input CSV file found or generated for period {period}."
    )

def download_verint_data(period=None, headless=True, progress_callback=None, output_dir=None):
    logger.callback = progress_callback
    try:
        return _download_verint_data_impl(period, headless, output_dir)
    finally:
        logger.callback = None

def _download_verint_data_impl(period=None, headless=True, output_dir=None):
    load_env()
    config = load_config()
    
    verint_settings = config.get("verint_settings", {})
    verint_url = verint_settings.get("verint_url", "https://wfo.mt5.verintcloudservices.com/wfo/control/signin")
    if output_dir:
        downloads_dir = str(Path(output_dir).resolve())
    else:
        downloads_dir = str(Path.home() / "Downloads")
    os.makedirs(downloads_dir, exist_ok=True)
    
    # Period calculations
    if not period:
        period = datetime.datetime.now().strftime("%Y%m")
    
    desde_str, hasta_str = get_date_range(period)
    logger.debug(f"Date range filter: From {desde_str} to {hasta_str}")
    
    # Locate CSV file
    csv_path = find_input_csv(period)
    
    # Load credentials
    username = os.environ.get("VERINT_USER")
    password = os.environ.get("VERINT_PASS")
    if not username or not password:
        raise ValueError("VERINT_USER or VERINT_PASS environment variables are not set in the environment or .env file.")
        
    logger.info("Iniciando conexión con Verint...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        
        # 1. Login
        logger.debug(f"Navegando a: {verint_url}")
        page.goto(verint_url)
        page.wait_for_load_state("domcontentloaded")
        
        # Wait for either the username input or check if already inside
        try:
            page.wait_for_selector("#username", timeout=5000)
        except Exception:
            logger.debug("Campo de usuario no encontrado, verificando si la sesión ya está activa...")
            
        if "signin" in page.url or page.query_selector("#username"):
            logger.info("Página de inicio de sesión detectada. Enviando credenciales...")
            page.fill("#username", username)
            page.press("#username", "Enter")
            
            page.wait_for_selector("#password")
            page.fill("#password", password)
            page.press("#password", "Enter")
            page.wait_for_timeout(3500)
            
        logger.info("Inicio de sesión completado.")
        
        # 2. Navigate directly to Speech Analytics Interactions workspace
        interactions_url = "https://wfo.mt5.verintcloudservices.com/wfo/ui/#wsm%5Bws%5D=speech_Listen"
        logger.debug(f"Navegando a la vista de interacciones: {interactions_url}")
        page.goto(interactions_url)
        
        # 2. Wait ONLY for sidebar menu button to be visible to start filtering immediately
        logger.info("Abriendo Speech Analytics y esperando que la barra lateral esté visible...")
        sidebar_proj_btn = page.locator('.SA_silderMenuButton.m_button_project, a[data-qtip="Proyecto"]').first
        sidebar_proj_btn.wait_for(state="visible", timeout=45000)
        
        logger.debug("Haciendo clic en la pestaña Proyecto de la barra lateral...")
        sidebar_proj_btn.click()
        page.wait_for_timeout(1000)
        
        # 3. Select Project "Televentas"
        logger.debug("Seleccionando el proyecto Televentas...")
        project_selected = page.evaluate("""
            (projectName) => {
                const label = Array.from(document.querySelectorAll('*')).find(el => {
                    const txt = (el.textContent || '').trim();
                    return (txt === 'Proyecto:' || txt === 'Project:') && el.offsetWidth > 0;
                });
                const projectInput = label ? (label.parentElement.querySelector('input') || label.closest('.x-container, .x-field').querySelector('input')) : null;
                
                if (!projectInput) return { success: false, error: "Input de Proyecto no encontrado" };
                if ((projectInput.value || '').trim() === projectName) {
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
        """, verint_settings.get("project_name", "Televentas"))
        
        page.wait_for_timeout(800)
        try:
            option = page.locator('.x-boundlist-item:has-text("Televentas")').first
            if option.is_visible(timeout=3000):
                option.click()
            else:
                page.evaluate("""
                    (projectName) => {
                        if (window.Ext && window.Ext.ComponentQuery) {
                            const combos = Ext.ComponentQuery.query('combo, combobox');
                            for (let c of combos) {
                                const label = (c.fieldLabel || c.name || (c.el ? c.el.dom.innerText : '') || '').toLowerCase();
                                if (label.includes('proyecto') || label.includes('project')) {
                                    c.setValue(projectName);
                                }
                            }
                        }
                    }
                """, verint_settings.get("project_name", "Televentas"))
        except Exception as ex_opt:
            logger.warning(f"Aviso al hacer clic en opción Televentas: {ex_opt}")
            
        logger.debug("Proyecto Televentas configurado.")
        
        # Switch to "Mi conjunto de datos" tab using exact F12 class .m_button_metadata
        logger.debug("Cambiando a la pestaña 'Mi conjunto de datos'...")
        sidebar_dataset_btn = page.locator('.SA_silderMenuButton.m_button_metadata, a[data-qtip="Mi conjunto de datos"], a.m_button_metadata').first
        sidebar_dataset_btn.wait_for(state="visible", timeout=15000)
        sidebar_dataset_btn.click()
        page.wait_for_timeout(1500)
        
        # 4. Set Date Filter
        logger.debug(f"Setting Date range: {desde_str} - {hasta_str}...")
        date_set = page.evaluate("""
            ([desdeDDMM, hastaDDMM]) => {
                // Formatear DD/MM/YYYY a MM/DD/YYYY según lo indicado en F12 (Formato de fecha esperado como 07/16/2026)
                const toMMDD = (s) => {
                    const p = (s || '').split('/');
                    return p.length === 3 ? `${p[1].padStart(2, '0')}/${p[0].padStart(2, '0')}/${p[2]}` : s;
                };
                const desdeMM = toMMDD(desdeDDMM);
                const hastaMM = toMMDD(hastaDDMM);

                // 1. Encontrar y activar el radio button 'Entre' / 'Between' (aria-label="Entre")
                const radio = Array.from(document.querySelectorAll('input[type="radio"]')).find(r => {
                    const ariaLabel = (r.getAttribute('aria-label') || '').toLowerCase();
                    const parent = r.closest('tr, .x-field, .x-table-layout-cell') || r.parentElement;
                    const parentText = (parent ? parent.textContent : '').toLowerCase();
                    return ariaLabel === 'entre' || ariaLabel.includes('between') || parentText.includes('entre') || parentText.includes('between');
                });
                
                if (radio) {
                    radio.click();
                    radio.checked = true;
                    try { radio.dispatchEvent(new Event('change', { bubbles: true })); } catch(e) {}
                }

                // 2. ExtJS ComponentQuery: Habilitar y asignar objetos Date
                if (window.Ext && window.Ext.ComponentQuery) {
                    try {
                        const radios = Ext.ComponentQuery.query('radiofield, radio');
                        for (let r of radios) {
                            const label = (r.boxLabel || r.fieldLabel || r.ariaLabel || (r.el ? r.el.dom.innerText : '') || '').toLowerCase();
                            if (label.includes('entre') || label.includes('between')) {
                                r.setValue(true);
                                if (r.fireEvent) r.fireEvent('change', r, true);
                            }
                        }

                        const parseDate = (dStr) => {
                            if (!dStr) return null;
                            const parts = dStr.split('/');
                            if (parts.length === 3) {
                                return new Date(parseInt(parts[2]), parseInt(parts[1]) - 1, parseInt(parts[0]));
                            }
                            return null;
                        };

                        const dateFields = Ext.ComponentQuery.query('datefield');
                        if (dateFields.length >= 2) {
                            dateFields[0].enable();
                            dateFields[1].enable();

                            const dtDesde = parseDate(desdeDDMM);
                            const dtHasta = parseDate(hastaDDMM);

                            if (dtDesde) dateFields[0].setValue(dtDesde);
                            else dateFields[0].setValue(desdeMM);

                            if (dtHasta) dateFields[1].setValue(dtHasta);
                            else dateFields[1].setValue(hastaMM);

                            if (dateFields[0].fireEvent) dateFields[0].fireEvent('change', dateFields[0], dateFields[0].getValue());
                            if (dateFields[1].fireEvent) dateFields[1].fireEvent('change', dateFields[1], dateFields[1].getValue());
                        }
                    } catch(e) {}
                }

                // 3. Fallback directo DOM: Habilitar inputs deshabilitados y asignar valor en formato MM/DD/YYYY
                const dateInputs = Array.from(document.querySelectorAll('input')).filter(i => {
                    const parentText = (i.closest('.x-field, .x-form-item') ? i.closest('.x-field, .x-form-item').textContent : '');
                    const title = i.getAttribute('title') || '';
                    return title.includes('Formato de fecha') || parentText.includes('Desde:') || parentText.includes('Hasta:') || i.id.includes('datefield');
                });
                
                if (dateInputs.length >= 2) {
                    dateInputs[0].removeAttribute('disabled');
                    dateInputs[1].removeAttribute('disabled');
                    dateInputs[0].value = desdeMM;
                    dateInputs[1].value = hastaMM;
                    ['change', 'input', 'blur'].forEach(evtName => {
                        try { dateInputs[0].dispatchEvent(new Event(evtName, { bubbles: true })); } catch(e) {}
                        try { dateInputs[1].dispatchEvent(new Event(evtName, { bubbles: true })); } catch(e) {}
                    });
                }

                return { success: true };
            }
        """, [desde_str, hasta_str])
        
        if not date_set.get("success"):
            raise RuntimeError(f"Could not set date filter: {date_set.get('error')}")
        logger.debug("Filtro de fechas configurado.")
        
        # 5. Configure Custom Data -> cti_AGENTID
        logger.debug("Expandiendo el panel de 'Datos personalizados' / 'Custom data' si está colapsado...")
        expanded = page.evaluate("""
            () => {
                const headers = Array.from(document.querySelectorAll('.x-panel-header, .x-accordion-hd, [id^=\"panel-\"] .x-panel-header'));
                const targetHeader = headers.find(h => {
                    const text = h.textContent || '';
                    return text.includes('Datos personalizados') || text.toLowerCase().includes('custom data') || text.toLowerCase().includes('custom fields');
                });
                if (!targetHeader) return { success: false, error: "Datos personalizados / Custom data header not found" };
                
                const panel = targetHeader.closest('.x-panel');
                if (panel && panel.classList.contains('x-panel-collapsed')) {
                    targetHeader.click();
                    return { success: true, clicked: true };
                }
                return { success: true, clicked: false };
            }
        """)
        if not expanded.get("success"):
            raise RuntimeError(f"Could not expand 'Datos personalizados': {expanded.get('error')}")
        logger.debug(f"Accordion expansion status: {expanded}")
        page.wait_for_timeout(3000)
        
        logger.debug("Seleccionando el dato personalizado cti_AGENTID...")
        custom_data_selected = page.evaluate("""
            (fieldName) => {
                const customDataInput = document.querySelector('input[id^=\"extcdscombo-\"]');
                if (!customDataInput) return { success: false, error: "Custom data input not found" };
                
                const triggerId = customDataInput.id.replace('-inputEl', '-trigger-picker');
                const trigger = document.getElementById(triggerId);
                if (trigger) {
                    trigger.click();
                } else {
                    customDataInput.click();
                }
                
                return new Promise((resolve) => {
                    setTimeout(() => {
                        const items = Array.from(document.querySelectorAll('.x-boundlist-item'));
                        const item = items.find(el => el.textContent && el.textContent.trim() === fieldName);
                        if (item) {
                            item.click();
                            resolve({ success: true });
                        } else {
                            resolve({ success: false, error: "Field option not found", items: items.map(el => el.textContent) });
                        }
                    }, 3000);
                });
            }
        """, "cti_AGENTID")
        
        if not custom_data_selected.get("success"):
            raise RuntimeError(f"Could not select cti_AGENTID: {custom_data_selected.get('error')}")
        logger.debug("cti_AGENTID seleccionado con éxito.")
        page.wait_for_timeout(2000)
        
        # 6. Click three-dots to open upload popup
        logger.debug("Abriendo ventana de carga de CSV...")
        click_result = page.evaluate("""
            () => {
                const input = Array.from(document.querySelectorAll('input')).find(i => {
                    const ph = i.placeholder || i.getAttribute('placeholder') || '';
                    const phL = ph.toLowerCase(); return phL.includes('introducir valor') || phL.includes('enter value') || phL.includes('type value');
                });
                if (!input) return { success: false, error: "Introducir valor input not found" };
                
                const td = input.closest('td');
                if (!td || !td.nextElementSibling) return { success: false, error: "Sibling table cell not found" };
                
                const trigger = td.nextElementSibling.querySelector('.Mini-Hamburger-Menu') || 
                                td.nextElementSibling.querySelector('[id^=\"clickimage-\"]');
                if (!trigger) return { success: false, error: "Trigger button (.Mini-Hamburger-Menu) not found" };
                
                trigger.click();
                return { success: true, triggerId: trigger.id };
            }
        """)
        
        if not click_result.get("success"):
            raise RuntimeError(f"Could not open CSV upload dialog: {click_result.get('error')}")
        logger.debug(f"Trigger clicked: {click_result}")
        
        try:
            page.wait_for_selector("text=Carga de lista de archivos", timeout=5000)
        except Exception:
            page.wait_for_selector("text=File list upload", timeout=15000)
        
        # 7. Upload CSV file using Playwright
        logger.debug(f"Uploading file: {csv_path}...")
        page.set_input_files("input[type='file'].x-form-file-input", csv_path)
        
        # Fill filter name
        filter_name = verint_settings.get("filter_name", "Filtro_Calidad")
        logger.debug(f"Asignando nombre al filtro: {filter_name}...")
        filter_name_filled = page.evaluate("""
            (name) => {
                const label = Array.from(document.querySelectorAll('label, span')).find(el => { const t = el.textContent || ''; return t.includes('Nombre de filtro') || t.includes('Filter name') || t.includes('Filter Name'); });
                if (!label) return { success: false, error: "Label not found" };
                const container = label.closest('.x-field');
                const input = container ? container.querySelector('input') : null;
                if (input) {
                    input.value = name;
                    
                    const fireChange = (el) => {
                        try {
                            el.dispatchEvent(new Event('change'));
                        } catch (e) {
                            const evt = document.createEvent('HTMLEvents');
                            evt.initEvent('change', true, true);
                            el.dispatchEvent(evt);
                        }
                    };
                    fireChange(input);
                    return { success: true };
                }
                return { success: false, error: "Filter name input not found" };
            }
        """, filter_name)
        
        if not filter_name_filled.get("success"):
            raise RuntimeError(f"Could not fill filter name: {filter_name_filled.get('error')}")
            
        # Click Cargar button natively
        logger.debug("Haciendo clic en 'Cargar' / 'Upload'...")
        try:
            page.locator(".x-btn:has-text('Cargar')").first.click(timeout=5000)
        except Exception:
            page.locator(".x-btn:has-text('Load'), .x-btn:has-text('Upload')").first.click()
            
        # Wait for dialog to disappear (or loading mask)
        logger.debug("Waiting for upload processing to complete...")
        try:
            page.wait_for_selector("text=Procesando...", state="hidden", timeout=5000)
        except Exception:
            page.wait_for_selector("text=Processing...", state="hidden", timeout=15000)
            
        try:
            page.wait_for_selector("text=Carga de lista de archivos", state="hidden", timeout=5000)
        except Exception:
            page.wait_for_selector("text=File list upload", state="hidden", timeout=15000)
        logger.debug("Carga y procesamiento de lista de agentes completada.")
        
        # 8. Click Aplicar natively
        logger.info("Aplicando filtros y cargando resultados en Verint...")
        try:
            page.locator(".x-btn:has-text('Aplicar')").first.click(timeout=5000)
        except Exception:
            page.locator(".x-btn:has-text('Apply')").first.click()
            
        # Wait for data table refresh (loading mask detached)
        try:
            page.wait_for_selector("text=Cargando...", state="hidden", timeout=5000)
        except Exception:
            page.wait_for_selector("text=Loading...", state="hidden", timeout=15000)
        logger.debug("Filtros aplicados con éxito y resultados cargados.")
        
        # 9. Trigger Export Menu
        logger.debug("Abriendo el menú de exportación de interacciones...")
        export_menu_opened = page.evaluate("""
            () => {
                const exportLink = Array.from(document.querySelectorAll('a, span, div')).find(el => el.textContent && el.textContent.trim() === 'Exportar datos de interacción' && el.offsetWidth > 0);
                if (exportLink) {
                    exportLink.click();
                    return { success: true, openedDirectly: true };
                }
                
                const drawerBtn = document.querySelector('.open-nav-button') || document.querySelector('[id^=\"button-\"][class*=\"open-nav\"]');
                if (drawerBtn) {
                    drawerBtn.click();
                    return new Promise((resolve) => {
                        setTimeout(() => {
                            const link = Array.from(document.querySelectorAll('a, span, div')).find(el => el.textContent && el.textContent.trim() === 'Exportar datos de interacción');
                            if (link) {
                                link.click();
                                resolve({ success: true, openedViaDrawer: true });
                            } else {
                                resolve({ success: false, error: "Exportar option not found after opening drawer" });
                            }
                        }, 1000);
                    });
                }
                return { success: false, error: "Drawer button not found" };
            }
        """)
        if not export_menu_opened.get("success"):
            raise RuntimeError(f"Could not trigger export menu: {export_menu_opened.get('error')}")
            
        try:
            page.wait_for_selector(".x-window:has-text('Exportar datos')", timeout=5000)
        except Exception:
            page.wait_for_selector(".x-window:has-text('Export')", timeout=15000)
        
        # 10. Select export range and name natively
        logger.debug("Configurando los parámetros de exportación...")
        try:
            page.locator("label:has-text('Conjunto de resultados actual')").first.click(timeout=5000)
        except Exception:
            page.locator("label:has-text('Current result set'), label:has-text('Current results')").first.click()
        
        # Click Continuar natively
        logger.debug("Haciendo clic en Continuar...")
        try:
            page.locator(".x-btn:has-text('Continuar')").first.click(timeout=5000)
        except Exception:
            page.locator(".x-btn:has-text('Continue')").first.click()
            
        # Fill export name
        timestamp_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        export_name = f"Export_Calidad_{timestamp_str}"
        logger.debug(f"Asignando nombre al archivo exportado: {export_name}...")
        export_name_filled = page.evaluate("""
            (name) => {
                const fireChange = (el) => {
                    try {
                        el.dispatchEvent(new Event('change'));
                    } catch (e) {
                        const evt = document.createEvent('HTMLEvents');
                        evt.initEvent('change', true, true);
                        el.dispatchEvent(evt);
                    }
                };
                
                const labels = Array.from(document.querySelectorAll('label, span')).filter(el => { const t = el.textContent || ''; return t.trim().toLowerCase() === 'nombre:' || t.trim().toLowerCase() === 'name:'; });
                const label = labels.find(el => el.offsetWidth > 0);
                if (label) {
                    const container = label.closest('.x-field') || label.parentElement;
                    const input = container ? container.querySelector('input') : null;
                    if (input) {
                        input.value = name;
                        fireChange(input);
                        return { success: true };
                    }
                }
                const dialog = document.querySelector('.x-window') || Array.from(document.querySelectorAll('.x-panel')).find(el => { const t = el.textContent || ''; return t.includes('EXPORTAR') || t.includes('EXPORT'); });
                if (dialog) {
                    const inputs = Array.from(dialog.querySelectorAll('input[type=\"text\"]'));
                    if (inputs.length > 0) {
                        inputs[0].value = name;
                        fireChange(inputs[0]);
                        return { success: true, fallbackUsed: true };
                    }
                }
                return { success: false, error: "Export name input not found" };
            }
        """, export_name)
        if not export_name_filled.get("success"):
            raise RuntimeError(f"Could not fill export name: {export_name_filled.get('error')}")
            
        # Click Terminar natively
        logger.debug("Enviando reporte a la cola de exportación...")
        try:
            page.locator(".x-btn:has-text('Terminar')").first.click(timeout=5000)
        except Exception:
            page.locator(".x-btn:has-text('Finish'), .x-btn:has-text('Submit')").first.click()
            
        # Wait for dialog to disappear
        try:
            page.wait_for_selector(".x-window:has-text('Exportar datos')", state="hidden", timeout=5000)
        except Exception:
            page.wait_for_selector(".x-window:has-text('Export')", state="hidden", timeout=15000)
        logger.info(f"Reporte '{export_name}' enviado con éxito a la cola de exportación.")
        
        # 11. Navigate to "Mis exportaciones"
        exports_url = "https://wfo.mt5.verintcloudservices.com/wfo/ui/#wsm%5Bws%5D=speech_SavedReports"
        logger.debug(f"Navegando a Reportes Guardados / Mis Exportaciones: {exports_url}")
        page.goto(exports_url)
        page.wait_for_load_state("domcontentloaded")
        try:
            page.wait_for_selector("text=/MIS EXPORTACIONES|MY EXPORTS/i", timeout=30000)
        except Exception:
            raise RuntimeError("No se pudo cargar la vista de Mis Exportaciones / My Exports.")
        
        # Select project in exports view
        logger.debug("Configurando proyecto en la vista de exportaciones...")
        try:
            page.wait_for_selector("text=Proyecto:", timeout=10000)
            exports_project_label = "Proyecto:"
        except Exception:
            try:
                page.wait_for_selector("text=Project:", timeout=10000)
                exports_project_label = "Project:"
            except Exception:
                exports_project_label = None
                
        if exports_project_label:
            project_selected_exports = page.evaluate("""
                ([projectName, labelText]) => {
                    const label = Array.from(document.querySelectorAll('*')).find(el => {
                        return el.textContent && el.textContent.trim() === labelText && el.offsetWidth > 0;
                    });
                    const projectInput = label ? (label.parentElement.querySelector('input') || label.closest('.x-container').querySelector('input')) : null;
                    if (!projectInput) return { success: false, error: "Project input not found" };
                    if (projectInput.value === projectName) {
                        return { success: true, alreadySelected: true };
                    }
                    
                    const triggerId = projectInput.id.replace('-inputEl', '-trigger-picker');
                    const trigger = document.getElementById(triggerId);
                    if (trigger) {
                        trigger.click();
                    } else {
                        projectInput.click();
                    }
                    
                    return new Promise((resolve) => {
                        setTimeout(() => {
                            const items = Array.from(document.querySelectorAll('.x-boundlist-item'));
                            const item = items.find(el => el.textContent && el.textContent.trim() === projectName);
                            if (item) {
                                item.click();
                                resolve({ success: true });
                            } else {
                                resolve({ success: false, error: "Project option not found", items: items.map(el => el.textContent) });
                            }
                        }, 1000);
                    });
                }
            """, [verint_settings.get("project_name", "Televentas"), exports_project_label])
        logger.info("Esperando que se procese la exportación en Verint (tope máximo: 45 minutos)...")
        max_attempts = 45
        poll_interval = 60 # seconds (1 minuto por intento)
        download_triggered = False
        downloaded_paths = []
        
        for attempt in range(1, max_attempts + 1):
            logger.info(f"Comprobación {attempt}/{max_attempts} (próximo refresco en 60s)...")
            
            # Verificación de sesión activa / Auto-relogin si caducó la sesión o saltó 'Error desconocido'
            is_signed_out = "signin" in page.url or page.query_selector("#username") is not None
            has_error_banner = page.evaluate("""
                () => {
                    const text = document.body.innerText || '';
                    return text.includes('Error desconocido') || text.includes('Unknown error') || text.includes('Session expired') || text.includes('Sesión expirada');
                }
            """)
            
            if is_signed_out or has_error_banner:
                logger.warning("Se detectó sesión expirada o banner de error en Verint. Ejecutando auto-relogin de recuperación...")
                try:
                    if "signin" in page.url or page.query_selector("#username"):
                        page.fill("#username", username)
                        page.press("#username", "Enter")
                        page.wait_for_selector("#password", timeout=10000)
                        page.fill("#password", password)
                        page.press("#password", "Enter")
                        page.wait_for_timeout(3500)
                    
                    # Re-navegar a la vista de exportaciones guardadas
                    page.goto(exports_url)
                    page.wait_for_timeout(3000)
                    logger.info("Auto-relogin completado con éxito. Reanudando verificación de la grilla...")
                except Exception as e:
                    logger.error(f"Error al intentar re-iniciar sesión: {e}")
            page.evaluate("""
                () => {
                    // Dismiss red error toasts/banners if present
                    const closeBtns = document.querySelectorAll('.x-tool-close, .x-message-box-close');
                    closeBtns.forEach(btn => { try { btn.click(); } catch(e) {} });

                    if (window.Ext && window.Ext.ComponentQuery) {
                        try {
                            const grids = window.Ext.ComponentQuery.query('gridpanel, grid');
                            grids.forEach(g => { if (g.getStore()) g.getStore().reload(); });
                        } catch(e) {}
                    }
                    const btn = document.getElementById('refreshButton') || 
                                document.querySelector('.utility-pane-refresh') || 
                                document.querySelector('.x-tbar-loading') ||
                                Array.from(document.querySelectorAll('button, a, span, div')).find(el => { 
                                    const t = (el.textContent || '').trim().toLowerCase(); 
                                    const q = (el.getAttribute('data-qtip') || '').toLowerCase();
                                    return t === 'actualizar' || t === 'refresh' || q.includes('refresh') || q.includes('actualizar'); 
                                });
                    if (btn) btn.click();
                }
            """)
            page.wait_for_timeout(3500) # Wait 3.5s after clicking refresh for DOM to stabilize
            try:
                page.wait_for_selector("text=Cargando...", state="hidden", timeout=5000)
            except Exception:
                page.wait_for_selector("text=Loading...", state="hidden", timeout=15000)
            
            # Check row statuses matching our export name with instant DOM fallback
            export_status = page.evaluate("""
                (name) => {
                    const nameNodes = Array.from(document.querySelectorAll('*')).filter(el => {
                        const t = (el.textContent || '').trim();
                        return t.includes(name);
                    });
                    
                    if (nameNodes.length === 0) return { found: false };
                    
                    const rowsSet = new Set();
                    nameNodes.forEach(node => {
                        const row = node.closest('tr, .x-grid-row, .x-grid-item, div[role="row"]') || node.parentElement;
                        if (row) rowsSet.add(row);
                    });
                    
                    const matchingRows = Array.from(rowsSet);
                    if (matchingRows.length === 0) return { found: false };
                    
                    const details = matchingRows.map(row => {
                        const statusCell = row.querySelector('.x-grid-cell-reportHeaderStatus') || row;
                        const statusImg = statusCell ? (statusCell.querySelector('.SA_gridImage') || statusCell.querySelector('img')) : null;
                        const statusText = statusImg ? (statusImg.getAttribute('data-qtip') || statusImg.getAttribute('title') || '') : statusCell.textContent || '';
                        
                        const nameCell = row.querySelector('.x-grid-cell-reportHeaderName') || row;
                        const link = nameCell.querySelector('a, span') || nameCell;
                        const qtip = (link ? (link.getAttribute('data-qtip') || link.getAttribute('title')) : null) || nameCell.getAttribute('data-qtip') || nameCell.getAttribute('title');
                        const rowName = (qtip && qtip.includes('Export_Calidad')) ? qtip.trim() : (nameCell ? nameCell.textContent.trim() : name);
                        
                        const statusLower = statusText.toLowerCase();
                        const htmlLower = (row.outerHTML || '').toLowerCase();
                        const isLoading = statusLower.includes('proceso') || statusLower.includes('progress') || statusLower.includes('nueva') || statusLower.includes('cola') || statusLower.includes('pendient') || (statusImg && statusImg.className.includes('statusloading'));
                        const isCompleted = !isLoading && (statusLower.includes('completad') || statusLower.includes('completed') || statusLower.includes('finalizad') || (statusImg && statusImg.className.includes('statusok')) || htmlLower.includes('statusok'));
                        
                        return {
                            rowName,
                            isCompleted: isCompleted,
                            isLoading: isLoading,
                            statusText
                        };
                    });
                    
                    const allCompleted = details.length > 0 && details.every(d => d.isCompleted);
                    const anyLoading = details.some(d => d.isLoading);
                    
                    return {
                        found: true,
                        allCompleted,
                        anyLoading,
                        details
                    };
                }
            """, export_name)
            
            # If not found immediately after store reload, retry once after 2 seconds before declaring not visible
            if not export_status.get("found"):
                page.wait_for_timeout(2000)
                export_status = page.evaluate("""
                    (name) => {
                        const nameNodes = Array.from(document.querySelectorAll('*')).filter(el => {
                            const t = (el.textContent || '').trim();
                            return t.includes(name);
                        });
                        if (nameNodes.length === 0) return { found: false };
                        const rowsSet = new Set();
                        nameNodes.forEach(node => {
                            const row = node.closest('tr, .x-grid-row, .x-grid-item, div[role="row"]') || node.parentElement;
                            if (row) rowsSet.add(row);
                        });
                        const matchingRows = Array.from(rowsSet);
                        if (matchingRows.length === 0) return { found: false };
                        const details = matchingRows.map(row => {
                            const statusCell = row.querySelector('.x-grid-cell-reportHeaderStatus') || row;
                            const statusImg = statusCell ? (statusCell.querySelector('.SA_gridImage') || statusCell.querySelector('img')) : null;
                            const statusText = statusImg ? (statusImg.getAttribute('data-qtip') || statusImg.getAttribute('title') || '') : statusCell.textContent || '';
                            const nameCell = row.querySelector('.x-grid-cell-reportHeaderName') || row;
                            const link = nameCell.querySelector('a, span') || nameCell;
                            const qtip = (link ? (link.getAttribute('data-qtip') || link.getAttribute('title')) : null) || nameCell.getAttribute('data-qtip') || nameCell.getAttribute('title');
                            const rowName = (qtip && qtip.includes('Export_Calidad')) ? qtip.trim() : (nameCell ? nameCell.textContent.trim() : name);
                            const statusLower = statusText.toLowerCase();
                            const htmlLower = (row.outerHTML || '').toLowerCase();
                            const isLoading = statusLower.includes('proceso') || statusLower.includes('progress') || statusLower.includes('nueva') || statusLower.includes('cola') || statusLower.includes('pendient') || (statusImg && statusImg.className.includes('statusloading'));
                            const isCompleted = !isLoading && (statusLower.includes('completad') || statusLower.includes('completed') || statusLower.includes('finalizad') || (statusImg && statusImg.className.includes('statusok')) || htmlLower.includes('statusok'));
                            return { rowName, isCompleted, isLoading, statusText };
                        });
                        return { found: true, allCompleted: details.length > 0 && details.every(d => d.isCompleted), anyLoading: details.some(d => d.isLoading), details };
                    }
                """, export_name)
            
            if not export_status.get("found"):
                logger.debug(f"La exportación '{export_name}' aún no es visible en la lista. Esperando...")
            else:
                raw_details = export_status.get("details", [])
                # Deduplicar por nombre exacto de la partición (rowName)
                unique_details = {}
                for d in raw_details:
                    if d['rowName'] not in unique_details:
                        unique_details[d['rowName']] = d
                details = list(unique_details.values())

                all_completed = len(details) > 0 and all(d['isCompleted'] for d in details)
                logger.info(f"Se encontraron {len(details)} partición(es) de la exportación:")
                for d in details:
                    logger.info(f"  - {d['rowName']}: estado='{d['statusText']}' (completado={d['isCompleted']})")
                    
                if all_completed:
                    logger.info("¡Exportación completada! Descargando reporte...")
                    
                    # Click name links to trigger download events sequentially
                    # Get list of report names to download
                    report_names = [d["rowName"] for d in details]
                    
                    for report_name in report_names:
                        # Clean report_name for file matching
                        clean_rep_name = re.sub(r'[\\/*?:"<>|]', "_", report_name)
                        
                        # Check if a non-empty file matching report_name already exists in downloads_dir
                        existing = [
                            os.path.join(downloads_dir, f)
                            for f in os.listdir(downloads_dir)
                            if (clean_rep_name.lower() in f.lower() or report_name.lower() in f.lower()) 
                            and os.path.isfile(os.path.join(downloads_dir, f)) 
                            and os.path.getsize(os.path.join(downloads_dir, f)) > 0
                        ]
                        if existing:
                            logger.info(f"El reporte '{report_name}' ya existe descargado previamente: {os.path.basename(existing[0])}")
                            downloaded_paths.append(existing[0])
                            continue

                        logger.debug(f"Descargando reporte: {report_name}...")
                        
                        with page.expect_download(timeout=60000) as download_info:
                            # Click the specific link
                            page.evaluate("""
                                (repName) => {
                                    const rows = Array.from(document.querySelectorAll('.x-grid-item, .x-grid-row, tr.x-grid-row'));
                                    const targetRow = rows.find(row => {
                                        const html = (row.outerHTML || '');
                                        const text = (row.textContent || '');
                                        return html.includes(repName) || text.includes(repName);
                                    });
                                    if (targetRow) {
                                        const nameLink = targetRow.querySelector('.SA_reportLikeLink, a, span');
                                        if (nameLink) {
                                            nameLink.click();
                                        } else {
                                            targetRow.click();
                                        }
                                    }
                                }
                            """, report_name)
                            
                        download = download_info.value
                        suggested_filename = download.suggested_filename
                        # Clean suggested filename or use timestamp
                        if suggested_filename:
                            if report_name.lower() in suggested_filename.lower():
                                target_filename = suggested_filename
                            else:
                                target_filename = f"{report_name}_{suggested_filename}"
                        else:
                            target_filename = f"{report_name}.xlsx"
                        # Clean up any bad filename characters
                        target_filename = re.sub(r'[\\/*?:"<>|]', "_", target_filename)
                        
                        target_path = os.path.join(downloads_dir, target_filename)
                        logger.debug(f"Guardando archivo descargado en: {target_path}...")
                        download.save_as(target_path)
                        downloaded_paths.append(target_path)
                        
                    download_triggered = True
                    break
                    
            time.sleep(poll_interval)
            
        if not download_triggered:
            raise TimeoutError(f"Export '{export_name}' did not complete within the maximum polling time.")
            
        logger.info(f"Se descargaron correctamente {len(downloaded_paths)} archivo(s) Excel de Verint.")
        logger.debug("Cerrando el navegador.")
        browser.close()
        
    return downloaded_paths

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Verint WFO Quality Report Downloader")
    parser.add_argument("--period", type=str, help="Period in YYYYMM format (e.g. 202606)")
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode")
    args = parser.parse_args()
    
    # Read VERINT_HEADLESS from env to default
    load_env()
    env_headless = os.environ.get("VERINT_HEADLESS", "True").lower() == "true"
    headless = not args.headed if args.headed else env_headless
    
    try:
        paths = download_verint_data(period=args.period, headless=headless)
        print("SUCCESS_DOWNLOADS:", ",".join(paths))
    except Exception as e:
        logger.exception(f"Automation execution failed: {e}")
        sys.exit(1)
