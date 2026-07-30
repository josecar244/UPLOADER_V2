import requests
import time
import os
from dotenv import load_dotenv
import urllib3
from pathlib import Path
import datetime

# Suppress only the InsecureRequestWarning from urllib3 for verify=False requests
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def _request_with_retry(session, method, url, max_retries=3, backoff_factor=2, progress_callback=None, **kwargs):
    for attempt in range(max_retries):
        try:
            resp = session.request(method, url, **kwargs)
            if resp.status_code == 200:
                return resp
            if progress_callback:
                progress_callback(f"⚠️ Reintentando conexión con Insight ({attempt + 1}/{max_retries})...")
        except requests.RequestException:
            if progress_callback:
                progress_callback(f"⚠️ Reintentando conexión con Insight ({attempt + 1}/{max_retries})...")
        if attempt < max_retries - 1:
            time.sleep(backoff_factor * (2 ** attempt))
    # Final direct attempt
    return session.request(method, url, **kwargs)

def download_insight_data(query_name="EVALUATIONS", username=None, password=None, progress_callback=None, output_dir=None):
    """
    Downloads Insight evaluations data and saves it.
    """
    if not username or not password:
        load_dotenv()
        username = username or os.getenv("USERNAME_INSIGHT")
        password = password or os.getenv("PASSWORD_INSIGHT")
        
    if not username or not password:
        raise ValueError("Faltan credenciales de Insight. Configura USERNAME_INSIGHT y PASSWORD_INSIGHT.")
        
    if progress_callback:
        progress_callback("Iniciando sesión en Insight...")
        
    session = requests.Session()
    login_resp = _request_with_retry(
        session, "POST",
        "https://s425vp01/Insight",
        data={
            "registro": username,
            "password": password
        },
        verify=False,
        progress_callback=progress_callback
    )
    
    if login_resp.status_code != 200:
        raise RuntimeError(f"Fallo en login de Insight. Código de respuesta: {login_resp.status_code}")
        
    if progress_callback:
        progress_callback("Obteniendo lista de consultas guardadas...")
        
    queries_url = "https://s425vp01/Insight/api/Insight/getQueries?areaId=GCI_PRD_Insight_TLVentas,GCI_PRD_Insight_ACOE"
    resp = _request_with_retry(
        session, "GET",
        queries_url,
        verify=False,
        progress_callback=progress_callback
    )
    
    if resp.status_code != 200:
        raise RuntimeError(f"Error al obtener consultas: {resp.status_code}")
        
    queries = resp.json().get("data", [])
    
    # cambia aquí el nombre que quieras ejecutar
    NOMBRE_QUERY = query_name
    query_sql = None
    area_id_used = "GCI_PRD_Insight_TLVentas"
    
    for q in queries:
        if q.get("queryCustomName") == NOMBRE_QUERY:
            query_sql = q.get("queryData")
            area_id_used = q.get("areaId", "GCI_PRD_Insight_TLVentas")
            break
            
    if not query_sql:
        raise Exception(f"No se encontró la consulta con nombre '{NOMBRE_QUERY}'")
        
    if progress_callback:
        progress_callback(f"Ejecutando consulta '{NOMBRE_QUERY}' en el área '{area_id_used}'...")
        
    url = "https://s425vp01/Insight/api/Insight/executeQuery"
    payload = {
        "queryDelimiter": "\t",
        "query": query_sql,
        "areaId": area_id_used
    }
    
    resp = _request_with_retry(
        session, "POST",
        url,
        json=payload,
        verify=False,
        progress_callback=progress_callback
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Error al ejecutar consulta: {resp.status_code}")
        
    data = resp.json()
    if "data" not in data or not data["data"] or "nomArchivo" not in data["data"]:
        raise RuntimeError(f"Respuesta inesperada de la consulta: {data}")
        
    file_id = data["data"]["nomArchivo"]
    
    if progress_callback:
        progress_callback("Esperando que el archivo sea generado en Insight...")
        
    export_url = f"https://s425vp01/Insight/api/Insight/exportData?fileId={file_id}"
    
    export_data = None
    for attempt in range(60):
        export_resp = _request_with_retry(
            session, "GET",
            export_url,
            verify=False,
            progress_callback=progress_callback
        )
        if export_resp.status_code == 200:
            export_data = export_resp.json()
            if export_data.get("data") is not None:
                break
        if progress_callback:
            progress_callback(f"Esperando archivo... (intento {attempt+1}/60)")
        time.sleep(5)
        
    if not export_data or export_data.get("data") is None:
        raise TimeoutError("Límite de tiempo excedido esperando el archivo de Insight (máximo 5 minutos).")
        
    file_url = export_data["data"]["fileSource"]
    if progress_callback:
        progress_callback("Descargando archivo desde Insight...")
        
    file_resp = _request_with_retry(
        session, "GET",
        file_url,
        verify=False,
        progress_callback=progress_callback
    )
    if file_resp.status_code != 200:
        raise RuntimeError(f"Error al descargar archivo: {file_resp.status_code}")
        
    downloads_dir = Path(output_dir) if output_dir else Path.home() / "Downloads"
    os.makedirs(str(downloads_dir), exist_ok=True)
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    output_filename = f"Reporte_Insight_{query_name}_{date_str}.txt"
    output_path = str(downloads_dir / output_filename)
    
    with open(output_path, "wb") as f:
        f.write(file_resp.content)
        
    if progress_callback:
        progress_callback(f"Descarga finalizada. Guardado en '{output_path}'.")
        
    return output_path

if __name__ == "__main__":
    import sys
    print("Iniciando descarga de evaluaciones de Insight...")
    def console_callback(msg):
        print(f"-> {msg}")
    try:
        path = download_insight_data(progress_callback=console_callback)
        print(f"✅ Descarga completada con éxito: {path}")
    except Exception as e:
        print(f"❌ Error en la descarga: {e}", file=sys.stderr)
        sys.exit(1)