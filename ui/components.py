import streamlit as st
import json
import os
import pandas as pd
from core.cleaners import suggest_sql_type, sanitize_identifier

def load_templates():
    """Loads templates from JSON configuration file."""
    path = 'appsFiles/excelToTeraFiles/plantillas.json'
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def render_sidebar():
    """Renders the Streamlit sidebar controls and returns configuration settings."""
    st.sidebar.markdown(
        """
        <div style='background-color: #1E293B; padding: 15px; border-radius: 10px; margin-bottom: 20px;'>
            <h2 style='color: #F8FAFC; margin: 0; font-size: 1.2rem;'>⚙️ Configuración de Lectura</h2>
        </div>
        """,
        unsafe_allow_html=True
    )
    
    file_type = st.sidebar.selectbox(
        "Tipo de archivo a cargar", 
        ["Excel", "CSV", "Texto Unicode"],
        help="Seleccione el formato del archivo origen.",
        key="uploader_file_type"
    )
    
    # 2. Templates Section
    st.sidebar.markdown("<hr style='border-color: #334155;'>", unsafe_allow_html=True)
    st.sidebar.markdown("<h3 style='color: #E2E8F0; font-size: 1rem; margin-bottom: 10px;'>📋 Plantillas de Mapeo</h3>", unsafe_allow_html=True)
    templates = load_templates()
    opciones_plantillas = ['Ninguno'] + list(templates.keys())
    
    # Ensure default is selected in session state if not set
    if "uploader_template" not in st.session_state:
        st.session_state.uploader_template = "Ninguno"
        
    plantilla_seleccionada = st.sidebar.selectbox(
        "Seleccione una plantilla", 
        opciones_plantillas,
        key="uploader_template"
    )
    
    # 3. Text cleaning options
    st.sidebar.markdown("<hr style='border-color: #334155;'>", unsafe_allow_html=True)
    st.sidebar.markdown("<h3 style='color: #E2E8F0; font-size: 1rem; margin-bottom: 10px;'>🧹 Limpieza de Datos</h3>", unsafe_allow_html=True)
    
    convertir_sin_acentos = st.sidebar.checkbox(
        "Eliminar acentos en textos", 
        value=True,
        help="Reemplaza caracteres acentuados por sus letras base correspondientes (ej. á -> a, ñ -> n)."
    )
    
    transformar_varchar_latin = st.sidebar.checkbox(
        "Limpiar caracteres especiales (LATIN)", 
        value=False,
        help="Elimina caracteres especiales incompatibles (como emojis o símbolos no estándar) para evitar errores de guardado."
    )
    
    max_len_varchar = 3000
    if transformar_varchar_latin:
        max_len_varchar = st.sidebar.number_input(
            "Longitud máxima de texto", 
            min_value=1, 
            max_value=5000, 
            value=3000,
            step=100
        )

    # 4. Pre-Flight Health Check Widget
    st.sidebar.markdown("<hr style='border-color: #334155;'>", unsafe_allow_html=True)
    st.sidebar.markdown("<h3 style='color: #E2E8F0; font-size: 1rem; margin-bottom: 10px;'>🩺 Diagnóstico de Entorno</h3>", unsafe_allow_html=True)
    
    if st.sidebar.button("🔍 Verificar Entorno", key="btn_run_health_check", width="stretch"):
        from core.health_check import run_preflight_health_check
        st.session_state.health_status = run_preflight_health_check()

    health = st.session_state.get("health_status", None)
    if health:
        for key, info in health.items():
            icon = "✅" if info["status"] else "⚠️"
            st.sidebar.caption(f"{icon} {info['message']}")
    else:
        st.sidebar.caption("Consulta el estado de Outlook, Chrome CDP y Teradata.")
        
    return {
        "file_type": file_type,
        "selected_template": plantilla_seleccionada,
        "templates": templates,
        "convertir_sin_acentos": convertir_sin_acentos,
        "transformar_varchar_latin": transformar_varchar_latin,
        "max_len_varchar": max_len_varchar
    }

def render_column_editor(df, selected_template, templates, editor_key="column_config_editor"):
    """
    Renders an interactive st.data_editor table to configure columns.
    Returns a list of column configurations.
    """
    st.markdown("<h3 style='margin-top: 20px; color: #1E293B;'>📊 Configuración de Columnas</h3>", unsafe_allow_html=True)
    st.markdown("Ajuste las columnas que desea conservar, cambie nombres y configure tipos de datos destino:")
    
    # Get columns and compute statistics
    columns = df.columns
    total_rows = len(df)
    
    configuraciones_plantilla = {}
    if selected_template != 'Ninguno':
        configuraciones_plantilla = templates.get(selected_template, {})
        
    # Build editor configuration DataFrame
    config_rows = []
    for col in columns:
        # Compute null stats
        non_null_count = total_rows - df[col].null_count()
        non_null_pct = (non_null_count / total_rows) * 100
        non_null_str = f"{non_null_count} ({non_null_pct:.2f}%)"
        
        # Inferred type mapping
        suggested_type = suggest_sql_type(df[col].dtype)
        normalized_name = str(col).lower()
        if "clave sid" in normalized_name or ("sid" in normalized_name and "clave" in normalized_name):
            suggested_type = 'VARCHAR(255)'
        
        # Load defaults from template if available
        if not configuraciones_plantilla:
            default_selected = True
            default_new_name = sanitize_identifier(col)
            default_convert_null = False
            default_type = suggested_type
        elif col in configuraciones_plantilla:
            default_selected = configuraciones_plantilla[col].get('Añadir', True)
            default_new_name = sanitize_identifier(configuraciones_plantilla[col].get('Nuevo nombre', col))
            default_convert_null = configuraciones_plantilla[col].get('Null:0/No Null:1', False)
            default_type = configuraciones_plantilla[col].get('Tipo de dato', suggested_type)
        else:
            default_selected = False
            default_new_name = sanitize_identifier(col)
            default_convert_null = False
            default_type = suggested_type
            
        config_rows.append({
            "Columna Original": col,
            "Añadir": default_selected,
            "Null:0/1": default_convert_null,
            "Tipo de dato": default_type,
            "Nuevo nombre": default_new_name,
            "Completitud": non_null_str
        })
        
    config_df = pd.DataFrame(config_rows)
    
    # Render using st.data_editor
    edited_df = st.data_editor(
        config_df,
        column_config={
            "Columna Original": st.column_config.TextColumn("Columna Original", disabled=True),
            "Añadir": st.column_config.CheckboxColumn("Incluir en carga", default=True),
            "Null:0/1": st.column_config.CheckboxColumn("Convertir a Indicador (1/0)", default=False),
            "Tipo de dato": st.column_config.SelectboxColumn(
                "Tipo de dato destino",
                options=['VARCHAR(255)', 'INTEGER', 'FLOAT', 'TIMESTAMP', 'DATE', 'CHAR(1)'],
                default='VARCHAR(255)'
            ),
            "Nuevo nombre": st.column_config.TextColumn("Nombre destino (BD)"),
            "Completitud": st.column_config.TextColumn("Registros completos", disabled=True)
        },
        hide_index=True,
        width="stretch",
        key=editor_key
    )
    
    # Process results back into standard list of dicts
    selections = []
    for idx, row in edited_df.iterrows():
        # Sanitize the new name input
        clean_new_name = sanitize_identifier(row["Nuevo nombre"])
        selections.append({
            "name": row["Columna Original"],
            "selected": bool(row["Añadir"]),
            "convert_nulls": bool(row["Null:0/1"]),
            "datatype": row["Tipo de dato"],
            "new_name": clean_new_name
        })
        
    return selections
