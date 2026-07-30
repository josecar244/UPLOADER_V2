import os
import re
import datetime

class SQLScriptExecutionError(Exception):
    def __init__(self, script_name, statement_index, sql_content, original_error):
        self.script_name = script_name
        self.statement_index = statement_index
        self.sql_content = sql_content
        self.original_error = original_error
        super().__init__(f"Error en script '{script_name}' (sentencia {statement_index}): {original_error}")


def get_period_params(period_str):
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

    fec_inicio_mes = f"{period_str}01"

    return {
        "periodo": period_str,
        "periodo_num": int(period_str),
        "periodo_prev": period_prev,
        "anio": year,
        "mes": month,
        "fec_inicio_mes": fec_inicio_mes
    }


def split_sql_statements(sql_content):
    """
    Splits SQL script content into individual executable statements.
    Removes comments and handles semicolons safely.
    """
    # Remove single line comments
    content = re.sub(r'--.*', '', sql_content)
    # Remove multiline comments
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)

    # Split by semicolon
    statements = content.split(';')
    cleaned_statements = []

    for stmt in statements:
        stmt_clean = stmt.strip()
        if stmt_clean:
            cleaned_statements.append(stmt_clean)

    return cleaned_statements


def get_friendly_script_name(script_path_or_name):
    # Extract basename
    name = os.path.basename(script_path_or_name).lower()

    # Predefined dictionary
    friendly_names = {
        "01_evaluacion_manual_pc.sql": "Evaluaciones Manuales Pure Cloud",
        "02_sa_marcacion_ventas_lpdp.sql": "Consentimientos y Ventas LPDP",
        "03_sa_calculo_pesos_unpivot.sql": "Cálculo de Pesos e Indicadores",
        "04_sa_ajustes_curva.sql": "Ajustes de Curva de Notas",
        "04_b_sa_parche_nota_cero.sql": "Verificación de Notas Cero",
        "05_consolidacion_nota_final.sql": "Consolidación de Nota Final de Calidad",
        "ventas_dn.sql": "Ventas DN",
        "cd40k.sql": "Base CD40K",
        "source_tvl.sql": "Fuente TVL",
        "ca_consentimiento_diario.sql": "Consentimiento Diario",
        "kri_ventas_sin_audio.sql": "Ventas Sin Audio",
        "tlf_no_autorizado.sql": "Teléfonos No Autorizados",
        "consumo_select_tc_cd_seg.sql": "Vistas de Consumo (TC, CD, SEG)"
    }

    if name in friendly_names:
        return friendly_names[name]

    # Clean fallback: remove extension, numbers prefix, replace underscores with spaces
    base = os.path.splitext(name)[0]
    base_clean = re.sub(r'^\d+_+', '', base)  # remove e.g. "01_"
    base_clean = base_clean.replace('_', ' ').strip().title()
    return base_clean


def execute_sql_script(con, script_path, params, progress_callback=None):
    """
    Reads, parameterizes and executes all SQL statements in a script file.
    """
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script SQL no encontrado: {script_path}")

    friendly_name = get_friendly_script_name(script_path)
    if progress_callback:
        progress_callback(f"⚙️ Procesando: **{friendly_name}**...", "info")

    with open(script_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Replace placeholders dynamically.
    # Replaces :periodo_num first to avoid prefix conflict with :periodo.
    replaced_content = content
    replaced_content = replaced_content.replace(":periodo_num", str(params["periodo_num"]))
    replaced_content = replaced_content.replace(":periodo_prev", f"'{params['periodo_prev']}'")
    replaced_content = replaced_content.replace(":periodo", f"'{params['periodo']}'")
    replaced_content = replaced_content.replace(":fec_inicio_mes", f"'{params['fec_inicio_mes']}'")
    replaced_content = replaced_content.replace(":anio", str(params["anio"]))
    replaced_content = replaced_content.replace(":mes", f"{params['mes']:02d}")

    statements = split_sql_statements(replaced_content)

    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Se detectaron {len(statements)} sentencias en {os.path.basename(script_path)}")

    with con.cursor() as cursor:
        # Enable autocommit to avoid explicit transaction errors when mixing DDL and DML
        try:
            cursor.execute("{fn teradata_nativesql}{fn teradata_autocommit_on}")
        except Exception:
            pass

        for idx, stmt in enumerate(statements, 1):
            stmt_clean = stmt.strip()
            if not stmt_clean:
                continue
            snippet = stmt_clean[:60].replace('\n', ' ') + "..." if len(stmt_clean) > 60 else stmt_clean
            logger.info(f"   [{idx}/{len(statements)}] Ejecutando: {snippet}")
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
                cursor.execute(stmt_clean)
            except Exception as err:
                err_str = str(err)
                # COLLECT STATISTICS is a non-critical optimizer hint.
                # Error 3523 = no STATISTICS privilege → skip with warning instead of crashing.
                is_collect_stats = stmt_clean.upper().lstrip().startswith("COLLECT STAT")
                if is_collect_stats and "3523" in err_str:
                    logger.warning(f"   ⚠️ COLLECT STATISTICS omitido (sin permiso): {snippet}")
                    continue
                # DROP TABLE / DROP VIEW is non-critical if the object does not exist.
                # Error 3807 = Object does not exist → skip with warning.
                is_drop = stmt_clean.upper().lstrip().startswith("DROP ")
                if is_drop and "3807" in err_str:
                    logger.warning(f"   ⚠️ DROP omitido (objeto no existe): {snippet}")
                    continue
                raise SQLScriptExecutionError(os.path.basename(script_path), idx, stmt_clean, err)

    if progress_callback:
        progress_callback(f"✅ Completado: **{friendly_name}**", "success")


def run_post_load_transformations(con, period_str, clear_consent=False, progress_callback=None, start_from_script=None):
    """
    Executes the SQL consumption scripts in Teradata (Ventas DN, CD40K, etc.).
    """
    # Enable autocommit at connection level to avoid DDL/DML mixed transaction errors (Error 3932)
    con.autocommit = True

    params = get_period_params(period_str)

    # Target SQL scripts in correct dependency order
    opt_sql_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sql_consumo')

    scripts = [
        "VENTAS_DN.sql",
        "CD40K.sql",
        "SOURCE_TVL.sql",
        "CA_CONSENTIMIENTO_DIARIO.sql",
        "KRI_VENTAS_SIN_AUDIO.sql",
        "TLF_NO_AUTORIZADO.sql"
    ]

    if start_from_script:
        clean_start = os.path.basename(start_from_script).lower()
        matched_idx = -1
        for idx, s in enumerate(scripts):
            if s.lower() == clean_start:
                matched_idx = idx
                break
        if matched_idx != -1:
            scripts = scripts[matched_idx:]
        else:
            if progress_callback:
                progress_callback(f"⚠️ Advertencia: No se encontró el script '{start_from_script}' en la lista. Se ejecutarán todos.", "warning")

    for script_name in scripts:
        script_path = os.path.join(opt_sql_dir, script_name)

        if script_name == "CA_CONSENTIMIENTO_DIARIO.sql" and clear_consent:
            if progress_callback:
                progress_callback("🧹 Opción 'Limpiar Consentimientos' activada. Descomentando DELETEs iniciales...", "info")
            with open(script_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # Uncomment the lines: -- DELETE FROM ...
            uncommented = re.sub(
                r'--\s*(DELETE\s+FROM\s+DLAB_GEC\.M_EXP_CONSENTIMIENTO_[A-Z]+\s+ALL;)',
                r'\1',
                content
            )
            temp_script_path = script_path + ".temp"
            with open(temp_script_path, 'w', encoding='utf-8') as f:
                f.write(uncommented)
            try:
                execute_sql_script(con, temp_script_path, params, progress_callback)
            finally:
                if os.path.exists(temp_script_path):
                    os.remove(temp_script_path)
        else:
            execute_sql_script(con, script_path, params, progress_callback)

    if progress_callback:
        progress_callback("✅ ¡Transformaciones SQL de Consumo completadas exitosamente en Teradata!", "success")


def run_selection_transformation(period_str, progress_callback=None):
    """
    Executes the selection SQL script using the secondary read-only connection.
    """
    params = get_period_params(period_str)
    opt_sql_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sql_consumo')

    # --- EJECUCIÓN DEL SCRIPT SECUNDARIO CON CONEXIÓN SEPARADA ---
    sec_user = os.getenv("TERADATA_USER_SELECT")
    sec_password = os.getenv("TERADATA_PASSWORD_SELECT")

    if not sec_user or not sec_password:
        if progress_callback:
            progress_callback("❌ Error: No se encontraron las credenciales secundarias (TERADATA_USER_SELECT/TERADATA_PASSWORD_SELECT) en el archivo .env.", "error")
        raise ValueError("Credenciales secundarias de Teradata no configuradas en el archivo .env.")

    host = os.getenv("TERADATA_HOST_SELECT", "IBKTD")
    logmech = os.getenv("TERADATA_LOGMECH_SELECT", "LDAP")

    if progress_callback:
        progress_callback(f"🔑 Conectando a Teradata con el usuario secundario ({sec_user}) para CONSUMO_SELECT_TC_CD_SEG...", "info")

    from core.database import connect_teradata
    try:
        con_sec = connect_teradata(sec_user, sec_password, host=host, logmech=logmech)
        con_sec.autocommit = True  # Enable autocommit for secondary connection
    except Exception as conn_err:
        if progress_callback:
            progress_callback(f"❌ Error al conectar con el usuario secundario de Teradata: {conn_err}", "error")
        raise conn_err

    try:
        script_sec_path = os.path.join(opt_sql_dir, "CONSUMO_SELECT_TC_CD_SEG.sql")
        execute_sql_script(con_sec, script_sec_path, params, progress_callback)
    finally:
        try:
            con_sec.close()
        except Exception:
            pass

    if progress_callback:
        progress_callback("✅ ¡Selección de Consumo completada exitosamente en Teradata!", "success")
