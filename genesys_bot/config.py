import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Directorios base
BASE_DIR = Path(__file__).parent.resolve()
DOWNLOADS_DIR = Path.home() / "Downloads"

# Archivos de persistencia
TRACKING_FILE = BASE_DIR / "tracking.json"
TELEFONOS_CACHE_FILE = BASE_DIR / "telefonos_cache.json"
NO_ENCONTRADOS_FILE = BASE_DIR / "no_encontrados.csv"

# Configuración de CDP y Navegador
CDP_URL = os.getenv("GENESYS_CDP_URL", "http://localhost:9222")
GENESYS_URL = os.getenv("GENESYS_URL", "https://apps.mypurecloud.com/directory/#/analytics/interactions")
PROFILE_DIR = BASE_DIR / ".chrome_genesys_profile"

# Credenciales y conexión Teradata
TERADATA_USER = os.getenv("TERADATA_USER")
TERADATA_PASSWORD = os.getenv("TERADATA_PASSWORD")
TERADATA_HOST = os.getenv("TERADATA_HOST", "IBKTD")
TERADATA_LOGMECH = os.getenv("TERADATA_LOGMECH", "LDAP")

# Timeouts (en milisegundos para Playwright)
TIMEOUT_DEFAULT = 10000
TIMEOUT_LONG = 60000
TIMEOUT_DETAILS_LOAD = 90000

# Selectores CSS/XPath centralizados para Genesys Cloud UI
SELECTORS = {
    "analytics_iframe_url": "analytics-ui",
    "details_iframe_url": "interaction-details-ui",
    "toggle_filters_btn": "button.toggle-filters",
    "clear_filters_btn": "a.clear-all-filters",
    "interactions_section": 'h3.section-name:has-text("Interacciones")',
    "user_filter_input": 'input[placeholder="Buscar usuarios"]',
    "contact_filter_input": 'input[placeholder="Filtrar por ID de contacto"]',
    "dnis_filter_input": 'input[aria-label="Filtrar por DNIS"]',
    "action_rows": "div.dt-row.action-row",
    "loading_spinner": "gux-page-loading-spinner.active, gux-page-loading-spinner.loading-overlay-v2.active",
    "pager_count": "span.pager-count",
    "prev_recording_btn": "gux-button.previous-recording",
    "next_recording_btn": "gux-button.next-recording",
    "duration_container": "div.content-duration",
    "download_trigger_btn": "div.details-subrow.edit-download gux-button",
    "filename_input": "#download-file-name-input",
    "format_dropdown": 'button[name="Opus Desplegable"]',
    "format_mp3_option": 'gux-option:has-text("MP3")',
    "confirm_download_btn": "gux-button.recording-download-button",
    "tab_button": "button.gux-tab-button",
}
