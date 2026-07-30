import urllib.request
import socket
import os
from typing import Dict

def check_outlook_mapi() -> Dict[str, any]:
    """Verifica si la sesión MAPI de Outlook Desktop está accesible."""
    try:
        import win32com.client
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        _ = outlook.GetDefaultFolder(6)  # Inbox
        return {"status": True, "message": "Outlook MAPI Conectado"}
    except Exception as e:
        return {"status": False, "message": f"Outlook no disponible"}

def check_chrome_cdp(cdp_url: str = "http://localhost:9222") -> Dict[str, any]:
    """Verifica si Chrome fue lanzado con la opción --remote-debugging-port=9222."""
    try:
        url = f"{cdp_url.rstrip('/')}/json/version"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=2) as response:
            if response.status == 200:
                return {"status": True, "message": "Chrome CDP activo (9222)"}
    except Exception:
        pass
    return {"status": False, "message": "Chrome CDP inactivo (9222)"}

def check_teradata_config() -> Dict[str, any]:
    """Verifica si las credenciales básicas de Teradata están presentes en variables de entorno."""
    user = os.getenv("TERADATA_USER")
    password = os.getenv("TERADATA_PASSWORD")
    host = os.getenv("TERADATA_HOST", "IBKTD")
    
    if user and password:
        return {"status": True, "message": f"Credenciales Teradata ({host})"}
    return {"status": False, "message": "Faltan credenciales Teradata"}

def run_preflight_health_check() -> Dict[str, Dict[str, any]]:
    """Ejecuta todos los chequeos de diagnóstico de salud del sistema."""
    return {
        "outlook": check_outlook_mapi(),
        "chrome_cdp": check_chrome_cdp(),
        "teradata": check_teradata_config()
    }
