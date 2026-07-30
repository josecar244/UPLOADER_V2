import warnings
warnings.filterwarnings("ignore", message=".*use_container_width.*")

import streamlit as st
import polars as pl
import traceback
import time
import os
import re
import datetime

import textwrap

from core.readers import read_excel_file, read_csv_file, read_unicode_text_file
from core.cleaners import clean_dataframe, sanitize_identifier
from core.database import load_credentials, connect_teradata, load_to_teradata
from core.logging_config import logger, setup_logging
from ui.components import render_sidebar, render_column_editor

from core.Insight_downloader import download_insight_data
from core.verint_downloader import download_verint_data
from core.sql_executor import SQLScriptExecutionError
from core.orchestrator import run_orchestration_flow
from core.quality_process_orchestrator import run_quality_process_flow

from genesys_bot.services.outlook_service import OutlookService
from genesys_bot.services.teradata_service import TeradataService
from genesys_bot.services.genesys_browser import GenesysBrowserAutomation
from genesys_bot.models import SolicitudAudio
from genesys_bot.config import DOWNLOADS_DIR

# Initialize Session State
if 'df' not in st.session_state:
    st.session_state.df = None
if 'columns_selected' not in st.session_state:
    st.session_state.columns_selected = None
if 'ingestion_completed' not in st.session_state:
    st.session_state.ingestion_completed = False
if 'last_ingested_file_name' not in st.session_state:
    st.session_state.last_ingested_file_name = None
if 'last_ingested_table' not in st.session_state:
    st.session_state.last_ingested_table = None
if 'last_uploaded_name' not in st.session_state:
    st.session_state.last_uploaded_name = None
if 'user_logs' not in st.session_state:
    st.session_state.user_logs = []
if 'pbi_running' not in st.session_state:
    st.session_state.pbi_running = False
if 'quality_running' not in st.session_state:
    st.session_state.quality_running = False
if 'audios_running' not in st.session_state:
    st.session_state.audios_running = False
if 'upload_running' not in st.session_state:
    st.session_state.upload_running = False
if 'outlook_correos_cache' not in st.session_state:
    st.session_state.outlook_correos_cache = []

def is_any_process_running():
    return (
        st.session_state.get('pbi_running', False)
        or st.session_state.get('quality_running', False)
        or st.session_state.get('audios_running', False)
        or st.session_state.get('upload_running', False)
    )

def get_running_process_name():
    if st.session_state.get('pbi_running', False):
        return "PBI Base Consumo"
    if st.session_state.get('quality_running', False):
        return "PBI Evaluaciones Calidad"
    if st.session_state.get('audios_running', False):
        return "Solicitud de Audios (Genesys)"
    if st.session_state.get('upload_running', False):
        return "Subida a Teradata"
    return None

def add_user_log(message, type="info"):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    prefix = "ℹ️"
    if type == "warning":
        prefix = "⚠️"
    elif type == "error":
        prefix = "❌"
    elif type == "success":
        prefix = "✅"
    
    # Strip any common emojis to ensure double emojis are not printed
    clean_msg = message
    for emoji in ["🛠️", "📡", "🚀", "ℹ️", "⚠️", "❌", "✅", "📌", "👉", "💾", "🎉", "⚙️", "📂"]:
        clean_msg = clean_msg.replace(emoji, "")
    clean_msg = clean_msg.strip()
    
    # Bypass Streamlit UI logs for sql execution statement logs
    if "ejecutando sentencia" in clean_msg.lower():
        if type == "warning":
            logger.warning(clean_msg)
        elif type == "error":
            logger.error(clean_msg)
        elif type == "success":
            logger.info(f"SUCCESS: {clean_msg}")
        else:
            logger.info(clean_msg)
        return
        
    st.session_state.user_logs.append(f"[{timestamp}] {prefix} {clean_msg}")
    
    # Write to Python's logging file
    if type == "warning":
        logger.warning(clean_msg)
    elif type == "error":
        logger.error(clean_msg)
    elif type == "success":
        logger.info(f"SUCCESS: {clean_msg}")
    else:
        logger.info(clean_msg)

def render_phase_stepper(current_phase, run_phases, phase_labels):
    """Renders a beautiful stepper showing the execution phases."""
    steps_html = []
    
    # Calculate progress line percentage based on current active phase
    # Steps are 1, 2, 3, 4, 5. So interval is 25% each.
    if current_phase <= 0:
        progress_width = 0
    elif current_phase >= 6:
        progress_width = 100
    else:
        progress_width = (current_phase - 1) * 25
        
    for i in range(1, 6):
        is_run = run_phases[i-1]
        label = phase_labels[i-1]
        
        step_class = ""
        icon = str(i)
        
        if not is_run:
            step_class = "skipped"
            icon = "✖"
        elif i < current_phase:
            step_class = "completed"
            icon = "✔"
        elif i == current_phase:
            step_class = "active"
        else:
            step_class = ""
            
        steps_html.append(
            f'<div class="step-item {step_class}">'
            f'<div class="step-circle">{icon}</div>'
            f'<div class="step-label">{label}</div>'
            f'</div>'
        )
        
    html = f"""
    <style>
    .stepper-container {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        position: relative;
        margin: 20px 0 30px 0;
        padding: 0 10px;
    }}
    .stepper-line {{
        position: absolute;
        top: 18px;
        left: 30px;
        right: 30px;
        height: 4px;
        background: #E2E8F0;
        z-index: 1;
    }}
    .stepper-progress {{
        position: absolute;
        top: 18px;
        left: 30px;
        height: 4px;
        background: linear-gradient(90deg, #3B82F6 0%, #10B981 100%);
        z-index: 1;
        width: {progress_width}%;
        transition: width 0.4s ease;
    }}
    .step-item {{
        display: flex;
        flex-direction: column;
        align-items: center;
        z-index: 2;
    }}
    .step-circle {{
        width: 40px;
        height: 40px;
        border-radius: 50%;
        background: #FFFFFF;
        border: 2px solid #CBD5E1;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: bold;
        color: #64748B;
        transition: all 0.3s ease;
    }}
    .step-label {{
        margin-top: 8px;
        font-size: 0.8rem;
        color: #64748B;
        text-align: center;
        max-width: 100px;
    }}
    .step-item.active .step-circle {{
        border-color: #3B82F6;
        background: #3B82F6;
        color: #FFFFFF;
        box-shadow: 0 0 10px rgba(59, 130, 246, 0.5);
        animation: pulse-step 1.5s infinite alternate;
    }}
    .step-item.completed .step-circle {{
        border-color: #10B981;
        background: #10B981;
        color: #FFFFFF;
    }}
    .step-item.skipped .step-circle {{
        border-color: #94A3B8;
        background: #F1F5F9;
        color: #94A3B8;
    }}
    .step-item.active .step-label {{
        color: #3B82F6;
        font-weight: bold;
    }}
    @keyframes pulse-step {{
        0% {{ transform: scale(1); }}
        100% {{ transform: scale(1.08); box-shadow: 0 0 15px rgba(59, 130, 246, 0.8); }}
    }}
    </style>
    <div class="stepper-container">
        <div class="stepper-line"></div>
        <div class="stepper-progress"></div>
        {"".join(steps_html)}
    </div>
    """
    return textwrap.dedent(html)

def render_terminal_logs(logs_list, placeholder):
    """Renders a beautiful, auto-scrolling terminal console in HTML."""
    lines = []
    for log in logs_list:
        # Convert markdown **bold** to HTML <b>bold</b>
        formatted_log = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', log)
        
        color = "#E2E8F0"  # default off-white
        background = "transparent"
        padding = "0"
        border_radius = "0"
        font_weight = "normal"
        border = "none"
        margin = "0 0 2px 0"
        
        is_phase = False
        # Check if log refers to a Fase X
        if any(f"fase {i}" in formatted_log.lower() for i in range(1, 6)):
            is_phase = True
            
        if "❌" in formatted_log or "error" in formatted_log.lower() or "fallo" in formatted_log.lower() or "timeout" in formatted_log.lower():
            color = "#F87171"  # red
        elif "⚠️" in formatted_log or "warning" in formatted_log.lower() or "advertencia" in formatted_log.lower() or "alerta" in formatted_log.lower():
            color = "#FBBF24"  # yellow
        elif "✅" in formatted_log or "success" in formatted_log.lower() or "exitoso" in formatted_log.lower() or "completado" in formatted_log.lower():
            color = "#34D399"  # green
        elif "📡" in formatted_log or "conectando" in formatted_log.lower():
            color = "#60A5FA"  # blue
        elif "⚙️" in formatted_log or "procesando" in formatted_log.lower():
            color = "#C084FC"  # purple
            
        if is_phase:
            color = "#FFFFFF"
            background = "linear-gradient(90deg, #1E3A8A 0%, #2563EB 100%)"
            padding = "6px 10px"
            border_radius = "4px"
            font_weight = "bold"
            border = "1px solid #3B82F6"
            margin = "6px 0"
            lines.append(f"<div style='color: {color}; background: {background}; padding: {padding}; border-radius: {border_radius}; font-weight: {font_weight}; border: {border}; margin: {margin}; font-family: monospace;'>{formatted_log}</div>")
        else:
            lines.append(f"<div style='color: {color}; margin: {margin}; font-family: monospace;'>{formatted_log}</div>")
        
    salt = int(time.time() * 1000)
    html_content = f"""
    <style>
        #term-container-{salt} b {{
            font-weight: bold !important;
            color: #FFFFFF !important;
        }}
    </style>
    <div id="term-container-{salt}" style="
        height: 220px;
        overflow-y: auto;
        font-family: 'Courier New', Courier, monospace;
        font-size: 0.82rem;
        background-color: #0B0F19;
        border: 1px solid #1E293B;
        border-radius: 8px;
        padding: 12px;
        line-height: 1.4;
    ">
        {"".join(lines)}
    </div>
    <script>
        var el = document.getElementById("term-container-{salt}");
        if (el) {{
            el.scrollTop = el.scrollHeight;
        }}
    </script>
    """
    placeholder.markdown(textwrap.dedent(html_content), unsafe_allow_html=True)

# Premium Page Styling
st.set_page_config(
    page_title="De Excel a Tera - Optimizado",
    layout="wide",
    initial_sidebar_state="auto"
)

# Custom Glassmorphism CSS for Premium Aesthetics
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .main-title {
        font-size: 2.5rem;
        font-weight: 700;
        background: linear-gradient(135deg, #3B82F6 0%, #1E3A8A 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 5px;
    }
    .subtitle {
        color: #64748B;
        font-size: 1.1rem;
        margin-bottom: 25px;
    }
    
    /* Custom Card Style */
    .dashboard-card {
        background: rgba(255, 255, 255, 0.7);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(226, 232, 240, 0.8);
        border-radius: 12px;
        padding: 20px;
        box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.05), 0 2px 4px -2px rgb(0 0 0 / 0.05);
        margin-bottom: 20px;
    }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown("<h1 class='main-title'>📁 Plataforma Unificada de Datos</h1>", unsafe_allow_html=True)
st.markdown("<p class='subtitle'>Descarga, configuración e ingesta de datos a Teradata</p>", unsafe_allow_html=True)

# Render Sidebar Configuration and get settings
config = render_sidebar()

def sanitize_secret_text(text: str) -> str:
    if not text:
        return ""
    clean_text = str(text)
    for key in ["TERADATA_PASSWORD", "PASSWORD_INSIGHT", "VERINT_PASS"]:
        secret = os.getenv(key)
        if secret and len(secret) > 2:
            clean_text = clean_text.replace(secret, "***MASKED***")
    return clean_text

def handle_execution_error(ex, status_box, log_prefix):
    clean_err = sanitize_secret_text(str(ex))
    if isinstance(ex, SQLScriptExecutionError):
        clean_orig = sanitize_secret_text(str(ex.original_error))
        status_box.update(label="❌ Error en Script SQL", state="error", expanded=True)
        st.error(f"### ❌ Fallo en ejecución de Script SQL")
        st.markdown(f"**Archivo:** `{ex.script_name}`")
        st.markdown(f"**Sentencia:** `{ex.statement_index}`")
        st.error(f"**Mensaje de Teradata:** {clean_orig}")
        with st.expander("🔍 Ver SQL que falló"):
            st.code(sanitize_secret_text(ex.sql_content), language="sql")
        add_user_log(f"Fallo SQL en {ex.script_name} (sentencia {ex.statement_index}): {clean_orig}", "error")
    else:
        status_box.update(label=f"❌ Error durante {log_prefix}", state="error", expanded=True)
        st.error(f"Fallo crítico: {clean_err}")
        add_user_log(f"Fallo en {log_prefix}: {clean_err}", "error")

# Create Tabs
tab_upload, tab_audios, tab_orchestrator, tab_quality_process = st.tabs([
    "📁 Subir a Teradata",
    "🎧 Solicitud de Audios (Genesys)",
    "⚡ PBI Base Consumo",
    "⚙️ PBI Evaluaciones Calidad"
])

# ==========================================
# TAB 1: UPLOADER TO TERADATA
# ==========================================
with tab_upload:
    # File Uploader Container
    st.markdown("<div class='dashboard-card'>", unsafe_allow_html=True)
    st.subheader("📁 Cargar archivo origen")
    
    if config["file_type"] == "Excel":
        uploaded_file = st.file_uploader("Seleccione un archivo Excel", type=["xlsx", "xls"], key="uploader_excel")
    elif config["file_type"] == "CSV":
        uploaded_file = st.file_uploader("Seleccione un archivo CSV", type=["csv"], key="uploader_csv")
    elif config["file_type"] == "Texto Unicode":
        uploaded_file = st.file_uploader("Seleccione un archivo de texto tabulado", type=["txt"], key="uploader_txt")
    
    st.markdown("</div>", unsafe_allow_html=True)
    
    # Determine source (uploaded file vs preloaded)
    use_preloaded = False
    if st.session_state.df is not None and st.session_state.last_uploaded_name is not None:
        if uploaded_file is not None and uploaded_file.name != st.session_state.last_uploaded_name:
            use_preloaded = False
        else:
            use_preloaded = True
            
    if use_preloaded:
        st.info(f"📂 Usando archivo cargado en sesión: **{st.session_state.last_uploaded_name}**")
        if st.button("❌ Quitar archivo cargado"):
            st.session_state.df = None
            st.session_state.last_uploaded_name = None
            st.session_state.ingestion_completed = False
            st.session_state.columns_selected = None
            st.rerun()
        current_file_name = st.session_state.last_uploaded_name
    else:
        if uploaded_file is None:
            st.session_state.df = None
            st.session_state.columns_selected = None
            st.session_state.ingestion_completed = False
            st.session_state.last_ingested_file_name = None
            st.session_state.last_ingested_table = None
            st.session_state.last_uploaded_name = None
            
            st.info("👋 Por favor, seleccione y cargue un archivo en el panel superior, o descargue datos en las otras pestañas para comenzar.")
        else:
            current_file_name = uploaded_file.name
            
            # File loading logic
            if current_file_name != st.session_state.get('last_uploaded_name'):
                st.session_state.ingestion_completed = False
                st.session_state.last_ingested_file_name = None
                st.session_state.last_ingested_table = None
                
            if (st.session_state.df is None) or ('last_uploaded_name' not in st.session_state) or (st.session_state.last_uploaded_name != current_file_name):
                try:
                    with st.spinner("Leyendo archivo de origen..."):
                        if config["file_type"] == "Excel":
                            logger.info("Inicio de lectura de Excel: %s", current_file_name)
                            df = read_excel_file(
                                uploaded_file,
                                selected_template=config["selected_template"],
                                templates=config["templates"]
                            )
                        elif config["file_type"] == "CSV":
                            logger.info("Inicio de lectura de CSV: %s", current_file_name)
                            df = read_csv_file(uploaded_file)
                        elif config["file_type"] == "Texto Unicode":
                            logger.info("Inicio de lectura de texto tabulado: %s", current_file_name)
                            df = read_unicode_text_file(uploaded_file)
                            
                        # Drop fully empty "Unnamed" columns
                        cols_to_drop = [col for col in df.columns if "Unnamed" in col and df[col].null_count() == len(df)]
                        if cols_to_drop:
                            df = df.drop(cols_to_drop)
                            
                        logger.info("Lectura exitosa: %s filas, %s columnas", len(df), len(df.columns))
                        st.session_state.df = df
                        st.session_state.last_uploaded_name = current_file_name
                except Exception as e:
                    logger.exception("Error al leer el archivo %s", uploaded_file.name if uploaded_file is not None else "<sin archivo>")
                    st.error(f"Error al leer el archivo: {e}")
                    st.code(traceback.format_exc())
                    st.stop()
                    
    # Render preview and loading settings if dataframe is active
    if st.session_state.df is not None:
        df = st.session_state.df
        
        st.markdown("<div class='dashboard-card'>", unsafe_allow_html=True)
        st.subheader("👀 Vista previa del archivo")
        st.write(f"Total de registros detectados: **{len(df):,}**")
        st.dataframe(df.head(5), width="stretch")
        st.markdown("</div>", unsafe_allow_html=True)
        
        # Column configuration editor
        st.markdown("<div class='dashboard-card'>", unsafe_allow_html=True)
        selections = render_column_editor(
            df,
            config["selected_template"],
            config["templates"],
            editor_key=f"column_config_editor_{current_file_name}_{config['selected_template']}"
        )
        st.session_state.columns_selected = selections
        st.markdown("</div>", unsafe_allow_html=True)
        
        # Database credentials & ingestion form
        st.markdown("<div class='dashboard-card'>", unsafe_allow_html=True)
        st.subheader("🔌 Destino Teradata")
        
        credenciales = load_credentials()
        teradata_user_default = credenciales.get('teradata_user', "")
        teradata_password_default = credenciales.get('teradata_password', "")
        
        col_c1, col_c2, col_c3 = st.columns(3)
        with col_c1:
            teradata_user = st.text_input("Usuario de Teradata", value=teradata_user_default)
        with col_c2:
            teradata_password = st.text_input("Contraseña de Teradata", type="password", value=teradata_password_default)
        with col_c3:
            teradata_table = st.text_input("Nombre de la tabla destino", placeholder="base.nombre_tabla")
            
        accion_seleccionada = st.selectbox(
            "Acción de carga",
            ["Seleccione una opción...", "Solo agregar nuevos registros", "Reemplazar registros existentes (Vaciar y cargar)"],
            index=0,
            help="Permite elegir entre adicionar los datos al final de la tabla o vaciar la tabla antes de la carga."
        )
        
        same_file_already_loaded = (
            st.session_state.get("ingestion_completed", False)
            and st.session_state.get("last_ingested_file_name") == current_file_name
        )
        
        if is_any_process_running() and not st.session_state.upload_running:
            running_proc = get_running_process_name()
            st.warning(f"⚠️ El proceso **'{running_proc}'** se encuentra actualmente en ejecución. Espere a que finalice para iniciar la carga.")

        if same_file_already_loaded:
            st.warning("Este archivo ya fue cargado correctamente. Sube otro archivo o reinicia la sesión para volver a cargarlo.")
            
        btn_disabled = same_file_already_loaded or is_any_process_running()
        if st.button("🚀 Cargar a Teradata", width="stretch", disabled=btn_disabled):
            if same_file_already_loaded:
                st.error("❌ Este archivo ya fue cargado. Por favor recarga la página o sube un archivo diferente.")
                st.stop()
                
            if accion_seleccionada == "Seleccione una opción...":
                st.error("Por favor, seleccione una acción de carga antes de continuar.")
            elif not teradata_user or not teradata_password or not teradata_table:
                st.error("Por favor, ingrese el usuario, la contraseña y la tabla de destino.")
            else:
                st.session_state.upload_running = True
                logger_setup = setup_logging(log_prefix="plantilla")
                with st.status("Preparando carga de datos...") as status_container:
                    start_time = time.time()
                    try:
                        def status_callback(msg):
                            status_container.write(msg)
                            log_type = "info"
                            if "advertencia" in msg.lower() or "warning" in msg.lower():
                                log_type = "warning"
                            elif "error" in msg.lower() or "fallo" in msg.lower():
                                log_type = "error"
                            elif "éxito" in msg.lower() or "completada" in msg.lower() or "completado" in msg.lower():
                                log_type = "success"
                            add_user_log(msg, log_type)
                            
                        status_callback("🛠️ Preparando y limpiando datos...")
                        df_clean = clean_dataframe(
                            df,
                            selections,
                            config["convertir_sin_acentos"],
                            config["transformar_varchar_latin"],
                            config["max_len_varchar"]
                        )
                        
                        status_callback("📡 Conectando a Teradata...")
                        host = credenciales.get('teradata_host', 'IBKTD')
                        logmech = credenciales.get('teradata_logmech', 'TD2')
                        con = connect_teradata(teradata_user, teradata_password, host=host, logmech=logmech)
                        
                        clear_table = (accion_seleccionada == "Reemplazar registros existentes (Vaciar y cargar)")
                        
                        load_to_teradata(
                            con,
                            teradata_table,
                            df_clean,
                            selections,
                            clear_table,
                            progress_callback=status_callback
                        )
                        
                        con.close()
                        elapsed = time.time() - start_time
                        st.session_state.ingestion_completed = True
                        st.session_state.last_ingested_file_name = current_file_name
                        st.session_state.last_ingested_table = teradata_table
                        status_container.update(label="🎉 ¡Ingesta completada con éxito!", state="complete", expanded=False)
                        st.success(f"Se cargaron los datos correctamente en la tabla '{teradata_table}'. Tiempo total: {elapsed:.2f} segundos.")
                        add_user_log(f"Carga finalizada con éxito en la tabla '{teradata_table}' en {elapsed:.2f}s.", "success")
                        
                    except Exception as e:
                        logger.exception("Error durante la carga en %s", teradata_table)
                        status_container.update(label="❌ Error durante la carga", state="error", expanded=True)
                        st.error(f"Fallo en la ingesta: {e}")
                        add_user_log(f"Fallo en la ingesta en Teradata: {e}", "error")
                    finally:
                        st.session_state.upload_running = False
                        
        st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# TAB 2: SOLICITUD DE AUDIOS (GENESYS BOT)
# ==========================================
with tab_audios:
    st.markdown("<div class='dashboard-card'>", unsafe_allow_html=True)
    st.subheader("🎧 Solicitud y Descarga de Audios de Genesys")
    st.markdown("Obtén audios de llamadas desde Genesys mediante la lectura de correos de Outlook o ingreso manual de solicitudes.")

    origen = st.radio(
        "Seleccione la fuente de solicitudes:",
        ["📧 Leer de Outlook", "✏️ Ingreso Manual"],
        horizontal=True,
        key="audio_source_radio"
    )

    solicitudes_a_procesar = []

    if origen == "📧 Leer de Outlook":
        st.markdown("##### 📥 Correos de Solicitud en Outlook")
        col_btn, col_info = st.columns([1, 3])
        with col_btn:
            if st.button("🔄 Buscar últimos 3 correos", key="btn_fetch_outlook"):
                with st.spinner("Consultando Outlook Desktop..."):
                    try:
                        outlook_svc = OutlookService()
                        correos = outlook_svc.obtener_ultimos_correos(limit=3)
                        st.session_state.outlook_correos_cache = correos
                        if not correos:
                            st.warning("No se encontraron correos con asunto 'Solicitud de audio...'.")
                    except Exception as e:
                        st.error(f"Error al conectar con Outlook: {e}")

        correos_cache = st.session_state.get("outlook_correos_cache", [])
        if correos_cache:
            options = [
                f"Correo #{c['index']} | Asunto: {c['asunto']} | De: {c['remitente']} | Registros: {c['cant_registros']} ({c['fecha']})"
                for c in correos_cache
            ]
            selected_idx = st.radio(
                "Seleccione el correo específico a atender:",
                range(len(options)),
                format_func=lambda i: options[i],
                key="selected_email_index"
            )
            selected_mail = correos_cache[selected_idx]
            st.info(f"📌 Seleccionado: **{selected_mail['asunto']}** ({selected_mail['cant_registros']} registros detectados)")
            solicitudes_a_procesar = selected_mail["solicitudes"]
        else:
            st.caption("Haz clic en 'Buscar últimos 3 correos' para consultar la bandeja de Outlook.")

    else:
        st.markdown("##### ✏️ Ingreso Manual de Solicitudes")
        tab_m1, tab_m2, tab_m3 = st.tabs(["Formulario Directo", "📋 Copiar y Pegar de Excel", "📁 Subir Archivo Excel"])
        
        with tab_m1:
            c_reg, c_dni, c_pref = st.columns(3)
            with c_reg:
                reg_ev_input = st.text_input("Registro Ejecutivo (Reg EV)", placeholder="Ej: B12345", key="man_reg_ev")
            with c_dni:
                dni_input = st.text_input("DNI", placeholder="Ej: 72839405", key="man_dni")
            with c_pref:
                pref_input = st.selectbox("Producto / Prefijo", ["AUDIO", "EC", "CC", "SEG", "HIP", "PP", "TC"], key="man_pref")
            
            if reg_ev_input and dni_input:
                reg_clean = reg_ev_input.strip().upper()
                dni_clean = dni_input.strip().zfill(8)
                sol_manual = SolicitudAudio(
                    reg_ev=reg_clean,
                    dni=dni_clean,
                    nombre_archivo=f"{pref_input}_{reg_clean}_DNI{dni_clean}",
                    prefijo=pref_input
                )
                solicitudes_a_procesar = [sol_manual]
                st.success(f"Solicitud manual agregada: Ejecutivo {reg_clean} - DNI {dni_clean}")

        with tab_m2:
            st.markdown("Copia cualquier selección de columnas desde Excel y pégala aquí (no importa el nombre de las columnas ni el orden):")
            pref_paste = st.selectbox("Producto / Prefijo", ["AUDIO", "EC", "CC", "SEG", "HIP", "PP", "TC"], key="paste_audio_pref")
            pasted_text = st.text_area(
                "Pegar celdas/columnas de Excel:",
                height=180,
                placeholder="7468339\tB35381\n46816480\tB35381\n45181595\tB36759",
                key="pasted_excel_text"
            )
            if pasted_text:
                import re
                solicitudes_a_procesar = []
                seen_keys = set()
                lines = pasted_text.strip().splitlines()
                
                for line in lines:
                    if not line.strip():
                        continue
                    m_reg = re.search(r'\b([A-Za-z]\d{5})\b', line)
                    m_dni = re.search(r'\b(\d{7,8})\b', line)
                    
                    if m_reg and m_dni:
                        r_ev = m_reg.group(1).upper()
                        d_val = m_dni.group(1).zfill(8)
                        key = f"{r_ev}_{d_val}"
                        if key not in seen_keys:
                            seen_keys.add(key)
                            nom = f"{pref_paste}_{r_ev}_DNI{d_val}"
                            solicitudes_a_procesar.append(
                                SolicitudAudio(reg_ev=r_ev, dni=d_val, nombre_archivo=nom, prefijo=pref_paste)
                            )
                            
                if solicitudes_a_procesar:
                    st.success(f"✅ Se detectaron **{len(solicitudes_a_procesar)}** solicitud(es) válida(s) (Ejecutivo + DNI) en el texto pegado.")
                    st.dataframe([{"Ejecutivo": s.reg_ev, "DNI": s.dni, "Archivo": s.nombre_archivo} for s in solicitudes_a_procesar])
                else:
                    st.warning("⚠️ No se encontraron parejas válidas de Ejecutivo (ej: B12345) y DNI (7-8 dígitos) en el texto pegado.")

        with tab_m3:
            file_manual = st.file_uploader("Subir Excel con solicitudes [Promotor/Ejecutivo, DNI]", type=["xlsx", "xls"], key="manual_audio_excel")
            if file_manual:
                try:
                    import pandas as pd
                    df_man = pd.read_excel(file_manual)
                    outlook_svc = OutlookService()
                    solicitudes_a_procesar = outlook_svc._normalizar_dataframe(df_man)
                    st.success(f"Se detectaron {len(solicitudes_a_procesar)} solicitudes válidas en el Excel.")
                except Exception as e:
                    st.error(f"Error al leer el archivo Excel manual: {e}")

    st.markdown("<hr style='margin: 15px 0; border-color: #E2E8F0;'>", unsafe_allow_html=True)

    if is_any_process_running() and not st.session_state.audios_running:
        running_proc = get_running_process_name()
        st.warning(f"⚠️ El proceso **'{running_proc}'** se encuentra actualmente en ejecución. Espere a que finalice para iniciar este proceso.")

    if st.session_state.audios_running:
        st.info("⏳ El proceso de descarga de audios Genesys está en ejecución. Por favor espere...")

    if solicitudes_a_procesar:
        st.markdown(f"📊 **Solicitudes listas para procesar:** `{len(solicitudes_a_procesar)}`")
        
        btn_disabled = is_any_process_running()
        if st.button("🚀 Iniciar Descarga de Audios en Genesys", key="btn_run_genesys_audios", disabled=btn_disabled):
            st.session_state.audios_running = True
            st.rerun()

    if st.session_state.audios_running:
        status_box = st.status("Iniciando flujo de descarga de audios...", expanded=True)
        setup_logging(log_prefix="genesys_audios")
        try:
            status_box.write("📡 Paso 1: Consultando Teradata para obtener teléfonos de los DNI (GESTION >= 202501)...")
            add_user_log("Consultando Teradata para enriquecer DNI con números telefónicos...", "info")
            
            td_svc = TeradataService()
            solicitudes_enriquecidas = td_svc.enriquecer_solicitudes(solicitudes_a_procesar)
            
            if not solicitudes_enriquecidas:
                status_box.update(label="⚠️ Sin teléfonos encontrados en Teradata", state="error", expanded=True)
                st.warning("No se encontraron registros de teléfonos en Teradata para los DNI ingresados.")
            else:
                status_box.write(f"✅ Se obtuvieron teléfonos para {len(solicitudes_enriquecidas)} solicitud(es).")
                status_box.write("🌐 Paso 2: Iniciando automatización de navegador en Genesys Cloud...")
                add_user_log("Iniciando bot de navegador Genesys Cloud...", "info")
                
                output_dir = DOWNLOADS_DIR
                output_dir.mkdir(parents=True, exist_ok=True)
                
                bot = GenesysBrowserAutomation()
                bot.ejecutar_descargas(solicitudes_enriquecidas)
                
                status_box.update(label="🎉 ¡Descarga de audios completada!", state="complete")
                st.success(f"Archivos descargados en la carpeta: `{output_dir}`")
                add_user_log(f"Descarga de audios finalizada con éxito. Destino: {output_dir}", "success")
        except Exception as e:
            status_box.update(label="❌ Error durante la descarga de audios", state="error", expanded=True)
            st.error(f"Fallo en el proceso de audios: {e}")
            add_user_log(f"Fallo en el proceso de audios: {e}", "error")
        finally:
            st.session_state.audios_running = False

    st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# TAB 4: AUTOMATIC CALIDAD INSUMOS ORCHESTRATOR
# ==========================================
with tab_orchestrator:
    import datetime
    st.markdown("<div class='dashboard-card'>", unsafe_allow_html=True)
    st.subheader("⚡ Actualizar Calidad Insumos (PBI)")
    st.markdown("Automatiza la descarga de los 7 insumos de Insight, su carga limpia en Teradata y la ejecución de transformaciones SQL.")
    
    credenciales = load_credentials()
    
    col_auth1, col_auth2 = st.columns(2)
    with col_auth1:
        st.markdown("#### 🔐 Credenciales Insight")
        username_ins = st.text_input("Usuario de Insight", value=os.getenv("USERNAME_INSIGHT", ""), key="orch_user_insight")
        password_ins = st.text_input("Contraseña de Insight", type="password", value=os.getenv("PASSWORD_INSIGHT", ""), key="orch_pass_insight")
    with col_auth2:
        st.markdown("#### 🔐 Credenciales Teradata")
        username_td = st.text_input("Usuario de Teradata", value=credenciales.get('teradata_user', ""), key="orch_user_td")
        password_td = st.text_input("Contraseña de Teradata", type="password", value=credenciales.get('teradata_password', ""), key="orch_pass_td")
        
    st.markdown("<hr style='margin: 15px 0; border-color: #E2E8F0;'>", unsafe_allow_html=True)
    
    st.markdown("#### ⚙️ Parámetros de Ejecución")
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        current_period_default = datetime.datetime.now().strftime("%Y%m")
        period_str = st.text_input("Periodo de Proceso (YYYYMM)", value=current_period_default, key="orch_period")
    with col_p2:
        clear_consent = st.checkbox("Limpiar Consentimientos", value=False, key="orch_clear_consent", help="Si se marca, limpiará los consentimientos LPDP acumulados antes de cargar los nuevos.")
        
    st.markdown("#### 🕒 Fases a Ejecutar")
    col_f1, col_f2, col_f3, col_f4, col_f5 = st.columns(5)
    with col_f1:
        run_f1 = st.checkbox("Fase 1: Insight (7 Insumos)", value=True, key="orch_run_f1")
    with col_f2:
        run_f2 = st.checkbox("Fase 2: Ingesta CD40K Manual", value=True, key="orch_run_f2")
    with col_f3:
        run_f3 = st.checkbox("Fase 3: Desembolsos (SQL Server)", value=True, key="orch_run_f3")
    with col_f4:
        run_f4 = st.checkbox("Fase 4: Pipeline SQL Consumo", value=True, key="orch_run_f4")
    with col_f5:
        run_f5 = st.checkbox("Fase 5: Ingesta Selección (Secundario)", value=True, key="orch_run_f5")
        
    start_from_script = None
    if run_f4:
        start_from_script = st.selectbox(
            "▶️ Iniciar Fase 4 desde:",
            ["Todo", "ventas_dn.sql", "cd40k.sql", "source_tvl.sql", "ca_consentimiento_diario.sql", "kri_ventas_sin_audio.sql", "tlf_no_autorizado.sql", "consumo_select_tc_cd_seg.sql"],
            index=0,
            key="orch_start_script",
            help="Seleccione el script desde el cual desea iniciar la ejecución de la Fase 4 en caso de haber reanudado tras una falla."
        )
        if start_from_script == "Todo":
            start_from_script = None

    st.markdown("<hr style='margin: 15px 0; border-color: #E2E8F0;'>", unsafe_allow_html=True)
    
    if is_any_process_running() and not st.session_state.pbi_running:
        running_proc = get_running_process_name()
        st.warning(f"⚠️ El proceso **'{running_proc}'** se encuentra actualmente en ejecución. Espere a que finalice para iniciar este proceso.")

    if st.session_state.pbi_running:
        st.info("⏳ El proceso automático está en ejecución. Por favor, espere a que finalice...")
        st.button("⏳ Procesando...", key="btn_run_orchestrator_disabled", disabled=True)
    else:
        btn_disabled = is_any_process_running()
        if st.button("🚀 Iniciar Proceso Automático", key="btn_run_orchestrator", type="primary", disabled=btn_disabled):
            if run_f1 and (not username_ins or not password_ins):
                st.error("Por favor, ingrese sus credenciales de Insight.")
            elif (run_f1 or run_f2 or run_f3 or run_f4) and (not username_td or not password_td):
                st.error("Por favor, ingrese sus credenciales de Teradata.")
            elif not period_str or len(period_str) != 6 or not period_str.isdigit():
                st.error("Por favor, ingrese un periodo válido en formato YYYYMM (6 dígitos).")
            elif not (run_f1 or run_f2 or run_f3 or run_f4 or run_f5):
                st.error("Por favor, seleccione al menos una fase para ejecutar.")
            else:
                st.session_state.pbi_running = True
                st.rerun()

    if st.session_state.pbi_running:
        status_box = st.status("Iniciando flujo de orquestación...", expanded=True)
        setup_logging(log_prefix="pbi_insumos")
        with status_box:
            stepper_placeholder = st.empty()
            progress_bar = st.progress(0.0)
            progress_text = st.empty()
            console_placeholder = st.empty()
        try:
            log_history = []
            state = {"current_script": "", "current_phase": 0}
            run_phases = [run_f1, run_f2, run_f3, run_f4, run_f5]
            labels = ["Insight Insumos", "CD40K Manual", "Desembolsos SQL", "SQL Consumo", "Selección"]
            
            def progress_cb(msg, log_type="info", *args, **kwargs):
                if "ejecutando sentencia" in msg.lower():
                    add_user_log(msg, log_type)
                    return
                    
                progress = kwargs.get("progress")
                if progress is None and args:
                    progress = args[0]
                    
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                prefix = "ℹ️"
                if log_type == "warning":
                    prefix = "⚠️"
                elif log_type == "error":
                    prefix = "❌"
                elif log_type == "success":
                    prefix = "✅"
                
                clean_msg = msg
                for emoji in ["🛠️", "📡", "🚀", "ℹ️", "⚠️", "❌", "✅", "⚙️", "📂"]:
                    clean_msg = clean_msg.replace(emoji, "")
                clean_msg = clean_msg.strip()
                
                log_history.append(f"[{timestamp}] {prefix} {clean_msg}")
                render_terminal_logs(log_history, console_placeholder)
                
                # Detect and update current phase
                for i in range(1, 6):
                    if f"fase {i}" in msg.lower():
                        state["current_phase"] = i
                        status_box.update(label=f"🏃 Ejecutando: {clean_msg}", expanded=True)
                        break
                
                # Update stepper
                if state["current_phase"] > 0:
                    stepper_placeholder.markdown(
                        render_phase_stepper(state["current_phase"], run_phases, labels),
                        unsafe_allow_html=True
                    )
                
                if "⚙️ Procesando:" in msg or "Procesando:" in msg:
                    name_part = msg.replace("⚙️", "").replace("Procesando:", "").replace("...", "").strip()
                    state["current_script"] = name_part
                    status_box.update(label=f"⏳ Procesando: {name_part}...", expanded=True)
                    
                # Highlight active phase in the main progress message
                is_phase_msg = any(f"fase {i}" in msg.lower() for i in range(1, 6))
                
                if is_phase_msg:
                    progress_text.markdown(textwrap.dedent(f"""
                    <div style="padding: 12px; background-color: #1E293B; border-left: 5px solid #2563EB; border-radius: 6px; margin: 10px 0;">
                        <h4 style="margin: 0 0 4px 0; color: #3B82F6; font-size: 1.05rem; font-weight: bold; font-family: 'Inter', sans-serif;">🚀 FASE ACTIVA</h4>
                        <div style="color: #F8FAFC; font-size: 0.9rem; font-family: 'Inter', sans-serif;">{msg.strip()}</div>
                    </div>
                    """), unsafe_allow_html=True)
                elif progress is not None:
                    try:
                        val = min(max(float(progress), 0.0), 1.0)
                        progress_bar.progress(val)
                        script_info = f" ({state['current_script']})" if state["current_script"] else ""
                        progress_text.markdown(f"**Progreso{script_info}:** {msg.strip()}")
                    except (ValueError, TypeError):
                        progress_text.markdown(f"**Actual:** {msg.strip()}")
                else:
                    progress_text.markdown(f"**Actual:** {msg.strip()}")
                    
                add_user_log(msg, log_type)
                
            run_orchestration_flow(
                insight_user=username_ins,
                insight_password=password_ins,
                td_user=username_td,
                td_password=password_td,
                period_str=period_str,
                clear_consent=clear_consent,
                progress_callback=progress_cb,
                run_phase1=run_f1,
                run_phase2=run_f2,
                run_phase3=run_f3,
                run_phase4=run_f4,
                run_phase5=run_f5,
                start_from_script=start_from_script
            )
            progress_bar.progress(1.0)
            progress_text.empty()
            stepper_placeholder.markdown(
                render_phase_stepper(6, run_phases, labels),
                unsafe_allow_html=True
            )
            status_box.update(label="🎉 ¡Orquestación completada con éxito! Listo para PBI.", state="complete")
            st.success("🎉 El proceso automático finalizó correctamente. Todas las fases seleccionadas se ejecutaron correctamente.")
        except Exception as ex:
            progress_bar.empty()
            progress_text.empty()
            handle_execution_error(ex, status_box, "la orquestación")
        finally:
            st.session_state.pbi_running = False
                
    st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# TAB 5: QUALITY PROCESS ORCHESTRATOR
# ==========================================
with tab_quality_process:
    st.markdown("<div class='dashboard-card'>", unsafe_allow_html=True)
    st.subheader("⚙️ Pipeline de Proceso Calidad Completo")
    st.markdown("Ejecuta la secuencia completa de descargas de Insight y Verint, ingesta del Excel de acciones tomadas y ejecución de scripts SQL de consolidación.")
    
    credenciales = load_credentials()
    
    col_qauth1, col_qauth2, col_qauth3 = st.columns(3)
    with col_qauth1:
        st.markdown("#### 🔐 Credenciales Insight")
        q_username_ins = st.text_input("Usuario de Insight", value=os.getenv("USERNAME_INSIGHT", ""), key="q_user_insight")
        q_password_ins = st.text_input("Contraseña de Insight", type="password", value=os.getenv("PASSWORD_INSIGHT", ""), key="q_pass_insight")
    with col_qauth2:
        st.markdown("#### 🔐 Credenciales Verint")
        q_username_ver = st.text_input("Usuario de Verint", value=os.getenv("VERINT_USER", ""), key="q_user_verint")
        q_password_ver = st.text_input("Contraseña de Verint", type="password", value=os.getenv("VERINT_PASS", ""), key="q_pass_verint")
    with col_qauth3:
        st.markdown("#### 🔐 Credenciales Teradata")
        q_username_td = st.text_input("Usuario de Teradata", value=credenciales.get('teradata_user', ""), key="q_user_td")
        q_password_td = st.text_input("Contraseña de Teradata", type="password", value=credenciales.get('teradata_password', ""), key="q_pass_td")
        
    st.markdown("<hr style='margin: 15px 0; border-color: #E2E8F0;'>", unsafe_allow_html=True)
    
    st.markdown("#### ⚙️ Parámetros de Ejecución")
    col_qp1, col_qp2 = st.columns(2)
    with col_qp1:
        current_period_default = datetime.datetime.now().strftime("%Y%m")
        q_period_str = st.text_input("Periodo de Proceso (YYYYMM)", value=current_period_default, key="q_period")
    with col_qp2:
        st.markdown("<br>", unsafe_allow_html=True)
        st.info("💡 Coloca el archivo **ACCION_TOMADA.xlsx** en la carpeta **INPUT_PROCESO_CALIDAD** antes de ejecutar.")
        
    st.markdown("#### 🕒 Fases a Ejecutar")
    col_f1, col_f2, col_f3, col_f4, col_f5 = st.columns(5)
    with col_f1:
        run_f1 = st.checkbox("Fase 1: Insight (Evaluaciones)", value=True, key="q_run_f1")
    with col_f2:
        run_f2 = st.checkbox("Fase 2: Verint (Speech Analytics)", value=True, key="q_run_f2")
    with col_f3:
        run_f3 = st.checkbox("Fase 3: Acciones Tomadas Excel", value=True, key="q_run_f3")
    with col_f4:
        run_f4 = st.checkbox("Fase 4: Transformaciones SQL", value=True, key="q_run_f4")
    with col_f5:
        run_f5 = st.checkbox("Fase 5: Proceso NTD", value=True, key="q_run_f5")
        
    q_start_from_script = None
    if run_f4:
        q_start_from_script = st.selectbox(
            "▶️ Iniciar Fase 4 desde:",
            ["Todo", "01_evaluacion_manual_pc.sql", "02_sa_marcacion_ventas_lpdp.sql", "03_sa_calculo_pesos_unpivot.sql", "04_sa_ajustes_curva.sql", "04_b_sa_parche_nota_cero.sql", "05_consolidacion_nota_final.sql"],
            index=0,
            key="q_start_script",
            help="Seleccione el script desde el cual desea iniciar la ejecución de la Fase 4 en caso de haber reanudado tras una falla."
        )
        if q_start_from_script == "Todo":
            q_start_from_script = None

    if is_any_process_running() and not st.session_state.quality_running:
        running_proc = get_running_process_name()
        st.warning(f"⚠️ El proceso **'{running_proc}'** se encuentra actualmente en ejecución. Espere a que finalice para iniciar este proceso.")

    if st.session_state.quality_running:
        st.info("⏳ El proceso de calidad está en ejecución. Por favor, espere a que finalice...")
        st.button("⏳ Procesando...", key="btn_run_quality_process_disabled", disabled=True)
    else:
        btn_disabled = is_any_process_running()
        if st.button("🚀 Iniciar Proceso de Calidad Completo", key="btn_run_quality_process", type="primary", disabled=btn_disabled):
            if run_f1 and (not q_username_ins or not q_password_ins):
                st.error("Por favor, ingrese sus credenciales de Insight.")
            elif run_f2 and (not q_username_ver or not q_password_ver):
                st.error("Por favor, ingrese sus credenciales de Verint.")
            elif not q_username_td or not q_password_td:
                st.error("Por favor, ingrese sus credenciales de Teradata.")
            elif not q_period_str or len(q_period_str) != 6 or not q_period_str.isdigit():
                st.error("Por favor, ingrese un periodo válido en formato YYYYMM (6 dígitos).")
            elif not (run_f1 or run_f2 or run_f3 or run_f4 or run_f5):
                st.error("Por favor, seleccione al menos una fase para ejecutar.")
            else:
                st.session_state.quality_running = True
                st.rerun()

    if st.session_state.quality_running:
        status_box = st.status("Iniciando pipeline de calidad unificado...", expanded=True)
        setup_logging(log_prefix="proceso_calidad")
        with status_box:
            stepper_placeholder = st.empty()
            progress_bar = st.progress(0.0)
            progress_text = st.empty()
            console_placeholder = st.empty()
        try:
            log_history = []
            state = {"current_script": "", "current_phase": 0}
            run_phases = [run_f1, run_f2, run_f3, run_f4, run_f5]
            labels = ["Insight Eval", "Verint Speech", "Acciones Tomadas", "SQL Calidad", "Proceso NTD"]
            
            def progress_cb(msg, log_type="info", *args, **kwargs):
                if "ejecutando sentencia" in msg.lower():
                    add_user_log(msg, log_type)
                    return
                    
                progress = kwargs.get("progress")
                if progress is None and args:
                    progress = args[0]
                    
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                prefix = "ℹ️"
                if log_type == "warning":
                    prefix = "⚠️"
                elif log_type == "error":
                    prefix = "❌"
                elif log_type == "success":
                    prefix = "✅"
                
                clean_msg = msg
                for emoji in ["🛠️", "📡", "🚀", "ℹ️", "⚠️", "❌", "✅", "⚙️", "📂"]:
                    clean_msg = clean_msg.replace(emoji, "")
                clean_msg = clean_msg.strip()
                
                log_history.append(f"[{timestamp}] {prefix} {clean_msg}")
                render_terminal_logs(log_history, console_placeholder)
                
                # Detect and update current phase
                for i in range(1, 6):
                    if f"fase {i}" in msg.lower():
                        state["current_phase"] = i
                        status_box.update(label=f"🏃 Ejecutando: {clean_msg}", expanded=True)
                        break
                
                # Update stepper
                if state["current_phase"] > 0:
                    stepper_placeholder.markdown(
                        render_phase_stepper(state["current_phase"], run_phases, labels),
                        unsafe_allow_html=True
                    )
                
                if "⚙️ Procesando:" in msg or "Procesando:" in msg:
                    name_part = msg.replace("⚙️", "").replace("Procesando:", "").replace("...", "").strip()
                    state["current_script"] = name_part
                    status_box.update(label=f"⏳ Procesando: {name_part}...", expanded=True)
                    
                # Highlight active phase in the main progress message
                is_phase_msg = any(f"fase {i}" in msg.lower() for i in range(1, 6))
                
                if is_phase_msg:
                    progress_text.markdown(textwrap.dedent(f"""
                    <div style="padding: 12px; background-color: #1E293B; border-left: 5px solid #2563EB; border-radius: 6px; margin: 10px 0;">
                        <h4 style="margin: 0 0 4px 0; color: #3B82F6; font-size: 1.05rem; font-weight: bold; font-family: 'Inter', sans-serif;">🚀 FASE ACTIVA</h4>
                        <div style="color: #F8FAFC; font-size: 0.9rem; font-family: 'Inter', sans-serif;">{msg.strip()}</div>
                    </div>
                    """), unsafe_allow_html=True)
                elif progress is not None:
                    try:
                        val = min(max(float(progress), 0.0), 1.0)
                        progress_bar.progress(val)
                        script_info = f" ({state['current_script']})" if state["current_script"] else ""
                        progress_text.markdown(f"**Progreso{script_info}:** {msg.strip()}")
                    except (ValueError, TypeError):
                        progress_text.markdown(f"**Actual:** {msg.strip()}")
                else:
                    progress_text.markdown(f"**Actual:** {msg.strip()}")
                    
                add_user_log(msg, log_type)
                
            run_quality_process_flow(
                insight_user=q_username_ins,
                insight_password=q_password_ins,
                verint_user=q_username_ver,
                verint_password=q_password_ver,
                td_user=q_username_td,
                td_password=q_password_td,
                period_str=q_period_str,
                progress_callback=progress_cb,
                run_phase1=run_f1,
                run_phase2=run_f2,
                run_phase3=run_f3,
                run_phase4=run_f4,
                run_phase5=run_f5,
                start_from_script=q_start_from_script
            )
            progress_bar.progress(1.0)
            progress_text.empty()
            stepper_placeholder.markdown(
                render_phase_stepper(6, run_phases, labels),
                unsafe_allow_html=True
            )
            status_box.update(label="🎉 ¡Pipeline de Calidad completado con éxito!", state="complete")
            st.success("🎉 El proceso de calidad finalizó correctamente. Todo fue descargado, cargado y procesado en Teradata de forma transaccional.")
        except Exception as ex:
            progress_bar.empty()
            progress_text.empty()
            handle_execution_error(ex, status_box, "el pipeline de calidad")
        finally:
            st.session_state.quality_running = False
                
    st.markdown("</div>", unsafe_allow_html=True)
