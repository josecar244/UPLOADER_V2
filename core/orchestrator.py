import os
import re
import shutil
import datetime
import subprocess
import polars as pl
from pathlib import Path
from core.Insight_downloader import download_insight_data
from core.cleaners import clean_dataframe, sanitize_identifier
from core.database import load_credentials, connect_teradata, load_to_teradata, check_table_exists
from ui.components import load_templates

# Insumos configuration mapping
INSUMOS_CONFIG = {
    "TRAFICO_GENESYS": {
        "query_name": "TRAFICO_GENESYS",
        "template_key": "P009-INSIGHT_01_TRAFICO_GENESYS",
        "tables": ["DLAB_GEC.M_EXP_TRAFICO_GENESIS"]
    },
    "CONV_ATTRIBUTES": {
        "query_name": "CONV_ATTRIBUTES",
        "template_key": "P010-INSIGHT_02_CONV_ATTRIBUTES",
        "tables": ["DLAB_GEC.M_EXP_BT_CONVERSATIONS_ATTRIBUTES"]
    },
    "DERIVA_BT": {
        "query_name": "DERIVA_BT",
        "template_key": "P011-INSIGHT_03_DERIVA_BT",
        "tables": ["DLAB_GEC.M_EXP_DERIVA_BT_TIEMPOS"]
    },
    "CLOUD_MARCA_TRANSF": {
        "query_name": "CLOUD_MARCA_TRANSF",
        "template_key": "P012-INSIGHT_04_CLOUD_MARCA_TRANSF",
        "tables": ["DLAB_GEC.M_EXP_CO_CLOUD_MARCA_TRASNFERENCIA_PRE"]
    },
    "BT_TRANSFERENCIA": {
        "query_name": "BT_TRANSFERENCIA",
        "template_key": "P013-INSIGHT_05_BT_TRANSFERENCIA",
        "tables": ["DLAB_GEC.M_DERIVA_BT_EV_TRANSFERENCIA"]
    },
    "IVR_VENTAS": {
        "query_name": "IVR_VENTAS",
        "template_key": "P014-INSIGHT_06_IVR_VENTAS",
        "tables": ["DLAB_GEC.M_EXP_IVR_VENTAS_2022"]
    },
    "EVALUATIONS": {
        "query_name": "EVALUATIONS",
        "template_key": "P008-INSIGHT_07_EVALUATIONS",
        "tables": ["DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE"]
    }
}

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

def run_orchestration_flow(
    insight_user, insight_password, td_user, td_password, period_str, 
    clear_consent=False, progress_callback=None,
    run_phase1=True, run_phase2=True, run_phase3=True, run_phase4=True, run_phase5=True,
    start_from_script=None
):
    """
    Runs the orchestration process in phases:
    Fase 1: Insight (7 Insumos)
    Fase 2: Ingesta CD40K Manual
    Fase 3: Ingesta Desembolsos (SQL Server)
    Fase 4: Pipeline SQL Consumo
    Fase 5: Ingesta Selección (Secundario)
    """
    templates = load_templates()
    credenciales = load_credentials()
    host = credenciales.get('teradata_host', 'IBKTD')
    logmech = credenciales.get('teradata_logmech', 'TD2')
    
    INPUT_BASE_CONSUMO_DIR = os.path.join(os.getcwd(), "INPUT_BASE_CONSUMO")
    os.makedirs(INPUT_BASE_CONSUMO_DIR, exist_ok=True)
 
    downloaded_files = {}
    
    if run_phase1:
        if progress_callback:
            progress_callback("⚡ [Fase 1] Iniciando descarga de insumos de Insight...", "info")
     
        # Sequence of Insight downloads
        for insumo_key, conf in INSUMOS_CONFIG.items():
            q_name = conf["query_name"]
            
            # Verificar si el archivo ya fue descargado el día de hoy
            today_str = datetime.datetime.now().strftime("%Y%m%d")
            expected_filename = f"Reporte_Insight_{q_name}_{today_str}.txt"
            expected_path = os.path.join(INPUT_BASE_CONSUMO_DIR, expected_filename)
            
            if os.path.exists(expected_path) and os.path.getsize(expected_path) > 0:
                if progress_callback:
                    progress_callback(f"ℹ️ El archivo de hoy '{expected_filename}' ya existe localmente. Se omitirá la descarga de Insight.", "info")
                downloaded_files[insumo_key] = expected_path
                continue
                
            if progress_callback:
                progress_callback(f"📡 Descargando insumo '{q_name}' desde Insight...", "info")
                
            try:
                local_path = download_insight_data(
                    query_name=q_name,
                    username=insight_user,
                    password=insight_password,
                    progress_callback=progress_callback,
                    output_dir=INPUT_BASE_CONSUMO_DIR
                )
                downloaded_files[insumo_key] = local_path
            except Exception as err:
                msg = f"⚠️ ALERTA: Falló al descargar insumo '{q_name}' desde Insight. Error: {err}. Se continuará con el flujo."
                if progress_callback:
                    progress_callback(msg, "warning")

    # Connect to Teradata if any phase requiring the main connection is True
    con = None
    if run_phase1 or run_phase2 or run_phase3 or run_phase4:
        if progress_callback:
            progress_callback("📡 Conectando a Teradata para iniciar las fases correspondientes...", "info")
            
        try:
            con = connect_teradata(td_user, td_password, host=host, logmech=logmech)
            con.autocommit = True
        except Exception as err:
            raise RuntimeError(f"Error de conexión con Teradata: {err}")
            
    try:
        # ----------------------------------------------------
        # FASE 1: UPLOAD INSIGHT INSUMOS TO TERADATA
        # ----------------------------------------------------
        if run_phase1 and con:
            for insumo_key, conf in INSUMOS_CONFIG.items():
                q_name = conf["query_name"]
                t_key = conf["template_key"]
                tables = conf["tables"]
                
                # Check if file path is registered in downloads, or look for files in INPUT_BASE_CONSUMO
                local_path = downloaded_files.get(insumo_key)
                if not local_path or not os.path.exists(local_path):
                    import glob
                    matching_files = glob.glob(os.path.join(INPUT_BASE_CONSUMO_DIR, f"Reporte_Insight_{q_name}_*.txt"))
                    if matching_files:
                        local_path = sorted(matching_files)[-1]
                    
                if not local_path or not os.path.exists(local_path):
                    msg = f"⚠️ ALERTA: El archivo para el insumo '{q_name}' no existe en la carpeta INPUT_BASE_CONSUMO. Se omitirá la carga de esta tabla."
                    if progress_callback:
                        progress_callback(msg, "warning")
                    continue
                    
                # Check if file is empty
                if os.path.getsize(local_path) == 0:
                    msg = f"⚠️ ALERTA: El archivo '{os.path.basename(local_path)}' está vacío (0 bytes). Se omitirá la carga de esta tabla."
                    if progress_callback:
                        progress_callback(msg, "warning")
                    continue
                    
                if progress_callback:
                    progress_callback(f"🧹 Leyendo y limpiando archivo '{os.path.basename(local_path)}'...", "info")
                    
                # Read tab-separated txt file, forcing all columns as string to avoid parsing errors.
                # Types are cleaned and cast later in clean_dataframe based on templates.
                df = pl.read_csv(local_path, separator='\t', infer_schema_length=0, truncate_ragged_lines=True)
                
                if df.is_empty():
                    msg = f"⚠️ ALERTA: El archivo '{os.path.basename(local_path)}' no contiene registros. Se omitirá la carga de esta tabla."
                    if progress_callback:
                        progress_callback(msg, "warning")
                    continue
                
                # Map selections using template
                template_config = templates.get(t_key, {})
                if not template_config:
                    if progress_callback:
                        progress_callback(f"⚠️ Advertencia: No se encontró la plantilla '{t_key}' en plantillas.json. Se cargará con mapeo automático.", "warning")
                
                selections = get_selections_from_template(df, template_config)
                
                # Clean dataframe
                df_clean = clean_dataframe(
                    df,
                    selections,
                    convertir_sin_acentos=True,
                    transformar_varchar_latin=False,
                    max_len_varchar=3000
                )
                
                # Load to Teradata table(s)
                for table_name in tables:
                    if progress_callback:
                        progress_callback(f"🚀 Subiendo datos a la tabla '{table_name}'...", "info")
                    
                    load_to_teradata(
                        con=con,
                        table_name=table_name,
                        df=df_clean,
                        selected_columns_config=selections,
                        clear_table=True, # Always clear and load
                        progress_callback=progress_callback
                    )
                    
        # ----------------------------------------------------
        # FASE 2: AUTOMATIC UPLOAD OF CD40K MANUAL EXCEL
        # ----------------------------------------------------
        if run_phase2 and con:
            cd40k_path = os.path.join(INPUT_BASE_CONSUMO_DIR, "CD40K_NEW.xlsx")
            
            if os.path.exists(cd40k_path):
                if progress_callback:
                    progress_callback(f"📂 [Fase 2] Archivo manual de CD40K detectado en: {os.path.basename(cd40k_path)}. Iniciando carga automática...", "info")
                try:
                    # Actualizar el Excel desde SharePoint vía COM antes de leerlo
                    from core.quality_process_orchestrator import refresh_excel_sharepoint_data
                    try:
                        refresh_excel_sharepoint_data(cd40k_path, progress_callback)
                    except Exception as refresh_err:
                        if progress_callback:
                            progress_callback(f"⚠️ Advertencia al actualizar CD40K desde SharePoint: {refresh_err}. Se continuará leyendo el archivo en su estado actual.", "warning")
    
                    # Read excel
                    df_cd40k = pl.read_excel(cd40k_path)
                    template_cd40k = templates.get("P003-CD40K", {})
                    
                    if template_cd40k:
                        selections_cd40k = get_selections_from_template(df_cd40k, template_cd40k)
                        df_cd40k_clean = clean_dataframe(
                            df_cd40k,
                            selections_cd40k,
                            convertir_sin_acentos=True,
                            transformar_varchar_latin=False,
                            max_len_varchar=3000
                        )
                        
                        if progress_callback:
                            progress_callback(f"🚀 Subiendo base manual a Teradata 'DLAB_GEC.T_SP_CD40K'...", "info")
                            
                        load_to_teradata(
                            con=con,
                            table_name="DLAB_GEC.T_SP_CD40K",
                            df=df_cd40k_clean,
                            selected_columns_config=selections_cd40k,
                            clear_table=True,
                            progress_callback=progress_callback
                        )
                    else:
                        if progress_callback:
                            progress_callback("⚠️ No se encontró la plantilla P003-CD40K para la carga automática del Excel manual.", "warning")
                except Exception as cd_err:
                    if progress_callback:
                        progress_callback(f"⚠️ Advertencia: Error al cargar el Excel manual de CD40K: {cd_err}. Se continuará con el flujo de orquestación.", "warning")
    
        # ----------------------------------------------------
        # FASE 3: AUTOMATIC UPLOAD OF BN_DESEMBOLSOS_GENERAL FROM SQL SERVER
        # ----------------------------------------------------
        if run_phase3 and con:
            sql_server = os.getenv("SQLSERVER_SERVER")
            if sql_server and sql_server != "tu_servidor_sql":
                if progress_callback:
                    progress_callback("📡 [Fase 3] Conectando a SQL Server para extraer BN_DESEMBOLSOS_GENERAL...", "info")
                try:
                    import pyodbc
                    
                    sql_database = os.getenv("SQLSERVER_DATABASE")
                    sql_user = os.getenv("SQLSERVER_USER")
                    sql_password = os.getenv("SQLSERVER_PASSWORD")
                    sql_driver = os.getenv("SQLSERVER_DRIVER", "{ODBC Driver 17 for SQL Server}")
                    
                    if sql_user and sql_password and sql_user != "tu_usuario":
                        conn_str = f"DRIVER={sql_driver};SERVER={sql_server};DATABASE={sql_database};UID={sql_user};PWD={sql_password}"
                    else:
                        conn_str = f"DRIVER={sql_driver};SERVER={sql_server};DATABASE={sql_database};Trusted_Connection=yes;"
                        
                    sql_conn = pyodbc.connect(conn_str)
                    
                    # Convertir período a formato numérico (ej. 202607) para el filtro
                    periodo_num = int(period_str)
                    query = f"SELECT * FROM BN_DESEMBOLSOS_GENERAL WHERE periodo >= {periodo_num}"
                    
                    # Leer usando Polars
                    df_desemb = pl.read_database(query=query, connection=sql_conn)
                    sql_conn.close()
                    
                    if progress_callback:
                        progress_callback(f"📥 Extracción exitosa. {len(df_desemb):,} registros obtenidos.", "info")
                        progress_callback("🚀 Transformando datos en Python y subiendo a Teradata 'DLAB_GEC.T_VENTAS_BPE_MARKET'...", "info")
                    
                    # Realizar transformaciones equivalentes a las de Teradata en Polars
                    df_desemb_clean = df_desemb.with_columns([
                        pl.col("COD_DOC").alias("CODDOC"),
                        pl.col("CLIENTE").alias("REPRESENTANTE_LEGAL"),
                        pl.col("CAMPANA_VPC").alias("CAMPANA_N2"),
                        pl.col("REG_EJECUTIVO").str.slice(0, 8).alias("REGISTRO"),
                        pl.when(pl.col("NUM_RUC").cast(pl.Utf8).str.strip_chars().str.len_chars() == 8).then(pl.lit("PN"))
                        .when(pl.col("NUM_RUC").cast(pl.Utf8).str.strip_chars().str.len_chars() == 11).then(pl.lit("PJ"))
                        .otherwise(pl.lit("DESCONOCIDO"))
                        .alias("TIPO_PERSONA")
                    ]).select([
                        "FECHA_DESEMBOLSADO",
                        "COD_UNICO",
                        "CODDOC",
                        "REPRESENTANTE_LEGAL",
                        "TIPO_PERSONA",
                        "CANAL",
                        "REGISTRO",
                        "NOM_EJECUTIVO",
                        "COLOCACION_NETA",
                        "CAMPANA_N2"
                    ])
                    
                    # Configurar mapeo de selección local para el cargador de Teradata
                    selections_desemb = [
                        {"name": col, "selected": True, "convert_nulls": False, "datatype": "VARCHAR(255)" if col not in ("REGISTRO", "TIPO_PERSONA") else "VARCHAR(50)", "new_name": col}
                        for col in df_desemb_clean.columns
                    ]
                    
                    load_to_teradata(
                        con=con,
                        table_name="DLAB_GEC.T_VENTAS_BPE_MARKET",
                        df=df_desemb_clean,
                        selected_columns_config=selections_desemb,
                        clear_table=True,
                        progress_callback=progress_callback
                    )
                    
                    # Ejecutar actualizaciones post-carga para los flags EVALUADO y FECHA_UPDATE
                    if progress_callback:
                        progress_callback("🔄 Actualizando flags EVALUADO y FECHA_UPDATE en Teradata...", "info")
                    try:
                        with con.cursor() as cursor:
                            cursor.execute("UPDATE DLAB_GEC.T_VENTAS_BPE_MARKET SET EVALUADO = 'NO'")
                            cursor.execute("UPDATE DLAB_GEC.T_VENTAS_BPE_MARKET FROM DLAB_GEC.M_EXP_DOCUMENTOS_EVALUADOS B SET EVALUADO = 'SI' WHERE CODDOC = B.DOCUMENTO")
                            cursor.execute("UPDATE DLAB_GEC.T_VENTAS_BPE_MARKET SET FECHA_UPDATE = CURRENT_TIMESTAMP(0)")
                    except Exception as upd_err:
                        if progress_callback:
                            progress_callback(f"⚠️ Advertencia al actualizar flags en T_VENTAS_BPE_MARKET: {upd_err}", "warning")
                            
                    if progress_callback:
                        progress_callback("✅ Tabla Teradata 'DLAB_GEC.T_VENTAS_BPE_MARKET' cargada y actualizada exitosamente.", "success")
                except Exception as desemb_err:
                    if progress_callback:
                        progress_callback(f"⚠️ Advertencia: Error al cargar BN_DESEMBOLSO desde SQL Server: {desemb_err}. Se continuará con el flujo.", "warning")
    
        # ----------------------------------------------------
        # FASE 4: RUN POST-LOAD SQL TRANSFORMATIONS
        # ----------------------------------------------------
        if run_phase4 and con:
            if progress_callback:
                progress_callback("⚡ [Fase 4] Iniciando ejecución de scripts SQL optimizados en Teradata...", "info")
            
            from core.sql_executor import run_post_load_transformations
            run_post_load_transformations(
                con=con,
                period_str=period_str,
                clear_consent=clear_consent,
                progress_callback=progress_callback,
                start_from_script=start_from_script
            )

        # ----------------------------------------------------
        # FASE 5: RUN SELECTION TRANSFORMATION (Secondary Connection)
        # ----------------------------------------------------
        if run_phase5:
            if progress_callback:
                progress_callback("⚡ [Fase 5] Iniciando ejecución del script de selección con conexión secundaria...", "info")
            
            from core.sql_executor import run_selection_transformation
            run_selection_transformation(
                period_str=period_str,
                progress_callback=progress_callback
            )
            
        if progress_callback:
            progress_callback("🎉 ¡Proceso de orquestación finalizado con éxito para Calidad Insumos!", "success")
    finally:
        if con:
            try:
                con.close()
            except Exception:
                pass
