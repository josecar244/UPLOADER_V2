# Genesys Bot & Outlook Automation

Módulo aislado para la extracción de solicitudes de audios desde Outlook y automatización de descargas en Genesys Cloud.

## Requisitos

Instalar las librerías necesarias en la laptop personal/trabajo:

```bash
pip install pywin32 pandas beautifulsoup4 openpyxl playwright
```

## Estructura

- `outlook_reader.py`: Se conecta a Outlook Desktop vía COM, busca correos con el asunto *"Solicitud de audio"*, procesa las tablas HTML o adjuntos `.xlsx`, y normaliza los registros (`PROMOTOR_CD`, `DNI`).
- `genesys_downloader.py`: Automatización Playwright optimizada con esperas explícitas, captura de adjuntos y manejo de logs.

## Ejecución

1. Asegurarse de tener el navegador Chrome abierto con depuración remota activada:
   ```bash
   chrome.exe --remote-debugging-port=9222
   ```
2. Ejecutar el script principal desde la carpeta `genesys_bot`:
   ```bash
   python genesys_downloader.py
   ```
