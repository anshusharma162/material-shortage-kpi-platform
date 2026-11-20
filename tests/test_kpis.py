import io

import pandas as pd
import pytest
from openpyxl import load_workbook

from kpi_platform.data import generate
from kpi_platform.kpis import headline, material_kpis, monthly_otd, open_purchase_orders, stock_projection, supplier_otd, to_sqlite
from kpi_platform.report import build_excel


def tiny_tables():
    """One material, hand-made numbers so every KPI can be checked by hand."""
    snap = pd.Timestamp("2026-01-01")
    return {
        "materials": pd.DataFrame([{
            "material_id": "M1", "description": "Bolt", "category": "Fasteners", "plant": "P1",
            "supplier_id": "S1", "unit_cost": 10.0, "lead_time_days": 5, "safety_stock": 20,
        }]),
        "suppliers": pd.DataFrame([{"supplier_id": "S1", "supplier_name": "Acme"}]),
        "inventory": pd.DataFrame([{"material_id": "M1", "snapshot_date": snap, "on_hand_qty": 30}]),
        # 10 per day for 90 days
        "requirements": pd.DataFrame({
            "material_id": "M1",
            "req_date": pd.date_range(snap + pd.Timedelta(days=1), periods=90),
            "required_qty": 10,
        }),
        "purchase_orders": pd.DataFrame([
            # closed, on time, in full
            {"po_number": "P1", "material_id": "M1", "supplier_id": "S1", "order_date": snap - pd.Timedelta(days=30),
             "promised_date": snap - pd.Timedelta(days=20), "ordered_qty": 100,
             "delivered_date": snap - pd.Timedelta(days=21), "delivered_qty": 100},
            # closed, late
            {"po_number": "P2", "material_id": "M1", "supplier_id": "S1", "order_date": snap - pd.Timedelta(days=20),
             "promised_date": snap - pd.Timedelta(days=10), "ordered_qty": 100,
             "delivered_date": snap - pd.Timedelta(days=7), "delivered_qty": 100},
            # open, arrives day 10
            {"po_number": "P3", "material_id": "M1", "supplier_id": "S1", "order_date": snap - pd.Timedelta(days=1),
             "promised_date": snap + pd.Timedelta(days=10), "ordered_qty": 200,
             "delivered_date": pd.NaT, "delivered_qty": None},
        ]),
    }


def test_projection_math():
    p = stock_projection(tiny_tables(), horizon=12).set_index("date").projected_balance
    assert p.iloc[0] == 20          # 30 - 10
    assert p.iloc[3] == -10         # day 4: stockout
    assert p.iloc[9] == 30 - 100 + 200  # day 10: receipt of 200


def test_material_kpis_flags_critical_shortage():
    k = material_kpis(tiny_tables()).iloc[0]
    assert k.coverage_days == pytest.approx(3.0)
    assert k.days_to_stockout == 4
    assert k.status == "Critical"   # stockout (day 4) inside lead time (5 days)
    assert k.shortage_qty == 20     # deepest balance in lead time = -20 on day 5
    assert k.value_at_risk == 200


def test_otd_sql():
    s = supplier_otd(to_sqlite(tiny_tables())).iloc[0]
    assert s.deliveries == 2
    assert s.otd_pct == 50.0
    assert s.otif_pct == 50.0


def test_open_po_detection():
    po = open_purchase_orders(tiny_tables())
    assert list(po.po_number) == ["P3"]


def test_full_dataset_and_excel_export():
    t = generate(n_materials=30, seed=3)
    conn = to_sqlite(t)
    k = material_kpis(t)
    sup = supplier_otd(conn)
    assert len(k) == 30
    assert set(k.status.astype(str)) <= {"Critical", "At Risk", "Healthy", "Excess"}
    h = headline(k, sup)
    assert 0 <= h["otd_pct"] <= 100
    xlsx = build_excel(k, sup, monthly_otd(conn), open_purchase_orders(t), t["inventory"].snapshot_date.max())
    wb = load_workbook(io.BytesIO(xlsx))
    assert wb.sheetnames == ["Summary", "Shortages", "All Materials", "Supplier OTD", "Monthly OTD", "Overdue POs"]
