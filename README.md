#⚡ Cargador de Datos: De Excel a Teradata (Optimizado)

Este proyecto es una aplicación web interactiva en **Streamlit** que permite subir archivos de datos (Excel, CSV o Texto tabulado), configurar sus tipos de datos y cargarlos masivamente en una base de datos **Teradata** de forma ultra rápida y segura.

---

## 📋 Tabla de Contenidos
1. [Cómo Funciona (Flujo General)](#-cómo-funciona-flujo-general)
2. [Orquestación Automática (Calidad Insumos PBI)](#-orquestación-automática-calidad-insumos-pbi)
3. [Requisitos e Instalación](#-requisitos-e-instalación)
4. [Cómo Ejecutar](#-cómo-ejecutar-la-aplicación)
5. [Estructura del Código](#-estructura-del-código-modular)
6. [Algoritmos Principales](#-algoritmos-principales)
7. [Protección contra Cargas Duplicadas](#-protección-contra-cargas-duplicadas)
8. [Archivos a Trasladar](#-archivos-que-debes-trasladar)

---

## 🔄 Cómo Funciona (Flujo General)

El programa ejecuta los siguientes pasos cuando cargas un archivo:

```
1. SUBIR ARCHIVO
   ↓
2. LEER ARCHIVO (Excel/CSV/TXT)
   ↓
3. VISTA PREVIA (primeras 5 filas)
   ↓
4. CONFIGURAR COLUMNAS (seleccionar, renombrar, tipos de datos)
   ↓
5. INGRESAR CREDENCIALES DE TERADATA
   ↓
6. SELECCIONAR ACCIÓN (Insertar o Limpiar + Insertar)
   ↓
7. PROCESAR DATOS
   ├─ Limpiar valores (acentos, caracteres especiales)
   ├─ Convertir tipos de datos
   ├─ Validar nombres de columnas
   ↓
8. CONECTAR A TERADATA
   ↓
9. CARGAR DATOS
   ├─ Si tabla vacía → Usar FastLoad (ultra rápido)
   ├─ Si tabla con datos → Inserción estándar en lotes
   ↓
10. MARCAR ARCHIVO COMO CARGADO
    (Evita cargas duplicadas)
```

---

## ⚡ Orquestación Automática (Calidad Insumos PBI)

Para automatizar la actualización mensual o recurrente de la base de consumo del PBI de Calidad, la aplicación dispone de una pestaña dedicada a la orquestación de extremo a extremo:

1. **Credenciales Centralizadas:** Permite ingresar tanto las credenciales del portal **Insight** (para descargas de reportes) como las de **Teradata** (para cargas).
2. **Descarga Masiva de 7 Insumos:**
   - La aplicación consulta el servicio web de Insight y descarga automáticamente las consultas parametrizadas:
     * `TRAFICO_GENESYS` ➔ `DLAB_GEC.M_EXP_TRAFICO_GENESIS`
     * `CONV_ATTRIBUTES` ➔ `DLAB_GEC.M_EXP_BT_CONVERSATIONS_ATTRIBUTES`
     * `DERIVA_BT` ➔ `DLAB_GEC.M_EXP_DERIVA_BT_TIEMPOS`
     * `CLOUD_MARCA_TRANSF` ➔ `DLAB_GEC.M_EXP_CO_CLOUD_MARCA_TRASNFERENCIA_PRE`
     * `BT_TRANSFERENCIA` ➔ `DLAB_GEC.M_DERIVA_BT_EV_TRANSFERENCIA`
     * `IVR_VENTAS` ➔ `DLAB_GEC.M_EXP_IVR_VENTAS_2022`
     * `EVALUATIONS` ➔ Se carga en `DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE` (sirviendo de entrada única para transformaciones SQL de Calidad y el proceso NTD).
3. **Limpieza e Ingesta Directa:** Los archivos `.txt` descargados se leen con Polars, se formatean y limpian según el mapeo de `plantillas.json`, y se cargan limpios a Teradata (Delete + FastLoad).
4. **Estado Interactivo:** Un panel visual muestra el estado de avance en tiempo real.

---

## 🛠️ Requisitos e Instalación

Para ejecutar la aplicación, necesitas instalar las siguientes librerías en tu entorno de Python:

```bash
# 1. Crear un entorno virtual (opcional pero recomendado)
python -m venv .venv
.venv\Scripts\activate   # En Windows

# 2. Instalar las dependencias
pip install streamlit polars python-calamine pyarrow teradatasql
```

### Detalle de Librerías Utilizadas:
- **`streamlit`**: Crea la interfaz web interactiva.
- **`polars`**: Motor de datos en Rust (~30x más rápido que Pandas). Realiza todas las transformaciones vectorizadas.
- **`python-calamine`**: Lee archivos Excel ultra rápido (escrito en Rust).
- **`pyarrow`**: Lectura multihilo de archivos CSV y TXT.
- **`teradatasql`**: Driver oficial de Teradata para Python.
- **`python-dotenv`**: Carga variables de entorno desde archivo `.env`.

---

## 🚀 Cómo Ejecutar la Aplicación

Una vez instaladas las dependencias:

```bash
streamlit run index.py
```

Se abrirá automáticamente tu navegador en `http://localhost:8501` con la interfaz del cargador.

---

## 🏗️ Estructura del Código (Modular)

```
UPLOADER_V2/
├── index.py                              # Orquestador principal (flujo Streamlit)
├── requirements.txt                      # Dependencias del proyecto
├── README.md                             # Este archivo
│
├── core/                                 # Motores de procesamiento
│   ├── readers.py                       # Lectores de archivos (Excel/CSV/TXT)
│   ├── cleaners.py                      # Limpieza y transformación de datos
│   ├── database.py                      # Conexión y carga a Teradata
│   ├── orchestrator.py                  # Orquestador de descarga e ingesta automática
│   └── logging_config.py                # Configuración de logs
│
├── ui/                                   # Interfaz de usuario
│   └── components.py                    # Componentes Streamlit reutilizables
│
├── appsFiles/excelToTeraFiles/
│   ├── credenciales.json                # Credenciales de Teradata (opcional)
│   └── plantillas.json                  # Plantillas de mapeo de columnas
│
├── logs/                                 # Logs de ejecución
└── tests/                                # Suite de pruebas
    └── test_logging_config.py
```

---

## 🔧 Algoritmos Principales

### 1️⃣ **ALGORITMO DE LECTURA DE ARCHIVOS** (`core/readers.py`)

#### Para archivos **Excel** (.xlsx, .xls):
```python
# Intenta usar plantilla especial P001-CALIDAD_SA si está seleccionada
SI selected_template == "P001-CALIDAD_SA":
    └─ Leer con openpyxl (preserva encabezados exactos en fila 28)
SINO:
    └─ Leer con calamine (motor ultra rápido en Rust)
```

**Por qué dos métodos?** Algunas plantillas tienen encabezados en filas específicas que calamine omite automáticamente.

#### Para archivos **CSV**:
```
Usar motor multihilo de Polars:
├─ Lee en paralelo (múltiples threads)
├─ Detecta automáticamente separadores
└─ Convierte a Polars DataFrame
```

#### Para archivos **TXT** (tabulados):
```
Usar Polars con separador = TAB:
├─ Lee como CSV pero con \t como delimitador
├─ Soporta UTF-8 y UTF-8-BOM automáticamente
└─ Convierte a Polars DataFrame
```

---

### 2️⃣ **ALGORITMO DE LIMPIEZA DE DATOS** (`core/cleaners.py`)

Este es el "corazón" del procesamiento. Utiliza **expresiones vectorizadas de Polars** para máxima velocidad.

#### Paso A: **Sanitización de nombres de columnas**
```
ENTRADA: "Nombre % Especial@123"
└─ Reemplazar caracteres no alfanuméricos por _
└─ Eliminar _ al inicio y final
└─ Si empieza en número, prefijo _
SALIDA: "nombre_especial_123"
```

**¿Por qué?** Previene inyección SQL y cumple con reglas de nombres Teradata.

#### Paso B: **Conversión de tipos de datos**
```
Polars dtype → SQL Teradata:
├─ Int8/16/32/64, UInt8/16/32/64 → INTEGER
├─ Float32/64 → FLOAT
├─ Date/Datetime/Time → TIMESTAMP
├─ Boolean → CHAR(1)
└─ String/Otros → VARCHAR(255)
```

#### Paso C: **Transformación de nulos**
```
SI col_dict['convert_nulls'] == True:
   └─ NULL → 0
   └─ NOT NULL → 1
   (Útil para columnas booleanas de control)
```

#### Paso D: **Limpieza de strings (VARCHAR)**
```
PARA cada columna de tipo VARCHAR/CHAR:

1. Convertir a String:
   └─ Cualquier tipo → str(valor)

2. Mapear literales nulos a NULL real:
   └─ "nan", "none", "<na>", "null" → NULL

3. SI transformar_varchar_latin == True:
   └─ Eliminar caracteres fuera de Latin-1 (emojis, etc.)
   └─ Truncar a max_len_varchar caracteres

4. SI convertir_sin_acentos == True:
   └─ Normalizar a NFKD: á → a, ñ → n, etc.

5. SI tipo == CHAR(1):
   └─ Mantener solo primer carácter
```

**Ejemplo completo:**
```
Entrada: "Café ☺️ LATÍN"
Sin acentos + sin emojis: "Cafe LATIN"
CHAR(1): "C"
```

#### Paso E: **Conversión de tipos numéricos**
```
Para INTEGER:
└─ Intentar cast a Int64 sin error si falla

Para FLOAT:
└─ Intentar cast a Float64 sin error si falla
```

#### Paso F: **Conversión de fechas**
```
SI tipo == TIMESTAMP:
   └─ SI es texto: str.to_datetime(strict=False)
   └─ SI es numérico: cast a Datetime

SI tipo == DATE:
   └─ SI es texto: str.to_date(strict=False)
   └─ SI es numérico: cast a Date
```

**Ventaja**: Se usan **expresiones Polars vectorizadas**, no loops. ¡Hasta 100x más rápido!

---

### 3️⃣ **ALGORITMO DE CARGA EN TERADATA** (`core/database.py`)

#### Paso 1: **Verificar credenciales**
```
Buscar en este orden:
1. Variables de entorno (.env file)
   ├─ TERADATA_USER
   ├─ TERADATA_PASSWORD
   ├─ TERADATA_HOST
   └─ TERADATA_LOGMECH

2. Archivo JSON (appsFiles/excelToTeraFiles/credenciales.json)
   ├─ teradata_user
   ├─ teradata_password
   ├─ teradata_host (default: IBKTD)
   └─ teradata_logmech (default: TD2)

3. Fallback: {}
```

#### Paso 2: **Conectar a Teradata**
```
teradatasql.connect(
    host=host,           # IBKTD
    user=user,           # Tu usuario
    password=password,   # Tu contraseña
    logmech=logmech      # TD2 (mecanismo de autenticación)
)
```

#### Paso 3: **Verificar si tabla existe**
```
SELECT TOP 1 * FROM {table_name}
├─ SI existe: use its structure
└─ SI no existe: create it from column config
```

#### Paso 4: **Crear tabla si no existe**
```
CREATE MULTISET TABLE {table_name} (
    "col1" VARCHAR(255),
    "col2" INTEGER,
    "col3" FLOAT,
    ...
);
```

**¿MULTISET?** Permite filas duplicadas (estándar en Teradata).

#### Paso 5: **Alinear columnas DataFrame con tabla**
```
PARA cada columna en tabla:
  SI existe en DataFrame:
    └─ Usarla
  SINO:
    └─ Insertar NULL
```

**¿Por qué?** El archivo puede tener menos columnas que la tabla.

#### Paso 6: **Extraer datos como tuplas**
```
df.rows() → [(val1, val2, ...), (val1, val2, ...), ...]

Esto es ultra rápido en Polars (optimizado en Rust)
```

#### Paso 7: **Elegir método de carga**

```
SI (clear_table == True) O (tabla no existe):
    ├─ Usar TERADATA FASTLOAD
    │  └─ Protocolo ultra optimizado para cargas vacías
    │  └─ Velocidad: ~100,000 filas/segundo
    │  └─ SI falla: fallback a inserción estándar
    │
SINO:
    └─ Usar INSERCIÓN ESTÁNDAR EN LOTES
       └─ Tamaño lote: 50,000 filas
       └─ Velocidad: ~10,000 filas/segundo
```

#### **FastLoad - Configuración especial:**
```python
cur.execute("{fn teradata_nativesql}{fn teradata_autocommit_off}")
fastload_insert = f"{{fn teradata_require_fastload}}{INSERT...}"
cur.executemany(fastload_insert, data)
con.commit()
```

#### **Inserción Estándar - Lotes:**
```python
batch_size = 50000
PARA cada lote de 50,000 filas:
    cur.executemany(INSERT_query, batch)
    Mostrar progreso: "Inserción: 1-50,000 de 1,000,000"
```

---

## 🛡️ Protección Contra Cargas Duplicadas

La aplicación previene que el mismo archivo se cargue dos veces:

### Mecanismo de Bloqueo:

```
State Variables (Session Memory):
├─ ingestion_completed: bool          # ¿Última carga fue exitosa?
├─ last_ingested_file_name: str       # Nombre archivo cargado
└─ last_ingested_table: str           # Tabla destino

Lógica de Validación:
├─ Comparar: uploaded_file.name == last_ingested_file_name
├─ SI coincide + ingestion_completed == True:
│  ├─ Mostrar advertencia (⚠️ naranja)
│  ├─ DESHABILITAR botón "Cargar a Teradata"
│  └─ BLOQUEAR ejecución con st.stop()
└─ SI es archivo diferente:
   └─ Permitir carga (resetear flags)
```

### Código en `index.py`:

```python
same_file_already_loaded = (
    st.session_state.get("ingestion_completed", False)
    and st.session_state.get("last_ingested_file_name") == uploaded_file.name
)

if same_file_already_loaded:
    st.warning("Este archivo ya fue cargado correctamente...")

if st.button("🚀 Cargar a Teradata", disabled=same_file_already_loaded):
    if same_file_already_loaded:
        st.stop()  # Bloqueo adicional por seguridad
```

### Cómo resetear:

```
❌ NO puedes simplemente hacer clic en el botón
✅ Debes:
   1. Subir un archivo diferente, O
   2. Hacer F5 / Recargar la página, O
   3. Iniciar una nueva sesión de Streamlit
```

---

## 📂 Archivos que debes trasladar

Si llevas este desarrollo a otro servidor:

```
Obligatorios:
├─ index.py
├── core/
│   ├─ readers.py
│   ├─ cleaners.py
│   ├─ database.py
│   └─ logging_config.py
├── ui/
│   └─ components.py
└─ requirements.txt

Opcionales (pero recomendados):
├── appsFiles/excelToTeraFiles/credenciales.json
├── appsFiles/excelToTeraFiles/plantillas.json
└── logs/
```

---

## 🔐 Configuración de Credenciales

### Opción 1: Archivo `.env` (Recomendado para producción)

Crea un archivo `.env` en la raíz del proyecto:

```env
TERADATA_USER=tu_usuario
TERADATA_PASSWORD=tu_contraseña
TERADATA_HOST=IBKTD
TERADATA_LOGMECH=TD2
```

**Ventajas:**
- Las credenciales NO se versionar en Git (agregar `.env` a `.gitignore`)
- Automático: el programa las carga al iniciar
- Seguro: no expones datos sensibles en código

### Opción 2: Archivo JSON

Crear `appsFiles/excelToTeraFiles/credenciales.json`:

```json
{
    "teradata_user": "tu_usuario",
    "teradata_password": "tu_contraseña",
    "teradata_host": "IBKTD",
    "teradata_logmech": "TD2"
}
```

**Desventajas:**
- Tener credenciales en archivo JSON es menos seguro
- Usa solo para desarrollo local

### Orden de búsqueda:

1. ✅ Variables de entorno (.env)
2. ❌ Archivo JSON (fallback)
3. ❌ Usuario ingresa manualmente en interfaz

---

## 📋 Configuración de Plantillas

Las plantillas permiten automatizar la configuración de columnas para archivos recurrentes.

### Ubicación:
`appsFiles/excelToTeraFiles/plantillas.json`

### Estructura:

```json
{
  "Plantilla_1": {
    "Nombre Original 1": {
      "Añadir": true,
      "Nuevo nombre": "nombre_nuevo_1",
      "Null:0/No Null:1": false,
      "Tipo de dato": "VARCHAR(255)"
    },
    "Nombre Original 2": {
      "Añadir": true,
      "Nuevo nombre": "nombre_nuevo_2",
      "Null:0/No Null:1": false,
      "Tipo de dato": "INTEGER"
    }
  },
  "Plantilla_2": {
    ...
  }
}
```

### Ejemplo Real:

```json
{
  "P001-CALIDAD_SA": {
    "CLAVE SID": {
      "Añadir": true,
      "Nuevo nombre": "id_cliente",
      "Null:0/No Null:1": false,
      "Tipo de dato": "VARCHAR(255)"
    },
    "MONTO": {
      "Añadir": true,
      "Nuevo nombre": "monto_total",
      "Null:0/No Null:1": false,
      "Tipo de dato": "FLOAT"
    },
    "FECHA PROCESO": {
      "Añadir": true,
      "Nuevo nombre": "fecha_carga",
      "Null:0/No Null:1": false,
      "Tipo de dato": "TIMESTAMP"
    },
    "Columna no usada": {
      "Añadir": false,
      "Nuevo nombre": "ignorar",
      "Null:0/No Null:1": false,
      "Tipo de dato": "VARCHAR(255)"
    }
  }
}
```

### Cómo usar:

1. **En la interfaz Streamlit:**
   - Abre la aplicación
   - En la barra lateral, selecciona tu plantilla en **"Plantillas de Mapeo"**
   - Los campos se rellenan automáticamente
   - Puedes ajustarlos manualmente si necesitas

2. **Primera vez:**
   - Sube un archivo
   - Configura columnas manualmente
   - La próxima vez, crea una plantilla con esa configuración

---

## 📊 Ejemplo Práctico Paso a Paso

### Escenario: Cargar archivo de clientes en Teradata

#### **Archivo fuente:** `clientes_2024.xlsx`
```
┌─────────────┬──────────────┬────────────┬──────────────────┐
│ CLAVE SID   │ NOMBRE FULL  │ MONTO SALDO│ FECHA AFILIACION │
├─────────────┼──────────────┼────────────┼──────────────────┤
│ SID001      │ Juan García  │ 5000.50    │ 2024-01-15       │
│ SID002      │ María López  │ 3200.75    │ 2024-02-20       │
│ SID003      │ José Martín  │ 1500.25    │ 2024-03-10       │
└─────────────┴──────────────┴────────────┴──────────────────┘
```

#### **Pasos:**

1. **Abrir aplicación**
   ```bash
   streamlit run index.py
   ```

2. **Subir archivo**
   - Clic en "Seleccione un archivo Excel"
   - Elegir `clientes_2024.xlsx`

3. **Ver vista previa**
   - La app muestra: 3 registros, 4 columnas

4. **Configurar columnas** (tabla editable):
   ```
   ┌────────────────────┬────────┬──────────┬────────────┬──────────────────┐
   │ Columna Original   │ Añadir │ Null:0/1 │ Tipo dato  │ Nuevo nombre     │
   ├────────────────────┼────────┼──────────┼────────────┼──────────────────┤
   │ CLAVE SID          │ ✓      │ □        │ VARCHAR... │ id_cliente       │
   │ NOMBRE FULL        │ ✓      │ □        │ VARCHAR... │ cliente_nombre   │
   │ MONTO SALDO        │ ✓      │ □        │ FLOAT      │ saldo_actual     │
   │ FECHA AFILIACION   │ ✓      │ □        │ TIMESTAMP  │ fecha_afiliacion │
   └────────────────────┴────────┴──────────┴────────────┴──────────────────┘
   ```

5. **Ingresar credenciales Teradata**
   - Usuario: `TU_USUARIO`
   - Contraseña: `TU_CONTRASEÑA`
   - Tabla: `BASE_DATOS.CLIENTES`

6. **Seleccionar acción**
   - ✓ "Limpiar tabla y cargar (Requiere FastLoad)" - Para tabla vacía
   - Insertar - Para tabla con datos

7. **Hacer clic en "🚀 Cargar a Teradata"**
   ```
   Preparando carga de datos...
   🛠️ Aplicando transformaciones vectoriales de limpieza...
   📡 Conectando a Teradata...
   Creando tabla BASE_DATOS.CLIENTES...
   Preparando matriz de datos para inserción...
   Iniciando carga rápida (Teradata FastLoad)...
   Carga rápida (FastLoad) completada de forma exitosa.
   ✅ Se cargaron los datos correctamente en la tabla 'BASE_DATOS.CLIENTES'.
      Tiempo total: 2.34 segundos.
   ```

8. **Intentar cargar de nuevo**
   - Mismo archivo = ⚠️ Advertencia
   - Botón deshabilitado
   - Para recargar: F5 o subir archivo diferente

---

## 🔧 Solución de Problemas

### ❌ Problema: "ModuleNotFoundError: No module named 'streamlit'"

**Solución:**
```bash
pip install streamlit polars python-calamine pyarrow teradatasql python-dotenv
```

---

### ❌ Problema: El archivo Excel se lee incorrectamente

**Causas comunes:**
- Encabezados en fila incorrecta (especialmente con P001-CALIDAD_SA)
- Hojas múltiples (Calamine lee la primera)

**Solución:**
```python
# Si tu archivo tiene estructura especial:
# En ui/components.py, línea 1:
selected_template = "P001-CALIDAD_SA"  # Usa openpyxl en lugar de calamine
```

---

### ❌ Problema: "Connection refused" a Teradata

**Causas:**
- Host incorrecto (IBKTD vs IBKTD2)
- Usuario/contraseña incorrectos
- Sin conexión de red a Teradata

**Solución:**
1. Verifica credenciales en `.env` o `credenciales.json`
2. Testa conexión manualmente:
   ```python
   import teradatasql
   con = teradatasql.connect(host='IBKTD', user='XXX', password='XXX', logmech='TD2')
   print("✅ Conexión exitosa")
   ```

---

### ❌ Problema: FastLoad falla, pero inserción estándar funciona

**Causa:** Tabla no está vacía y forzaste FastLoad

**Solución:**
- No selecciones "Limpiar tabla y cargar" si la tabla tiene datos
- O, si necesitas vaciarla, hazlo manualmente:
  ```sql
  DELETE FROM tu_tabla;
  ```

---

### ❌ Problema: Caracteres especiales (ñ, é, ü) se ven como ????

**Causa:** Archivo no está en UTF-8

**Solución:**
Marca en la interfaz:
- ✓ "Eliminar acentos en textos"
- ✓ "Cumplir formato LATIN"

---

### ❌ Problema: El botón "Cargar" está deshabilitado después de cargar

**Espera, esto es normal.** Es la protección contra duplicados.

**Para recargar el mismo archivo:**
1. Haz F5 en el navegador, O
2. Sube un archivo diferente, O
3. Reinicia Streamlit con `Ctrl+C` y `streamlit run index.py`

---

## 📈 Optimización y Rendimiento

### Velocidades Típicas:

| Operación | Rendimiento |
|-----------|------------|
| Lectura Excel (10K filas) | ~0.5 seg |
| Lectura CSV (1M filas) | ~1-2 seg |
| Limpieza datos (vectorizada) | ~0.2 seg por 100K filas |
| **FastLoad (tabla vacía)** | **~100K filas/seg** ⭐ |
| Inserción estándar (lotes) | ~10K filas/seg |

### Cómo mejorar velocidad:

1. **Siempre usa FastLoad:** Vacía tabla antes de cargar
2. **Aumenta batch size** en `core/database.py`:
   ```python
   batch_size = 100000  # En lugar de 50000
   ```
3. **Usa CSV en lugar de Excel** (más rápido leer)
4. **Desactiva limpieza de acentos** si no la necesitas

---

## 📝 Historial de Cambios

### v2.0 (Actual)
- ✅ Protección contra cargas duplicadas
- ✅ Soporte para plantillas de mapeo
- ✅ FastLoad automático para tablas vacías
- ✅ Logs detallados en `logs/`
- ✅ Interfaz mejorada con Streamlit

### v1.0
- Versión inicial monolítica

---

## 📧 Contacto y Soporte

Para reportar bugs o sugerir mejoras:
- Revisar sección "Solución de Problemas"
- Consultar logs en carpeta `logs/`
- Verificar conexión a Teradata manualmente

---

**Última actualización:** 2026-06-19  
**Estado:** Producción ✅
