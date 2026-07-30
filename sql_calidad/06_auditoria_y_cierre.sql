-- =====================================================================
-- 06_AUDITORIA_Y_CIERRE.SQL
-- Cierre del proceso de calidad: consolidación del período cerrado
-- {PERIODO_ANTERIOR} en tablas históricas y mapeo de la estructura organizativa.
-- =====================================================================

-- Insertar datos del período cerrado en la tabla consolidadora gerencial
INSERT INTO DLAB_GEC.M_EXP_CALIDAD_NOTAS_TOTAL_GERENCIAL
(
    PERIODO,
    EVALUACIONES,
    REG_EJECUTIVO,
    EJECUTIVO,
    SUPERVISOR,
    JEFE,
    EQUIPO,
    SUB_EQUIPO,
    NUM_EVALUACION,
    NOTA_PC,
    NOTA_SA,
    NOTA_FINAL
)
SELECT 
    PERIODO,
    NULL AS EVALUACIONES,
    REG_EJECUTIVO,
    NULL AS EJECUTIVO,
    NULL AS SUPERVISOR,
    NULL AS JEFE,
    NULL AS EQUIPO,
    NULL AS SUB_EQUIPO,
    NUM_EVALUACION,
    NOTA_PC,
    NOTA_SA,
    NOTA_FINAL
FROM DLAB_GEC.V_EXP_CALIDAD_NOTA_FINAL
WHERE PERIODO = '{PERIODO_ANTERIOR}';

-- Actualizar jerarquías organizativas (Supervisor, Jefe, Equipo, Sub-equipo)
-- a partir de la matriz de personal activo del mes correspondiente
UPDATE DLAB_GEC.M_EXP_CALIDAD_NOTAS_TOTAL_GERENCIAL
FROM   DLAB_GEC.M_EXP_TELEVENTAS_EJECUTIVOS_GROUPED B
SET
    EJECUTIVO   = B.NOM_EJECUTIVO,
    SUPERVISOR  = B.NOM_SUPERVISOR,
    JEFE        = B.NOM_JEFE,
    EQUIPO      = B.EQUIPO,
    SUB_EQUIPO  = B.SUB_EQUIPO
WHERE
    M_EXP_CALIDAD_NOTAS_TOTAL_GERENCIAL.PERIODO = '{PERIODO_ANTERIOR}'
    AND B.PERIODO = '{PERIODO_ANTERIOR}'
    AND M_EXP_CALIDAD_NOTAS_TOTAL_GERENCIAL.REG_EJECUTIVO = B.REG_EJECUTIVO;
