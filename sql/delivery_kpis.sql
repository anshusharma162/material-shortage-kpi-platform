-- Supplier delivery performance (SQLite / PostgreSQL compatible)
-- On-time  : delivered on or before the promised date
-- In-full  : delivered quantity >= ordered quantity
-- OTIF     : both at once
-- Only closed (delivered) purchase orders are scored.

DROP VIEW IF EXISTS v_po_scored;
CREATE VIEW v_po_scored AS
SELECT
    po.po_number,
    po.material_id,
    po.supplier_id,
    s.supplier_name,
    m.plant,
    m.category,
    substr(po.promised_date, 1, 7)                                    AS promised_month,
    po.promised_date,
    po.delivered_date,
    julianday(po.delivered_date) - julianday(po.promised_date)       AS delay_days,
    CASE WHEN po.delivered_date <= po.promised_date THEN 1 ELSE 0 END AS on_time,
    CASE WHEN po.delivered_qty >= po.ordered_qty THEN 1 ELSE 0 END     AS in_full
FROM purchase_orders po
JOIN suppliers s ON s.supplier_id = po.supplier_id
JOIN materials m ON m.material_id = po.material_id
WHERE po.delivered_date IS NOT NULL;

DROP VIEW IF EXISTS v_supplier_otd;
CREATE VIEW v_supplier_otd AS
SELECT
    supplier_id,
    supplier_name,
    COUNT(*)                                        AS deliveries,
    ROUND(100.0 * AVG(on_time), 1)                  AS otd_pct,
    ROUND(100.0 * AVG(in_full), 1)                  AS in_full_pct,
    ROUND(100.0 * AVG(on_time * in_full), 1)        AS otif_pct,
    ROUND(AVG(CASE WHEN delay_days > 0 THEN delay_days END), 1) AS avg_days_late
FROM v_po_scored
GROUP BY supplier_id, supplier_name;

DROP VIEW IF EXISTS v_monthly_otd;
CREATE VIEW v_monthly_otd AS
SELECT
    promised_month,
    plant,
    supplier_id,
    COUNT(*)                           AS deliveries,
    SUM(on_time)                       AS on_time,
    SUM(on_time * in_full)             AS otif
FROM v_po_scored
GROUP BY promised_month, plant, supplier_id;
