-- =====================================================================
-- 04_B_SA_PARCHE_NOTA_CERO.SQL
-- Identifica de forma automática evaluaciones de Speech Analytics
-- con nota final 0 para ejecutivos mixtos y las reemplaza con la 
-- evaluación de puntuación máxima correspondiente a su producto/sala.
-- Excluye explícitamente al subequipo SELECT.
-- =====================================================================

-- -------------------------------------------------------------
-- PASO 1: IDENTIFICAR LOS CASOS CON NOTA MÁXIMA POR PRODUCTO
-- -------------------------------------------------------------
CREATE VOLATILE TABLE VT_MAX_SA_CASES AS (
    SELECT
        sa.PERIODO,
        sa.PRODUCTO,
        sa.NEVALUACION,
        sa.REG_EV AS BASE_REG_EV,
        t_total.TOTAL_PUNTAJE
    FROM (
        SELECT
            s.PERIODO,
            s.PRODUCTO,
            s.NEVALUACION,
            s.REG_EV,
            SUM(s.PUNTAJE) AS TOTAL_PUNTAJE,
            ROW_NUMBER() OVER (
                PARTITION BY s.PERIODO, s.PRODUCTO, s.NEVALUACION 
                ORDER BY SUM(s.PUNTAJE) DESC, s.REG_EV ASC
            ) AS rn
        FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS s
        INNER JOIN DLAB_GEC.M_EXP_TELEVENTAS_EJECUTIVOS eje
            ON eje.REG_EJECUTIVO = s.REG_EV
        WHERE s.PERIODO = '{PERIODO}'
          AND TRIM(eje.SUB_EQUIPO) <> 'SELECT'
        GROUP BY 1,2,3,4
    ) t_total
    INNER JOIN DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS sa
        ON sa.PERIODO = t_total.PERIODO
       AND sa.PRODUCTO = t_total.PRODUCTO
       AND sa.NEVALUACION = t_total.NEVALUACION
       AND sa.REG_EV = t_total.REG_EV
    WHERE t_total.rn = 1 
      AND t_total.TOTAL_PUNTAJE > 0
    GROUP BY 1,2,3,4,5
) WITH DATA PRIMARY INDEX (PERIODO, PRODUCTO, NEVALUACION) ON COMMIT PRESERVE ROWS;


-- -------------------------------------------------------------
-- PASO 1.5: DETERMINAR EL PRODUCTO PRINCIPAL POR SUB_EQUIPO EN EL PERIODO
-- -------------------------------------------------------------
CREATE VOLATILE TABLE VT_SUB_EQUIPO_PRODUCTO AS (
    SELECT
        TRIM(eje.SUB_EQUIPO) AS SUB_EQUIPO,
        sa.PRODUCTO,
        ROW_NUMBER() OVER (
            PARTITION BY TRIM(eje.SUB_EQUIPO)
            ORDER BY COUNT(*) DESC
        ) AS rn
    FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS sa
    INNER JOIN DLAB_GEC.M_EXP_TELEVENTAS_EJECUTIVOS eje
        ON eje.REG_EJECUTIVO = sa.REG_EV
    WHERE sa.PERIODO = '{PERIODO}'
    GROUP BY 1,2
) WITH DATA PRIMARY INDEX (SUB_EQUIPO) ON COMMIT PRESERVE ROWS;


-- -------------------------------------------------------------
-- PASO 2: IDENTIFICAR EJECUTIVOS CON NOTA 0 O SIN REGISTROS EN SA (EXCLUYENDO SELECT)
-- -------------------------------------------------------------
CREATE VOLATILE TABLE VT_ZERO_SA_CASES AS (
    -- Caso A: Tienen registros en SA pero su suma de puntaje es 0
    SELECT
        s.PERIODO,
        s.PRODUCTO,
        s.NEVALUACION,
        s.REG_EV,
        s.CODIGO
    FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS s
    INNER JOIN DLAB_GEC.M_EXP_TELEVENTAS_EJECUTIVOS eje
        ON eje.REG_EJECUTIVO = s.REG_EV
    WHERE s.PERIODO = '{PERIODO}'
      AND TRIM(eje.SUB_EQUIPO) <> 'SELECT'
    GROUP BY 1,2,3,4,5
    HAVING SUM(s.PUNTAJE) = 0

    UNION

    -- Caso B: Tienen evaluaciones en PC pero no existen en SA (nueva casuística)
    SELECT
        pc.PERIODO,
        COALESCE(map_prod.PRODUCTO, 'TC') AS PRODUCTO,
        pc.NUM_EVALUACION AS NEVALUACION,
        pc.REG_EJECUTIVO AS REG_EV,
        pc.CODIGO
    FROM (
        SELECT 
            PERIODO, 
            REG_EJECUTIVO, 
            NUM_EVALUACION,
            CODIGO
        FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_PURE_CLOUD
        WHERE PERIODO = '{PERIODO}'
          AND NUM_EVALUACION <> 0
        GROUP BY 1,2,3,4
    ) pc
    INNER JOIN DLAB_GEC.M_EXP_TELEVENTAS_EJECUTIVOS eje
        ON eje.REG_EJECUTIVO = pc.REG_EJECUTIVO
    LEFT JOIN (
        SELECT SUB_EQUIPO, PRODUCTO 
        FROM VT_SUB_EQUIPO_PRODUCTO 
        WHERE rn = 1
    ) map_prod
        ON map_prod.SUB_EQUIPO = TRIM(eje.SUB_EQUIPO)
    LEFT JOIN DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS sa
        ON sa.PERIODO = pc.PERIODO
       AND sa.REG_EV = pc.REG_EJECUTIVO
       AND sa.NEVALUACION = pc.NUM_EVALUACION
    WHERE TRIM(eje.SUB_EQUIPO) <> 'SELECT'
      AND sa.REG_EV IS NULL
) WITH DATA PRIMARY INDEX (PERIODO, PRODUCTO, NEVALUACION, REG_EV) ON COMMIT PRESERVE ROWS;


-- -------------------------------------------------------------
-- PASO 3: BORRAR REGISTROS DE EVALUACIÓN DE NOTA 0
-- -------------------------------------------------------------
DELETE FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS
WHERE PERIODO = '{PERIODO}'
  AND (PRODUCTO, NEVALUACION, REG_EV) IN (
      SELECT PRODUCTO, NEVALUACION, REG_EV FROM VT_ZERO_SA_CASES
  );


-- -------------------------------------------------------------
-- PASO 4: REPLICAR CASOS DE NOTA MÁXIMA EN LOS DESTINOS PARCHEADOS
-- -------------------------------------------------------------
INSERT INTO DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS
(
    PERIODO,
    PRODUCTO,
    REG_EV,
    CATEGORIA,
    OBTENIDO,
    NEVALUACION,
    ESPERADO,
    PESO_CATEGORIA,
    PESO_GRUPO,
    PUNTAJE,
    FLAG,
    GRUPO_CATEGORIA,
    CODIGO
)
SELECT
    det.PERIODO,
    det.PRODUCTO,
    z.REG_EV,
    det.CATEGORIA,
    det.OBTENIDO,
    det.NEVALUACION,
    det.ESPERADO,
    det.PESO_CATEGORIA,
    det.PESO_GRUPO,
    det.PUNTAJE,
    det.FLAG,
    det.GRUPO_CATEGORIA,
    det.PERIODO || '_' || z.REG_EV AS CODIGO
FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS det
INNER JOIN VT_MAX_SA_CASES m
    ON det.PERIODO = m.PERIODO
   AND det.PRODUCTO = m.PRODUCTO
   AND det.NEVALUACION = m.NEVALUACION
   AND det.REG_EV = m.BASE_REG_EV
INNER JOIN VT_ZERO_SA_CASES z
    ON z.PERIODO = m.PERIODO
   AND z.PRODUCTO = m.PRODUCTO
   AND z.NEVALUACION = m.NEVALUACION;


-- -------------------------------------------------------------
-- PASO 5: LIMPIEZA DE TABLAS VOLÁTILES
-- -------------------------------------------------------------
DROP TABLE VT_MAX_SA_CASES;
DROP TABLE VT_SUB_EQUIPO_PRODUCTO;
DROP TABLE VT_ZERO_SA_CASES;
