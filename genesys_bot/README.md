# Genesys Bot & Outlook Automation

Módulo aislado para la extracción de solicitudes de audios desde Outlook y automatización de descargas en Genesys Cloud UI.

## Requisitos

Instalar las librerías necesarias:

```bash
pip install pywin32 pandas beautifulsoup4 openpyxl playwright
```

E instalar los navegadores de Playwright (si no se han instalado previamente):
```bash
playwright install chromium
```

## Estructura de Componentes

- `outlook_service.py` / `outlook_reader.py`: Se conecta a Outlook Desktop vía COM, extrae correos con el asunto *"Solicitud de audio"*, procesa tablas HTML o adjuntos `.xlsx`, y normaliza los registros (`PROMOTOR_CD`, `DNI`).
- `teradata_service.py`: Enriquece los registros cruzándolos con Teradata o la caché local para obtener el número de teléfono del cliente (DNIS).
- `genesys_browser.py`: Automatización de descarga en Genesys Cloud UI vía Playwright + CDP.
- `main.py`: Orquestador principal del proceso de extremo a extremo.

## Autenticación y Persistencia de Sesión (Microsoft SSO / Genesys)

El bot cuenta con un sistema de **autolanzamiento y perfil persistente** (`.chrome_genesys_profile`):

1. **Auto-inicio de Chrome CDP:** Si Chrome no está abierto en el puerto de depuración remota (`9222`), el bot detecta y ejecuta automáticamente el Chrome del sistema con el puerto activo y la carpeta de datos `.chrome_genesys_profile`.
2. **Primera Vez / Login de Microsoft:** 
   - Si no hay sesión activa de Genesys/Microsoft, el navegador se abrirá y el bot otorgará **5 minutos de tolerancia** para realizar el login manual y la autenticación MFA.
   - Asegúrate de marcar **"Mantener sesión iniciada"** ("Keep me signed in").
3. **Reutilización de Sesión:** En las siguientes ejecuciones, Chrome restaurará automáticamente la sesión guardada en `.chrome_genesys_profile`, ejecutando las descargas directamente en segundo plano sin pedir credenciales.

## Ejecución

### Opción 1: Ejecución Total Autónoma (Recomendado)
Ejecutar directamente el orquestador principal:

```bash
python main.py
```

*Nota: No se requiere abrir Chrome previamente. El bot lo lanzará y gestionará automáticamente.*

### Opción 2: Ejecución con Chrome Pre-abierto (Opcional)
Si prefieres usar un Chrome abierto manualmente:

1. Abrir Chrome con depuración remota:
   ```bash
   chrome.exe --remote-debugging-port=9222
   ```
2. Iniciar sesión en Genesys Cloud en dicho navegador.
3. Ejecutar el script:
   ```bash
   python main.py
   ```
