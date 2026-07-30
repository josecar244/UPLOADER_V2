import os
import re
import time
import datetime
import json
import logging
import polars as pl
import teradatasql
from pathlib import Path

from core.Insight_downloader import download_insight_data
from core.verint_downloader import download_verint_data
from core.readers import read_excel_file
from core.cleaners import clean_dataframe, sanitize_identifier
from core.database import load_credentials, connect_teradata, load_to_teradata
from core.sql_executor import get_friendly_script_name
from ui.components import load_templates

logger = logging.getLogger(__name__)

# Suppress warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_quality_period_params(period_str):
    """
    Calculates parameters based on a period string in YYYYMM format (e.g. '202607').
    """
    if not re.match(r'^\d{6}$', period_str):
        raise ValueError(f"Formato de periodo inválido '{period_str}'. Debe ser YYYYMM.")
        
    year = int(period_str[:4])
    month = int(period_str[4:])
    
    # Previous month
    if month == 1:
        prev_year = year - 1
        prev_month = 12
    else:
        prev_year = year
        prev_month = month - 1
    period_prev = f"{prev_year}{prev_month:02d}"
    
    return {
        "PERIODO": period_str,
        "PERIODO_ANTERIOR": period_prev,
    }

def inject_variables(sql_text, context):
    """
    Reemplaza variables de tipo {VARIABLE} con sus valores del diccionario de contexto.
    """
    for key, val in context.items():
        pattern = r'\{' + re.escape(str(key)) + r'\}'
        sql_text = re.compile(pattern, re.IGNORECASE).sub(str(val), sql_text)
    return sql_text

def parse_statements(sql_text):
    """
    Limpia comentarios (tanto de bloque /* */ como de línea simple --) y separa sentencias por punto y coma,
    respetando las cadenas de texto literales.
    """
    # Eliminar comentarios de bloque
    sql_cleaned = re.sub(r'/\*.*?\*/', '', sql_text, flags=re.DOTALL)
    
    # Eliminar comentarios de línea simple línea por línea
    lines = []
    for line in sql_cleaned.split('\n'):
        in_quote = False
        quote_char = None
        comment_idx = -1
        i = 0
        while i < len(line):
            c = line[i]
            if c in ("'", '"') and (i == 0 or line[i-1] != '\\'):
                if not in_quote:
                    in_quote = True
                    quote_char = c
                elif c == quote_char:
                    in_quote = False
                    quote_char = None
            elif c == '-' and i + 1 < len(line) and line[i+1] == '-' and not in_quote:
                comment_idx = i
                break
            i += 1
        if comment_idx != -1:
            line = line[:comment_idx]
        lines.append(line)
        
    cleaned_text = '\n'.join(lines)
    
    # Dividir por punto y coma respetando bloques entre comillas
    statements = []
    current = []
    in_quote = False
    quote_char = None
    i = 0
    while i < len(cleaned_text):
        c = cleaned_text[i]
        if c in ("'", '"') and (i == 0 or cleaned_text[i-1] != '\\'):
            if not in_quote:
                in_quote = True
                quote_char = c
            elif c == quote_char:
                in_quote = False
                quote_char = None
            current.append(c)
        elif c == ';' and not in_quote:
            stmt = ''.join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(c)
        i += 1
        
    stmt = ''.join(current).strip()
    if stmt:
        statements.append(stmt)
        
    return statements

def get_selections_from_template(df, template_config):
    """Maps DataFrame columns using the template configuration."""
    selections = []
    for col in df.columns:
        if col in template_config:
            selections.append({
                "name": col,
                "selected": template_config[col].get('Añadir', True),
                "convert_nulls": template_config[col].get('Null:0/No Null:1', False),
                "datatype": template_config[col].get('Tipo de dato', 'VARCHAR(255)'),
                "new_name": sanitize_identifier(template_config[col].get('Nuevo nombre', col))
            })
        else:
            selections.append({
                "name": col,
                "selected": False,
                "convert_nulls": False,
                "datatype": 'VARCHAR(255)',
                "new_name": sanitize_identifier(col)
            })
    return selections

def validate_source_tables(cursor, config, context, progress_callback=None):
    """
    Verifica que ninguna tabla de origen configurada esté vacía para el período y fechas de corte actuales.
    Lanza ValueError si alguna tabla tiene 0 filas.
    """
    periodo = context.get("PERIODO")
    corte_inicio = context.get("corte_dia_inicio_1")
    corte_fin = context.get("corte_dia_fin_2")
    
    if progress_callback:
        progress_callback(f"🔍 Iniciando verificación de tablas de origen para el período: {periodo} (Corte: Días {corte_inicio} al {corte_fin})", "info")
        
    validation_settings = config.get("quality_validation_settings", {})
    tables_to_check = validation_settings.get("source_tables_to_check", [])
    
    if not tables_to_check:
        if progress_callback:
            progress_callback("⚠️ No se configuraron tablas de origen para validación en config.json.", "warning")
        return
        
    empty_tables = []
    
    for item in tables_to_check:
        table_name = item.get("table_name")
        raw_query = item.get("query")
        
        # Inyectar variables dinámicas del contexto
        prepared_query = inject_variables(raw_query, context)
        
        try:
            cursor.execute(prepared_query)
            row = cursor.fetchone()
            count = row[0] if row else 0
            if count == 0:
                empty_tables.append(table_name)
                if progress_callback:
                    progress_callback(f"❌ Tabla vacía o sin registros: {table_name}", "error")
            else:
                if progress_callback:
                    progress_callback(f"✅ Tabla {table_name}: {count:,} registros", "info")
        except Exception as err:
            empty_tables.append(table_name)
            if progress_callback:
                progress_callback(f"❌ Error al consultar la tabla {table_name}: {err}", "error")
            
    if empty_tables:
        msg = f"⚠️ ADVERTENCIA: Las siguientes tablas de origen están vacías o fallaron al consultarse: {', '.join(empty_tables)}. Continuando con el procesamiento SQL..."
        if progress_callback:
            progress_callback(msg, "warning")
    else:
        if progress_callback:
            progress_callback("✅ Verificación completada con éxito. Todas las tablas origen contienen registros.", "success")

def check_unmapped_questions(cursor, progress_callback=None):
    """
    Verifica si existen preguntas crudas en Pure Cloud que no estén mapeadas en la tabla maestra de calidad.
    Registra advertencias detalladas si se encuentran discrepancias.
    """
    if progress_callback:
        progress_callback("🔍 Verificando preguntas de Pure Cloud sin mapear en la maestra...", "info")
        
    query = """
        SELECT DISTINCT RAW_PREGUNTA_CLEAN, MAP_PREGUNTA, PLANTILLA, RAW_GRUPO
        FROM (
            SELECT
                UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(questionText, CHR(13), ''), CHR(10), ''))) AS RAW_PREGUNTA_CLEAN,
                OREPLACE(OREPLACE(questionGroupName, CHR(13), ''), CHR(10), '') AS RAW_GRUPO,
                OREPLACE(OREPLACE(evaluationFormName, CHR(13), ''), CHR(10), '') AS PLANTILLA,
                COALESCE(p.TARGET, UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(r.questionText, CHR(13), ''), CHR(10), '')))) AS MAP_PREGUNTA
            FROM DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE r
            LEFT JOIN DLAB_GEC.M_EXP_CALIDAD_HOMOLOGA_PREGUNTA p ON UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(r.questionText, CHR(13), ''), CHR(10), ''))) = p.ORIGINAL
        ) r
        WHERE NOT EXISTS (
            SELECT 1 FROM (
                SELECT 
                    b.PLANTILLA,
                    COALESCE(g.TARGET, b.GRUPO_PREGUNTAS) AS MAP_GRUPO,
                    COALESCE(p.TARGET, b.PREGUNTA) AS MAP_PREGUNTA
                FROM DLAB_GEC.M_EXP_CALIDAD_MAESTRA_GRUPO_PREGUNTAS_PCLOUD b
                LEFT JOIN DLAB_GEC.M_EXP_CALIDAD_HOMOLOGA_GRUPO g 
                    ON UPPER(TRIM(BOTH ' ' FROM b.GRUPO_PREGUNTAS)) = g.ORIGINAL
                LEFT JOIN DLAB_GEC.M_EXP_CALIDAD_HOMOLOGA_PREGUNTA p 
                    ON UPPER(TRIM(BOTH ' ' FROM b.PREGUNTA)) = p.ORIGINAL
            ) b
            WHERE UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(r.PLANTILLA, CHR(13), ''), CHR(10), ''))) = UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(b.PLANTILLA, CHR(13), ''), CHR(10), '')))
              AND UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(r.RAW_GRUPO, CHR(13), ''), CHR(10), ''))) = UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(b.MAP_GRUPO, CHR(13), ''), CHR(10), '')))
              AND UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(r.MAP_PREGUNTA, CHR(13), ''), CHR(10), ''))) = UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(b.MAP_PREGUNTA, CHR(13), ''), CHR(10), '')))
        )
    """
    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        if rows:
            if progress_callback:
                progress_callback(f"⚠️ Se encontraron {len(rows)} preguntas sin mapear en DLAB_GEC.M_EXP_CALIDAD_MAESTRA_GRUPO_PREGUNTAS_PCLOUD", "warning")
                for row in rows[:10]:
                    raw_q, map_q, template, group = row
                    progress_callback(f"   - Pregunta: '{raw_q}' (Mapeada: '{map_q}') | Plantilla: {template} | Grupo: {group}", "warning")
                if len(rows) > 10:
                    progress_callback("   ... (más preguntas omitidas en el log rápido)", "warning")
        else:
            if progress_callback:
                progress_callback("✅ Todas las preguntas crudas de Pure Cloud están homologadas correctamente.", "success")
    except Exception as err:
        if progress_callback:
            progress_callback(f"⚠️ Error al verificar preguntas sin mapear: {err}", "warning")

def check_table_has_data(con, table_name) -> bool:
    """
    Checks if a Teradata table contains at least one row.
    """
    try:
        cur = con.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        row = cur.fetchone()
        count = row[0] if row else 0
        cur.close()
        return count > 0
    except Exception:
        return False

def _refresh_excel_com_process(file_path):
    """
    Called in an isolated subprocess to refresh Excel. Prevents hang.
    """
    import os
    import sys
    import time
    
    abs_path = os.path.abspath(file_path)
    
    try:
        import win32com.client
        import pythoncom
    except ImportError:
        sys.exit(2) # Code 2: pywin32 missing

    pythoncom.CoInitialize()
    excel = None
    wb = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        
        # 1. Open workbook, resolving Protected View if necessary
        try:
            wb = excel.Workbooks.Open(abs_path)
        except Exception:
            pv_win = None
            for i in range(1, excel.ProtectedViewWindows.Count + 1):
                win = excel.ProtectedViewWindows.Item(i)
                if win.SourceName.lower() == os.path.basename(abs_path).lower():
                    pv_win = win
                    break
            if pv_win:
                wb = pv_win.Edit()
            else:
                raise
        
        # 2. Disable background queries so RefreshAll runs synchronously
        for i in range(1, wb.Connections.Count + 1):
            conn = wb.Connections.Item(i)
            try:
                if conn.Type == 1:
                    conn.OLEDBConnection.BackgroundQuery = False
                elif conn.Type == 2:
                    conn.ODBCConnection.BackgroundQuery = False
            except Exception:
                pass
                
        # 3. Perform refresh, save, and quit
        wb.RefreshAll()
        time.sleep(2)
        wb.Save()
        wb.Close(SaveChanges=True)
        excel.Quit()
        sys.exit(0)
    except Exception as e:
        sys.stderr.write(str(e))
        sys.exit(1)
    finally:
        pythoncom.CoUninitialize()

def refresh_excel_sharepoint_data(file_path, progress_callback=None):
    """
    Refreshes Excel data connections in a timeout-protected subprocess.
    """
    import sys
    import subprocess
    
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"No se encontró el archivo Excel en {abs_path}")
        
    # 1. Unblock file in Windows
    try:
        subprocess.run(["powershell.exe", "-Command", f"Unblock-File -Path '{abs_path}'"], capture_output=True, check=False)
    except Exception:
        pass

    # 2. Check/Install pywin32 in parent
    try:
        import win32com.client
    except ImportError:
        if progress_callback:
            progress_callback("📦 Biblioteca 'pywin32' no detectada en Python. Instalándola automáticamente...", "info")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pywin32"])
        except Exception as inst_err:
            raise RuntimeError(f"No se pudo instalar pywin32: {inst_err}")

    # 3. Launch subprocess to refresh Excel with a 25-second hard timeout
    if progress_callback:
        progress_callback("🔄 Conectando con Excel para actualizar desde SharePoint...", "info")
        
    # Build command pointing to this module's _refresh_excel_com_process
    # We append the parent path so Python can find core.quality_process_orchestrator
    parent_dir = os.path.dirname(os.path.dirname(abs_path))
    cmd = [
        sys.executable,
        "-c",
        f"import sys; sys.path.append(r'{parent_dir}'); "
        f"from core.quality_process_orchestrator import _refresh_excel_com_process; "
        f"_refresh_excel_com_process(r'{abs_path}')"
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            if progress_callback:
                progress_callback("✅ Excel actualizado correctamente desde SharePoint.", "success")
        elif result.returncode == 2:
            raise RuntimeError("pywin32 no cargó correctamente en el subproceso.")
        else:
            error_details = result.stderr.strip()
            raise RuntimeError(f"Error en actualización de Excel: {error_details}")
    except subprocess.TimeoutExpired:
        # Forcefully terminate any orphaned Excel processes
        try:
            subprocess.run(["taskkill", "/f", "/im", "excel.exe"], capture_output=True, check=False)
        except Exception:
            pass
        raise TimeoutError("Se superó el límite de tiempo de 60s al actualizar Excel desde SharePoint. (Se omitió actualización automática)")

def deduplicate_observations_by_severity(df: pl.DataFrame) -> pl.DataFrame:
    """
    Deduplica un DataFrame de Polars por la columna 'CODIGO_NTD',
    conservando la fila con la acción tomada de mayor severidad.
    """
    severity_order = {
        "DESVINCULACION": 1,
        "DESVINCULACIÓN": 1,
        "Desvinculación": 1,
        "EV YA NO LABORA EN IBK": 2,
        "SUSPENSION": 3,
        "SUSPENSIÓN": 3,
        "Suspensión": 3,
        "ENVIADO A GDH": 4,
        "CARTA DE LLAMADA DE ATENCION SEVERA": 5,
        "CARTA DE LLAMADA DE ATENCIÓN SEVERA": 5,
        "Ll. atencion severa": 6,
        "Ll. atención severa": 6,
        "CARTA DE LLAMADA DE ATENCION SIMPLE": 7,
        "CARTA DE LLAMADA DE ATENCIÓN SIMPLE": 7,
        "ACTA DE LLAMADA DE ATENCION": 8,
        "ACTA DE LLAMADA DE ATENCIÓN": 8,
        "Ll. atencion simple": 9,
        "Ll. atención simple": 9,
        "Ll. atencion verbal": 10,
        "Ll. atención verbal": 10,
        "FEEDBACK": 11,
        "Feedback": 11,
        "-": 12,
        "Accion No Definida": 12,
        "Acción No Definida": 12
    }
    
    # Map each value to rank
    df_with_rank = df.with_columns(
        pl.col("ACCION_TOMADA")
        .map_elements(lambda x: severity_order.get(str(x).strip() if x is not None else "-", 99), return_dtype=pl.Int32)
        .alias("SEVERITY_RANK")
    )
    
    # Sort and deduplicate keeping the first occurrence (lowest rank = highest severity)
    df_sorted = df_with_rank.sort(["CODIGO_NTD", "SEVERITY_RANK"])
    df_deduplicated = df_sorted.unique(subset=["CODIGO_NTD"], keep="first")
    
    return df_deduplicated.drop("SEVERITY_RANK")

def run_quality_process_flow(
    insight_user, 
    insight_password, 
    verint_user, 
    verint_password, 
    td_user, 
    td_password, 
    period_str, 
    progress_callback=None,
    run_phase1=True,
    run_phase2=True,
    run_phase3=True,
    run_phase4=True,
    run_phase5=True,
    start_from_script=None
):
    """
    Executes the entire Quality Process Flow:
    1. Downloads & loads Insight evaluations.
    2. Downloads & loads Verint speech analytics.
    3. Loads local ACCION_TOMADA.xlsx observations excel.
    4. Executes quality SQL scripts in transaction mode.
    """
    # ----------------------------------------------------
    # PHASE 0: SETUP AND VARIABLES
    # ----------------------------------------------------
    if progress_callback:
        progress_callback("🚀 Iniciando Pipeline Unificado de Proceso Calidad...", "info")
        
    templates = load_templates()
    credenciales = load_credentials()
    
    # Read config.json
    config_path = os.path.join(os.getcwd(), "config", "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
        
    business_vars = config.get("business_vars", {})
    sequence = config.get("quality_execution_sequence", [])
    
    # Resolve parameters
    params = get_quality_period_params(period_str)
    context = {
        **params,
        **business_vars
    }
    
    host = credenciales.get('teradata_host', 'IBKTD')
    logmech = credenciales.get('teradata_logmech', 'TD2')
    
    INPUT_PROCESO_CALIDAD_DIR = os.path.join(os.getcwd(), "INPUT_PROCESO_CALIDAD")
    os.makedirs(INPUT_PROCESO_CALIDAD_DIR, exist_ok=True)
    
    # ----------------------------------------------------
    # PHASE 1: DOWNLOAD & INGEST INSIGHT (EVALUATIONS)
    # ----------------------------------------------------
    if run_phase1:
        if progress_callback:
            progress_callback("📥 Fase 1: Descargando Evaluaciones de Insight...", "info")
            
        today_str = datetime.datetime.now().strftime("%Y%m%d")
        expected_insight_file = os.path.join(INPUT_PROCESO_CALIDAD_DIR, f"Reporte_Insight_EVALUATIONS_{today_str}.txt")
        
        if os.path.exists(expected_insight_file) and os.path.getsize(expected_insight_file) > 0:
            local_insight_path = expected_insight_file
            if progress_callback:
                progress_callback(f"ℹ️ Archivo de Insight para hoy ya existe localmente ({os.path.basename(local_insight_path)}). Omitiendo descarga.", "info")
        else:
            try:
                local_insight_path = download_insight_data(
                    query_name="EVALUATIONS",
                    username=insight_user,
                    password=insight_password,
                    progress_callback=progress_callback,
                    output_dir=INPUT_PROCESO_CALIDAD_DIR
                )
            except Exception as err:
                raise RuntimeError(f"Fallo crítico al descargar evaluaciones de Insight: {err}")
            
        if True:
            if progress_callback:
                progress_callback("🧹 Cargando Evaluaciones de Insight a Teradata...", "info")
                
            df_insight = pl.read_csv(local_insight_path, separator='\t', infer_schema_length=0, truncate_ragged_lines=True)
            template_insight = templates.get("P008-INSIGHT_07_EVALUATIONS", {})
            if not template_insight:
                raise ValueError("No se encontró la plantilla P008-INSIGHT_07_EVALUATIONS en plantillas.json.")
                
            selections_insight = get_selections_from_template(df_insight, template_insight)
            df_insight_clean = clean_dataframe(
                df_insight,
                selections_insight,
                convertir_sin_acentos=True,
                transformar_varchar_latin=False,
                max_len_varchar=3000
            )
            
            try:
                con = connect_teradata(td_user, td_password, host=host, logmech=logmech)
            except Exception as err:
                raise RuntimeError(f"Error de conexión con Teradata: {err}")
                
            try:
                # Load into DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE
                if progress_callback:
                    progress_callback("🚀 Cargando en la tabla DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE...", "info")
                load_to_teradata(
                    con=con,
                    table_name="DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE",
                    df=df_insight_clean,
                    selected_columns_config=selections_insight,
                    clear_table=True,
                    progress_callback=progress_callback
                )
            finally:
                try:
                    con.close()
                except Exception:
                    pass
                
    # ----------------------------------------------------
    # PHASE 2: DOWNLOAD & INGEST VERINT (SPEECH ANALYTICS)
    # ----------------------------------------------------
    if run_phase2:
        if progress_callback:
            progress_callback(f"📥 Fase 2: Descargando Speech Analytics de Verint para el periodo {period_str}...", "info")
            
        import glob
        all_files = sorted(glob.glob(os.path.join(INPUT_PROCESO_CALIDAD_DIR, "Export_Calidad_*.xlsx")))
        if not all_files:
            all_files = sorted(glob.glob(os.path.join(INPUT_PROCESO_CALIDAD_DIR, "Export_Calidad_*.xls")))
            
        # Only keep files that were downloaded/modified today
        today_date = datetime.date.today()
        existing_verint_files = [
            f for f in all_files
            if datetime.date.fromtimestamp(os.path.getmtime(f)) == today_date and os.path.getsize(f) > 0
        ]
            
        if existing_verint_files:
            downloaded_verint_files = existing_verint_files
            if progress_callback:
                progress_callback(f"ℹ️ Archivos de Verint descargados hoy detectados ({len(downloaded_verint_files)} archivo(s)). Omitiendo descarga.", "info")
        else:
            # Set credentials for Verint downloader environment variables temporarily
            os.environ["VERINT_USER"] = verint_user
            os.environ["VERINT_PASS"] = verint_password
            os.environ["TERADATA_USER"] = td_user
            os.environ["TERADATA_PASSWORD"] = td_password
            os.environ["TERADATA_HOST"] = host
            os.environ["TERADATA_LOGMECH"] = logmech
            
            try:
                downloaded_verint_files = download_verint_data(
                    period=period_str,
                    headless=True,
                    progress_callback=progress_callback,
                    output_dir="INPUT_PROCESO_CALIDAD"
                )
            except Exception as err:
                raise RuntimeError(f"Fallo crítico al descargar datos de Verint: {err}")
                
        if not downloaded_verint_files:
            raise ValueError("No se obtuvieron archivos desde la descarga de Verint.")
            
        if True:
            if progress_callback:
                progress_callback(f"🧹 Cargando {len(downloaded_verint_files)} archivo(s) de Verint a Teradata...", "info")
                
            template_verint = templates.get("P001-CALIDAD_SA", {})
            if not template_verint:
                raise ValueError("No se encontró la plantilla P001-CALIDAD_SA en plantillas.json.")
                
            try:
                con = connect_teradata(td_user, td_password, host=host, logmech=logmech)
            except Exception as err:
                raise RuntimeError(f"Error de conexión con Teradata: {err}")
                
            try:
                clear_table = True
                for file_path in downloaded_verint_files:
                    filename = os.path.basename(file_path)
                    if progress_callback:
                        progress_callback(f"📂 Procesando archivo Verint: {filename}...", "info")
                        
                    df_verint = read_excel_file(file_path, selected_template="P001-CALIDAD_SA", templates=templates)
                    if df_verint.is_empty():
                        if progress_callback:
                            progress_callback(f"⚠️ El archivo '{filename}' no contiene registros. Se omitirá.", "warning")
                        continue
                        
                    selections_verint = get_selections_from_template(df_verint, template_verint)
                    df_verint_clean = clean_dataframe(
                        df_verint,
                        selections_verint,
                        convertir_sin_acentos=True,
                        transformar_varchar_latin=False,
                        max_len_varchar=3000
                    )
                    
                    if progress_callback:
                        progress_callback(f"🚀 Subiendo a la tabla DLAB_GEC.M_EXP_CALIDAD_DATA_SPEECH_ANALYTICS (Vaciar={clear_table})...", "info")
                    load_to_teradata(
                        con=con,
                        table_name="DLAB_GEC.M_EXP_CALIDAD_DATA_SPEECH_ANALYTICS",
                        df=df_verint_clean,
                        selected_columns_config=selections_verint,
                        clear_table=clear_table,
                        progress_callback=progress_callback
                    )
                    # Only clear table on the first partition, append subsequent partitions
                    clear_table = False
            finally:
                try:
                    con.close()
                except Exception:
                    pass

    # ----------------------------------------------------
    # PHASE 3: INGEST LOCAL ACCION_TOMADA EXCEL
    # ----------------------------------------------------
    if run_phase3:
        if progress_callback:
            progress_callback("📂 Fase 3: Procesando Excel de Acciones Tomadas local...", "info")
            
        excel_path = os.path.join(INPUT_PROCESO_CALIDAD_DIR, "ACCION_TOMADA.xlsx")
        if not os.path.exists(excel_path):
            raise FileNotFoundError(f"No se encontró el archivo Excel requerido en: {excel_path}. Por favor, colócalo en la carpeta 'INPUT_PROCESO_CALIDAD'.")
            
        # Auto-refresh Excel via COM (Option B) before loading
        try:
            refresh_excel_sharepoint_data(excel_path, progress_callback)
        except Exception as refresh_err:
            if progress_callback:
                progress_callback(f"⚠️ Advertencia al actualizar Excel desde SharePoint: {refresh_err}. Se continuará leyendo el archivo en su estado actual.", "warning")
                
        df_observaciones = read_excel_file(excel_path)
        template_obs = templates.get("P004-ACC_TOMADA", {})
        if not template_obs:
            raise ValueError("No se encontró la plantilla P004-ACC_TOMADA en plantillas.json.")
            
        selections_obs = get_selections_from_template(df_observaciones, template_obs)
        df_obs_clean = clean_dataframe(
            df_observaciones,
            selections_obs,
            convertir_sin_acentos=True,
            transformar_varchar_latin=False,
            max_len_varchar=3000
        )
        
        # Deduplicar por severidad de acción tomada antes de cargar a Teradata
        if progress_callback:
            progress_callback("🧹 Deduplicando registros de Acciones Tomadas por orden de severidad...", "info")
        df_obs_clean = deduplicate_observations_by_severity(df_obs_clean)
        
        try:
            con = connect_teradata(td_user, td_password, host=host, logmech=logmech)
        except Exception as err:
            raise RuntimeError(f"Error de conexión con Teradata: {err}")
            
        try:
            if progress_callback:
                progress_callback("🚀 Cargando en la tabla DLAB_GEC.M_EXP_NTD_OBSERVACIONES_PRE...", "info")
            load_to_teradata(
                con=con,
                table_name="DLAB_GEC.M_EXP_NTD_OBSERVACIONES_PRE",
                df=df_obs_clean,
                selected_columns_config=selections_obs,
                clear_table=True,
                progress_callback=progress_callback
            )
        finally:
            try:
                con.close()
            except Exception:
                pass

    # ----------------------------------------------------
    # PHASE 4: EXECUTE SQL TRANSFORMATION SCRIPTS
    # ----------------------------------------------------
    if run_phase4:
        if progress_callback:
            progress_callback("⚡ Fase 4: Ejecutando Scripts SQL del Pipeline de Calidad...", "info")
            
        try:
            con = connect_teradata(td_user, td_password, host=host, logmech=logmech)
            con.autocommit = True # Evitar errores de transaccion explicita con DDL/DML mixtos
            cursor = con.cursor()
            
            # 4.1 Check source tables and questions mapping
            validate_source_tables(cursor, config, context, progress_callback)
            check_unmapped_questions(cursor, progress_callback)
            
            # 4.2 Run execution sequence
            scripts_to_run = sequence
            if start_from_script:
                clean_start = os.path.basename(start_from_script).lower()
                matched_idx = -1
                for idx, s in enumerate(sequence):
                    if os.path.basename(s).lower() == clean_start or s.lower() == clean_start:
                        matched_idx = idx
                        break
                if matched_idx != -1:
                    scripts_to_run = sequence[matched_idx:]
                else:
                    if progress_callback:
                        progress_callback(f"⚠️ Advertencia: No se encontró el script '{start_from_script}' en la secuencia. Se ejecutarán todos.", "warning")

            for script_rel_path in scripts_to_run:
                script_path = os.path.join(os.getcwd(), script_rel_path)
                if not os.path.exists(script_path):
                    raise FileNotFoundError(f"Archivo de script SQL no encontrado: {script_rel_path}")
                
                friendly_name = get_friendly_script_name(script_path)
                if progress_callback:
                    progress_callback(f"⚙️ Procesando: **{friendly_name}**...", "info")
                    
                with open(script_path, "r", encoding="utf-8") as f:
                    raw_sql = f.read()
                    
                # Inyectar variables dinámicas
                prepared_sql = inject_variables(raw_sql, context)
                statements = parse_statements(prepared_sql)
                
                logger.info(f"Se detectaron {len(statements)} sentencias en {os.path.basename(script_path)}")
                    
                for idx, stmt in enumerate(statements, 1):
                    stmt_str = stmt.strip()
                    if not stmt_str:
                        continue
                    preview = stmt_str.split("\n")[0][:100] + "..." if len(stmt_str.split("\n")[0]) > 100 else stmt_str.split("\n")[0]
                    logger.info(f"   [{idx}/{len(statements)}] Ejecutando: {preview}")
                    if progress_callback:
                        try:
                            progress_callback(
                                f"   🔹 [{idx}/{len(statements)}] Ejecutando sentencia...",
                                "info",
                                progress=float(idx) / len(statements)
                            )
                        except TypeError:
                            progress_callback(
                                f"   🔹 [{idx}/{len(statements)}] Ejecutando sentencia...",
                                "info"
                            )
                    try:
                        cursor.execute(stmt_str)
                    except Exception as stmt_err:
                        from core.sql_executor import SQLScriptExecutionError
                        raise SQLScriptExecutionError(os.path.basename(script_path), idx, stmt_str, stmt_err)
                
                if progress_callback:
                    progress_callback(f"✅ Completado: **{friendly_name}**", "success")
                        
            # If everything succeeded, commit the transaction
            if progress_callback:
                progress_callback("💾 Todo el procesamiento SQL se ejecutó exitosamente. Guardando transacciones (Commit)...", "info")
            con.commit()
            if progress_callback:
                progress_callback("🎉 ¡Pipeline de Calidad Completo ejecutado exitosamente!", "success")
                
        except Exception as e:
            if progress_callback:
                progress_callback(f"❌ Fallo crítico en el procesamiento SQL. Revirtiendo cambios (Rollback)... Error: {e}", "error")
            try:
                con.rollback()
            except Exception:
                pass
            raise e
        finally:
            try:
                con.close()
            except Exception:
                pass

    # ----------------------------------------------------
    # PHASE 5: NTD PROCESS
    # ----------------------------------------------------
    if run_phase5:
        if progress_callback:
            progress_callback("⚡ Fase 5: Ejecutando Proceso NTD (Not To Do)...", "info")
            
        try:
            con = connect_teradata(td_user, td_password, host=host, logmech=logmech)
            con.autocommit = True
            cursor = con.cursor()
            
            # 5.1 Validation: check DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE
            if progress_callback:
                progress_callback("🔍 Validando tabla de entrada de Evaluaciones (M_EXP_CALIDAD_PURECLOUD_PRE)...", "info")
            
            cursor.execute("SELECT COUNT(*) FROM DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE")
            row_count = cursor.fetchone()
            count_pre = row_count[0] if row_count else 0
            if count_pre == 0:
                raise ValueError("La tabla DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE está vacía. Proceso abortado.")
                
            cursor.execute("SELECT MAX(conversationStartTime) FROM DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE")
            row_max = cursor.fetchone()
            max_date = row_max[0] if row_max else None
            
            if not max_date:
                raise ValueError("No se pudo obtener la fecha máxima de M_EXP_CALIDAD_PURECLOUD_PRE. Proceso abortado.")
                
            # Parse period
            if hasattr(max_date, 'strftime'):
                max_period = max_date.strftime("%Y%m")
            else:
                match = re.search(r'(\d{4})-(\d{2})', str(max_date))
                if match:
                    max_period = match.group(1) + match.group(2)
                else:
                    max_period = None
                    
            if max_period and max_period != period_str:
                raise ValueError(f"La fecha máxima de M_EXP_CALIDAD_PURECLOUD_PRE ({max_period}) no corresponde al mes parametrizado ({period_str}). Proceso abortado.")
                
            # 5.2 Validation: check DLAB_GEC.M_EXP_NTD_OBSERVACIONES_PRE
            if progress_callback:
                progress_callback("🔍 Validando tabla de entrada de Observaciones (M_EXP_NTD_OBSERVACIONES_PRE)...", "info")
                
            cursor.execute("SELECT COUNT(*) FROM DLAB_GEC.M_EXP_NTD_OBSERVACIONES_PRE")
            row_obs = cursor.fetchone()
            count_obs = row_obs[0] if row_obs else 0
            if count_obs == 0:
                raise ValueError("La tabla DLAB_GEC.M_EXP_NTD_OBSERVACIONES_PRE está vacía. Proceso abortado.")
                
            # 5.3 Run 06_carga_ntd.sql script
            script_rel_path = "sql_calidad/06_carga_ntd.sql"
            script_path = os.path.join(os.getcwd(), script_rel_path)
            if not os.path.exists(script_path):
                raise FileNotFoundError(f"Archivo de script SQL no encontrado: {script_rel_path}")
                
            friendly_name = get_friendly_script_name(script_path)
            if progress_callback:
                progress_callback(f"⚙️ Procesando: **{friendly_name}**...", "info")
                
            with open(script_path, "r", encoding="utf-8") as f:
                raw_sql = f.read()
                
            prepared_sql = inject_variables(raw_sql, context)
            statements = parse_statements(prepared_sql)
            
            logger.info(f"Se detectaron {len(statements)} sentencias en {os.path.basename(script_path)}")
            
            for idx, stmt in enumerate(statements, 1):
                stmt_str = stmt.strip()
                if not stmt_str:
                    continue
                preview = stmt_str.split("\n")[0][:100] + "..." if len(stmt_str.split("\n")[0]) > 100 else stmt_str.split("\n")[0]
                logger.info(f"   [{idx}/{len(statements)}] Ejecutando: {preview}")
                try:
                    cursor.execute(stmt_str)
                except Exception as stmt_err:
                    from core.sql_executor import SQLScriptExecutionError
                    raise SQLScriptExecutionError(os.path.basename(script_path), idx, stmt_str, stmt_err)
                    
            if progress_callback:
                progress_callback(f"✅ Completado: **{friendly_name}**", "success")
                
            # Commit the transaction
            if progress_callback:
                progress_callback("💾 Procesamiento SQL de NTD ejecutado exitosamente. Guardando transacciones (Commit)...", "info")
            con.commit()
            if progress_callback:
                progress_callback("🎉 ¡Proceso NTD (Fase 5) completado exitosamente!", "success")
                
        except Exception as e:
            if progress_callback:
                progress_callback(f"❌ Fallo crítico en el procesamiento SQL de NTD. Revirtiendo cambios (Rollback)... Error: {e}", "error")
            try:
                con.rollback()
            except Exception:
                pass
            raise e
        finally:
            try:
                con.close()
            except Exception:
                pass
