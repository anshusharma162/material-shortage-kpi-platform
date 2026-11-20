"""Exportable Excel report (used by the CLI and the dashboard's download button).

    python -m kpi_platform.report --data data --out reports/kpi_report.xlsx
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .kpis import headline, load, material_kpis, monthly_otd, open_purchase_orders, snapshot_date, supplier_otd, to_sqlite

STATUS_FILL = {
    "Critical": "F8D7DA",
    "At Risk": "FFF3CD",
    "Healthy": "D1E7DD",
    "Excess": "CFE2FF",
}
HEADER_FILL = PatternFill("solid", fgColor="1F3864")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.date
        elif isinstance(df[c].dtype, pd.CategoricalDtype):
            df[c] = df[c].astype(str)
    return df.replace([np.inf, -np.inf], np.nan)


def _format(ws, df: pd.DataFrame) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for i, col in enumerate(df.columns, 1):
        width = max([len(str(col))] + [len(str(v)) for v in df[col].head(200)]) + 2
        ws.column_dimensions[get_column_letter(i)].width = min(width, 40)
    if "status" in df.columns:
        idx = list(df.columns).index("status") + 1
        for row in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
            for cell in row:
                if cell.value in STATUS_FILL:
                    cell.fill = PatternFill("solid", fgColor=STATUS_FILL[cell.value])


def build_excel(k: pd.DataFrame, sup: pd.DataFrame, monthly: pd.DataFrame, open_po: pd.DataFrame, snap) -> bytes:
    head = headline(k, sup)
    summary = pd.DataFrame(
        [
            ("Snapshot date", str(pd.Timestamp(snap).date())),
            ("Materials tracked", head["materials"]),
            ("Critical (stockout within lead time)", head["critical"]),
            ("At risk", head["at_risk"]),
            ("Shortage rate %", head["shortage_rate_pct"]),
            ("Median stock coverage (days)", head["median_coverage_days"]),
            ("Value at risk", head["value_at_risk"]),
            ("Excess stock value", head["excess_stock_value"]),
            ("Supplier OTD %", head["otd_pct"]),
            ("Supplier OTIF %", head["otif_pct"]),
        ],
        columns=["KPI", "Value"],
    )
    shortages = k[k.status.isin(["Critical", "At Risk"])]
    overdue = open_po[open_po.days_overdue > 0].sort_values("days_overdue", ascending=False)
    sheets = {
        "Summary": summary,
        "Shortages": shortages,
        "All Materials": k,
        "Supplier OTD": sup,
        "Monthly OTD": monthly,
        "Overdue POs": overdue.drop(columns=["delivered_date", "delivered_qty"], errors="ignore"),
    }
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df = _clean(df)
            df.to_excel(xw, sheet_name=name, index=False)
            _format(xw.sheets[name], df)
    return buf.getvalue()


def main() -> None:
    p = argparse.ArgumentParser(description="Export the KPI report to Excel")
    p.add_argument("--data", default="data")
    p.add_argument("--out", default="reports/kpi_report.xlsx")
    a = p.parse_args()
    t = load(a.data)
    conn = to_sqlite(t)
    k = material_kpis(t)
    data = build_excel(k, supplier_otd(conn), monthly_otd(conn), open_purchase_orders(t), snapshot_date(t))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_bytes(data)
    print(f"wrote {a.out}")
    for key, v in headline(k, supplier_otd(conn)).items():
        print(f"  {key:22s} {v:,}")


if __name__ == "__main__":
    main()
