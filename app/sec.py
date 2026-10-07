"""Small SEC EDGAR client: company lookup, filing lists, XBRL facts, document text.

SEC asks every client to send a descriptive User-Agent with a contact address
(https://www.sec.gov/os/accessing-edgar-data) and to stay under 10 requests/second.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from functools import lru_cache

from bs4 import BeautifulSoup

UA = os.environ.get("SEC_USER_AGENT", "ModelStarter research tool admin@example.com")
_lock = threading.Lock()
_last = [0.0]


class FetchError(RuntimeError):
    pass


def _get(url: str, timeout: int = 30) -> bytes:
    host = urllib.parse.urlparse(url).hostname or ""
    headers = {"Accept-Encoding": "gzip", "Accept": "*/*"}
    if host.endswith("sec.gov"):
        headers["User-Agent"] = UA
        with _lock:  # SEC rate limit: stay well under 10 req/s
            wait = 0.15 - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
    else:
        headers["User-Agent"] = "Mozilla/5.0 (compatible; ModelStarter/1.0)"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return data
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"could not reach {url}: {e.reason}") from e


def _json(url: str):
    return json.loads(_get(url))


@lru_cache(maxsize=1)
def _tickers():
    data = _json("https://www.sec.gov/files/company_tickers.json")
    return {v["ticker"].upper(): v for v in data.values()}


@lru_cache(maxsize=64)
def submissions(cik: int) -> dict:
    return _json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")


@lru_cache(maxsize=32)
def companyfacts(cik: int) -> dict:
    return _json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")


def lookup(ticker: str) -> dict:
    t = ticker.upper().strip().replace(".", "-")
    rec = _tickers().get(t)
    if not rec:
        raise FetchError(f"ticker {ticker} not found in SEC's company list (non-US listings are not on EDGAR)")
    cik = int(rec["cik_str"])
    sub = submissions(cik)
    return {
        "ticker": t, "cik": cik, "name": sub.get("name") or rec["title"], "sic": sub.get("sic"), "sic_description": sub.get("sicDescription"),
        "fiscal_year_end_mmdd": sub.get("fiscalYearEnd"), "exchanges": sub.get("exchanges"), "website": sub.get("website"),
        "edgar_filings_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}&type=&dateb=&owner=include&count=40",
    }


def list_filings(ticker: str, forms=("10-K", "10-Q", "8-K"), since: str = "2020-01-01", items_contains: str | None = None) -> list[dict]:
    info = lookup(ticker)
    cik = info["cik"]
    sub = submissions(cik)
    blocks = [sub["filings"]["recent"]]
    for f in sub["filings"].get("files", []):
        if f.get("filingTo", "9999") >= since:
            blocks.append(_json(f"https://data.sec.gov/submissions/{f['name']}"))
    out = []
    for b in blocks:
        for i, form in enumerate(b["form"]):
            if form not in forms or b["filingDate"][i] < since:
                continue
            items = b.get("items", [""] * len(b["form"]))[i] or ""
            if items_contains and items_contains not in items:
                continue
            acc = b["accessionNumber"][i].replace("-", "")
            doc = b["primaryDocument"][i]
            out.append({
                "form": form, "filing_date": b["filingDate"][i], "report_date": b["reportDate"][i], "items": items,
                "accession": b["accessionNumber"][i], "description": b.get("primaryDocDescription", [""] * len(b["form"]))[i],
                "document_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}",
                "index_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/",
            })
    out.sort(key=lambda r: r["filing_date"], reverse=True)
    return out


def filing_index(index_url: str) -> list[dict]:
    """Documents inside one filing (e.g. to find the 8-K exhibit 99.1 earnings release)."""
    data = _json(index_url.rstrip("/") + "/index.json")
    return [{"name": it["name"], "type": it.get("type", ""), "size": it.get("size", ""), "url": index_url.rstrip("/") + "/" + it["name"]}
            for it in data.get("directory", {}).get("item", [])]


def search_tags(ticker: str, query: str, since: str = "2021-01-01", limit: int = 40) -> list[dict]:
    cf = companyfacts(lookup(ticker)["cik"])
    words = [w.lower() for w in re.split(r"\s+", query.strip()) if w]
    out = []
    for ns, tags in cf.get("facts", {}).items():
        for tag, body in tags.items():
            hay = f"{tag} {body.get('label', '')} {body.get('description', '')}".lower()
            if words and not all(w in hay for w in words):
                continue
            ends = [f["end"] for u in body.get("units", {}).values() for f in u]
            last = max(ends) if ends else ""
            if last < since:
                continue
            out.append({"tag": f"{ns}:{tag}", "label": body.get("label"), "units": list(body.get("units", {}).keys()), "latest_end": last,
                        "n_facts": sum(len(u) for u in body.get("units", {}).values())})
    out.sort(key=lambda r: (-r["n_facts"], r["tag"]))
    return out[:limit]


def _month_end(d: dt.date) -> dt.date:
    """Snap 52/53-week period ends (e.g. 2024-01-27) to the nearest calendar month end."""
    if d.day < 15:
        d = d.replace(day=1) - dt.timedelta(days=1)
    nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return nxt - dt.timedelta(days=1)


def fiscal_label(end: str, fye_mmdd: str | None) -> dict:
    e = dt.date.fromisoformat(end)
    me = _month_end(e)
    fm = int((fye_mmdd or "1231")[:2])
    off = (me.month - fm) % 12
    q = {3: 1, 6: 2, 9: 3, 0: 4}.get(off)
    fy = me.year if me.month <= fm else me.year + 1
    return {"fy": fy, "fq": q, "label": f"Q{q} FY{fy}" if q else None}


def quarterly_facts(ticker: str, tag: str, unit: str | None = None, since: str = "2020-06-30") -> dict:
    """Return one value per fiscal quarter for an XBRL concept.

    Flows (facts with a start date): uses three-month facts where filed, otherwise
    differences year-to-date facts that share a start date (so Q4 = FY - 9M).
    Instants (balance sheet): the value at each quarter end.
    Facts with dimensions (segments) are not in this API, so values are consolidated.
    """
    info = lookup(ticker)
    ns, _, name = tag.partition(":") if ":" in tag else ("us-gaap", "", tag)
    cf = companyfacts(info["cik"])
    body = cf.get("facts", {}).get(ns, {}).get(name)
    if not body:
        raise FetchError(f"{ns}:{name} has no facts for {info['ticker']}; use search_xbrl_tags to find the tag the company uses")
    units = body.get("units", {})
    if unit is None:
        unit = "USD" if "USD" in units else next(iter(units))
    facts = units.get(unit) or []
    best = {}
    for f in facts:  # keep the most recently filed value per (start, end)
        key = (f.get("start"), f["end"])
        if key not in best or f.get("filed", "") > best[key].get("filed", ""):
            best[key] = f
    fye = info["fiscal_year_end_mmdd"]
    out = {}
    instants = [f for (s, e), f in best.items() if s is None]
    if instants:
        for f in instants:
            if f["end"] < since:
                continue
            lab = fiscal_label(f["end"], fye)
            if lab["fq"]:
                out[f["end"]] = {"end": f["end"], **lab, "value": f["val"], "basis": "instant", "form": f.get("form"), "accn": f.get("accn")}
        kind = "instant"
    else:
        kind = "flow"
        by_start = {}
        for (s, e), f in best.items():
            days = (dt.date.fromisoformat(e) - dt.date.fromisoformat(s)).days
            by_start.setdefault(s, []).append((e, days, f))
        for s, lst in by_start.items():
            lst.sort()
            prev_end, prev_val = None, 0.0
            for e, days, f in lst:
                if 80 <= days <= 100:
                    val, basis = f["val"], "reported 3-month"
                elif prev_end is not None and 80 <= (dt.date.fromisoformat(e) - dt.date.fromisoformat(prev_end)).days <= 100:
                    val, basis = f["val"] - prev_val, f"YTD difference ({days} days)"
                else:
                    prev_end, prev_val = e, f["val"]
                    continue
                prev_end, prev_val = e, f["val"]
                if e < since:
                    continue
                lab = fiscal_label(e, fye)
                if not lab["fq"]:
                    continue
                cur = out.get(e)
                if cur is None or (cur["basis"] != "reported 3-month" and basis == "reported 3-month"):
                    out[e] = {"end": e, **lab, "value": val, "basis": basis, "form": f.get("form"), "accn": f.get("accn")}
    rows = sorted(out.values(), key=lambda r: r["end"])
    return {"tag": f"{ns}:{name}", "label": body.get("label"), "unit": unit, "kind": kind, "fiscal_year_end": fye, "quarters": rows}


def page_text(url: str, offset: int = 0, max_chars: int = 40000) -> dict:
    raw = _get(url, timeout=60)
    ctype = "html"
    if url.lower().endswith((".txt", ".csv", ".json")) or not raw.lstrip()[:1] in (b"<",):
        text = raw.decode("utf-8", "replace")
        ctype = "text"
    else:
        soup = BeautifulSoup(raw, "html.parser")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        for ix in soup.find_all(re.compile(r"^ix:header$", re.I)):
            ix.decompose()
        # keep table structure: one line per row, cells separated by " | "
        for table in soup.find_all("table"):
            lines = []
            for tr in table.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                cells = [c for c in cells if c and c not in ("$", ")", "%")]
                if cells:
                    lines.append(" | ".join(cells))
            table.replace_with("\n[TABLE]\n" + "\n".join(lines) + "\n[/TABLE]\n")
        text = soup.get_text("\n")
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        text = re.sub(r"[ \t\xa0]+", " ", text)
    total = len(text)
    chunk = text[offset: offset + max_chars]
    return {"url": url, "type": ctype, "total_chars": total, "offset": offset, "returned_chars": len(chunk),
            "next_offset": offset + len(chunk) if offset + len(chunk) < total else None, "text": chunk}


def download(url: str, dest_dir: str, filename: str | None = None) -> str:
    data = _get(url, timeout=120)
    name = filename or os.path.basename(urllib.parse.urlparse(url).path) or "document.htm"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120]
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, name)
    with open(path, "wb") as f:
        f.write(data)
    return name
