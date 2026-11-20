"""KPI engine: shortage, stock coverage and on-time delivery.

Definitions
-----------
* **Projected balance** - on-hand stock + open PO receipts (at promised date)
  - cumulative production requirements, day by day for the planning horizon.
* **Stock coverage (days)** - on-hand / average daily requirement over the next 30 days.
* **Shortage** - the projected balance goes below zero. The shortage quantity is
  the deepest negative balance inside the material's replenishment lead time
  (a new order cannot arrive sooner, so this gap must be expedited).
* **Status**
    - Critical : stockout projected within the lead time
    - At Risk  : stockout within lead time + 14 days (order must be placed now),
                 or balance dips below safety stock within the lead time
    - Excess   : coverage above 90 days
    - Healthy  : everything else
* **OTD / OTIF** - see ``sql/delivery_kpis.sql`` (computed in SQL).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SQL_FILE = ROOT / "sql" / "delivery_kpis.sql"
RISK_BUFFER_DAYS = 14
STATUS_ORDER = ["Critical", "At Risk", "Healthy", "Excess"]
DATE_COLS = {
    "inventory": ["snapshot_date"],
    "requirements": ["req_date"],
    "purchase_orders": ["order_date", "promised_date", "delivered_date"],
}


def load(data_dir: str | Path) -> dict[str, pd.DataFrame]:
    d = Path(data_dir)
    names = ["materials", "suppliers", "inventory", "requirements", "purchase_orders"]
    return {n: pd.read_csv(d / f"{n}.csv", parse_dates=DATE_COLS.get(n, [])) for n in names}


def snapshot_date(t: dict[str, pd.DataFrame]) -> pd.Timestamp:
    return t["inventory"].snapshot_date.max()


# --------------------------------------------------------------------------- SQL
def to_sqlite(t: dict[str, pd.DataFrame], path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    for name, df in t.items():
        out = df.copy()
        for c in out.select_dtypes("datetime").columns:
            out[c] = out[c].dt.strftime("%Y-%m-%d")
        out.to_sql(name, conn, index=False, if_exists="replace")
    conn.executescript(SQL_FILE.read_text())
    return conn


def supplier_otd(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM v_supplier_otd ORDER BY otif_pct", conn)


def monthly_otd(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM v_monthly_otd ORDER BY promised_month", conn)


# ----------------------------------------------------------------- projection
def open_purchase_orders(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    snap = snapshot_date(t)
    po = t["purchase_orders"]
    open_po = po[po.delivered_date.isna()].copy()
    open_po["days_overdue"] = (snap - open_po.promised_date).dt.days.clip(lower=0)
    # overdue POs are assumed to arrive tomorrow (optimistic - flagged separately)
    open_po["expected_date"] = open_po.promised_date.where(
        open_po.promised_date > snap, snap + pd.Timedelta(days=1)
    )
    return open_po


def stock_projection(t: dict[str, pd.DataFrame], horizon: int = 90) -> pd.DataFrame:
    """Daily projected balance per material (long format)."""
    snap = snapshot_date(t)
    days = pd.date_range(snap + pd.Timedelta(days=1), periods=horizon)
    mats = t["materials"].material_id
    grid = pd.MultiIndex.from_product([mats, days], names=["material_id", "date"]).to_frame(index=False)

    req = t["requirements"].rename(columns={"req_date": "date"})
    rec = (
        open_purchase_orders(t)
        .groupby(["material_id", "expected_date"], as_index=False)["ordered_qty"].sum()
        .rename(columns={"expected_date": "date", "ordered_qty": "receipt_qty"})
    )
    p = grid.merge(req, on=["material_id", "date"], how="left").merge(rec, on=["material_id", "date"], how="left")
    p[["required_qty", "receipt_qty"]] = p[["required_qty", "receipt_qty"]].fillna(0)
    p = p.merge(t["inventory"][["material_id", "on_hand_qty"]], on="material_id")
    p = p.sort_values(["material_id", "date"])
    p["net"] = p.receipt_qty - p.required_qty
    p["projected_balance"] = p.on_hand_qty + p.groupby("material_id").net.cumsum()
    return p.drop(columns="net")


def material_kpis(t: dict[str, pd.DataFrame], horizon: int = 90) -> pd.DataFrame:
    snap = snapshot_date(t)
    m = t["materials"].merge(t["inventory"][["material_id", "on_hand_qty"]], on="material_id")
    m = m.merge(t["suppliers"], on="supplier_id", how="left")
    proj = stock_projection(t, horizon)
    proj["day"] = (proj.date - snap).dt.days
    proj = proj.merge(m[["material_id", "lead_time_days", "safety_stock"]], on="material_id")

    req30 = proj[proj.day <= 30].groupby("material_id").required_qty.sum() / 30
    stockout = proj[proj.projected_balance < 0].groupby("material_id").date.min()
    in_lt = proj[proj.day <= proj.lead_time_days]
    min_bal_lt = in_lt.groupby("material_id").projected_balance.min()
    below_ss_lt = (in_lt.projected_balance < in_lt.safety_stock).groupby(in_lt.material_id).any()

    opo = open_purchase_orders(t)
    open_qty = opo.groupby("material_id").ordered_qty.sum()
    overdue = opo[opo.days_overdue > 0].groupby("material_id").po_number.count()

    k = m.set_index("material_id")
    k["avg_daily_req"] = req30.reindex(k.index).fillna(0).round(1)
    k["coverage_days"] = np.where(k.avg_daily_req > 0, k.on_hand_qty / k.avg_daily_req.replace(0, np.nan), np.inf)
    k["coverage_days"] = k.coverage_days.round(1)
    k["open_po_qty"] = open_qty.reindex(k.index).fillna(0)
    k["overdue_pos"] = overdue.reindex(k.index).fillna(0).astype(int)
    k["stockout_date"] = stockout.reindex(k.index)
    k["days_to_stockout"] = (k.stockout_date - snap).dt.days
    k["shortage_qty"] = (-min_bal_lt.reindex(k.index).fillna(0)).clip(lower=0)
    k["value_at_risk"] = (k.shortage_qty * k.unit_cost).round(0)
    k["stock_value"] = (k.on_hand_qty * k.unit_cost).round(0)

    critical = k.days_to_stockout.notna() & (k.days_to_stockout <= k.lead_time_days)
    soon = k.days_to_stockout.notna() & (k.days_to_stockout <= k.lead_time_days + RISK_BUFFER_DAYS)
    at_risk = ~critical & (soon | below_ss_lt.reindex(k.index).fillna(False).astype(bool))
    excess = ~critical & ~at_risk & (k.coverage_days > 90)
    k["status"] = np.select([critical, at_risk, excess], ["Critical", "At Risk", "Excess"], "Healthy")
    k["status"] = pd.Categorical(k.status, STATUS_ORDER, ordered=True)

    cols = [
        "description", "plant", "category", "supplier_id", "supplier_name", "status",
        "on_hand_qty", "safety_stock", "avg_daily_req", "coverage_days", "lead_time_days",
        "open_po_qty", "overdue_pos", "stockout_date", "days_to_stockout", "shortage_qty",
        "unit_cost", "value_at_risk", "stock_value",
    ]
    return k[cols].reset_index().sort_values(["status", "days_to_stockout"])


def headline(k: pd.DataFrame, sup: pd.DataFrame) -> dict[str, float]:
    n = len(k)
    w = sup.deliveries.sum()
    return {
        "materials": n,
        "critical": int((k.status == "Critical").sum()),
        "at_risk": int((k.status == "At Risk").sum()),
        "shortage_rate_pct": round(float(100 * (k.status == "Critical").mean()), 1) if n else 0.0,
        "median_coverage_days": float(np.median(k.coverage_days[np.isfinite(k.coverage_days)])) if n else 0.0,
        "value_at_risk": float(k.value_at_risk.sum()),
        "excess_stock_value": float(k.loc[k.status == "Excess", "stock_value"].sum()),
        "otd_pct": round(float((sup.otd_pct * sup.deliveries).sum() / w), 1) if w else 0.0,
        "otif_pct": round(float((sup.otif_pct * sup.deliveries).sum() / w), 1) if w else 0.0,
    }
