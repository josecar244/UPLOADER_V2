-- =====================================================================
-- 04_SA_AJUSTES_CURVA.SQL
-- Aplica los pesos de la Maestra de Speech Analytics, calcula puntajes,
-- carga la tabla de detalle y ejecuta las curvas de ajuste y topes.
-- =====================================================================

-- Quitar espacios en blanco en códigos de categoría y producto en la tabla volátil
UPDATE VT_EXP_CALIDAD_PESOS_SA SET CATEGORIA = TRIM(CATEGORIA);
UPDATE VT_EXP_CALIDAD_PESOS_SA SET PRODUCTO = TRIM(PRODUCTO);

-- Actualizar categorías desde la Tabla Maestra
UPDATE t_pesos
FROM   VT_EXP_CALIDAD_PESOS_SA      AS t_pesos,
       DLAB_GEC.M_EXP_MAESTRA_PESOS_SA      AS t_maestra
SET
    PESO_CATEGORIA = t_maestra.PESO_CATEGORIA,
    PESO_GRUPO     = t_maestra.PESO_GRUPO,
    ESPERADO       = t_maestra.ESPERADO,
    FLAG           = t_maestra.FLAG,
    GRUPO_CATEGORIA = TRIM(t_maestra.GRUPO_CATEGORIA)
WHERE TRIM(t_pesos.CATEGORIA) = TRIM(t_maestra.CATEGORIA)
  AND TRIM(t_pesos.PRODUCTO)  = TRIM(t_maestra.PRODUCTO);

-- Excluir categorías que no cruzan con la maestra
DELETE FROM VT_EXP_CALIDAD_PESOS_SA
WHERE GRUPO_CATEGORIA IS NULL;

-- Llenar valores nulos en la columna de obtenido
UPDATE VT_EXP_CALIDAD_PESOS_SA
SET OBTENIDO =
    CASE
        WHEN OBTENIDO IS NULL THEN 0.0
        ELSE OBTENIDO
    END;

-- Calcular puntajes para categorías de detalle (FLAG = 0)
UPDATE t_pesos
FROM   VT_EXP_CALIDAD_PESOS_SA AS t_pesos
SET
PUNTAJE =
    CASE
        WHEN t_pesos.ESPERADO IS NULL OR t_pesos.ESPERADO = 0 THEN 0.0
        WHEN t_pesos.OBTENIDO IS NULL THEN 0.0
        WHEN t_pesos.OBTENIDO >= t_pesos.ESPERADO THEN t_pesos.PESO_CATEGORIA
        ELSE (t_pesos.OBTENIDO / t_pesos.ESPERADO) * t_pesos.PESO_CATEGORIA
    END
WHERE t_pesos.FLAG = 0;

-- Calcular factores para categorías a nivel de grupo (FLAG = 1)
-- (Se usa tabla volátil en lugar de tabla temporal física en base de datos)
CREATE VOLATILE TABLE VT_FACTORES_GRUPO AS (
    SELECT
        TRIM(PRODUCTO)        AS PRODUCTO,
        TRIM(GRUPO_CATEGORIA) AS GRUPO_CATEGORIA,
        CASE
            WHEN SUM(CASE WHEN OBTENIDO IS NULL THEN 1 ELSE 0 END) > 0 THEN 0.90
            WHEN SUM(CASE WHEN OBTENIDO < ESPERADO THEN 1 ELSE 0 END) > 0 THEN 0.95
            WHEN SUM(CASE WHEN OBTENIDO >= ESPERADO THEN 1 ELSE 0 END) > 0 THEN 1.00
            ELSE NULL
        END AS FACTOR
    FROM VT_EXP_CALIDAD_PESOS_SA
    WHERE FLAG = 1
    GROUP BY 1,2
) WITH DATA PRIMARY INDEX (PRODUCTO, GRUPO_CATEGORIA) ON COMMIT PRESERVE ROWS;

-- Actualizar puntaje de grupos aplicando el factor
UPDATE t_pesos
FROM   VT_EXP_CALIDAD_PESOS_SA AS t_pesos,
       VT_FACTORES_GRUPO    AS t_fact
SET
PUNTAJE = 
    CASE 
        WHEN t_fact.FACTOR IS NOT NULL
            THEN t_fact.FACTOR * t_pesos.PESO_CATEGORIA
        ELSE t_pesos.PUNTAJE
    END
WHERE t_pesos.FLAG = 1
  AND TRIM(t_pesos.PRODUCTO)        = t_fact.PRODUCTO
  AND TRIM(t_pesos.GRUPO_CATEGORIA) = t_fact.GRUPO_CATEGORIA;

-- Eliminar tabla volátil de factores
DROP TABLE VT_FACTORES_GRUPO;

-- Limpiar tabla física de detalle de Speech Analytics
DELETE FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS ALL;

-- Poblar la tabla de detalle desde la tabla volátil
INSERT INTO DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS
SELECT * FROM VT_EXP_CALIDAD_PESOS_SA;


-- Aplicar suavizado de curvas para categorías de campañas tipo mixto
UPDATE det_sa
FROM   DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS AS det_sa,
       (
           SELECT
               sa.CODIGO,
               sa.NEVALUACION,
               CASE
                   WHEN SUM(sa.PUNTAJE) >= 0.59 THEN 1.0
                   WHEN SUM(sa.PUNTAJE) =  0.00 THEN 1.0
                   WHEN SUM(sa.PUNTAJE) <  0.45 THEN (0.56 / SUM(sa.PUNTAJE))
                   WHEN SUM(sa.PUNTAJE) <  0.52 THEN (0.57 / SUM(sa.PUNTAJE))
                   ELSE                          (0.58 / SUM(sa.PUNTAJE))
               END AS FACTOR_AJUSTE
           FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS AS sa
           INNER JOIN DLAB_GEC.M_EXP_TELEVENTAS_EJECUTIVOS AS eje
               ON eje.REG_EJECUTIVO = SUBSTR(sa.CODIGO, 8)
           WHERE TRIM(eje.SUB_EQUIPO) IN ('BNB','BNC','CD','CONV_TLV','EC','PP','HIP','R_CO','R_MULTI','SEG','TC')
           GROUP BY 1,2
           HAVING SUM(sa.PUNTAJE) > 0.0
              AND SUM(sa.PUNTAJE) < 0.59
       ) AS fac
SET
    PUNTAJE = det_sa.PUNTAJE * fac.FACTOR_AJUSTE
WHERE det_sa.CODIGO      = fac.CODIGO
  AND det_sa.NEVALUACION = fac.NEVALUACION;

-- Aplicar tope final (máximo 0.6) a las puntuaciones SA en campañas tipo mixto
UPDATE det_sa
FROM   DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS AS det_sa,
       (
           SELECT
               sa.CODIGO,
               sa.NEVALUACION,
               (0.6 / SUM(sa.PUNTAJE)) AS FACTOR_CAP
           FROM DLAB_GEC.M_EXP_CALIDAD_DETALLE_SPEECH_ANALYTICS AS sa
           INNER JOIN DLAB_GEC.M_EXP_TELEVENTAS_EJECUTIVOS AS eje
               ON eje.REG_EJECUTIVO = SUBSTR(sa.CODIGO, 8)
           WHERE TRIM(eje.SUB_EQUIPO) IN ('BNB','BNC','CD','CONV_TLV','EC','PP','HIP','R_CO','R_MULTI','SEG','TC')
           GROUP BY 1,2
           HAVING SUM(sa.PUNTAJE) > 0.6
       ) AS cap
SET
    PUNTAJE = det_sa.PUNTAJE * cap.FACTOR_CAP
WHERE det_sa.CODIGO      = cap.CODIGO
  AND det_sa.NEVALUACION = cap.NEVALUACION;
