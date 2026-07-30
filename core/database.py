import os
import teradatasql
import polars as pl
from dotenv import load_dotenv

def load_credentials():
    """
    Loads Teradata credentials from environment variables / .env file.
    """
    load_dotenv()
    env_user = os.getenv("TERADATA_USER")
    env_password = os.getenv("TERADATA_PASSWORD")
    env_host = os.getenv("TERADATA_HOST")
    env_logmech = os.getenv("TERADATA_LOGMECH")
    
    return {
        "teradata_user": env_user or "",
        "teradata_password": env_password or "",
        "teradata_host": env_host or "IBKTD",
        "teradata_logmech": env_logmech or "LDAP"
    }

def connect_teradata(user, password, host='IBKTD', logmech='TD2'):
    """Establishes connection to the Teradata database."""
    return teradatasql.connect(
        host=host,
        user=user,
        password=password,
        logmech=logmech
    )

def check_table_exists(con, table_name) -> bool:
    """Checks if a table exists in Teradata."""
    cur = con.cursor()
    try:
        cur.execute(f"SELECT TOP 1 * FROM {table_name}")
        cur.close()
        return True
    except Exception:
        return False

def get_table_columns(con, table_name) -> list:
    """Gets column names of an existing table."""
    cur = con.cursor()
    try:
        cur.execute(f"SELECT TOP 1 * FROM {table_name}")
        cols = [desc[0] for desc in cur.description]
        cur.close()
        return cols
    except Exception:
        return []

def execute_create_table(con, table_name, columns_with_types):
    """Creates a new table with sanitized column names and specified types."""
    cur = con.cursor()
    cols_defs = [f'"{col}" {dtype}' for col, dtype in columns_with_types.items()]
    create_query = f"CREATE MULTISET TABLE {table_name} (\n" + ",\n".join(cols_defs) + "\n);"
    cur.execute(create_query)
    cur.close()
    return create_query

def clear_table_data(con, table_name):
    """Deletes all records from the target table."""
    cur = con.cursor()
    cur.execute(f"DELETE FROM {table_name}")
    cur.close()

def load_to_teradata(con, table_name, df: pl.DataFrame, selected_columns_config, clear_table, progress_callback=None):
    """
    Ingests a Polars DataFrame into Teradata.
    Uses optimized standard batch insertion with real-time percentage progress updates.
    """
    cur = con.cursor()
    
    # 1. Determine if table exists
    table_exists = check_table_exists(con, table_name)
    
    # 2. Get table structure
    if table_exists:
        if clear_table:
            if progress_callback:
                progress_callback("Limpiando datos existentes en la tabla...")
            clear_table_data(con, table_name)
        columns_in_table = get_table_columns(con, table_name)
    else:
        # Create table based on configuration
        if progress_callback:
            progress_callback("Creando tabla de destino...")
        columns_with_types = {col['new_name']: col['datatype'] for col in selected_columns_config if col['selected']}
        create_query = execute_create_table(con, table_name, columns_with_types)
        columns_in_table = list(columns_with_types.keys())
        if progress_callback:
            progress_callback("Tabla de destino creada con éxito.")

    if not columns_in_table:
        raise ValueError("No se pudo obtener ni crear la estructura de la tabla.")

    # 3. Filter DataFrame columns to align with target table columns
    # We rename columns first to match their 'new_name'
    rename_mapping = {col['name']: col['new_name'] for col in selected_columns_config if col['selected']}
    df_renamed = df.rename({k: v for k, v in rename_mapping.items() if k in df.columns})
    
    # Select only columns present in target table, fill missing with None
    select_exprs = []
    for col in columns_in_table:
        if col in df_renamed.columns:
            select_exprs.append(pl.col(col))
        else:
            select_exprs.append(pl.lit(None).alias(col))
            
    df_to_load = df_renamed.select(select_exprs)

    # Sanitize string columns to prevent Teradata Error 6706 (untranslatable character)
    string_cols = [col for col, dtype in df_to_load.schema.items() if dtype in (pl.String, pl.Utf8)]
    if string_cols:
        df_to_load = df_to_load.with_columns([
            pl.col(c).str.replace_all("“", '"')
                     .str.replace_all("”", '"')
                     .str.replace_all("‘", "'")
                     .str.replace_all("’", "'")
                     .str.replace_all("—", "-")
                     .str.normalize("NFKD")
                     .str.replace_all(r"\p{CombiningMark}", "")
                     .str.replace_all(r"[^\x00-\xff]", "")
            for c in string_cols
        ])

    # 4. Extract data as list of tuples (extremely fast in Polars)
    if progress_callback:
        progress_callback("Preparando registros para la carga...")
    data = df_to_load.rows()

    # 5. Insert Query
    placeholders = ", ".join(['?' for _ in columns_in_table])
    cols_str = ", ".join([f'"{col}"' for col in columns_in_table])
    insert_query = f'INSERT INTO {table_name} ({cols_str}) VALUES ({placeholders})'

    # 6. Standard Batch Ingestion
    try:
        cur.execute("{fn teradata_nativesql}{fn teradata_autocommit_on}")
    except Exception:
        pass

    batch_size = 50000
    total_rows = len(data)
    
    if progress_callback:
        progress_callback(f"Iniciando carga de {total_rows:,} registros...")
        
    for i in range(0, total_rows, batch_size):
        batch = data[i : i + batch_size]
        p_actual = min(i + batch_size, total_rows)
        porcentaje = int((p_actual / total_rows) * 100)
        if progress_callback:
            progress_callback(f"Cargando registros: {i+1:,} a {p_actual:,} de {total_rows:,} ({porcentaje}%)")
        cur.executemany(insert_query, batch)
        
    if progress_callback:
        progress_callback("Carga de datos finalizada con éxito.")
    cur.close()
    return True
