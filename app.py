"""Streamlit dashboard:  streamlit run app.py"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from kpi_platform.kpis import (
    STATUS_ORDER, headline, load, material_kpis, monthly_otd, open_purchase_orders,
    snapshot_date, stock_projection, supplier_otd, to_sqlite,
)
from kpi_platform.report import build_excel

DATA_DIR = os.environ.get("KPI_DATA_DIR", "data")
COLORS = {"Critical": "#d62728", "At Risk": "#ff9f1c", "Healthy": "#2ca02c", "Excess": "#1f77b4"}

st.set_page_config(page_title="Material Shortage & KPI Platform", layout="wide")


@st.cache_data
def get_data(data_dir: str):
    t = load(data_dir)
    conn = to_sqlite(t)
    return t, material_kpis(t), supplier_otd(conn), monthly_otd(conn), open_purchase_orders(t), stock_projection(t)


t, kpis, sup_all, monthly_all, open_po, proj = get_data(DATA_DIR)
snap = snapshot_date(t)

# ------------------------------------------------------------------ filters
st.sidebar.header("Filters")
plants = st.sidebar.multiselect("Plant", sorted(kpis.plant.unique()), default=sorted(kpis.plant.unique()))
cats = st.sidebar.multiselect("Category", sorted(kpis.category.unique()), default=sorted(kpis.category.unique()))
sups = st.sidebar.multiselect("Supplier", sorted(kpis.supplier_name.unique()), default=sorted(kpis.supplier_name.unique()))
stats = st.sidebar.multiselect("Status", STATUS_ORDER, default=STATUS_ORDER)

k = kpis[kpis.plant.isin(plants) & kpis.category.isin(cats) & kpis.supplier_name.isin(sups) & kpis.status.isin(stats)]
sup_ids = kpis.loc[kpis.supplier_name.isin(sups), "supplier_id"].unique()
sup = sup_all[sup_all.supplier_id.isin(sup_ids)]
monthly = monthly_all[monthly_all.plant.isin(plants) & monthly_all.supplier_id.isin(sup_ids)]
po_f = open_po[open_po.material_id.isin(k.material_id)]

# ------------------------------------------------------------------ header
st.title("Material Shortage & Manufacturing KPI Platform")
st.caption(f"Stock snapshot: {snap.date()}  ·  {len(k)} of {len(kpis)} materials in view")

h = headline(k, sup)
c = st.columns(6)
c[0].metric("Critical shortages", h["critical"], help="Stockout projected within replenishment lead time")
c[1].metric("At risk", h["at_risk"])
c[2].metric("Median coverage", f"{h['median_coverage_days']:.0f} days")
c[3].metric("Value at risk", f"₹{h['value_at_risk']:,.0f}")
c[4].metric("Supplier OTD", f"{h['otd_pct']}%")
c[5].metric("Supplier OTIF", f"{h['otif_pct']}%")

tab1, tab2, tab3 = st.tabs(["Shortage overview", "Supplier delivery", "Material drill-down"])

# ------------------------------------------------------------------ tab 1
with tab1:
    l, r = st.columns(2)
    by_plant = k.groupby(["plant", "status"], observed=True).size().reset_index(name="materials")
    l.plotly_chart(
        px.bar(by_plant, x="plant", y="materials", color="status", color_discrete_map=COLORS,
               category_orders={"status": STATUS_ORDER}, title="Material status by plant"),
        width="stretch",
    )
    sc = k.assign(coverage=k.coverage_days.replace(np.inf, np.nan).clip(upper=150))
    fig = px.scatter(sc, x="lead_time_days", y="coverage", color="status", color_discrete_map=COLORS,
                     size=sc.stock_value.clip(lower=1), hover_data=["material_id", "description", "supplier_name"],
                     title="Stock coverage vs lead time (below the line = cannot be replenished in time)")
    fig.add_scatter(x=[0, 50], y=[0, 50], mode="lines", line={"dash": "dash", "color": "grey"}, name="coverage = lead time")
    fig.update_layout(xaxis_title="Lead time (days)", yaxis_title="Coverage (days, capped at 150)")
    r.plotly_chart(fig, width="stretch")

    st.subheader("Shortage list")
    short = k[k.status.isin(["Critical", "At Risk"])]
    st.dataframe(
        short[["material_id", "description", "plant", "supplier_name", "status", "on_hand_qty", "coverage_days",
               "lead_time_days", "stockout_date", "shortage_qty", "open_po_qty", "overdue_pos", "value_at_risk"]],
        width="stretch", hide_index=True,
    )
    d1, d2 = st.columns(2)
    d1.download_button("Download filtered materials (CSV)", k.to_csv(index=False), "materials_kpis.csv", "text/csv")
    d2.download_button(
        "Download full KPI report (Excel)",
        build_excel(k, sup, monthly, po_f, snap),
        f"kpi_report_{snap.date()}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ------------------------------------------------------------------ tab 2
with tab2:
    l, r = st.columns(2)
    s = sup.sort_values("otif_pct")
    fig = go.Figure()
    fig.add_bar(y=s.supplier_name, x=s.otd_pct, name="On-time %", orientation="h")
    fig.add_bar(y=s.supplier_name, x=s.otif_pct, name="OTIF %", orientation="h")
    fig.update_layout(barmode="group", title="Delivery performance by supplier (last 12 months)", xaxis_range=[0, 100])
    l.plotly_chart(fig, width="stretch")

    m = monthly.groupby("promised_month")[["deliveries", "on_time", "otif"]].sum().reset_index()
    m["OTD %"] = 100 * m.on_time / m.deliveries
    m["OTIF %"] = 100 * m.otif / m.deliveries
    fig = px.line(m, x="promised_month", y=["OTD %", "OTIF %"], markers=True, title="Monthly delivery trend")
    fig.update_layout(yaxis_range=[0, 100], xaxis_title="Promised month", yaxis_title="%")
    r.plotly_chart(fig, width="stretch")

    st.subheader("Overdue open purchase orders")
    od = po_f[po_f.days_overdue > 0].merge(t["suppliers"], on="supplier_id").sort_values("days_overdue", ascending=False)
    st.dataframe(od[["po_number", "material_id", "supplier_name", "order_date", "promised_date", "ordered_qty", "days_overdue"]],
                 width="stretch", hide_index=True)

# ------------------------------------------------------------------ tab 3
with tab3:
    if k.empty:
        st.info("No materials match the filters.")
    else:
        options = k.material_id + " · " + k.description + " (" + k.status.astype(str) + ")"
        choice = st.selectbox("Material", options)
        mid = choice.split(" · ")[0]
        row = k[k.material_id == mid].iloc[0]
        p = proj[proj.material_id == mid]

        c = st.columns(5)
        c[0].metric("Status", str(row.status))
        c[1].metric("On hand", f"{row.on_hand_qty:,.0f}")
        c[2].metric("Coverage", f"{row.coverage_days:.1f} days" if np.isfinite(row.coverage_days) else "∞")
        c[3].metric("Lead time", f"{row.lead_time_days} days")
        c[4].metric("Stockout", "none in 90 days" if pd.isna(row.stockout_date) else str(pd.Timestamp(row.stockout_date).date()))

        fig = go.Figure()
        fig.add_scatter(x=p.date, y=p.projected_balance, name="Projected balance", mode="lines", line={"width": 3})
        fig.add_bar(x=p.date, y=p.receipt_qty, name="PO receipts", opacity=0.5)
        fig.add_bar(x=p.date, y=-p.required_qty, name="Requirements", opacity=0.4)
        fig.add_hline(y=row.safety_stock, line_dash="dash", line_color="orange", annotation_text="Safety stock")
        fig.add_hline(y=0, line_color="red")
        fig.add_vline(x=snap + pd.Timedelta(days=int(row.lead_time_days)), line_dash="dot",
                      annotation_text="Lead-time horizon")
        fig.update_layout(title=f"{mid} – 90-day stock projection", barmode="relative", yaxis_title="Quantity")
        st.plotly_chart(fig, width="stretch")

        st.write("Open purchase orders")
        st.dataframe(open_po[open_po.material_id == mid][["po_number", "order_date", "promised_date", "ordered_qty", "days_overdue"]],
                     width="stretch", hide_index=True)
