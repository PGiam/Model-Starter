from app import sec


def _fact(start, end, val, filed="2025-01-01"):
    f = {"end": end, "val": val, "filed": filed, "form": "10-Q", "accn": "x"}
    if start:
        f["start"] = start
    return f


def _patch(monkeypatch, facts, fye="1231"):
    monkeypatch.setattr(sec, "lookup", lambda t: {"ticker": t, "cik": 1, "fiscal_year_end_mmdd": fye})
    monkeypatch.setattr(sec, "companyfacts", lambda cik: {"facts": {"us-gaap": {"Revenues": {"label": "Revenues", "units": {"USD": facts}}}}})


def test_q4_from_annual_minus_nine_months(monkeypatch):
    _patch(monkeypatch, [
        _fact("2024-01-01", "2024-03-31", 100),
        _fact("2024-04-01", "2024-06-30", 110),
        _fact("2024-01-01", "2024-06-30", 210),
        _fact("2024-07-01", "2024-09-30", 120),
        _fact("2024-01-01", "2024-09-30", 330),
        _fact("2024-01-01", "2024-12-31", 460),
    ])
    rows = sec.quarterly_facts("TEST", "Revenues")["quarters"]
    assert [(r["label"], r["value"]) for r in rows] == [("Q1 FY2024", 100), ("Q2 FY2024", 110), ("Q3 FY2024", 120), ("Q4 FY2024", 130)]
    assert rows[1]["basis"] == "reported 3-month"


def test_latest_filing_wins_and_instants(monkeypatch):
    _patch(monkeypatch, [_fact(None, "2024-06-30", 5, "2024-08-01"), _fact(None, "2024-06-30", 7, "2025-08-01")])
    out = sec.quarterly_facts("TEST", "Revenues")
    assert out["kind"] == "instant" and out["quarters"][0]["value"] == 7


def test_fiscal_label_for_52_53_week_years():
    # A June fiscal year end; a period ending 2024-09-28 is Q1 of FY2025.
    assert sec.fiscal_label("2024-09-28", "0629")["label"] == "Q1 FY2025"
    assert sec.fiscal_label("2024-12-31", "1231")["label"] == "Q4 FY2024"
