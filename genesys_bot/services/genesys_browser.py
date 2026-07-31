import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import List, Optional
from playwright.sync_api import Frame, Page, sync_playwright

from genesys_bot.config import CDP_URL, DOWNLOADS_DIR, GENESYS_URL, PROFILE_DIR, SELECTORS, TIMEOUT_DEFAULT, TIMEOUT_DETAILS_LOAD
from genesys_bot.logger import get_logger
from genesys_bot.models import EstadoRegistro, SolicitudAudio
from genesys_bot.storage.tracking_store import TrackingStore

logger = get_logger("GenesysBrowser")


class GenesysBrowserAutomation:
    def __init__(self, cdp_url: str = CDP_URL, genesys_url: str = GENESYS_URL, tracking_store: TrackingStore = None):
        self.cdp_url = cdp_url
        self.genesys_url = genesys_url
        self.tracking_store = tracking_store or TrackingStore()

    def _lanzar_chrome_cdp_automatico(self) -> bool:
        """Verifica si Chrome CDP responde; si no, lanza Chrome del sistema con el perfil persistente (.chrome_genesys_profile)."""
        try:
            req = urllib.request.urlopen(f"{self.cdp_url}/json/version", timeout=1)
            if req.status == 200:
                logger.info(f"Chrome ya está escuchando en puerto CDP ({self.cdp_url})")
                return True
        except Exception:
            pass

        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
            shutil.which("chrome") or ""
        ]

        chrome_cmd = next((p for p in chrome_paths if p and os.path.exists(p)), None)
        if not chrome_cmd:
            logger.warning("No se encontró el ejecutable de Chrome en las rutas estándar.")
            return False

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        port = self.cdp_url.split(":")[-1].split("/")[0] if ":" in self.cdp_url else "9222"

        cmd = [
            chrome_cmd,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={PROFILE_DIR.resolve()}",
            "--no-first-run",
            "--no-default-browser-check",
            self.genesys_url
        ]

        logger.info(f"Auto-iniciando Chrome con perfil persistente ({PROFILE_DIR.name}) en puerto CDP {port}...")
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.error(f"Error al ejecutar Chrome: {e}")
            return False

        start_time = time.time()
        while time.time() - start_time < 12:
            try:
                req = urllib.request.urlopen(f"{self.cdp_url}/json/version", timeout=1)
                if req.status == 200:
                    logger.info("Chrome con puerto CDP inicializado correctamente.")
                    return True
            except Exception:
                time.sleep(0.5)

        logger.warning("Chrome fue ejecutado pero no respondió a tiempo en el puerto CDP.")
        return False

    def _obtener_page_principal(self, browser) -> Optional[Page]:
        if not browser or not browser.contexts:
            return None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                try:
                    if "/analytics/interactions" in pg.url or "purecloud" in pg.url or "genesys" in pg.url:
                        return pg
                except Exception:
                    continue
        # Retornar la primera pestaña si ninguna coincide
        for ctx in browser.contexts:
            if ctx.pages:
                return ctx.pages[0]
        return None

    def _localizar_iframe(self, page: Page, key_url: str, max_intentos: int = 10) -> Optional[Frame]:
        for _ in range(max_intentos):
            for f in page.frames:
                if key_url in f.url:
                    return f
            page.wait_for_timeout(1000)
        return None

    def _cerrar_pestanas_admin_rezagadas(self, page: Page) -> None:
        try:
            for pg in list(page.context.pages):
                if pg != page and "/admin" in pg.url:
                    try:
                        pg.close()
                    except Exception:
                        pass
        except Exception:
            pass

    def _asegurar_panel_filtros_abierto(self, analytics_frame: Frame) -> None:
        try:
            toggle_btn = analytics_frame.locator(SELECTORS["toggle_filters_btn"])
            if not toggle_btn.is_visible():
                return

            for intento in range(3):
                seccion = analytics_frame.locator(SELECTORS["interactions_section"]).first
                if seccion.is_visible():
                    return

                logger.info(f"Panel de filtros cerrado (intento {intento+1}/3). Abriendo...")
                toggle_btn.click()
                analytics_frame.wait_for_timeout(1200)
        except Exception as e:
            logger.debug(f"Error asegurando panel de filtros: {e}")

    def _limpiar_filtros_si_hay(self, analytics_frame: Frame) -> None:
        try:
            borrar = analytics_frame.locator(SELECTORS["clear_filters_btn"])
            if borrar.count() > 0 and borrar.is_visible():
                logger.info("Limpiando filtros previos...")
                borrar.click()
                analytics_frame.wait_for_timeout(1500)

                seccion_int = analytics_frame.locator(SELECTORS["interactions_section"])
                if seccion_int.is_visible():
                    seccion_int.click()
                    analytics_frame.wait_for_timeout(500)
        except Exception as e:
            logger.debug(f"Error limpiando filtros: {e}")

    def _rellenar_filtro_usuario(self, analytics_frame: Frame, reg_ev: str) -> None:
        try:
            seccion_int = analytics_frame.locator(SELECTORS["interactions_section"])
            if seccion_int.is_visible():
                usuario_visible = analytics_frame.locator(SELECTORS["user_filter_input"]).is_visible()
                if not usuario_visible:
                    seccion_int.click()
                    analytics_frame.wait_for_timeout(800)
        except Exception:
            pass

        usuario_input = analytics_frame.locator(SELECTORS["user_filter_input"])
        usuario_input.wait_for(state="visible", timeout=TIMEOUT_DEFAULT)
        usuario_input.click()
        usuario_input.press("Control+A")
        usuario_input.press("Backspace")
        analytics_frame.wait_for_timeout(400)
        usuario_input.press_sequentially(str(reg_ev), delay=80)
        analytics_frame.wait_for_timeout(1000)
        usuario_input.press("Enter")
        analytics_frame.wait_for_timeout(1200)
        usuario_input.press("Enter")
        analytics_frame.wait_for_timeout(500)
        logger.info(f"Filtro usuario '{reg_ev}' aplicado.")

    def _rellenar_filtro_dnis(self, analytics_frame: Frame, telefonos: List[str]) -> None:
        dnis_input = analytics_frame.locator(SELECTORS["dnis_filter_input"])
        dnis_input.wait_for(state="visible", timeout=TIMEOUT_DEFAULT)
        dnis_input.click()
        dnis_input.press("Control+A")
        dnis_input.press("Backspace")
        analytics_frame.wait_for_timeout(400)

        for tlf in telefonos:
            dnis_input.press_sequentially(str(tlf), delay=80)
            analytics_frame.wait_for_timeout(600)
            dnis_input.press("Enter")
            analytics_frame.wait_for_timeout(1200)
        logger.info(f"{len(telefonos)} teléfono(s) ingresados como DNIS.")

    def _esperar_y_contar_filas(self, analytics_frame: Frame, max_reintentos: int = 3) -> int:
        analytics_frame.wait_for_timeout(2000)
        filas = analytics_frame.locator(SELECTORS["action_rows"])
        for intento in range(max_reintentos):
            cantidad = filas.count()
            logger.info(f"Filas encontradas: {cantidad} (intento {intento + 1}/{max_reintentos})")
            if cantidad > 0:
                return cantidad
            analytics_frame.wait_for_timeout(1000)
        return 0

    def _convertir_duracion_segundos(self, dur_text: str) -> int:
        partes = [int(x) for x in dur_text.strip().split(":")]
        if len(partes) == 3:
            return partes[0] * 3600 + partes[1] * 60 + partes[2]
        if len(partes) == 2:
            return partes[0] * 60 + partes[1]
        return 0

    def ejecutar_descargas(self, solicitudes: List[SolicitudAudio], headless: bool = True) -> None:
        solicitudes = self.tracking_store.filtrar_no_procesados(solicitudes)
        if not solicitudes:
            logger.info("No hay solicitudes pendientes por procesar.")
            return

        logger.info(f"Iniciando descargas en Genesys Cloud para {len(solicitudes)} registro(s)...")

        with sync_playwright() as p:
            browser = None
            context = None
            page = None

            # 1. Verificar/Auto-lanzar Chrome CDP con perfil persistente (.chrome_genesys_profile)
            if self._lanzar_chrome_cdp_automatico():
                try:
                    browser = p.chromium.connect_over_cdp(self.cdp_url)
                    logger.info(f"Conectado exitosamente a Chrome vía CDP ({self.cdp_url})")
                    page = self._obtener_page_principal(browser)
                except Exception as e:
                    logger.warning(f"Error conectando vía CDP a Chrome: {e}")

            # 2. Si no se logró la conexión CDP, fallback a persistent context de Playwright
            if not page:
                user_data_dir = str(PROFILE_DIR)
                PROFILE_DIR.mkdir(parents=True, exist_ok=True)

                def _abrir_contexto(is_headless):
                    args = ["--start-maximized"] if not is_headless else []
                    try:
                        return p.chromium.launch_persistent_context(
                            user_data_dir=user_data_dir,
                            channel="chrome",
                            headless=is_headless,
                            args=args
                        )
                    except Exception:
                        return p.chromium.launch_persistent_context(
                            user_data_dir=user_data_dir,
                            headless=is_headless,
                            args=args
                        )

                context = _abrir_contexto(headless)
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(self.genesys_url)
                time.sleep(3)

            # 3. Detectar si la página está en pantalla de login de Microsoft / Genesys SSO
            if page:
                try:
                    current_url = page.url
                    if any(k in current_url for k in ["login", "accounts", "sso", "microsoftonline", "auth"]):
                        logger.info("🔑 Sesión no iniciada o token expirado en Microsoft/Genesys.")
                        logger.info("👉 Por favor complete el inicio de sesión en Chrome. (Tiempo de espera: 5 minutos)...")
                        start_time = time.time()
                        while time.time() - start_time < 300:
                            if page.is_closed():
                                logger.warning("Navegador cerrado por el usuario.")
                                return
                            try:
                                if "analytics/interactions" in page.url or "purecloud" in page.url:
                                    if not any(k in page.url for k in ["login", "sso", "microsoftonline"]):
                                        logger.info("✅ Login completado exitosamente. Sesión persistida.")
                                        break
                            except Exception:
                                pass
                            time.sleep(2)
                except Exception as e:
                    logger.debug(f"Error verificando redirección de login: {e}")

            if not page:
                for ctx in (browser.contexts if browser else [context] if context else []):
                    for pg in ctx.pages:
                        try:
                            if "purecloud" in pg.url or "genesys" in pg.url:
                                page = pg
                                break
                        except Exception:
                            continue
                    if page:
                        break

            if not page:
                logger.error("No se encontró sesión activa de Genesys Cloud.")
                return

            base_url = None
            if "/directory/#/" in page.url:
                base_url = page.url.split("/directory/#/")[0]

            for idx, sol in enumerate(solicitudes, 1):
                logger.info(f"--- Registro {idx}/{len(solicitudes)}: Promotor {sol.reg_ev} | DNI {sol.dni} ---")
                detalle_page = None
                try:
                    if idx > 1:
                        page = self._obtener_page_principal(browser)
                        if page and base_url:
                            try:
                                page.goto(f"{base_url}/directory/#/analytics/interactions", wait_until="networkidle")
                                page.wait_for_timeout(3000)
                            except Exception:
                                pass

                    page = self._obtener_page_principal(browser)
                    if not page:
                        logger.error("Pestaña de Interacciones no disponible. Abortando.")
                        break

                    self._cerrar_pestanas_admin_rezagadas(page)
                    page.wait_for_timeout(1000)

                    analytics_frame = self._localizar_iframe(page, SELECTORS["analytics_iframe_url"], max_intentos=15)
                    if not analytics_frame:
                        logger.warning("No se localizó iframe 'analytics-ui'. Saltando registro.")
                        continue

                    self._asegurar_panel_filtros_abierto(analytics_frame)
                    self._limpiar_filtros_si_hay(analytics_frame)

                    self._rellenar_filtro_usuario(analytics_frame, sol.reg_ev)
                    self._rellenar_filtro_dnis(analytics_frame, sol.telefonos)

                    analytics_frame.wait_for_timeout(3000)
                    cantidad = self._esperar_y_contar_filas(analytics_frame)

                    if cantidad == 0:
                        logger.warning(f"No encontrado en Genesys: {sol.reg_ev} - DNI {sol.dni}")
                        self.tracking_store.registrar_no_encontrado(sol.reg_ev, sol.dni)
                        self.tracking_store.marcar_como_procesado(sol.reg_ev, sol.dni, EstadoRegistro.NO_ENCONTRADO)
                        continue

                    try:
                        analytics_frame.locator(SELECTORS["loading_spinner"]).wait_for(state="hidden", timeout=15000)
                    except Exception:
                        pass

                    filas = analytics_frame.locator(SELECTORS["action_rows"])
                    total = filas.count()
                    candidatos = []
                    meses = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12}
                    fecha_fila = ""

                    for i in range(total):
                        try:
                            texto = filas.nth(i).inner_text()
                            if "ACEPTA CAMPANA" not in texto.upper() and "ACEPTA CAMPAÑA" not in texto.upper():
                                continue
                            candidatos.append(texto)
                            if len(candidatos) == 1:
                                m = re.search(r'(\d{1,2})\s+DE\s+([A-Z]+)\.?\s+DE\s+(\d{4})', texto.upper())
                                if m:
                                    dia, mes_txt, anio = m.group(1), m.group(2), m.group(3)
                                    mes_num = meses.get(mes_txt[:3], 1)
                                    fecha_fila = f"{anio}{mes_num:02d}{int(dia):02d}"
                        except Exception:
                            continue

                    if not candidatos:
                        logger.warning(f"No se hallaron filas con 'ACEPTA CAMPAÑA' para DNI {sol.dni}")
                        self.tracking_store.registrar_no_encontrado(sol.reg_ev, sol.dni)
                        self.tracking_store.marcar_como_procesado(sol.reg_ev, sol.dni, EstadoRegistro.NO_ENCONTRADO)
                        continue

                    nombre_archivo_base = sol.nombre_archivo
                    if fecha_fila and f"_{fecha_fila}" not in nombre_archivo_base:
                        nombre_archivo_base = f"{sol.prefijo}_{sol.reg_ev}_DNI{sol.dni}_{fecha_fila}"

                    for numero, texto_objetivo in enumerate(candidatos, start=1):
                        logger.info(f"Procesando interacción ACEPTA {numero}/{len(candidatos)}...")
                        detalle_page = None
                        try:
                            analytics_frame = self._localizar_iframe(page, SELECTORS["analytics_iframe_url"], max_intentos=5)
                            filas = analytics_frame.locator(SELECTORS["action_rows"])
                            fila_real = None
                            for i in range(filas.count()):
                                if filas.nth(i).inner_text() == texto_objetivo:
                                    fila_real = i
                                    break
                            if fila_real is None:
                                continue

                            filas.nth(fila_real).click(force=True, timeout=15000)

                            for _ in range(15):
                                admin_pages = [pg for pg in page.context.pages if "/admin" in pg.url]
                                if admin_pages:
                                    detalle_page = admin_pages[-1]
                                    break
                                page.wait_for_timeout(1000)

                            if not detalle_page:
                                logger.error("No se abrió pestaña de detalles.")
                                continue

                            frame_detalle = self._localizar_iframe(detalle_page, SELECTORS["details_iframe_url"])
                            if not frame_detalle:
                                logger.error("No se cargó iframe de detalles.")
                                continue

                            frame_detalle.locator(SELECTORS["duration_container"]).wait_for(state="visible", timeout=TIMEOUT_DETAILS_LOAD)

                            total_audios = 1
                            pager = frame_detalle.locator(SELECTORS["pager_count"])
                            if pager.count() > 0:
                                match = re.search(r'(\d+)\s+de\s+(\d+)', pager.inner_text())
                                if match:
                                    total_audios = int(match.group(2))

                            mejor_idx = 0
                            mejor_dur = -1

                            if total_audios > 1:
                                for _ in range(15):
                                    try:
                                        if frame_detalle.locator(SELECTORS["pager_count"]).inner_text().startswith("1 de"):
                                            break
                                        frame_detalle.locator(SELECTORS["prev_recording_btn"]).click(force=True)
                                        frame_detalle.wait_for_timeout(800)
                                    except Exception:
                                        break

                                for idx_a in range(total_audios):
                                    if idx_a > 0:
                                        frame_detalle.locator(SELECTORS["next_recording_btn"]).click(force=True)
                                        frame_detalle.wait_for_timeout(1500)
                                    try:
                                        d = frame_detalle.locator(SELECTORS["duration_container"]).inner_text()
                                        s = self._convertir_duracion_segundos(d)
                                        if s > mejor_dur:
                                            mejor_dur = s
                                            mejor_idx = idx_a
                                    except Exception:
                                        pass

                                for _ in range(15):
                                    try:
                                        if frame_detalle.locator(SELECTORS["pager_count"]).inner_text().startswith("1 de"):
                                            break
                                        frame_detalle.locator(SELECTORS["prev_recording_btn"]).click(force=True)
                                        frame_detalle.wait_for_timeout(800)
                                    except Exception:
                                        break

                                for _ in range(mejor_idx):
                                    frame_detalle.locator(SELECTORS["next_recording_btn"]).click(force=True)
                                    frame_detalle.wait_for_timeout(1500)

                            nombre_audio = f"{nombre_archivo_base}_P{numero:02d}" if len(candidatos) > 1 else nombre_archivo_base

                            frame_detalle.locator(SELECTORS["download_trigger_btn"]).click(force=True)
                            frame_detalle.wait_for_timeout(2000)
                            frame_detalle.locator(SELECTORS["filename_input"]).wait_for(state="visible", timeout=5000)
                            frame_detalle.locator(SELECTORS["filename_input"]).clear()
                            frame_detalle.locator(SELECTORS["filename_input"]).fill(nombre_audio)
                            frame_detalle.wait_for_timeout(1000)

                            op_desplegable = frame_detalle.locator(SELECTORS["format_dropdown"])
                            if op_desplegable.count() > 0 and op_desplegable.is_visible():
                                op_desplegable.click(force=True)
                                frame_detalle.wait_for_timeout(1000)
                                frame_detalle.locator(SELECTORS["format_mp3_option"]).evaluate("el => el.click()")
                                frame_detalle.wait_for_timeout(1000)

                            archivo_mp3 = DOWNLOADS_DIR / f"{nombre_audio}.mp3"
                            if archivo_mp3.exists():
                                archivo_mp3.unlink()

                            try:
                                with detalle_page.expect_download(timeout=60000) as dl_info:
                                    frame_detalle.locator(SELECTORS["confirm_download_btn"]).click(force=True)
                                    logger.info(f"Descargando audio {mejor_idx+1}/{total_audios}...")
                                download = dl_info.value
                                download.save_as(str(archivo_mp3))
                                logger.info(f"✓ MP3 guardado exitosamente: {archivo_mp3}")
                            except Exception as e:
                                logger.error(f"Error guardando descarga de audio: {e}")

                        except Exception as e:
                            logger.error(f"Error procesando fila ACEPTA {numero}: {e}")
                        finally:
                            if detalle_page and detalle_page != page:
                                try:
                                    detalle_page.close()
                                except Exception:
                                    pass

                    self.tracking_store.marcar_como_procesado(sol.reg_ev, sol.dni, EstadoRegistro.DESCARGADO)
                    logger.info(f"✓ Solicitud Promotor {sol.reg_ev} | DNI {sol.dni} completada.")

                except Exception as rec_err:
                    logger.error(f"❌ Error procesando registro {sol.reg_ev} | DNI {sol.dni}: {rec_err}")

            logger.info("Proceso de descargas finalizado correctamente.")
