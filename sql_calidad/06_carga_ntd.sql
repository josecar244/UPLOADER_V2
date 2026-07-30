-- =====================================================================
-- 06_CARGA_NTD.SQL
-- Proceso de Carga y Estandarización de Not To Do (NTD)
-- Consolida llamadas NTD de evaluaciones manuales y observaciones del supervisor
-- =====================================================================

-- 1. Actualizar la vista de verificación de fechas de carga
REPLACE VIEW DLAB_GEC.V_CHECK_FECHAS_NTD AS
(
    SELECT
        CAST('DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE' AS VARCHAR(200)) AS NOMBRE_TABLA,
        MAX(conversationStartTime)       AS FECHA_ULTIMA_ACTUALIZACION,
        'Evaluations | TXT Unicode vía UPLOADER (P008_INSIGHT_07_EVALUATIONS)'
            AS DESCRIPCION_CARGA
    FROM DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE

    UNION ALL

    SELECT
        'DLAB_GEC.M_EXP_NTD_OBSERVACIONES_PRE',
        MAX(FECHA_CREADO),
        'Acción Tomada | Excel vía UPLOADER (P004-ACC_TOMADA)'
    FROM DLAB_GEC.M_EXP_NTD_OBSERVACIONES_PRE

    UNION ALL

    SELECT
        'DLAB_GEC.M_EXP_NTD_OBSERVACIONES_NEW',
        MAX(FECHA_CREADO),
        'Tabla derivada | Se genera durante CARGA_NTD (proceso interno)'
    FROM DLAB_GEC.M_EXP_NTD_OBSERVACIONES_NEW
);


-- 2. Limpiar información del período objetivo para evitar duplicados
DELETE FROM DLAB_GEC.M_EXP_NOT_TO_DO
WHERE PERIODO = '{PERIODO}';


-- 3. Insertar registros base marcados como NTD en la tabla histórica
INSERT INTO DLAB_GEC.M_EXP_NOT_TO_DO (CONID, PERIODO, PLANTILLA)
SELECT DISTINCT 
   conversationId        AS CONID,
   '{PERIODO}'           AS PERIODO,
   CAST(evaluationFormName AS VARCHAR(50)) AS PLANTILLA
FROM DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE
WHERE questiongroupname = 'DATOS EVALUACION'
  AND questionText       = 'NTD'
  AND answerText         IN ('Si', 'SI', 'Sí');


-- 4. Pivote y carga consolidada de información de evaluaciones
UPDATE A
FROM DLAB_GEC.M_EXP_NOT_TO_DO A,
(
    SELECT
        conversationId AS CONID,
        MAX(CASE WHEN questiongroupname = 'NOT TO DO' THEN LEFT(agentName, 6) END) AS REG_EJECUTIVO,
        MIN(CASE 
            WHEN questiongroupname = 'NOT TO DO' AND POSITION('DNI ' IN evaluationComments) > 0 
                THEN SUBSTRING(evaluationComments FROM POSITION('DNI ' IN evaluationComments)+4 FOR 8)
            WHEN questiongroupname = 'NOT TO DO' AND POSITION('RUC ' IN evaluationComments) > 0 
                THEN SUBSTRING(evaluationComments FROM POSITION('RUC ' IN evaluationComments)+4 FOR 11)
        END) AS DNI,
        MIN(conversationStartTime) AS FECHA_AUDIO,
        MIN(assignedDate) AS FECHA_CREADO,
        MAX(LEFT(evaluatorName, 6)) AS REG_CREADO,
        MIN(changedDate) AS FECHA_MODIFICADO,
        MAX(CASE WHEN QUESTIONTEXT = 'ORIGEN' THEN ANSWERTEXT END) AS ORIGEN,
        MAX(CASE WHEN questionGroupName = 'DATOS EVALUACION' AND QUESTIONTEXT = 'NumEvaluacion' THEN ANSWERTEXT END) AS NumEvaluacion,
        MAX(CASE WHEN questionGroupName = 'NOT TO DO' AND QUESTIONTEXT = 'APLICA_NTD_1' THEN ANSWERTEXT END) AS APLICA_NTD_1,
        MAX(CASE WHEN questionGroupName = 'NOT TO DO' AND QUESTIONTEXT = 'APLICA_NTD_2' THEN ANSWERTEXT END) AS APLICA_NTD_2,
        MAX(CASE WHEN questionGroupName = 'NOT TO DO' AND QUESTIONTEXT = 'APLICA_NTD_3' THEN ANSWERTEXT END) AS APLICA_NTD_3,
        MAX(CASE WHEN questionGroupName = 'NOT TO DO' AND QUESTIONTEXT = 'REINCIDENCIA_NTD_1' THEN ANSWERTEXT END) AS REINCIDENCIA_NTD_1,
        MAX(CASE WHEN questionGroupName = 'NOT TO DO' AND QUESTIONTEXT = 'REINCIDENCIA_NTD_2' THEN ANSWERTEXT END) AS REINCIDENCIA_NTD_2,
        MAX(CASE WHEN questionGroupName = 'NOT TO DO' AND QUESTIONTEXT = 'REINCIDENCIA_NTD_3' THEN ANSWERTEXT END) AS REINCIDENCIA_NTD_3,
        MAX(CASE WHEN questionGroupName = 'NOT TO DO' AND QUESTIONTEXT = 'NTD_01_CASUISTICA_GRUPO' THEN ANSWERTEXT END) AS CASUISTICA_GRUPO_RAW,
        MAX(CASE WHEN questionGroupName = 'NOT TO DO' AND QUESTIONTEXT = 'NTD_02_CASUISTICA_GRUPO' THEN ANSWERTEXT END) AS CASUISTICA_GRUPO_2_RAW,
        MAX(CASE WHEN questionGroupName = 'NOT TO DO' AND QUESTIONTEXT = 'NTD_03_CASUISTICA_GRUPO' THEN ANSWERTEXT END) AS CASUISTICA_GRUPO_3_RAW,
        MAX(CASE WHEN questionGroupName = 'NOT TO DO' AND QUESTIONTEXT = 'NTD_01_CASUISTICA_DETALLE' THEN ANSWERTEXT END) AS CASUISTICA_DETALLE,
        MAX(CASE WHEN questionGroupName = 'NOT TO DO' AND QUESTIONTEXT = 'NTD_02_CASUISTICA_DETALLE' THEN ANSWERTEXT END) AS CASUISTICA_DETALLE_2,
        MAX(CASE WHEN questionGroupName = 'NOT TO DO' AND QUESTIONTEXT = 'NTD_03_CASUISTICA_DETALLE' THEN ANSWERTEXT END) AS CASUISTICA_DETALLE_3
    FROM DLAB_GEC.M_EXP_CALIDAD_PURECLOUD_PRE
    GROUP BY 1
) B
SET
    REG_EJECUTIVO = B.REG_EJECUTIVO,
    CODIGO = '{PERIODO}_' || COALESCE(B.REG_EJECUTIVO, ''),
    DNI = B.DNI,
    FECHA_AUDIO = B.FECHA_AUDIO,
    FECHA_CREADO = B.FECHA_CREADO,
    REG_CREADO = B.REG_CREADO,
    REG_MODIFICADO = B.REG_CREADO,
    FECHA_MODIFICADO = B.FECHA_MODIFICADO,
    ORIGEN = B.ORIGEN,
    ORIGEN_DETALLE = UPPER(CASE 
        WHEN B.ORIGEN = 'Calidad (Eval/Aud)' THEN
            CASE
                WHEN B.NumEvaluacion IN ('EVAL1', '1') THEN 'EVAL 1'
                WHEN B.NumEvaluacion IN ('EVAL2', '2') THEN 'EVAL 2'
                WHEN B.NumEvaluacion = '3' THEN 'EVAL 3'
                WHEN B.NumEvaluacion = '4' THEN 'EVAL 4'
                WHEN B.NumEvaluacion IN ('ADIC 1', 'ADI 1', 'EVAL ADI 1', 'EVAL AD1', 'EVAL AD 1', '5') THEN 'EVAL 5'
                WHEN B.NumEvaluacion IN ('ADI 2', 'ADIC 2', 'EVAL AD2', '6') THEN 'EVAL 6'
                WHEN B.NumEvaluacion = '0' THEN 'AUDITORIA'
                ELSE B.NumEvaluacion
            END
        WHEN B.ORIGEN = 'Reclamos' THEN 'RECLAMOS'
        ELSE 'AUDITORIA'
    END),
    APLICA_NTD_1 = NULLIF(NULLIF(B.APLICA_NTD_1, '-'), ''),
    APLICA_NTD_2 = NULLIF(NULLIF(B.APLICA_NTD_2, '-'), ''),
    APLICA_NTD_3 = NULLIF(NULLIF(B.APLICA_NTD_3, '-'), ''),
    REINCIDENCIA_NTD_1 = NULLIF(NULLIF(B.REINCIDENCIA_NTD_1, '-'), ''),
    REINCIDENCIA_NTD_2 = NULLIF(NULLIF(B.REINCIDENCIA_NTD_2, '-'), ''),
    REINCIDENCIA_NTD_3 = NULLIF(NULLIF(B.REINCIDENCIA_NTD_3, '-'), ''),
    CASUISTICA_GRUPO = NULLIF(NULLIF(B.CASUISTICA_GRUPO_RAW, '-'), ''),
    CASUISTICA_GRUPO_2 = NULLIF(NULLIF(B.CASUISTICA_GRUPO_2_RAW, '-'), ''),
    CASUISTICA_GRUPO_3 = NULLIF(NULLIF(B.CASUISTICA_GRUPO_3_RAW, '-'), ''),
    CASUISTICA_DETALLE = NULLIF(NULLIF(B.CASUISTICA_DETALLE, '-'), ''),
    CASUISTICA_DETALLE_2 = NULLIF(NULLIF(B.CASUISTICA_DETALLE_2, '-'), ''),
    CASUISTICA_DETALLE_3 = NULLIF(NULLIF(B.CASUISTICA_DETALLE_3, '-'), ''),
    CODIGO_NTD = B.REG_EJECUTIVO 
        || '_'
        || LTrim(RTrim(COALESCE(B.DNI, '')))
        || '_'
        || LTrim(RTrim(CAST(EXTRACT(YEAR FROM B.FECHA_CREADO) AS VARCHAR(4))))
        || CAST(CAST(B.FECHA_CREADO AS FORMAT 'MM') AS VARCHAR(2))
        || CAST(CAST(B.FECHA_CREADO AS FORMAT 'DD') AS VARCHAR(2))
        || CAST(CAST(B.FECHA_CREADO AS FORMAT 'HH') AS VARCHAR(2))
        || CAST(CAST(B.FECHA_CREADO AS FORMAT 'MI') AS VARCHAR(2))
WHERE A.CONID = B.CONID
  AND A.PERIODO = '{PERIODO}';


-- 5. Homologación de niveles y estandarización de categorías de casuística
UPDATE A
FROM DLAB_GEC.M_EXP_NOT_TO_DO A,
(
    SELECT 
        T.CONID,
        T.PERIODO,
        N1.NIVEL_NTD AS NIVEL_N1,
        N2.NIVEL_NTD AS NIVEL_N2,
        N3.NIVEL_NTD AS NIVEL_N3
    FROM DLAB_GEC.M_EXP_NOT_TO_DO T
    LEFT JOIN DLAB_GEC.M_EXP_MAESTRA_NIVEL_NTD_NORM N1 
      ON UPPER(TRIM(T.PLANTILLA)) = N1.PLANTILLA_NORM 
      AND UPPER(TRIM(T.CASUISTICA_DETALLE)) = N1.CASUISTICA_NORM
    LEFT JOIN DLAB_GEC.M_EXP_MAESTRA_NIVEL_NTD_NORM N2 
      ON UPPER(TRIM(T.PLANTILLA)) = N2.PLANTILLA_NORM 
      AND UPPER(TRIM(T.CASUISTICA_DETALLE_2)) = N2.CASUISTICA_NORM
    LEFT JOIN DLAB_GEC.M_EXP_MAESTRA_NIVEL_NTD_NORM N3 
      ON UPPER(TRIM(T.PLANTILLA)) = N3.PLANTILLA_NORM 
      AND UPPER(TRIM(T.CASUISTICA_DETALLE_3)) = N3.CASUISTICA_NORM
    WHERE T.PERIODO = '{PERIODO}'
) B
SET
    -- Determinar NIVEL_NTD aplicando regla de reincidencia a nivel N2 para casuísticas específicas
    NIVEL_NTD = CASE 
        WHEN TRIM(A.CASUISTICA_GRUPO) IN (
            'CO_Brindar informacion falsa, incompleta o ambigua.',
            'CO_No solicita EECC >40K para desembolso (CD).',
            'CO_Solicita EECC >40K y no lo registra en compartido.'
        ) AND A.REINCIDENCIA_NTD_1 IN ('Sí', 'Si', 'SI') THEN 'N2'
        ELSE B.NIVEL_N1
    END,
    
    NIVEL_NTD_2 = CASE 
        WHEN TRIM(A.CASUISTICA_GRUPO_2) IN (
            'CO_Brindar informacion falsa, incompleta o ambigua.',
            'CO_No solicita EECC >40K para desembolso (CD).',
            'CO_Solicita EECC >40K y no lo registra en compartido.'
        ) AND A.REINCIDENCIA_NTD_2 IN ('Sí', 'Si', 'SI') THEN 'N2'
        ELSE B.NIVEL_N2
    END,
    
    NIVEL_NTD_3 = CASE 
        WHEN TRIM(A.CASUISTICA_GRUPO_3) IN (
            'CO_Brindar informacion falsa, incompleta o ambigua.',
            'CO_No solicita EECC >40K para desembolso (CD).',
            'CO_Solicita EECC >40K y no lo registra en compartido.'
        ) AND A.REINCIDENCIA_NTD_3 IN ('Sí', 'Si', 'SI') THEN 'N2'
        ELSE B.NIVEL_N3
    END,

    -- Categorizar CASUISTICA_GRUPO según prefijo del detalle
    CASUISTICA_GRUPO = CASE 
        WHEN LEFT(A.CASUISTICA_DETALLE, 3) = 'NL_' THEN 'NORMAS LEGALES'
        WHEN LEFT(A.CASUISTICA_DETALLE, 3) = 'CO_' THEN 'CARACTERISTICAS OBLIGATORIAS'
        WHEN LEFT(A.CASUISTICA_DETALLE, 3) = 'ER_' THEN 'ERROR EN REGISTRO'
        WHEN LEFT(A.CASUISTICA_DETALLE, 3) = 'GR_' THEN 'GRAVE'
        WHEN LEFT(A.CASUISTICA_DETALLE, 3) = 'SE_' THEN 'SEGUROS'
        WHEN LEFT(A.CASUISTICA_DETALLE, 3) = 'VI_' THEN 'VALIDACION'
        ELSE A.CASUISTICA_GRUPO
    END,

    CASUISTICA_GRUPO_2 = CASE 
        WHEN LEFT(A.CASUISTICA_DETALLE_2, 3) = 'NL_' THEN 'NORMAS LEGALES'
        WHEN LEFT(A.CASUISTICA_DETALLE_2, 3) = 'CO_' THEN 'CARACTERISTICAS OBLIGATORIAS'
        WHEN LEFT(A.CASUISTICA_DETALLE_2, 3) = 'ER_' THEN 'ERROR EN REGISTRO'
        WHEN LEFT(A.CASUISTICA_DETALLE_2, 3) = 'GR_' THEN 'GRAVE'
        WHEN LEFT(A.CASUISTICA_DETALLE_2, 3) = 'SE_' THEN 'SEGUROS'
        WHEN LEFT(A.CASUISTICA_DETALLE_2, 3) = 'VI_' THEN 'VALIDACION'
        ELSE A.CASUISTICA_GRUPO_2
    END,

    CASUISTICA_GRUPO_3 = CASE 
        WHEN LEFT(A.CASUISTICA_DETALLE_3, 3) = 'NL_' THEN 'NORMAS LEGALES'
        WHEN LEFT(A.CASUISTICA_DETALLE_3, 3) = 'CO_' THEN 'CARACTERISTICAS OBLIGATORIAS'
        WHEN LEFT(A.CASUISTICA_DETALLE_3, 3) = 'ER_' THEN 'ERROR EN REGISTRO'
        WHEN LEFT(A.CASUISTICA_DETALLE_3, 3) = 'GR_' THEN 'GRAVE'
        WHEN LEFT(A.CASUISTICA_DETALLE_3, 3) = 'SE_' THEN 'SEGUROS'
        WHEN LEFT(A.CASUISTICA_DETALLE_3, 3) = 'VI_' THEN 'VALIDACION'
        ELSE A.CASUISTICA_GRUPO_3
    END
WHERE A.CONID = B.CONID
  AND A.PERIODO = B.PERIODO
  AND A.PERIODO = '{PERIODO}';


-- 6. Actualizar Niveles Finales y Acciones Recomendadas según el Nivel Homologado
UPDATE DLAB_GEC.M_EXP_NOT_TO_DO
SET
    NIVEL_NTD_FINAL = CASE 
        WHEN UPPER(TRIM(APLICA_NTD_1)) = 'SI' THEN NIVEL_NTD
        WHEN UPPER(TRIM(APLICA_NTD_1)) = 'NO' THEN 'NP'
    END,
    NIVEL_NTD_FINAL_2 = CASE 
        WHEN UPPER(TRIM(APLICA_NTD_2)) = 'SI' THEN NIVEL_NTD_2
        WHEN UPPER(TRIM(APLICA_NTD_2)) = 'NO' THEN 'NP'
    END,
    NIVEL_NTD_FINAL_3 = CASE 
        WHEN UPPER(TRIM(APLICA_NTD_3)) = 'SI' THEN NIVEL_NTD_3
        WHEN UPPER(TRIM(APLICA_NTD_3)) = 'NO' THEN 'NP'
    END,
    
    ACCION_RECOMENDADA = CASE 
        WHEN NIVEL_NTD = 'EO' THEN 'Feedback'
        WHEN NIVEL_NTD = 'N1' THEN 'Feedback - Ll. atención verbal - Ll. atención simple'
        WHEN NIVEL_NTD = 'N2' THEN 'Ll. atención simple - Ll. atención severa'
        WHEN NIVEL_NTD = 'N3' THEN 'Ll. atención severa - Suspensión'
    END,
    ACCION_RECOMENDADA_2 = CASE 
        WHEN NIVEL_NTD_2 = 'EO' THEN 'Feedback'
        WHEN NIVEL_NTD_2 = 'N1' THEN 'Feedback - Ll. atención verbal - Ll. atención simple'
        WHEN NIVEL_NTD_2 = 'N2' THEN 'Ll. atención simple - Ll. atención severa'
        WHEN NIVEL_NTD_2 = 'N3' THEN 'Ll. atención severa - Suspensión'
    END,
    ACCION_RECOMENDADA_3 = CASE 
        WHEN NIVEL_NTD_3 = 'EO' THEN 'Feedback'
        WHEN NIVEL_NTD_3 = 'N1' THEN 'Feedback - Ll. atención verbal - Ll. atención simple'
        WHEN NIVEL_NTD_3 = 'N2' THEN 'Ll. atención simple - Ll. atención severa'
        WHEN NIVEL_NTD_3 = 'N3' THEN 'Ll. atención severa - Suspensión'
    END
WHERE PERIODO = '{PERIODO}';


-- 7. Limpiar y procesar la tabla de observaciones del supervisor (deduplicada y estandarizada)
DELETE FROM DLAB_GEC.M_EXP_NTD_OBSERVACIONES_NEW;

INSERT INTO DLAB_GEC.M_EXP_NTD_OBSERVACIONES_NEW
(
    CODIGO_NTD,
    CODIGO,
    REGISTRO_SV,
    REGISTRO_EV,
    DNI_CLIENTE,
    ACCION_TOMADA,
    STATUS,
    OBSERVACIONES,
    FECHA_CREADO
)
SELECT
    A.CODIGO_NTD,
    CASE 
        WHEN LENGTH(LTRIM(RTRIM(A.DNI_CLIENTE))) = 8 THEN SUBSTRING(A.CODIGO_NTD FROM 17 FOR 6) || '_' || A.REGISTRO_EV
        WHEN LENGTH(LTRIM(RTRIM(A.DNI_CLIENTE))) = 11 THEN SUBSTRING(A.CODIGO_NTD FROM 20 FOR 6) || '_' || A.REGISTRO_EV
    END AS CODIGO,
    CAST(NULL AS VARCHAR(255)) AS REGISTRO_SV,
    A.REGISTRO_EV,
    LTRIM(RTRIM(A.DNI_CLIENTE)) AS DNI_CLIENTE,
    CASE 
        WHEN A.ACCION_TOMADA IN ('Ll. atencion simple', 'CARTA DE LLAMADA DE ATENCION SIMPLE', 'ACTA DE LLAMADA DE ATENCION') THEN 'Ll. atención simple'
        WHEN A.ACCION_TOMADA IN ('FEEDBACK', 'Feedback') THEN 'Feedback'
        WHEN A.ACCION_TOMADA = '-' THEN 'Acción No Definida'
        WHEN A.ACCION_TOMADA = 'ENVIADO A GDH' THEN 'Enviado a GDH'
        WHEN A.ACCION_TOMADA IN ('CARTA DE LLAMADA DE ATENCION SEVERA', 'Ll. atencion severa') THEN 'Ll. atención severa'
        WHEN A.ACCION_TOMADA IN ('DESVINCULACION', 'Desvinculación', 'EV YA NO LABORA EN IBK') THEN 'Desvinculación'
        WHEN A.ACCION_TOMADA IN ('SUSPENSION', 'Suspensión') THEN 'Suspensión'
        WHEN A.ACCION_TOMADA = 'Ll. atencion verbal' THEN 'Ll. atención verbal'
        ELSE A.ACCION_TOMADA
    END AS ACCION_TOMADA,
    A.STATUS,
    A.OBSERVACIONES,
    A.FECHA_CREADO
FROM (
    SELECT 
        CODIGO_NTD,
        REGISTRO_EV,
        DNI_CLIENTE,
        ACCION_TOMADA,
        STATUS,
        OBSERVACIONES,
        FECHA_CREADO,
        ROW_NUMBER() OVER (PARTITION BY CODIGO_NTD ORDER BY FECHA_CREADO DESC) AS RN
    FROM DLAB_GEC.M_EXP_NTD_OBSERVACIONES_PRE
) AS A
WHERE A.RN = 1;


-- 8. Cruzar y actualizar el flag de Acción Tomada en la tabla histórica de NTD
UPDATE A
FROM DLAB_GEC.M_EXP_NOT_TO_DO A,
(
    SELECT 
        T.CONID,
        T.PERIODO,
        CASE WHEN o.CODIGO_NTD IS NOT NULL THEN 1 ELSE 0 END AS ACCION_TOMADA_VAL
    FROM DLAB_GEC.M_EXP_NOT_TO_DO T
    LEFT JOIN DLAB_GEC.M_EXP_NTD_OBSERVACIONES_NEW o
      ON o.CODIGO_NTD IS NOT NULL
      AND TRIM(o.CODIGO_NTD) = TRIM(T.CODIGO_NTD)
    WHERE T.PERIODO = '{PERIODO}'
) B
SET ACCION_TOMADA = B.ACCION_TOMADA_VAL
WHERE A.CONID = B.CONID
  AND A.PERIODO = B.PERIODO
  AND A.PERIODO = '{PERIODO}';


-- 9. Reemplazar Vistas de Reporte finales

REPLACE VIEW DLAB_GEC.V_EXP_NOT_TO_DO_VIEW AS
(
SELECT
  CODIGO_NTD,
  CODIGO,
  PERIODO,
  REG_EJECUTIVO,
  DNI,
  CONID,
  FECHA_AUDIO,
  FECHA_CREADO,
  REG_CREADO,
  FECHA_MODIFICADO,
  REG_MODIFICADO,
  ORIGEN,
  ORIGEN_DETALLE,
  APLICA_NTD_1 AS APLICA_NTD,
  NIVEL_NTD,
  NIVEL_NTD_FINAL,
  CASUISTICA_GRUPO,
  /* Usamos REGEXP_REPLACE para eliminar el prefijo */
  REGEXP_REPLACE(CASUISTICA_DETALLE, '^[A-Z]{2}_', '') AS CASUISTICA_DETALLE,
  ACCION_RECOMENDADA,
  ACCION_TOMADA,
  PLANTILLA,
  REINCIDENCIA_NTD_1 AS REINCIDENCIA_NTD
FROM DLAB_GEC.M_EXP_NOT_TO_DO
WHERE UPPER(APLICA_NTD_1) = 'SI'
  AND PERIODO >= 202401
);


REPLACE VIEW DLAB_GEC.V_EXP_NOT_TO_DO_DETALLE AS
(SELECT
    CODIGO_NTD,
    CODIGO,
    PERIODO,
    REG_EJECUTIVO,
    DNI,
    CONID,
    FECHA_AUDIO,
    FECHA_CREADO,
    REG_CREADO,
    FECHA_MODIFICADO,
    REG_MODIFICADO,
    ORIGEN,
    ORIGEN_DETALLE,
    NTD_NUM,
    APLICA_NTD,
    NIVEL_NTD,
    NIVEL_NTD_FINAL,
    CASUISTICA_GRUPO,
    CASUISTICA_DETALLE,
    ACCION_RECOMENDADA,
    REINCIDENCIA_NTD,
    ACCION_TOMADA_FLAG,
    PLANTILLA
FROM
(
    SELECT
        v.*,
        ROW_NUMBER() OVER
        (
            PARTITION BY CODIGO_NTD
            ORDER BY NTD_NUM
        ) AS rn
    FROM
    (
        /* =========================
           NTD 1
           ========================= */
        SELECT
            CODIGO_NTD,
            CODIGO,
            PERIODO,
            REG_EJECUTIVO,
            DNI,
            CONID,
            FECHA_AUDIO,
            FECHA_CREADO,
            REG_CREADO,
            FECHA_MODIFICADO,
            REG_MODIFICADO,
            ORIGEN,
            ORIGEN_DETALLE,
            1 AS NTD_NUM,
            APLICA_NTD_1       AS APLICA_NTD,
            NIVEL_NTD          AS NIVEL_NTD,
            NIVEL_NTD_FINAL    AS NIVEL_NTD_FINAL,
            CASUISTICA_GRUPO   AS CASUISTICA_GRUPO,
            REGEXP_REPLACE(CASUISTICA_DETALLE, '^[A-Z]{2}_', '') AS CASUISTICA_DETALLE,
            ACCION_RECOMENDADA AS ACCION_RECOMENDADA,
            REINCIDENCIA_NTD_1 AS REINCIDENCIA_NTD,
            ACCION_TOMADA      AS ACCION_TOMADA_FLAG,
            PLANTILLA
        FROM DLAB_GEC.M_EXP_NOT_TO_DO
        WHERE UPPER(APLICA_NTD_1) = 'SI'
          AND PERIODO >= 202401

        UNION ALL

        /* =========================
           NTD 2
           ========================= */
        SELECT
            CODIGO_NTD,
            CODIGO,
            PERIODO,
            REG_EJECUTIVO,
            DNI,
            CONID,
            FECHA_AUDIO,
            FECHA_CREADO,
            REG_CREADO,
            FECHA_MODIFICADO,
            REG_MODIFICADO,
            ORIGEN,
            ORIGEN_DETALLE,
            2 AS NTD_NUM,
            APLICA_NTD_2,
            NIVEL_NTD_2,
            NIVEL_NTD_FINAL_2,
            CASUISTICA_GRUPO_2,
            REGEXP_REPLACE(CASUISTICA_DETALLE_2, '^[A-Z]{2}_', ''),
            ACCION_RECOMENDADA_2,
            REINCIDENCIA_NTD_2,
            ACCION_TOMADA,
            PLANTILLA
        FROM DLAB_GEC.M_EXP_NOT_TO_DO
        WHERE UPPER(APLICA_NTD_2) = 'SI'
          AND PERIODO >= 202401

        UNION ALL

        /* =========================
           NTD 3
           ========================= */
        SELECT
            CODIGO_NTD,
            CODIGO,
            PERIODO,
            REG_EJECUTIVO,
            DNI,
            CONID,
            FECHA_AUDIO,
            FECHA_CREADO,
            REG_CREADO,
            FECHA_MODIFICADO,
            REG_MODIFICADO,
            ORIGEN,
            ORIGEN_DETALLE,
            3 AS NTD_NUM,
            APLICA_NTD_3,
            NIVEL_NTD_3,
            NIVEL_NTD_FINAL_3,
            CASUISTICA_GRUPO_3,
            REGEXP_REPLACE(CASUISTICA_DETALLE_3, '^[A-Z]{2}_', ''),
            ACCION_RECOMENDADA_3,
            REINCIDENCIA_NTD_3,
            ACCION_TOMADA,
            PLANTILLA
        FROM DLAB_GEC.M_EXP_NOT_TO_DO
        WHERE UPPER(APLICA_NTD_3) = 'SI'
          AND PERIODO >= 202401
    ) v
) x
WHERE rn = 1
);
