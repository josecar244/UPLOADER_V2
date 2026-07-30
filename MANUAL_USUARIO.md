# 📖 Manual del Usuario Final - Cargador de Datos e Ingesta Automática

Este manual te guiará paso a paso para utilizar la aplicación web. El objetivo de esta herramienta es simplificar la descarga de reportes y la carga de datos a la base de datos de consulta, de manera rápida, segura y sin necesidad de realizar configuraciones complejas.

---

## ⚡ 1. Proceso Automático: Actualizar Calidad Insumos (Recomendado)
Usa esta opción para actualizar de forma automática la base de consumo utilizada para el Power BI (PBI) de calidad. Con un solo botón, la aplicación descargará los reportes requeridos de **Insight**, los limpiará y los subirá a la base de datos de consulta.

### Pasos para ejecutar el proceso:
1.  **Ingresar a la pestaña:** Haz clic en **"⚡ Calidad Insumos (PBI)"** en la parte superior de la aplicación.
2.  **Escribir tus credenciales:**
    *   **Insight:** Escribe tu usuario y contraseña con los que accedes al portal de reportes.
    *   **Teradata:** Escribe tu usuario y contraseña de red con los que accedes a la base de datos de consumo.
3.  **Iniciar la actualización:** Haz clic en el botón azul **"🚀 Iniciar Proceso Automático"**.
4.  **Monitorear el avance:** Verás un panel de estado que te indicará qué reporte se está descargando y cargando en cada momento.
5.  **Finalización exitosa:** Cuando termine el proceso, el panel se pondrá verde con el mensaje: **"¡Ingesta completada con éxito! Listo para PBI"**. En este momento, la base de datos estará actualizada y tu Power BI estará listo para ser refrescado.

*Nota:* Este proceso actualiza de forma automática los 7 insumos clave (Trafico Genesys, Atributos de Conversaciones, Derivaciones, Marcas de Transferencias, Evaluaciones, etc.).

---

## 📁 2. Carga Manual de Archivos Especiales
Usa esta opción si tienes un archivo de Excel, CSV o Texto en tu computadora y necesitas subirlo a una tabla específica de la base de datos de forma personalizada.

### Pasos para cargar:
1.  **Ingresar a la pestaña:** Haz clic en **"📁 Subir a Teradata"**.
2.  **Subir tu archivo:** Haz clic en el recuadro gris o arrastra tu archivo (`.xlsx`, `.csv` o `.txt`) dentro de él. La aplicación mostrará una vista previa de las primeras 5 filas para que confirmes que es el archivo correcto.
3.  **Configurar tus columnas (Panel interactivo):**
    *   **Columna "Añadir":** Marca la casilla si deseas que esa columna se suba a la base de datos, o desmárcala para ignorarla.
    *   **Tipo de dato:** Selecciona qué tipo de información contiene (Texto, Número entero, Número decimal, Fecha y hora, o Fecha simple).
    *   **Nuevo nombre:** Puedes cambiar el nombre con el que se guardará la columna en la base de datos. La aplicación corregirá automáticamente los espacios y caracteres especiales para evitar errores.
4.  **Ingresar datos de destino:**
    *   Escribe tu usuario y contraseña de red.
    *   Escribe el nombre exacto de la tabla de destino (ej. `DLAB_GEC.Mi_Tabla_Reporte`).
5.  **Seleccionar la acción:**
    *   **Opción A (Recomendada para tablas vacías):** Marca *"Limpiar tabla y cargar"* si deseas borrar todo lo que tiene la tabla en la base de datos antes de subir el nuevo archivo (es el método más rápido).
    *   **Opción B:** Si dejas la casilla desmarcada, la información se agregará al final de la tabla existente sin borrar nada.
6.  **Subir información:** Haz clic en **"🚀 Cargar a Teradata"** y espera el mensaje verde de éxito.

---

## 📊 3. Descargas Individuales de Reportes
Si en algún momento solo necesitas descargar los archivos a tu carpeta de Descargas personales sin subirlos a ninguna base de datos:

*   **Desde Insight:** Ve a la pestaña **"📊 Descargar de Insight"**, ingresa tus credenciales, y selecciona la consulta que deseas descargar de forma puntual. El archivo se guardará automáticamente en tu carpeta **Descargas** (`Downloads`).
*   **Desde Verint:** Ve a la pestaña **"📞 Descargar de Verint"**, configura la fecha y el rango de datos que necesitas bajar de forma manual y haz clic en descargar.

---

## ⚠️ 4. Mensajes de Alerta y Solución de Problemas

*   **El botón "Cargar" se bloqueó o aparece una advertencia naranja:**
    *   *¿Por qué pasa?* Es una protección contra cargas duplicadas. Evita que subas dos veces seguidas el mismo archivo por error.
    *   *¿Cómo lo soluciono?* Si realmente necesitas volver a cargarlo, simplemente presiona **F5** para recargar la página del navegador, o selecciona un archivo diferente.
*   **Aparece un recuadro rojo con un error de conexión:**
    *   Verifica que tu usuario o contraseña estén bien escritos.
    *   Asegúrate de estar conectado a la red interna del banco (o tener la VPN activa si trabajas remoto).
    *   Si el error persiste, toma una captura de pantalla del mensaje y compártela con el equipo de soporte de base de datos.
