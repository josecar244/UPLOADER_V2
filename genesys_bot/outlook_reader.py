import html
import os
import re
import tempfile
import pandas as pd


def _normalizar_dataframe(df, prefijo="AUDIO"):
    registros = []
    
    # Normalizar nombres de columnas
    df.columns = df.columns.str.strip().str.lower()
    
    # Identificar qué columnas contienen DNI y Promotor
    col_dni = None
    col_promotor = None
    col_fecha = None
    
    for col in df.columns:
        col_lower = col.lower()
        if 'dni' in col_lower or 'coddoc' in col_lower:
            col_dni = col
        if 'promotor' in col_lower and 'cd' in col_lower:
            col_promotor = col
        if 'fecha' in col_lower or 'desembolso' in col_lower:
            col_fecha = col
    
    # Si no encontró las columnas, volver a búsqueda genérica
    if not col_dni or not col_promotor:
        for _, row in df.iterrows():
            reg_ev = None
            dni = None
            fecha_str = ""

            for cell in row.values:
                if pd.isna(cell):
                    continue
                cell_str = str(cell).strip()

                if not reg_ev:
                    match_reg = re.search(r'\b(B\d{5})\b', cell_str, re.IGNORECASE)
                    if match_reg:
                        reg_ev = match_reg.group(1).upper()

                if not dni:
                    match_dni = re.search(r'\b(\d{8})\b', cell_str)
                    if match_dni:
                        dni = match_dni.group(1)

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
                registros.append((reg_ev, dni, nombre_archivo))
    else:
        # Extracción por columnas específicas
        for _, row in df.iterrows():
            try:
                promotor_val = str(row[col_promotor]).strip()
                dni_val = str(row[col_dni]).strip()
                
                # Buscar B-code en la columna de promotor
                match_reg = re.search(r'\b(B\d{5})\b', promotor_val, re.IGNORECASE)
                if not match_reg:
                    continue
                reg_ev = match_reg.group(1).upper()
                
                # Extraer DNI (8 dígitos)
                match_dni = re.search(r'\b(\d{8})\b', dni_val)
                if not match_dni:
                    continue
                dni = match_dni.group(1)
                
                # Fecha (opcional)
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
                registros.append((reg_ev, dni, nombre_archivo))
            except Exception as e:
                print(f"Error normalizando fila: {e}")
                continue

    return registros

def _normalizar_texto(texto):
    if not texto:
        return ""
    texto = re.sub(r"<br\s*/?>", "\n", texto, flags=re.IGNORECASE)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = html.unescape(texto)
    return re.sub(r"\s+", " ", texto).strip()


def _determinar_prefijo_asunto(subject):
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


def _coincide_asunto(subject, asunto_filtro="Solicitud de audio"):
    if not subject:
        return False

    subject_norm = re.sub(r"[^a-z0-9]+", " ", subject.lower()).strip()
    filtro_norm = re.sub(r"[^a-z0-9]+", " ", asunto_filtro.lower()).strip()

    if not filtro_norm:
        return True

    if filtro_norm in subject_norm:
        return True

    tokens = [token for token in filtro_norm.split() if len(token) > 2]
    return bool(tokens) and all(token in subject_norm for token in tokens)


def _extraer_registros_desde_texto(texto, prefijo="AUDIO"):
    registros = []
    if not texto:
        return registros

    texto_limpio = _normalizar_texto(texto)
    if not texto_limpio:
        return registros

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
            # Elegir el DNI más cercano al B-code (por posición absoluta)
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

        nombre_archivo = f"{prefijo}_{reg_ev}_DNI{dni}"
        registros.append((reg_ev, dni, nombre_archivo))

    return registros


def parsear_cuerpo_html(html_body, prefijo="AUDIO"):
    registros = []
    try:
        dfs = pd.read_html(html_body)
        for df in dfs:
            regs = _normalizar_dataframe(df, prefijo=prefijo)
            if regs:
                registros.extend(regs)
    except Exception:
        pass

    if not registros:
        registros.extend(_extraer_registros_desde_texto(html_body, prefijo=prefijo))

    return registros


def parsear_adjunto_excel(mail_item, temp_dir, prefijo="AUDIO"):
    registros = []
    for att in mail_item.Attachments:
        filename = getattr(att, "FileName", "") or ""
        filename_lower = filename.lower()
        if not filename_lower:
            continue

        if filename_lower.endswith(('.xlsx', '.xls', '.xlsm')):
            file_path = os.path.join(temp_dir, filename)
            att.SaveAsFile(file_path)
            try:
                df = pd.read_excel(file_path)
                regs = _normalizar_dataframe(df, prefijo=prefijo)
                registros.extend(regs)
            except Exception as e:
                print(f"Error procesando adjunto {filename}: {e}")
            finally:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
        elif filename_lower.endswith(('.csv', '.txt', '.html', '.htm')):
            file_path = os.path.join(temp_dir, filename)
            att.SaveAsFile(file_path)
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                    regs = _extraer_registros_desde_texto(fh.read(), prefijo=prefijo)
                registros.extend(regs)
            except Exception as e:
                print(f"Error procesando adjunto {filename}: {e}")
            finally:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
    return registros


def obtener_solicitudes_outlook(asunto_filtro="Solicitud de audio", solo_ultimo=True):
    try:
        import win32com.client
    except ImportError:
        raise ImportError("Se requiere la librería 'pywin32'. Instálala con: pip install pywin32")

    outlook = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
    inbox = outlook.GetDefaultFolder(6)

    carpetas = []

    def _recorrer_carpeta(folder):
        carpetas.append(folder)
        for subfolder in folder.Folders:
            _recorrer_carpeta(subfolder)

    _recorrer_carpeta(inbox)

    # Recopilar todos los correos que coinciden, con su fecha
    correos_encontrados = []

    for folder in carpetas:
        try:
            messages = folder.Items
            messages.Sort("[ReceivedTime]", True)
        except Exception:
            continue

        for item in messages:
            try:
                subject = getattr(item, 'Subject', '') or ''
                if not _coincide_asunto(subject, asunto_filtro):
                    continue

                received_time = getattr(item, 'ReceivedTime', None)
                correos_encontrados.append((item, subject, folder.Name, received_time))
            except Exception:
                continue

    # Si no hay correos, retornar lista vacía
    if not correos_encontrados:
        print(f"No se encontraron correos con asunto '{asunto_filtro}'")
        return []

    # Si solo_ultimo=True, procesar solo el último; si False, procesar todos
    if solo_ultimo:
        correos_a_procesar = [correos_encontrados[0]]  # El primero es el más reciente (ordenado por ReceivedTime DESC)
    else:
        correos_a_procesar = correos_encontrados

    todos_los_registros = []

    with tempfile.TemporaryDirectory() as temp_dir:
        for idx, (item, subject, folder_name, _) in enumerate(correos_a_procesar, 1):
            print(f"Procesando correo ({idx}): {subject} | Carpeta: {folder_name}")

            prefijo = _determinar_prefijo_asunto(subject)

            cuerpo = getattr(item, 'HTMLBody', '') or getattr(item, 'Body', '') or ''
            regs = []
            if cuerpo:
                regs = parsear_cuerpo_html(cuerpo, prefijo=prefijo)

            if not regs and getattr(item, 'Attachments', None) and item.Attachments.Count > 0:
                regs = parsear_adjunto_excel(item, temp_dir, prefijo=prefijo)

            if not regs:
                print(f"No se encontraron registros parseables en el correo: {subject}")

            todos_los_registros.extend(regs)

    vista_unica = list(dict.fromkeys(todos_los_registros))
    return vista_unica

if __name__ == "__main__":
    print("Buscando correos en Outlook...")
    solicitudes = obtener_solicitudes_outlook()
    print(f"Registros encontrados ({len(solicitudes)}):")
    for reg in solicitudes:
        print(reg)
