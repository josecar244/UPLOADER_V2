-- =====================================================================
-- 01_EVALUACION_MANUAL_PC.SQL
-- Procesamiento de evaluaciones manuales (Pure Cloud) refactorizado.
-- 1. Consulta las tablas de homologación permanentes en base de datos.
-- 2. Sin sentencias UPDATE secuenciales.
-- 3. Limpieza, mapeo e inyección de variables en un solo SELECT.
-- 4. Funciones de ventana calculan NumEvaluacion y filtros de error en memoria.
-- 5. Se elimina el uso de la tabla staging física M_EXP_CALIDAD_PURECLOUD_MANUAL_JI.
-- =====================================================================

-- -------------------------------------------------------------
-- PASO 1: PROCESAMIENTO SECUENCIAL EN MEMORIA (Tablas Volátiles)
-- -------------------------------------------------------------

-- 1. Limpieza inicial de datos crudos (fechas, strings y saltos de línea)
CREATE VOLATILE TABLE VT_PC_RAW AS (
    SELECT
        TRIM(conversationId) AS CONID,
        assignedDate - INTERVAL '5' HOUR AS FECHA_CREADO,
        changedDate - INTERVAL '5' HOUR AS FECHA_MODIFICADO,
        releaseDate - INTERVAL '5' HOUR AS FECHA_PUBLICADO,
        conversationStartTime - INTERVAL '5' HOUR AS FECHA_VENTA,
        queueName AS COLA,
        SUBSTRING(agentName FROM 1 FOR 6) AS REG_EJECUTIVO,
        SUBSTRING(evaluatorName FROM 1 FOR 6) AS REG_EVALUADOR,
        evaluationComments AS COMENTARIO,
        OREPLACE(OREPLACE(evaluationFormName, CHR(13), ''), CHR(10), '') AS PLANTILLA,
        OREPLACE(OREPLACE(questionGroupName, CHR(13), ''), CHR(10), '') AS RAW_GRUPO,
        UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(questionText, CHR(13), ''), CHR(10), ''))) AS RAW_PREGUNTA_CLEAN,
        OREPLACE(OREPLACE(answerText, CHR(13), ''), CHR(10), '') AS RAW_RESPUESTA,
        questionid AS ID_PREGUNTA,
        answerId AS ID_RESPUESTA
    FROM DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE
) WITH DATA PRIMARY INDEX (CONID) ON COMMIT PRESERVE ROWS;


-- 2. Cruce con tablas permanentes de homologación (grupos, preguntas, respuestas)
CREATE VOLATILE TABLE VT_PC_MAPPED AS (
    SELECT
        r.*,
        COALESCE(g.TARGET, r.RAW_GRUPO) AS MAP_GRUPO,
        COALESCE(p.TARGET, r.RAW_PREGUNTA_CLEAN) AS MAP_PREGUNTA,
        CASE
            WHEN r.ID_RESPUESTA IS NULL OR r.ID_RESPUESTA = '' THEN 'N/A'
            ELSE COALESCE(res.TARGET, r.RAW_RESPUESTA)
        END AS MAP_RESPUESTA
    FROM VT_PC_RAW r
    LEFT JOIN DLAB_GEC.M_EXP_CALIDAD_HOMOLOGA_GRUPO g ON r.RAW_GRUPO = g.ORIGINAL
    LEFT JOIN DLAB_GEC.M_EXP_CALIDAD_HOMOLOGA_PREGUNTA p ON r.RAW_PREGUNTA_CLEAN = p.ORIGINAL
    LEFT JOIN DLAB_GEC.M_EXP_CALIDAD_HOMOLOGA_RESPUESTA res ON r.RAW_RESPUESTA = res.ORIGINAL
) WITH DATA PRIMARY INDEX (CONID) ON COMMIT PRESERVE ROWS;


-- 3. Aplicación de reglas de re-agrupamiento, cálculo de NumEvaluacion y detección de errores
CREATE VOLATILE TABLE VT_PC_SCORED AS (
    SELECT
        m.CONID, m.FECHA_CREADO, m.FECHA_MODIFICADO, m.FECHA_PUBLICADO, m.FECHA_VENTA, m.COLA, m.REG_EJECUTIVO, m.REG_EVALUADOR, m.COMENTARIO, m.PLANTILLA,
        m.ID_PREGUNTA, m.ID_RESPUESTA, m.MAP_PREGUNTA AS PREGUNTA, m.MAP_RESPUESTA AS RESPUESTA,
        
        -- Reglas de re-agrupamiento basadas en la pregunta
        CASE
            WHEN m.MAP_GRUPO = 'CARACTERISTICAS OBLIGATORIAS' AND m.MAP_PREGUNTA = 'LPDP' THEN 'NORMAS LEGALES'
            WHEN m.MAP_GRUPO = 'PROTOCOLO' AND m.MAP_PREGUNTA IN ('LLAMADA DE RETENCION', 'RESULTADO LLAMADA') THEN 'REGISTRO'
            ELSE m.MAP_GRUPO
        END AS GRUPO_PREGUNTAS,
        
        '{PERIODO}' AS PERIODO,
        CAST('{PERIODO}' || '_' || m.REG_EJECUTIVO AS VARCHAR(13)) AS CODIGO,
        
        -- Extracción de DNI/RUC
        CASE
            WHEN POSITION('DNI ' IN m.COMENTARIO) > 0 THEN SUBSTRING(m.COMENTARIO FROM POSITION('DNI ' IN m.COMENTARIO) + 4 FOR 8)
            WHEN POSITION('RUC ' IN m.COMENTARIO) > 0 THEN SUBSTRING(m.COMENTARIO FROM POSITION('RUC ' IN m.COMENTARIO) + 4 FOR 11)
            ELSE NULL
        END AS DNI,
        
        -- Conteo de NumEvaluacion en la sesión
        MAX(CASE WHEN m.MAP_PREGUNTA = 'NUM. EVALUACION' THEN m.MAP_RESPUESTA END) OVER (PARTITION BY m.CONID) AS NUM_EVALUACION,
        
        -- Identificación de errores
        CASE
            WHEN m.MAP_RESPUESTA IN ('DIFIERE', 'INCOMPLETO', 'INCORRECTO', 'INDUCE', 'NO', 'NO INFORMA', 'NO REGISTRA', 'ANULA') THEN 1
            ELSE 0
        END AS ES_ERROR
    FROM VT_PC_MAPPED m
) WITH DATA PRIMARY INDEX (CONID) ON COMMIT PRESERVE ROWS;


-- 4. Asociación de pesos de la Maestra de Calidad e identificación de errores críticos
CREATE VOLATILE TABLE VT_PC_WEIGHTED AS (
    SELECT
        s.*,
        b.PREG_VALIDA,
        b.PREG_CRITICA,
        b.CANT_PREGUNTAS,
        b.PESO_GRUPO,
        b.PESO_PREGUNTA AS PESO_PREGUNTA,
        CASE WHEN s.ES_ERROR = 1 THEN 0.0 ELSE b.PESO_PREGUNTA END AS PESO_PREGUNTA_OBT,
        
        -- Invalida peso de grupo si hay un error crítico
        MAX(CASE WHEN b.PREG_CRITICA = 1 AND b.PREG_VALIDA = 1 AND s.ES_ERROR = 1 THEN 1 ELSE 0 END) OVER (PARTITION BY s.CONID, s.GRUPO_PREGUNTAS) AS HAS_CRITICAL_ERROR
    FROM VT_PC_SCORED s
    LEFT JOIN (
        SELECT 
            b.PLANTILLA,
            COALESCE(g.TARGET, b.GRUPO_PREGUNTAS) AS MAP_GRUPO,
            COALESCE(p.TARGET, b.PREGUNTA) AS MAP_PREGUNTA,
            b.PREG_VALIDA,
            b.PREG_CRITICA,
            b.CANT_PREGUNTAS,
            b.PESO_GRUPO,
            b.PESO_PREGUNTA
        FROM DLAB_GEC.M_EXP_CALIDAD_MAESTRA_GRUPO_PREGUNTAS_PCLOUD b
        LEFT JOIN DLAB_GEC.M_EXP_CALIDAD_HOMOLOGA_GRUPO g 
            ON UPPER(TRIM(BOTH ' ' FROM b.GRUPO_PREGUNTAS)) = g.ORIGINAL
        LEFT JOIN DLAB_GEC.M_EXP_CALIDAD_HOMOLOGA_PREGUNTA p 
            ON UPPER(TRIM(BOTH ' ' FROM b.PREGUNTA)) = p.ORIGINAL
    ) b
        ON UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(s.PLANTILLA, CHR(13), ''), CHR(10), ''))) = UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(b.PLANTILLA, CHR(13), ''), CHR(10), '')))
       AND UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(s.GRUPO_PREGUNTAS, CHR(13), ''), CHR(10), ''))) = UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(b.MAP_GRUPO, CHR(13), ''), CHR(10), '')))
       AND UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(s.PREGUNTA, CHR(13), ''), CHR(10), ''))) = UPPER(TRIM(BOTH ' ' FROM OREPLACE(OREPLACE(b.MAP_PREGUNTA, CHR(13), ''), CHR(10), '')))
) WITH DATA PRIMARY INDEX (CONID, GRUPO_PREGUNTAS) ON COMMIT PRESERVE ROWS;


-- 5. Tabla final con agregación de pesos y penalizaciones aplicadas
CREATE VOLATILE TABLE VT_PC_FINAL AS (
    SELECT
        w.CODIGO,
        w.PERIODO,
        w.CONID,
        w.FECHA_CREADO,
        w.FECHA_MODIFICADO,
        w.FECHA_PUBLICADO,
        w.FECHA_VENTA,
        w.COLA,
        w.REG_EJECUTIVO,
        w.DNI,
        w.PLANTILLA,
        CAST(w.NUM_EVALUACION AS INTEGER) AS NUM_EVALUACION,
        w.REG_EVALUADOR,
        CAST(NULL AS VARCHAR(255)) AS AGRUPACION,
        w.GRUPO_PREGUNTAS,
        w.PREGUNTA,
        w.ID_PREGUNTA,
        w.RESPUESTA,
        w.ID_RESPUESTA,
        w.CANT_PREGUNTAS,
        
        -- Conteo dinámico de respuestas por grupo
        SUM(CASE WHEN w.PREG_VALIDA = 1 THEN 1 ELSE 0 END) OVER (PARTITION BY w.CONID, w.GRUPO_PREGUNTAS) AS CANT_RESPUESTAS,
        
        -- Penalización si tiene error crítico
        CASE WHEN w.HAS_CRITICAL_ERROR = 1 THEN 0.0 ELSE w.PESO_GRUPO END AS PESO_GRUPO,
        CASE WHEN w.HAS_CRITICAL_ERROR = 1 THEN 0.0 ELSE w.PESO_GRUPO END AS PESO_GRUPO_OBT,
        w.PESO_PREGUNTA,
        CASE WHEN w.HAS_CRITICAL_ERROR = 1 THEN 0.0 ELSE w.PESO_PREGUNTA_OBT END AS PESO_PREGUNTA_OBT,
        
        w.PREG_VALIDA,
        w.PREG_CRITICA,
        w.ES_ERROR,
        w.COMENTARIO
    FROM VT_PC_WEIGHTED w
) WITH DATA PRIMARY INDEX (CODIGO, CONID, PREGUNTA, RESPUESTA) ON COMMIT PRESERVE ROWS;


-- -------------------------------------------------------------
-- PASO 2: VACIADO Y CARGA EN TABLA PRODUCTIVA
-- -------------------------------------------------------------

-- Vaciado eficiente
DELETE FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_PURE_CLOUD ALL;

-- Inserción directa
INSERT INTO DLAB_GEC.M_EXP_CALIDAD_DETALLE_PURE_CLOUD
SELECT * FROM VT_PC_FINAL;


-- -------------------------------------------------------------
-- PASO 3: LIMPIEZA DE SESIÓN
-- -------------------------------------------------------------
DROP TABLE VT_PC_RAW;
DROP TABLE VT_PC_MAPPED;
DROP TABLE VT_PC_SCORED;
DROP TABLE VT_PC_WEIGHTED;
DROP TABLE VT_PC_FINAL;

