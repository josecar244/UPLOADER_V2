import html
import os
import re
import tempfile
from pathlib import Path
from typing import List, Tuple
import pandas as pd

from genesys_bot.logger import get_logger
from genesys_bot.models import SolicitudAudio

logger = get_logger("OutlookService")


def _determinar_prefijo_asunto(subject: str) -> str:
    s = (subject or "").lower()
    if any(kw in s for kw in ["extracash", "ec"]):
        return "EC"
    if any(kw in s for kw in ["convenio", "convenios", "cc"]):
        return "CC"
    if any(kw in s for kw in ["seguro", "seguros", "seg"]):
        return "SEG"
    if any(kw in s for kw in ["hipotecario", "hipoteca", "hip"]):
        return "HIP"
    if any(kw in s for kw in ["préstamo", "prestamo", "pp"]):
        return "PP"
    if any(kw in s for kw in ["tarjeta", "tc"]):
        return "TC"
    return "AUDIO"


class OutlookService:
    def __init__(self, asunto_filtro: str = "Solicitud de audio"):
        self.asunto_filtro = asunto_filtro

    def _normalizar_dataframe(self, df: pd.DataFrame, prefijo: str = "AUDIO") -> List[SolicitudAudio]:
        solicitudes = []
        df.columns = df.columns.str.strip().str.lower()

        col_dni = None
        col_promotor = None
        col_fecha = None

        for col in df.columns:
            col_lower = str(col).lower()
            if 'dni' in col_lower or 'coddoc' in col_lower:
                col_dni = col
            if 'promotor' in col_lower and 'cd' in col_lower:
                col_promotor = col
            if 'fecha' in col_lower or 'desembolso' in col_lower:
                col_fecha = col

        if not col_dni or not col_promotor:
            # Búsqueda por celdas en cada fila
            for _, row in df.iterrows():
                reg_ev, dni, fecha_str = None, None, ""
                for cell in row.values:
                    if pd.isna(cell):
                        continue
                    cell_str = str(cell).strip()

                    if not reg_ev:
                        match_reg = re.search(r'\b(B\d{5})\b', cell_str, re.IGNORECASE)
                        if match_reg:
                            reg_ev = match_reg.group(1).upper()

                    if not dni:
                        match_dni = re.search(r'\b(\d{7,8})\b', cell_str)
                        if match_dni:
                            dni = match_dni.group(1).zfill(8)

                    if not fecha_str:
                        f_match = re.search(r'\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b', cell_str)
                        if f_match:
                            d, m, y = f_match.groups()
                            if len(y) == 2:
                                y = "20" + y
                            fecha_str = f"{y}{m.zfill(2)}{d.zfill(2)}"

                if reg_ev and dni:
                    nombre_archivo = f"{prefijo}_{reg_ev}_DNI{dni}"
                    if fecha_str:
                        nombre_archivo += f"_{fecha_str}"
                    solicitudes.append(SolicitudAudio(reg_ev=reg_ev, dni=dni, nombre_archivo=nombre_archivo, prefijo=prefijo))
        else:
            # Extracción estructurada por columnas
            for _, row in df.iterrows():
                try:
                    promotor_val = str(row[col_promotor]).strip()
                    dni_val = str(row[col_dni]).strip()

                    match_reg = re.search(r'\b(B\d{5})\b', promotor_val, re.IGNORECASE)
                    if not match_reg:
                        continue
                    reg_ev = match_reg.group(1).upper()

                    match_dni = re.search(r'\b(\d{7,8})\b', dni_val)
                    if not match_dni:
                        continue
                    dni = match_dni.group(1).zfill(8)

                    fecha_str = ""
                    if col_fecha:
                        try:
                            fecha_val = str(row[col_fecha]).strip()
                            f_match = re.search(r'\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b', fecha_val)
                            if f_match:
                                d, m, y = f_match.groups()
                                if len(y) == 2:
                                    y = "20" + y
                                fecha_str = f"{y}{m.zfill(2)}{d.zfill(2)}"
                        except Exception:
                            pass

                    nombre_archivo = f"{prefijo}_{reg_ev}_DNI{dni}"
                    if fecha_str:
                        nombre_archivo += f"_{fecha_str}"
                    solicitudes.append(SolicitudAudio(reg_ev=reg_ev, dni=dni, nombre_archivo=nombre_archivo, prefijo=prefijo))
                except Exception as e:
                    logger.debug(f"Error procesando fila de tabla: {e}")
                    continue

        return solicitudes

    def _normalizar_texto(self, texto: str) -> str:
        if not texto:
            return ""
        texto = re.sub(r"<br\s*/?>", "\n", texto, flags=re.IGNORECASE)
        texto = re.sub(r"<[^>]+>", " ", texto)
        texto = html.unescape(texto)
        return re.sub(r"\s+", " ", texto).strip()

    def _extraer_registros_desde_texto(self, texto: str, prefijo: str = "AUDIO") -> List[SolicitudAudio]:
        solicitudes = []
        if not texto:
            return solicitudes

        texto_limpio = self._normalizar_texto(texto)
        if not texto_limpio:
            return solicitudes

        for match in re.finditer(r"\b(B\d{5})\b", texto_limpio, flags=re.IGNORECASE):
            reg_ev = match.group(1).upper()
            bcode_pos = match.start()
            inicio = max(0, bcode_pos - 120)
            fin = min(len(texto_limpio), match.end() + 200)
            ventana = texto_limpio[inicio:fin]

            dni_match = re.search(r"\b(?:DNI|dni)\s*[:#-]?\s*(\d{8})\b", ventana)
            if not dni_match:
                dni_matches = list(re.finditer(r"\b(\d{8})\b", ventana))
                if not dni_matches:
                    continue
                mejor_dni = None
                mejor_dist = float("inf")
                for m in dni_matches:
                    dni_pos_abs = inicio + m.start()
                    dist = abs(dni_pos_abs - bcode_pos)
                    if dist < mejor_dist:
                        mejor_dist = dist
                        mejor_dni = m.group(1)
                dni = mejor_dni
            else:
                dni = dni_match.group(1)

            if dni:
                nombre_archivo = f"{prefijo}_{reg_ev}_DNI{dni}"
                solicitudes.append(SolicitudAudio(reg_ev=reg_ev, dni=dni, nombre_archivo=nombre_archivo, prefijo=prefijo))

        return solicitudes

    def parsear_cuerpo_html(self, html_body: str, prefijo: str = "AUDIO") -> List[SolicitudAudio]:
        solicitudes = []
        try:
            dfs = pd.read_html(html_body)
            for df in dfs:
                regs = self._normalizar_dataframe(df, prefijo=prefijo)
                if regs:
                    solicitudes.extend(regs)
        except Exception:
            pass

        if not solicitudes:
            solicitudes.extend(self._extraer_registros_desde_texto(html_body, prefijo=prefijo))

        return solicitudes

    def parsear_adjuntos(self, mail_item, temp_dir: str, prefijo: str = "AUDIO") -> List[SolicitudAudio]:
        solicitudes = []
        if not getattr(mail_item, "Attachments", None):
            return solicitudes

        for att in mail_item.Attachments:
            filename = getattr(att, "FileName", "") or ""
            filename_lower = filename.lower()
            if not filename_lower:
                continue

            file_path = os.path.join(temp_dir, filename)
            try:
                if filename_lower.endswith(('.xlsx', '.xls', '.xlsm')):
                    att.SaveAsFile(file_path)
                    df = pd.read_excel(file_path)
                    regs = self._normalizar_dataframe(df, prefijo=prefijo)
                    solicitudes.extend(regs)
                elif filename_lower.endswith(('.csv', '.txt', '.html', '.htm')):
                    att.SaveAsFile(file_path)
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                        regs = self._extraer_registros_desde_texto(fh.read(), prefijo=prefijo)
                    solicitudes.extend(regs)
            except Exception as e:
                logger.error(f"Error procesando adjunto '{filename}': {e}")
            finally:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass

        return solicitudes

    def obtener_ultimos_correos(self, limit: int = 3) -> List[dict]:
        try:
            import win32com.client
        except ImportError:
            raise ImportError("Se requiere pywin32. Instalar con: pip install pywin32")

        logger.info(f"Buscando los últimos {limit} correos en Outlook...")
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        inbox = outlook.GetDefaultFolder(6)

        correos_info = []

        try:
            messages = inbox.Items
            messages.Sort("[ReceivedTime]", True)

            filt = f'@SQL="urn:schemas:httpmail:subject" LIKE \'%{self.asunto_filtro}%\''
            try:
                filtered_messages = messages.Restrict(filt)
                if filtered_messages.Count > 0:
                    messages = filtered_messages
            except Exception:
                pass

            count = 0
            with tempfile.TemporaryDirectory() as temp_dir:
                for item in messages:
                    try:
                        subject = getattr(item, "Subject", "") or ""
                        if self.asunto_filtro.lower() in subject.lower():
                            sender = getattr(item, "SenderName", "Desconocido") or "Desconocido"
                            received_time = getattr(item, "ReceivedTime", None)
                            fecha_str = str(received_time)[:19] if received_time else "Sin fecha"
                            prefijo = _determinar_prefijo_asunto(subject)

                            cuerpo = getattr(item, "HTMLBody", "") or getattr(item, "Body", "") or ""
                            regs = []
                            if cuerpo:
                                regs = self.parsear_cuerpo_html(cuerpo, prefijo=prefijo)

                            if not regs:
                                regs = self.parsear_adjuntos(item, temp_dir, prefijo=prefijo)

                            correos_info.append({
                                "index": count + 1,
                                "asunto": subject,
                                "remitente": sender,
                                "fecha": fecha_str,
                                "solicitudes": regs,
                                "cant_registros": len(regs)
                            })

                            count += 1
                            if count >= limit:
                                break
                    except Exception as e:
                        logger.error(f"Error procesando correo en vista previa: {e}")
                        continue
        except Exception as e:
            logger.error(f"Error accediendo a la bandeja de entrada: {e}")

        return correos_info

    def obtener_solicitudes(self, solo_ultimo: bool = True) -> List[SolicitudAudio]:
        try:
            import win32com.client
        except ImportError:
            raise ImportError("Se requiere pywin32. Instalar com: pip install pywin32")

        logger.info("Conectando a Outlook Desktop MAPI...")
        outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        inbox = outlook.GetDefaultFolder(6)

        correos_encontrados = []

        try:
            messages = inbox.Items
            messages.Sort("[ReceivedTime]", True)

            # Aplicar Restrict si es posible
            filt = f'@SQL="urn:schemas:httpmail:subject" LIKE \'%{self.asunto_filtro}%\''
            try:
                filtered_messages = messages.Restrict(filt)
                if filtered_messages.Count > 0:
                    messages = filtered_messages
            except Exception:
                pass  # Fallback a iterar messages ordenados

            for item in messages:
                try:
                    subject = getattr(item, "Subject", "") or ""
                    if self.asunto_filtro.lower() in subject.lower():
                        received_time = getattr(item, "ReceivedTime", None)
                        correos_encontrados.append((item, subject, received_time))
                        if solo_ultimo and len(correos_encontrados) >= 1:
                            break
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Error accediendo a la bandeja de entrada: {e}")

        if not correos_encontrados:
            logger.warning(f"No se encontraron correos con asunto '{self.asunto_filtro}'")
            return []

        todas_las_solicitudes: List[SolicitudAudio] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            for idx, (item, subject, _) in enumerate(correos_encontrados, 1):
                logger.info(f"Procesando correo ({idx}): '{subject}'")
                prefijo = _determinar_prefijo_asunto(subject)

                cuerpo = getattr(item, "HTMLBody", "") or getattr(item, "Body", "") or ""
                regs = []
                if cuerpo:
                    regs = self.parsear_cuerpo_html(cuerpo, prefijo=prefijo)

                if not regs:
                    regs = self.parsear_adjuntos(item, temp_dir, prefijo=prefijo)

                todas_las_solicitudes.extend(regs)

        # Eliminar duplicados manteniendo orden
        vistas: Dict[str, SolicitudAudio] = {}
        for sol in todas_las_solicitudes:
            if sol.clave_unica not in vistas:
                vistas[sol.clave_unica] = sol

        resultado = list(vistas.values())
        logger.info(f"Solicitudes extraídas de Outlook: {len(resultado)}")
        return resultado
