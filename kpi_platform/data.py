"""Sample ERP-style data for a manufacturing planning team.

Tables (all CSV):
* materials.csv        material master: plant, category, supplier, lead time, safety stock, cost
* suppliers.csv        supplier master
* inventory.csv        on-hand stock per material at the snapshot date
* requirements.csv     daily production requirements (MRP demand) for the next 90 days
* purchase_orders.csv  12 months of POs: promised vs actual delivery date and quantity
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

PLANTS = ["Chennai-P1", "Pune-P2", "Sanand-P3"]
CATEGORIES = ["Electronics", "Fasteners", "Castings", "Plastics", "Packaging", "Chemicals"]
SUPPLIER_NAMES = [
    "Apex Components", "Bharat Castings", "Coastal Polymers", "Deccan Fasteners",
    "Eastern Electronics", "Fortune Packaging", "Ganga Chemicals", "Horizon Metals",
    "Indus Plastics", "Jupiter Electricals",
]


def generate(n_materials: int = 150, snapshot: str = "2026-11-15", seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    snap = pd.Timestamp(snapshot)

    suppliers = pd.DataFrame(
        {
            "supplier_id": [f"S{i:02d}" for i in range(1, len(SUPPLIER_NAMES) + 1)],
            "supplier_name": SUPPLIER_NAMES,
            # hidden reliability drives on-time behaviour
            "_reliability": rng.uniform(0.62, 0.97, len(SUPPLIER_NAMES)),
        }
    )

    cat = rng.choice(CATEGORIES, n_materials)
    materials = pd.DataFrame(
        {
            "material_id": [f"M{i:05d}" for i in range(10001, 10001 + n_materials)],
            "description": [f"{c[:-1] if c.endswith('s') else c} part {i}" for i, c in enumerate(cat, 1)],
            "category": cat,
            "plant": rng.choice(PLANTS, n_materials),
            "supplier_id": rng.choice(suppliers.supplier_id, n_materials),
            "unit_cost": rng.lognormal(4, 1.0, n_materials).round(2),
            "lead_time_days": rng.choice([7, 10, 14, 21, 30, 45], n_materials),
            "avg_daily_demand": rng.gamma(2.0, 25, n_materials).round(1),
        }
    )
    materials["safety_stock"] = (materials.avg_daily_demand * rng.uniform(3, 10, n_materials)).round()

    # ----- requirements: next 90 days -----
    days = pd.date_range(snap + pd.Timedelta(days=1), periods=90)
    req_rows = []
    for m in materials.itertuples():
        weekly = np.where(days.dayofweek == 6, 0.0, 1.0)  # no production on Sunday
        ramp = 1 + rng.uniform(-0.2, 0.4) * np.arange(90) / 90
        qty = rng.poisson(m.avg_daily_demand * weekly * ramp)
        req_rows += [(m.material_id, d, int(q)) for d, q in zip(days, qty) if q > 0]
    requirements = pd.DataFrame(req_rows, columns=["material_id", "req_date", "required_qty"])

    # ----- inventory snapshot: mix of healthy, short and excess -----
    cover = rng.choice([2, 5, 10, 20, 35, 60, 120], n_materials, p=[0.08, 0.12, 0.2, 0.25, 0.17, 0.12, 0.06])
    inventory = pd.DataFrame(
        {
            "material_id": materials.material_id,
            "snapshot_date": snap,
            "on_hand_qty": (materials.avg_daily_demand * cover * rng.uniform(0.7, 1.3, n_materials)).round(),
        }
    )

    # ----- purchase orders: 12 months history + open orders -----
    rel = suppliers.set_index("supplier_id")._reliability
    po_rows, po_n = [], 1
    for m in materials.itertuples():
        r = rel[m.supplier_id]
        order_dates = pd.date_range(snap - pd.Timedelta(days=365), snap, freq=f"{int(rng.integers(10, 30))}D")
        for od in order_dates:
            qty = int(max(1, m.avg_daily_demand * rng.uniform(10, 25)))
            promised = od + pd.Timedelta(days=int(m.lead_time_days))
            on_time = rng.random() < r
            delay = 0 if on_time else int(rng.integers(1, 15))
            delivered = promised + pd.Timedelta(days=delay - (int(rng.integers(0, 3)) if on_time else 0))
            short_ship = rng.random() > (r + 0.05)
            dqty = int(qty * rng.uniform(0.6, 0.95)) if short_ship else qty
            if delivered > snap:  # still open
                delivered, dqty = pd.NaT, np.nan
            po_rows.append((f"PO{po_n:06d}", m.material_id, m.supplier_id, od, promised, qty, delivered, dqty))
            po_n += 1
    pos = pd.DataFrame(
        po_rows,
        columns=["po_number", "material_id", "supplier_id", "order_date", "promised_date",
                 "ordered_qty", "delivered_date", "delivered_qty"],
    )

    materials = materials.drop(columns="avg_daily_demand")
    suppliers = suppliers.drop(columns="_reliability")
    return {
        "materials": materials,
        "suppliers": suppliers,
        "inventory": inventory,
        "requirements": requirements,
        "purchase_orders": pos,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Generate sample ERP data")
    p.add_argument("--out", default="data")
    p.add_argument("--materials", type=int, default=150)
    p.add_argument("--snapshot", default="2026-11-15")
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, df in generate(a.materials, a.snapshot).items():
        df.to_csv(out / f"{name}.csv", index=False)
        print(f"wrote {out / f'{name}.csv'} ({len(df):,} rows)")


if __name__ == "__main__":
    main()
