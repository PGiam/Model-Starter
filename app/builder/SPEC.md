# Model spec (input to `save_model_spec` / `build_workbook`)

One JSON object. Money values are in the reporting currency's **millions** and are
quarterly (three-month) amounts, never year-to-date. Every history array has exactly one
value per entry in `history.periods`, oldest first. Use `null` only where the field
says so; otherwise use 0.

Sign conventions: expenses are **positive** numbers (`cost_of_revenue`, `opex`,
`income_tax`). `interest_and_other` is income positive / expense negative. Cash-flow
lines use the statement's own signs (outflows negative: `capex`, `buybacks`,
`dividends`, `acquisitions`).

```jsonc
{
  "company": "C.H. Robinson Worldwide, Inc.",
  "ticker": "CHRW",
  "exchange": "NASDAQ",
  "currency": "USD",
  "units": "millions",
  "market": {"share_price": 133.71, "as_of": "2026-10-07"},

  "history": {
    "periods": [ {"label": "Q1 FY2021", "end": "2021-03-31", "fy": 2021, "fq": 1}, ... ],
      // consecutive fiscal quarters, up to 5 years back; "end" = the quarter's period-end date
    "opening_balance": {"cash": 243.8},          // balance-sheet cash at the end of the quarter before the first period (optional)

    "segments": [                                // reported segments; omit for a single-segment company
      {"name": "NAST",
       "revenue": [...], "gross_profit": [...],  // segment revenue and gross profit (or the company's gross metric) must sum to company totals
       "volume_growth": [0.015, null, ...],      // company-disclosed YoY volume growth as a fraction; null where not disclosed (optional)
       "volume_label": "truckload + LTL shipments", "price_label": "revenue per shipment"}
    ],
    "headcount": [...],                          // average or period-end employees (optional; enables the headcount cost driver)

    "income_statement": {
      "revenue": [...],
      "cost_of_revenue": [...],                  // everything between revenue and the gross metric
      "cost_of_revenue_label": "Cost of revenue", "gross_profit_label": "Gross profit",
      "opex": [                                  // every operating expense line between gross profit and operating income
        {"name": "Personnel expenses", "values": [...], "driver": "headcount"},      // driver: pct_revenue | pct_gross_profit | headcount
        {"name": "SG&A", "values": [...], "driver": "pct_revenue"}
      ],
      "da_in_opex": true,                        // true if D&A is already inside cost_of_revenue/opex; false adds a separate D&A expense line
      "depreciation_amortization": [...],        // from the cash-flow statement
      "interest_and_other": [...],               // all non-operating items between operating income and pre-tax income
      "income_tax": [...],
      "net_income_reported": [...],              // net income attributable to common (used as a check)
      "diluted_shares": [...],                   // millions
      "dps": [...],                              // dividends declared per share in the quarter
      "eps_reported": [...],                     // optional memo
      "adjustments": [...]                       // optional: company's non-GAAP add-backs to operating income (restructuring etc.)
    },

    "balance_sheet": {                           // period-end; assets must equal liabilities + equity every quarter
      "cash": [...], "receivables": [...], "inventory": [...], "other_current_assets": [...],
      "ppe_intangibles": [...], "goodwill": [...], "other_noncurrent_assets": [...],
      "payables": [...], "other_current_liabilities": [...], "short_term_debt": [...],
      "long_term_debt": [...], "other_noncurrent_liabilities": [...], "equity": [...],   // equity includes noncontrolling interest
      "total_assets_reported": [...]             // check value
    },

    "cash_flow": {                               // quarterly amounts (difference the YTD figures)
      "sbc": [...], "other_noncash": [...],
      "change_receivables": [...], "change_inventory": [...], "change_payables": [...], "change_other_wc": [...],
      "cfo_reported": [...],
      "capex": [...], "acquisitions": [...], "other_investing": [...], "cfi_reported": [...],
      "buybacks": [...], "dividends": [...], "debt_net": [...], "other_financing": [...], "cff_reported": [...],
      "fx": [...]
    },

    "kpis": [ {"name": "Truckload volume", "unit": "%", "values": [...], "source": "https://..."} ]   // reported operating KPIs (optional)
  },

  "forecast": {
    "end_fy": 2031,                              // last fiscal year to forecast (5 years forward)
    "assumptions": {                             // keyed by driver id; value = number, or {"2027": v, "2026Q3": v}. Missing keys use history-based defaults.
      "seg:NAST:volume": {"2026": 0.02, "2027": 0.03},
      "seg:NAST:price": {"2026Q3": 0.18},
      "seg:NAST:margin": {"2027": 0.145},
      "opex:SG&A": {"2027": 0.11},              // % of the line's driver base; for a headcount line use wage_inflation instead
      "productivity": 0.03, "wage_inflation": 0.03,
      "da_pct": 0.006, "sbc_pct": 0.01, "tax_rate": 0.22, "interest_rate": 0.05,
      "buybacks": 150, "share_price": 133.71, "dps": {"2027": 0.65},
      "dso": 55, "dio": 0, "dpo": 38, "oca_pct": 0.02, "ocl_pct": 0.08, "capex_pct": 0.004,
      "min_cash": 150, "acquisitions": 0
    }
  },

  "competitors": [                               // USD millions; include the company itself
    {"ticker": "EXPD", "name": "Expeditors International", "business": "Freight forwarding",
     "revenue": 11069, "gross_profit": 3667, "gross_metric_label": "net revenue", "operating_income": 1052.5,
     "ebitda": 1109, "net_income": 810, "ltm_revenue": 12036, "ltm_operating_income": 1183, "ltm_net_income": 919,
     "market_cap": 24930, "total_debt": 571, "cash": 1314,
     "sources": [ {"label": "FY2025 10-K", "url": "https://..."} ]}
  ],
  "competitor_notes": ["..."],

  "industry_series": [                           // government / industry data published at least quarterly
    {"name": "Cass Freight Index: Shipments", "publisher": "Cass Information Systems", "frequency": "Quarterly avg of monthly",
     "unit": "Index", "url": "https://fred.stlouisfed.org/series/FRGSHPUSM649NCIS", "notes": "...",
     "values": {"2021Q1": 1.12, "2021Q2": 1.18}}
  ],

  "sources": [ {"label": "Q2 FY2026 10-Q", "category": "10-Q", "url": "https://www.sec.gov/..."} ]
}
```

Mapping tips
- Put every balance-sheet line in exactly one bucket so the sheet balances; lease ROU assets, deferred taxes and held-for-sale assets go in the "other" buckets.
- If the company reports a non-GAAP gross metric (e.g. adjusted gross profit, net revenue), set `cost_of_revenue` to what reconciles revenue to that metric and name it with the labels.
- The builder adds reconciling lines so model cash flows tie to `cfo_reported`, `cfi_reported` and `cff_reported`; a large reconciling amount means a line was missed.
