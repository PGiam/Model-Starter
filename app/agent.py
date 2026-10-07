"""Claude agent that researches a company and builds the three-statement model.

One Session per analyst conversation. Each user message runs the tool loop in a
background thread; progress is written to an event log the web page polls.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
import subprocess
import threading
import time
import traceback
import uuid

import anthropic

from . import sec, store
from .builder.model_builder import build_workbook

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("CLAUDE_EFFORT", "high")
BETAS = ["server-side-fallback-2026-07-01", "context-management-2025-06-27"]
HERE = os.path.dirname(__file__)
SPEC_DOC = open(os.path.join(HERE, "builder", "SPEC.md")).read()
# Opus 5.5 list prices per million tokens, used for the cost estimate shown to admins
PRICE = {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0}

_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=4)
    return _client


SYSTEM_TEMPLATE = """You are a financial analyst assistant inside a web app used by an investment team. \
You build driver-based, quarterly three-statement financial models (income statement, balance sheet, \
cash flow) for public companies and deliver them as Excel workbooks.

Today's date is {today}.

How you work
- Follow the team playbook below. It reflects how this team wants models built.
- Use the SEC tools for filings and XBRL data; use web search for competitors, market data, industry \
data and anything else. read_document can open any public URL (FRED data files such as \
https://fred.stlouisfed.org/data/<SERIES>.txt work well for government series).
- Put numbers into the model spec with tools rather than retyping them: prefer import_xbrl_series for \
anything tagged in XBRL; use set_spec_value for figures read from releases (segments, KPIs), \
competitors, industry series and assumptions.
- Never invent a number. If a value cannot be found, leave the gap, say so, and pick a clearly \
labeled assumption only where the model needs one.
- Before presenting a model, run build_model and resolve every error it reports. Warnings can be \
explained instead of fixed.
- The analyst sees only your chat text and the files you save. Keep replies short; ask one clear \
question at a time when you need input, with short options.

Model spec format (what build_model consumes)
{spec_doc}

Team playbook (current approved version)
{playbook}
"""


def tool_defs():
    T = lambda name, desc, props, req: {"name": name, "description": desc,
                                       "input_schema": {"type": "object", "properties": props, "required": req, "additionalProperties": False}}
    S = {"type": "string"}
    return [
        T("lookup_company", "Look up a US-listed company on SEC EDGAR by ticker: CIK, legal name, industry code, fiscal year end (MMDD).",
          {"ticker": S}, ["ticker"]),
        T("list_filings", "List SEC filings for a ticker, newest first, with document and index URLs. Earnings releases are 8-Ks with items containing 2.02.",
          {"ticker": S, "forms": {"type": "array", "items": S, "description": "e.g. [\"10-K\",\"10-Q\"]"},
           "since": {"type": "string", "description": "YYYY-MM-DD"}, "items_contains": {"type": "string", "description": "optional 8-K item filter, e.g. 2.02"}},
          ["ticker", "forms", "since"]),
        T("list_filing_documents", "List the documents inside one filing (use the index_url from list_filings), e.g. to find Exhibit 99.1.",
          {"index_url": S}, ["index_url"]),
        T("read_document", "Fetch a web page or filing document as text (tables are rendered one row per line with ' | ' between cells). Returns up to 40,000 characters; pass next_offset to keep reading.",
          {"url": S, "offset": {"type": "integer"}}, ["url"]),
        T("save_document", "Download a filing or document into the analyst's downloads for this session (for 10-Ks, 10-Qs, earnings releases, transcripts).",
          {"url": S, "filename": {"type": "string", "description": "short descriptive file name, e.g. CHRW_10-Q_Q2FY2026.htm"}}, ["url", "filename"]),
        T("search_xbrl_tags", "Search the XBRL concepts a company has reported (all namespaces, including company-specific ones). Words must all match the tag, label or description.",
          {"ticker": S, "query": S}, ["ticker", "query"]),
        T("get_xbrl_quarterly", "Preview one XBRL concept as one value per fiscal quarter (3-month values, Q4 derived from full year, YTD differenced; instants for balance sheet items).",
          {"ticker": S, "tag": {"type": "string", "description": "namespace:Concept, e.g. us-gaap:Revenues"}, "unit": {"type": "string"}}, ["ticker", "tag"]),
        T("init_model_spec", "Start (or restart) the model spec: sets company info and the historical fiscal quarters from the period-end dates of a revenue tag. Overwrites any existing spec.",
          {"ticker": S, "revenue_tag": S, "quarters": {"type": "integer", "description": "number of historical quarters, max 20"},
           "forecast_end_fy": {"type": "integer"}, "units_note": {"type": "string", "description": "e.g. 'USD millions'"}},
          ["ticker", "revenue_tag", "quarters", "forecast_end_fy"]),
        T("import_xbrl_series", "Fill a spec path with an XBRL series aligned to the spec's quarters. Sum several tags with signs if needed. Values are scaled (default 1e-6, dollars to millions). Returns the values and any missing quarters.",
          {"path": {"type": "string", "description": "e.g. history.income_statement.revenue or history.income_statement.opex[1].values"},
           "terms": {"type": "array", "items": {"type": "object", "properties": {"tag": S, "sign": {"type": "number"}, "unit": S},
                                                 "required": ["tag", "sign"], "additionalProperties": False}},
           "scale": {"type": "number"}, "fill_missing_with_zero": {"type": "boolean"}},
          ["path", "terms"]),
        T("set_spec_value", "Set any part of the model spec to a JSON value (series, objects, assumptions, competitors, sources). Path uses dots and [index], e.g. forecast.assumptions or history.segments[0].revenue. Keys containing dots or colons can be quoted: forecast.assumptions[\"seg:NAST:price\"].",
          {"path": S, "value_json": {"type": "string", "description": "the value as JSON text"}}, ["path", "value_json"]),
        T("get_spec", "Read part of the model spec as JSON (omit path for an outline of what is filled in).", {"path": S}, []),
        T("build_model", "Build the Excel workbook from the spec, recalculate it and run the integrity checks. Returns errors, warnings, check results and the download file name.",
          {"version_note": {"type": "string", "description": "short note on what changed in this version"}}, []),
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 40},
    ]


# ---------------------------------------------------------------- spec path helpers
_tok = re.compile(r'\[\s*"([^"]+)"\s*\]|\[(\d+)\]|\.?([^.\[\]]+)')


def _parse_path(path: str):
    out = []
    for m in _tok.finditer(path or ""):
        if m.group(1) is not None:
            out.append(m.group(1))
        elif m.group(2) is not None:
            out.append(int(m.group(2)))
        elif m.group(3):
            out.append(m.group(3))
    return out


def _get_in(obj, parts):
    for p in parts:
        obj = obj[p]
    return obj


def _set_in(obj, parts, value):
    for i, p in enumerate(parts[:-1]):
        nxt = parts[i + 1]
        if isinstance(p, int):
            while len(obj) <= p:
                obj.append({} if not isinstance(nxt, int) else [])
            obj = obj[p]
        else:
            if p not in obj or obj[p] is None:
                obj[p] = [] if isinstance(nxt, int) else {}
            obj = obj[p]
    last = parts[-1]
    if isinstance(last, int):
        while len(obj) <= last:
            obj.append(None)
    obj[last] = value


def _outline(spec, depth=0, max_depth=3):
    if isinstance(spec, dict):
        if depth >= max_depth:
            return f"{{{len(spec)} keys}}"
        return {k: _outline(v, depth + 1, max_depth) for k, v in spec.items()}
    if isinstance(spec, list):
        if spec and all(isinstance(x, (int, float)) or x is None for x in spec):
            filled = sum(1 for x in spec if x is not None)
            return f"[{len(spec)} values, {filled} filled]"
        if depth >= max_depth:
            return f"[{len(spec)} items]"
        return [_outline(x, depth + 1, max_depth) for x in spec[:12]]
    return spec


# ---------------------------------------------------------------- session
class Session:
    def __init__(self, sid: str):
        self.id = sid
        self.dir = store.session_dir(sid)
        self.files_dir = os.path.join(self.dir, "files")
        os.makedirs(self.files_dir, exist_ok=True)
        self.meta = store.read_json(os.path.join(self.dir, "meta.json"), {})
        self.messages = store.read_json(os.path.join(self.dir, "messages.json"), [])
        self.spec = store.read_json(os.path.join(self.dir, "spec.json"), None)
        self.lock = threading.Lock()
        self.running = False

    # persistence
    def save(self):
        store.write_json(os.path.join(self.dir, "meta.json"), self.meta)
        store.write_json(os.path.join(self.dir, "messages.json"), self.messages)
        if self.spec is not None:
            store.write_json(os.path.join(self.dir, "spec.json"), self.spec)

    def event(self, etype, **data):
        store.append_event(self.id, {"t": time.time(), "type": etype, **data})

    # --------------------------------------------------- tools
    def run_tool(self, name, inp):
        try:
            if name == "lookup_company":
                return sec.lookup(inp["ticker"])
            if name == "list_filings":
                rows = sec.list_filings(inp["ticker"], tuple(inp["forms"]), inp["since"], inp.get("items_contains"))
                return {"count": len(rows), "filings": rows[:150]}
            if name == "list_filing_documents":
                return sec.filing_index(inp["index_url"])
            if name == "read_document":
                return sec.page_text(inp["url"], int(inp.get("offset") or 0))
            if name == "save_document":
                fn = sec.download(inp["url"], self.files_dir, inp.get("filename"))
                self.event("file", name=fn, kind="document", url=inp["url"])
                return {"saved": fn}
            if name == "search_xbrl_tags":
                return sec.search_tags(inp["ticker"], inp["query"])
            if name == "get_xbrl_quarterly":
                return sec.quarterly_facts(inp["ticker"], inp["tag"], inp.get("unit"))
            if name == "init_model_spec":
                return self._init_spec(inp)
            if name == "import_xbrl_series":
                return self._import_series(inp)
            if name == "set_spec_value":
                if self.spec is None:
                    return {"error": "call init_model_spec first"}
                try:
                    value = json.loads(inp["value_json"])
                except json.JSONDecodeError as e:
                    return {"error": f"value_json is not valid JSON: {e}"}
                parts = _parse_path(inp["path"])
                if not parts:
                    return {"error": "empty path"}
                _set_in(self.spec, parts, value)
                self.save()
                return {"ok": True, "path": inp["path"]}
            if name == "get_spec":
                if self.spec is None:
                    return {"spec": None}
                if not inp.get("path"):
                    return _outline(self.spec)
                return _get_in(self.spec, _parse_path(inp["path"]))
            if name == "build_model":
                return self._build(inp.get("version_note", ""))
            return {"error": f"unknown tool {name}"}
        except sec.FetchError as e:
            return {"error": str(e)}
        except (KeyError, IndexError, TypeError, ValueError) as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def _init_spec(self, inp):
        info = sec.lookup(inp["ticker"])
        q = sec.quarterly_facts(info["ticker"], inp["revenue_tag"])
        n = max(4, min(20, int(inp.get("quarters") or 20)))
        rows = q["quarters"][-n:]
        # keep only a consecutive run ending at the latest quarter
        run = [rows[-1]]
        for r in reversed(rows[:-1]):
            a = run[0]
            exp = (a["fy"], a["fq"] - 1) if a["fq"] > 1 else (a["fy"] - 1, 4)
            if (r["fy"], r["fq"]) != exp:
                break
            run.insert(0, r)
        self.spec = {
            "company": info["name"], "ticker": info["ticker"], "currency": "USD", "units": inp.get("units_note") or "millions",
            "history": {"periods": [{"label": r["label"], "end": r["end"], "fy": r["fy"], "fq": r["fq"]} for r in run]},
            "forecast": {"end_fy": int(inp["forecast_end_fy"]), "assumptions": {}},
            "competitors": [], "industry_series": [], "sources": [],
        }
        self.meta.update({"ticker": info["ticker"], "company": info["name"]})
        self.save()
        return {"periods": [r["label"] + " (" + r["end"] + ")" for r in run], "fiscal_year_end": info["fiscal_year_end_mmdd"],
                "note": "Fewer quarters than requested means the revenue tag has gaps; try another tag or accept the shorter history." if len(run) < n else ""}

    def _import_series(self, inp):
        if self.spec is None:
            return {"error": "call init_model_spec first"}
        per = self.spec["history"]["periods"]
        scale = float(inp.get("scale") or 1e-6)
        total = [0.0] * len(per)
        missing = set()
        detail = []
        for term in inp["terms"]:
            q = sec.quarterly_facts(self.spec["ticker"], term["tag"], term.get("unit"))
            by = {(r["fy"], r["fq"]): r for r in q["quarters"]}  # flows and instants both align by fiscal quarter
            got = 0
            for i, p in enumerate(per):
                r = by.get((p["fy"], p["fq"]))
                if r is None:
                    missing.add(p["label"])
                    continue
                total[i] += float(term.get("sign", 1)) * r["value"] * scale
                got += 1
            detail.append({"tag": q["tag"], "kind": q["kind"], "unit": q["unit"], "quarters_found": got})
        vals = [round(v, 4) for v in total]
        if missing and not inp.get("fill_missing_with_zero"):
            vals = [None if per[i]["label"] in missing else v for i, v in enumerate(vals)]
        _set_in(self.spec, _parse_path(inp["path"]), vals)
        self.save()
        return {"path": inp["path"], "values": dict(zip([p["label"] for p in per], vals)), "missing_quarters": sorted(missing), "terms": detail}

    def _build(self, note):
        if self.spec is None:
            return {"error": "no spec yet"}
        ver = int(self.meta.get("model_version", 0)) + 1
        fname = f"{self.spec.get('ticker', 'MODEL')}_Model_v{ver}.xlsx"
        path = os.path.join(self.files_dir, fname)
        spec = copy.deepcopy(self.spec)
        rep = build_workbook(spec, path)
        if not rep.get("ok"):
            if os.path.exists(path):
                os.remove(path)
            return {"built": False, **rep}
        rep["recalc"] = recalc_and_check(path)
        self.meta["model_version"] = ver
        self.meta["latest_model"] = fname
        self.save()
        self.event("file", name=fname, kind="model", note=note)
        return {"built": True, "file": fname, **rep}

    # --------------------------------------------------- agent loop
    def system_prompt(self):
        return SYSTEM_TEMPLATE.format(today=dt.date.today().isoformat(), spec_doc=SPEC_DOC, playbook=store.read_playbook())

    def handle(self, text: str, user: str = ""):
        with self.lock:
            if self.running:
                raise RuntimeError("busy")
            self.running = True
        self.messages.append({"role": "user", "content": text})
        self.event("user", text=text, user=user)
        self.save()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        try:
            system = self.system_prompt()
            tools = tool_defs()
            for _ in range(200):
                with client().beta.messages.stream(
                    model=MODEL,
                    max_tokens=64000,
                    system=system,
                    tools=tools,
                    messages=self.messages,
                    output_config={"effort": EFFORT},
                    cache_control={"type": "ephemeral"},
                    betas=BETAS,
                    fallbacks="default",
                    context_management={"edits": [{
                        "type": "clear_tool_uses_20250919",
                        "trigger": {"type": "input_tokens", "value": 300000},
                        "keep": {"type": "tool_uses", "value": 15},
                        "clear_at_least": {"type": "input_tokens", "value": 60000},
                        "exclude_tools": ["build_model", "init_model_spec"],
                    }]},
                ) as stream:
                    msg = stream.get_final_message()
                self._usage(msg.usage)
                content = [b.model_dump(mode="json", exclude_none=True) for b in msg.content]
                self.messages.append({"role": "assistant", "content": content})
                self.save()
                for b in msg.content:
                    if b.type == "text" and b.text.strip():
                        self.event("assistant", text=b.text)
                    elif b.type == "server_tool_use":
                        q = (getattr(b, "input", {}) or {}).get("query", "")
                        self.event("tool", name="web_search", summary=f"Searching the web: {q}")
                if msg.stop_reason == "refusal":
                    self.event("assistant", text="I couldn't complete that request. Try rephrasing it, or ask an admin to check the logs.")
                    break
                if msg.stop_reason == "pause_turn":
                    continue
                if msg.stop_reason == "max_tokens":
                    self.messages.append({"role": "user", "content": "Your last reply was cut off by the length limit. Continue, using smaller steps."})
                    continue
                uses = [b for b in msg.content if b.type == "tool_use"]
                if not uses:
                    break
                results = []
                for b in uses:
                    self.event("tool", name=b.name, summary=_summarize(b.name, b.input))
                    out = self.run_tool(b.name, b.input)
                    txt = json.dumps(out, default=str)
                    if len(txt) > 120000:
                        txt = txt[:120000] + '... [truncated: request a smaller range or the next offset]'
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": txt,
                                    **({"is_error": True} if isinstance(out, dict) and "error" in out else {})})
                self.messages.append({"role": "user", "content": results})
                self.save()
        except anthropic.APIStatusError as e:
            self.event("error", text=f"Claude API error {e.status_code}: {getattr(e, 'message', str(e))}")
        except anthropic.APIConnectionError:
            self.event("error", text="Could not reach the Claude API. Try again in a moment.")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.event("error", text=f"Unexpected error: {e}")
        finally:
            self.running = False
            self.save()
            self.event("done")

    def _usage(self, u):
        m = self.meta.setdefault("usage", {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
        m["input"] += getattr(u, "input_tokens", 0) or 0
        m["output"] += getattr(u, "output_tokens", 0) or 0
        m["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        m["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.meta["cost_usd"] = round(sum(m[k] * PRICE[k] for k in PRICE) / 1e6, 2)


def _summarize(name, inp):
    if name == "read_document":
        return f"Reading {inp.get('url', '')}"
    if name == "save_document":
        return f"Saving {inp.get('filename', '')}"
    if name == "list_filings":
        return f"Listing {', '.join(inp.get('forms', []))} filings for {inp.get('ticker', '')}"
    if name in ("import_xbrl_series", "set_spec_value"):
        return f"Filling {inp.get('path', '')}"
    if name == "build_model":
        return "Building and checking the workbook"
    if name == "get_xbrl_quarterly":
        return f"Pulling {inp.get('tag', '')}"
    return name.replace("_", " ").capitalize()


# ---------------------------------------------------------------- recalculation
XL_ERRORS = {"#REF!", "#NAME?", "#VALUE!", "#DIV/0!", "#N/A", "#NUM!", "#NULL!"}


def recalc_and_check(path: str) -> dict:
    """Recalculate with LibreOffice (when installed) so the file opens with values, then read the check rows."""
    soffice = os.environ.get("SOFFICE", "soffice")
    outdir = os.path.join(os.path.dirname(path), ".recalc-" + uuid.uuid4().hex[:8])
    os.makedirs(outdir, exist_ok=True)
    try:
        subprocess.run([soffice, "--headless", "--norestore", "--calc", "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", outdir, path],
                       check=True, capture_output=True, timeout=180)
        out = os.path.join(outdir, os.path.basename(path))
        if not os.path.exists(out):
            return {"recalculated": False, "note": "LibreOffice produced no output"}
        os.replace(out, path)
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        return {"recalculated": False, "note": f"LibreOffice not available ({type(e).__name__}); the workbook calculates when opened in Excel"}
    finally:
        try:
            os.rmdir(outdir)
        except OSError:
            pass
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    res = {"recalculated": True, "formula_errors": [], "checks": {}}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value in XL_ERRORS:
                    if len(res["formula_errors"]) < 20:
                        res["formula_errors"].append(f"{ws.title}!{c.coordinate} {c.value}")
    q = wb["Quarterly Model"]
    for r in range(1, q.max_row + 1):
        lab = q.cell(r, 1).value
        if isinstance(lab, str) and lab.startswith("Check:"):
            vals = [q.cell(r, c).value for c in range(3, q.max_column + 1)]
            nums = [abs(v) for v in vals if isinstance(v, (int, float))]
            res["checks"][lab] = round(max(nums), 3) if nums else None
    return res
