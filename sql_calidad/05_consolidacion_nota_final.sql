-- =====================================================================
-- 05_CONSOLIDACION_NOTA_FINAL.SQL
-- Consolida las notas de evaluaciones manuales y Speech Analytics,
-- aplica pesos finales, caps y puebla el histórico de errores de calidad.
-- =====================================================================



-- Limpiar registros del período objetivo para evitar duplicación al re-ejecutar
DELETE FROM DLAB_GEC.M_EXP_CALIDAD_NOTA_FINAL
WHERE PERIODO >= '{PERIODO}';

-- 1. Obtener sub_equipo por ejecutivo
CREATE VOLATILE TABLE VT_EXEC AS (
    SELECT
        REG_EJECUTIVO,
        TRIM(SUB_EQUIPO) AS SUB_EQUIPO
    FROM DLAB_GEC.M_EXP_TELEVENTAS_EJECUTIVOS
) WITH DATA PRIMARY INDEX (REG_EJECUTIVO) ON COMMIT PRESERVE ROWS;


-- 2. Agregar notas PC manuales por ejecutivo-período-número
CREATE VOLATILE TABLE VT_PC AS (
    SELECT
        a.PERIODO,
        a.REG_EJECUTIVO,
        a.NUM_EVALUACION,
        a.PERIODO || '_' || a.REG_EJECUTIVO AS CODIGO,
        (SUM(a.PESO_PREGUNTA_OBT) / 100.0) AS NOTA_PC_RAW
    FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_PURE_CLOUD AS a
    WHERE a.NUM_EVALUACION <> 0
    GROUP BY 1,2,3,4
) WITH DATA PRIMARY INDEX (CODIGO, NUM_EVALUACION) ON COMMIT PRESERVE ROWS;


-- 3. Deduplicar Speech Analytics por categoría para evitar inflación artificial
CREATE VOLATILE TABLE VT_SA_CAT AS (
    SELECT
        CODIGO,
        NEVALUACION,
        TRIM(CATEGORIA) AS CATEGORIA,
        MAX(PUNTAJE)    AS PUNTAJE_CAT
    FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS
    GROUP BY 1,2,3
) WITH DATA PRIMARY INDEX (CODIGO, NEVALUACION) ON COMMIT PRESERVE ROWS;


-- 4. Agregar puntajes de Speech Analytics
CREATE VOLATILE TABLE VT_SA AS (
    SELECT
        CODIGO,
        NEVALUACION,
        SUM(PUNTAJE_CAT) AS NOTA_SA_RAW
    FROM VT_SA_CAT
    GROUP BY 1,2
) WITH DATA PRIMARY INDEX (CODIGO, NEVALUACION) ON COMMIT PRESERVE ROWS;


-- 5. Dataset base para ejecutivos mixtos y select
CREATE VOLATILE TABLE VT_BASE_PC AS (
    -- Mixtos
    SELECT
        pc.CODIGO,
        pc.PERIODO,
        pc.REG_EJECUTIVO,
        pc.NUM_EVALUACION,
        pc.NOTA_PC_RAW,
        COALESCE(sa.NOTA_SA_RAW, 0.0) AS NOTA_SA_RAW,
        ex.SUB_EQUIPO
    FROM VT_PC AS pc
    INNER JOIN VT_EXEC AS ex ON pc.REG_EJECUTIVO = ex.REG_EJECUTIVO
    LEFT JOIN VT_SA AS sa ON pc.CODIGO = sa.CODIGO AND pc.NUM_EVALUACION = sa.NEVALUACION
    WHERE ex.SUB_EQUIPO IN ('BNB','BNC','CD','CONV_TLV','EC','PP','HIP','R_CO','R_MULTI','SEG','TC')

    UNION ALL

    -- Select: solo manual PC (SA = 0)
    SELECT
        pc.CODIGO,
        pc.PERIODO,
        pc.REG_EJECUTIVO,
        pc.NUM_EVALUACION,
        pc.NOTA_PC_RAW,
        0.0 AS NOTA_SA_RAW,
        ex.SUB_EQUIPO
    FROM VT_PC AS pc
    INNER JOIN VT_EXEC AS ex ON pc.REG_EJECUTIVO = ex.REG_EJECUTIVO
    WHERE ex.SUB_EQUIPO = 'SELECT'
) WITH DATA PRIMARY INDEX (CODIGO, NUM_EVALUACION) ON COMMIT PRESERVE ROWS;


-- 6. Obtener histórico de notas gerenciales
CREATE VOLATILE TABLE VT_BASE_GER AS (
    SELECT
        PERIODO || '_' || REG_EJECUTIVO AS CODIGO,
        PERIODO,
        REG_EJECUTIVO,
        NUM_EVALUACION,
        COALESCE(NOTA_PC, 0.0) AS NOTA_PC_RAW,
        COALESCE(NOTA_SA, 0.0) AS NOTA_SA_RAW,
        TRIM(SUB_EQUIPO)       AS SUB_EQUIPO
    FROM DLAB_GEC.M_EXP_CALIDAD_NOTAS_TOTAL_GERENCIAL
    WHERE PERIODO >= '202501'
) WITH DATA PRIMARY INDEX (CODIGO, NUM_EVALUACION) ON COMMIT PRESERVE ROWS;


-- 7. Combinar todos los conjuntos de datos
CREATE VOLATILE TABLE VT_ALL AS (
    SELECT * FROM VT_BASE_PC
    UNION ALL
    SELECT * FROM VT_BASE_GER
) WITH DATA PRIMARY INDEX (CODIGO, NUM_EVALUACION) ON COMMIT PRESERVE ROWS;


-- 8. Aplicar límites finales e insertar en tabla productiva: PC máx 0.4, SA máx 0.6, Nota Final máx 1.0
INSERT INTO DLAB_GEC.M_EXP_CALIDAD_NOTA_FINAL
(
    CODIGO,
    PERIODO,
    REG_EJECUTIVO,
    NUM_EVALUACION,
    NOTA_PC,
    NOTA_SA,
    NOTA_FINAL,
    SUB_EQUIPO
)
SELECT
    t_all.CODIGO,
    t_all.PERIODO,
    t_all.REG_EJECUTIVO,
    t_all.NUM_EVALUACION,

    -- Normalización para NOTA_PC
    CASE
        WHEN t_all.SUB_EQUIPO = 'SELECT' THEN
        	CASE
                WHEN t_all.NOTA_PC_RAW IS NULL THEN 0.0
                WHEN t_all.NOTA_PC_RAW < 0.0   THEN 0.0
                ELSE t_all.NOTA_PC_RAW
            END
        ELSE
            CASE
                WHEN t_all.NOTA_PC_RAW IS NULL THEN 0.0
                WHEN t_all.NOTA_PC_RAW < 0.0   THEN 0.0
                WHEN t_all.NOTA_PC_RAW > 0.4   THEN 0.4
                ELSE t_all.NOTA_PC_RAW
            END
    END AS NOTA_PC,

    -- Normalización para NOTA_SA
    CASE
        WHEN t_all.SUB_EQUIPO = 'SELECT' THEN 0.0
        ELSE
            CASE
                WHEN t_all.NOTA_SA_RAW IS NULL THEN 0.0
                WHEN t_all.NOTA_SA_RAW < 0.0   THEN 0.0
                WHEN t_all.NOTA_SA_RAW > 0.6   THEN 0.6
                ELSE t_all.NOTA_SA_RAW
            END
    END AS NOTA_SA,

    -- Normalización para NOTA_FINAL
    CASE
        WHEN (
            -- Cálculo idéntico de NOTA_PC
            CASE
                WHEN t_all.SUB_EQUIPO = 'SELECT' THEN
		        	CASE
		                WHEN t_all.NOTA_PC_RAW IS NULL THEN 0.0
		                WHEN t_all.NOTA_PC_RAW < 0.0   THEN 0.0
		                ELSE t_all.NOTA_PC_RAW
		            END
                ELSE
                    CASE
                        WHEN t_all.NOTA_PC_RAW IS NULL THEN 0.0
                        WHEN t_all.NOTA_PC_RAW < 0.0   THEN 0.0
                        WHEN t_all.NOTA_PC_RAW > 0.4   THEN 0.4
                        ELSE t_all.NOTA_PC_RAW
                    END
            END
            +
            -- Cálculo idéntico de NOTA_SA
            CASE
                WHEN t_all.SUB_EQUIPO = 'SELECT' THEN 0.0
                ELSE
                    CASE
                        WHEN t_all.NOTA_SA_RAW IS NULL THEN 0.0
                        WHEN t_all.NOTA_SA_RAW < 0.0   THEN 0.0
                        WHEN t_all.NOTA_SA_RAW > 0.6   THEN 0.6
                        ELSE t_all.NOTA_SA_RAW
                    END
            END
        ) > 1.0 THEN 1.0
        ELSE (
            CASE
                WHEN t_all.SUB_EQUIPO = 'SELECT' THEN
		        	CASE
		                WHEN t_all.NOTA_PC_RAW IS NULL THEN 0.0
		                WHEN t_all.NOTA_PC_RAW < 0.0   THEN 0.0
		                ELSE t_all.NOTA_PC_RAW
		            END
                ELSE
                    CASE
                        WHEN t_all.NOTA_PC_RAW IS NULL THEN 0.0
                        WHEN t_all.NOTA_PC_RAW < 0.0   THEN 0.0
                        WHEN t_all.NOTA_PC_RAW > 0.4   THEN 0.4
                        ELSE t_all.NOTA_PC_RAW
                    END
            END
            +
            CASE
                WHEN t_all.SUB_EQUIPO = 'SELECT' THEN 0.0
                ELSE
                    CASE
                        WHEN t_all.NOTA_SA_RAW IS NULL THEN 0.0
                        WHEN t_all.NOTA_SA_RAW < 0.0   THEN 0.0
                        WHEN t_all.NOTA_SA_RAW > 0.6   THEN 0.6
                        ELSE t_all.NOTA_SA_RAW
                    END
            END
        )
    END AS NOTA_FINAL,

    t_all.SUB_EQUIPO
FROM VT_ALL AS t_all;


-- 9. Dropeo de tablas volátiles para limpiar spool
DROP TABLE VT_EXEC;
DROP TABLE VT_PC;
DROP TABLE VT_SA_CAT;
DROP TABLE VT_SA;
DROP TABLE VT_BASE_PC;
DROP TABLE VT_BASE_GER;
DROP TABLE VT_ALL;

-- -------------------------------------------------------------
-- HISTÓRICO DE ERRORES DE CALIDAD
-- -------------------------------------------------------------

-- Limpiar registros existentes del período para evitar duplicación por re-ejecución
DELETE FROM DLAB_GEC.M_EXP_HIST_ERRORES_CALIDAD
WHERE PERIODO = '{PERIODO}';

-- Registrar evaluaciones donde ocurrieron errores
INSERT INTO DLAB_GEC.M_EXP_HIST_ERRORES_CALIDAD
SELECT
    CODIGO,
    PERIODO,
    FECHA_CREADO,
    REG_EJECUTIVO,
    CONID,
    GRUPO_PREGUNTAS,
    PREGUNTA,
    RESPUESTA,
    PREG_VALIDA,
    PREG_CRITICA,
    ES_ERROR,
    COMENTARIO,
    CURRENT_TIMESTAMP(6) AS FECHA_CARGA
FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_PURE_CLOUD
WHERE ES_ERROR = 1
  AND GRUPO_PREGUNTAS NOT IN (
        'AUDITORIAS',
        'DATOS EVALUACION',
        'NOT TO DO'
    );


