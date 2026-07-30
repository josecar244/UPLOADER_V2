-- ==============================================================================
-- PROCESO: CONSUMO_SELECT_TC_CD_SEG (Tarjetas de Crédito, Compra de Deuda y Seguros Select)
-- OPTIMIZACIÓN:
--   1. Vaciado e inserción limpia (DELETE/INSERT INTO) para evitar la recreación de la tabla.
--   2. Parametrización del periodo (:periodo).
--   3. Sin la cláusula SAMPLE 100 para cargar todo el dataset mensual.
-- ==============================================================================

-- [PASO 1]: Limpiar e insertar en M_EXP_CONSUMO_SELECT_TC_CD_SEG
DELETE FROM DLAB_GEC.M_EXP_CONSUMO_SELECT_TC_CD_SEG ALL;

INSERT INTO DLAB_GEC.M_EXP_CONSUMO_SELECT_TC_CD_SEG (
    CODMES, 
    CODPROMOT, 
    NOMPROMOT, 
    CODDOC, 
    CLIENTE, 
    FECDESEMBOLSO, 
    PRODUCTO, 
    SUBPRODUCTO, 
    MONTO, 
    CAMP_FLG
)
SELECT 
    vc.CODMES, 
    cl.REGEJECUTIVO AS CODPROMOT,
    cl.NOMCORTO AS NOMPROMOT,
    cl.CODDOC AS CODDOC, 
    cl.nomcliente AS CLIENTE, 
    vc.FEC_VENTA AS FECDESEMBOLSO, 
    CASE 
        WHEN VC.PRODUCTO IN (
            'TARJETAS ACTIVADAS',
            'TARJETAS APROBADAS',
            'TARJETAS ENTREGADAS',
            'UPGRADE TARJETA DE CREDITO',
            'TARJETAS ADICIONALES'
        ) THEN VC.PRODUCTO
        WHEN VC.PRODUCTO LIKE '%COMPRA DE DEUDA%' THEN 'COMPRA DE DEUDA'
        WHEN VC.PRODUCTO LIKE '%SEGUROS%' THEN 'SEGUROS'
        ELSE 'VALIDAR'
    END AS PRODUCTO,
    VC.NUEVO_SUBPRODUCTO AS SUBPRODUCTO, 
    VC.MONTO AS MONTO,
    VC.FLG_CAMP AS CAMP_FLG 
FROM e_dw_views.V_AGG_VENTAS_CONSOLIDADAS vc
INNER JOIN E_DW_VIEWS.V_CARTERA_CLIENTE_HIST cl 
    ON RIGHT(vc.codunicocli, 10) = cl.codunico 
    AND cl.codmes = vc.codmes
WHERE vc.EQUIPO = 'INTERBANK SELECT' 
  AND vc.CODMES = :periodo
  AND (
      VC.PRODUCTO IN (
          'TARJETAS ACTIVADAS',
          'TARJETAS APROBADAS',
          'TARJETAS ENTREGADAS',
          'UPGRADE TARJETA DE CREDITO',
          'TARJETAS ADICIONALES'
      )
      OR VC.PRODUCTO LIKE '%COMPRA DE DEUDA%'
      OR VC.PRODUCTO LIKE '%SEGUROS%'
  );

COLLECT STATISTICS ON DLAB_GEC.M_EXP_CONSUMO_SELECT_TC_CD_SEG COLUMN (CODDOC, CODPROMOT);
