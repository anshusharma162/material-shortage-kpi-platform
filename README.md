# Material Shortage & Manufacturing KPI Analytics Platform

A data platform for production-planning teams that answers three daily questions:

1. **Which materials will run out before we can replenish them?** (shortage)
2. **How many days of stock do we have?** (stock coverage)
3. **Which suppliers deliver on time and in full?** (OTD / OTIF)

It ships with an interactive **Streamlit + Plotly dashboard** (filters, drill-down to a
single material's 90-day stock projection) and a one-click **Excel report** export.

## KPIs and the business logic behind them

| KPI | Definition | Why planners care |
|---|---|---|
| Projected balance | on-hand + open PO receipts − production requirements, day by day for 90 days | shows *when* a material runs out, not just *if* |
| Stock coverage (days) | on-hand ÷ average daily requirement (next 30 days) | comparable across cheap and expensive parts |
| Shortage qty | deepest negative projected balance **within the supplier lead time** | this gap cannot be fixed by a normal order; it must be expedited |
| Value at risk | shortage qty × unit cost | lets the team prioritise by money, not count |
| OTD % | delivered on/before promised date | supplier reliability |
| OTIF % | on time **and** full quantity | the stricter measure used in supplier reviews |

**Status rules**

| Status | Rule |
|---|---|
| 🔴 Critical | projected stockout inside the lead time |
| 🟠 At Risk | stockout within lead time + 14 days, or balance drops below safety stock within lead time |
| 🔵 Excess | coverage above 90 days (cash tied up in stock) |
| 🟢 Healthy | everything else |

Overdue open POs are assumed to arrive the next day (an optimistic assumption) and are
listed separately so a buyer can chase them.

## Sample output (150 materials, 3 plants, 10 suppliers)

| Metric | Value |
|---|---|
| Critical shortages | 31 |
| At risk | 29 |
| Median stock coverage | 21 days |
| Value at risk | ₹2.96 lakh |
| Supplier OTD / OTIF | 82.4% / 72.8% |

## Architecture

```
ERP exports (CSV)                 SQLite (in-memory)            Python
materials, suppliers,   ──load──► sql/delivery_kpis.sql  ──►   kpis.py  ──► Streamlit dashboard (app.py)
inventory, requirements,          views: OTD, OTIF,            projection,      └─► Excel report (report.py)
purchase_orders                   monthly trend                shortage, status
```

- Delivery KPIs are written in **SQL views** (`sql/delivery_kpis.sql`) so they can be moved
  to PostgreSQL/MySQL unchanged.
- The day-by-day stock projection is done in **pandas** (cumulative sums per material).

## Run it

```bash
pip install -r requirements.txt
python -m kpi_platform.data            # generate sample ERP data into data/
streamlit run app.py                   # dashboard on http://localhost:8501
python -m kpi_platform.report          # export reports/kpi_report.xlsx
pytest -q                              # tests, incl. hand-checked KPI maths
```

To use your own data, put CSVs with the same columns in a folder and run
`KPI_DATA_DIR=/path/to/folder streamlit run app.py`.

## Dashboard

- **KPI cards**: critical, at-risk, median coverage, value at risk, OTD, OTIF
- **Shortage overview**: status by plant, coverage-vs-lead-time scatter (anything under the
  diagonal cannot be replenished in time), shortage list, CSV and Excel downloads
- **Supplier delivery**: OTD/OTIF per supplier, monthly trend, overdue open POs
- **Material drill-down**: 90-day projected balance with receipts, requirements,
  safety stock and lead-time horizon
- Sidebar filters (plant, category, supplier, status) apply to every view and to the exports

## Excel report sheets
Summary · Shortages · All Materials · Supplier OTD · Monthly OTD · Overdue POs
(header styling, frozen header, auto-filter, colour-coded status).

## Project structure

```
app.py                    Streamlit dashboard
kpi_platform/
  data.py                 sample ERP data generator
  kpis.py                 projection, shortage, coverage, status, SQL loader
  report.py               formatted Excel export (CLI + dashboard)
sql/delivery_kpis.sql     OTD / OTIF views
tests/                    pytest suite
```

## Tech
Python · pandas · SQL (SQLite) · Plotly · Streamlit · openpyxl
