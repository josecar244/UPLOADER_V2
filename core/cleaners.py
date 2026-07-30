import re
import polars as pl

def sanitize_identifier(name: str) -> str:
    """Sanitizes column and table names to prevent SQL Injection and comply with SQL naming rules."""
    if not name:
        return "column"
    # Replace non-alphanumeric/non-underscore characters with underscore
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    # Strip leading/trailing underscores
    sanitized = sanitized.strip('_')
    # If the first character is a digit, prefix with an underscore
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized if sanitized else "column"

def _is_sid_column(col_name: str | None, new_name: str | None = None) -> bool:
    """Returns True for the SID-like identifier column that must stay as text."""
    if not col_name and not new_name:
        return False

    haystack = " ".join(filter(None, [col_name, new_name])).lower()
    return "clave sid" in haystack or "sid" in haystack and "clave" in haystack


def suggest_sql_type(dtype) -> str:
    """Maps Polars data types to suggested Teradata SQL types."""
    # Integer types
    if dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64):
        return 'INTEGER'
    # Float types
    elif dtype in (pl.Float32, pl.Float64):
        return 'FLOAT'
    # Date/Time types
    elif dtype in (pl.Date, pl.Datetime, pl.Time):
        return 'TIMESTAMP'
    # Boolean type
    elif dtype == pl.Boolean:
        return 'CHAR(1)'
    # String or other
    else:
        return 'VARCHAR(255)'

def clean_dataframe(df: pl.DataFrame, selections: list, convertir_sin_acentos: bool, transformar_varchar_latin: bool, max_len_varchar: int) -> pl.DataFrame:
    """
    Cleans, projects, and renames the DataFrame columns in parallel using vectorized Polars expressions.
    """
    expressions = []
    
    for col_dict in selections:
        col_name = col_dict['name']
        new_name = col_dict['new_name']
        if not col_dict['selected']:
            continue
            
        # 1. Null transformation (0/1 check)
        if col_dict.get('convert_nulls', False):
            expr = pl.when(pl.col(col_name).is_null()).then(0).otherwise(1).alias(new_name)
            expressions.append(expr)
            continue
            
        data_type = col_dict['datatype']
        expr = pl.col(col_name)

        if _is_sid_column(col_name, new_name):
            data_type = 'VARCHAR(255)'
        
        # 2. String formatting and cleaning
        if data_type.startswith('VARCHAR') or data_type == 'CHAR(1)':
            # Cast to String
            expr = expr.cast(pl.String)
            
            # Map string literals for nulls to actual nulls
            expr = pl.when(
                expr.str.to_lowercase().is_in(["nan", "none", "<na>", "null"]) | expr.is_null()
            ).then(None).otherwise(expr)
            
            # Clean Latin-1 characters if checked
            if transformar_varchar_latin:
                expr = expr.str.replace_all(r"[^\x00-\xff]", "").str.slice(0, max_len_varchar)
                
            # Strip accents if checked
            if convertir_sin_acentos:
                expr = expr.str.normalize("NFKD").str.replace_all(r"\p{CombiningMark}", "")
                
            # Limit CHAR(1) columns
            if data_type == 'CHAR(1)':
                expr = expr.str.slice(0, 1)
                
        # 3. Numeric Types
        elif data_type == 'INTEGER':
            expr = expr.cast(pl.Int64, strict=False)
        elif data_type == 'FLOAT':
            expr = expr.cast(pl.Float64, strict=False)
            
        # 4. Dates and Timestamps
        elif data_type == 'TIMESTAMP':
            if df[col_name].dtype == pl.String:
                expr = expr.str.to_datetime(strict=False)
            else:
                expr = expr.cast(pl.Datetime, strict=False)
        elif data_type == 'DATE':
            if df[col_name].dtype == pl.String:
                expr = expr.str.to_date(strict=False)
            else:
                expr = expr.cast(pl.Date, strict=False)
                
        expressions.append(expr.alias(new_name))
        
    return df.select(expressions)
