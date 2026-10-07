"""Team feedback -> proposed improvements, applied only after an admin approves.

Two kinds of change:
- playbook: Claude rewrites the playbook text (instructions every model build follows).
- code: Claude writes a change request; on approval it becomes a GitHub issue that the
  Claude Code GitHub Action turns into a pull request for an admin to merge.
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import os
import threading
import urllib.request
import uuid

from . import store
from .agent import MODEL, client

_lock = threading.Lock()

SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["playbook", "code", "both", "none"]},
        "summary": {"type": "string"},
        "playbook_new": {"type": "string"},
        "code_request_title": {"type": "string"},
        "code_request_body": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["kind", "summary", "playbook_new", "code_request_title", "code_request_body", "reason"],
    "additionalProperties": False,
}

PROMPT = """You maintain a web app that builds quarterly three-statement Excel models with Claude.
An analyst on the team left feedback. Decide how to act on it.

- "playbook": the change is about how Claude researches, which sources it uses, how it talks to analysts, \
model conventions, or anything Claude can follow from written instructions. Return the full revised \
playbook in playbook_new: keep everything that still applies, make the smallest edit that captures the \
feedback, and keep the same style.
- "code": the change needs the program itself to change (new workbook tab or calculation, new data \
tool, UI change, bug). Write a clear change request for a developer: what to change, why, and how to \
tell it works. Leave playbook_new as an empty string.
- "both": do both.
- "none": the feedback is unclear, already covered, or conflicts with the playbook; explain in reason.

Use empty strings for fields that don't apply. The app's code layout: app/builder/model_builder.py \
(workbook), app/agent.py (Claude tools and loop), app/sec.py (SEC data), app/main.py (web API), \
web/ (pages).

Current playbook:
<playbook>
{playbook}
</playbook>

Feedback from {author}:
<feedback>
{text}
</feedback>
{context}"""


def submit(text: str, author: str, session_id: str | None = None) -> dict:
    item = {"id": uuid.uuid4().hex[:16], "at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(), "author": author, "text": text,
            "session_id": session_id, "status": "analyzing"}
    with _lock:
        items = store.list_feedback()
        items.insert(0, item)
        store.save_feedback(items)
    threading.Thread(target=_analyze, args=(item["id"],), daemon=True).start()
    return item


def _update(fid, **fields):
    with _lock:
        items = store.list_feedback()
        for it in items:
            if it["id"] == fid:
                it.update(fields)
        store.save_feedback(items)


def _get(fid):
    return next((it for it in store.list_feedback() if it["id"] == fid), None)


def _analyze(fid):
    it = _get(fid)
    try:
        ctx = ""
        if it.get("session_id"):
            meta = store.read_json(os.path.join(store.session_dir(it["session_id"]), "meta.json"), {})
            ctx = f"\nThe feedback was sent while working on a model for {meta.get('ticker', 'a company')}."
        current = store.read_playbook()
        resp = client().messages.create(
            model=MODEL, max_tokens=16000,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(playbook=current, author=it["author"], text=it["text"], context=ctx)}],
        )
        if resp.stop_reason == "refusal":
            _update(fid, status="error", error="Claude declined to process this feedback.")
            return
        data = json.loads(next(b.text for b in resp.content if b.type == "text"))
        diff = ""
        if data["kind"] in ("playbook", "both") and data["playbook_new"].strip():
            diff = "".join(difflib.unified_diff(current.splitlines(True), data["playbook_new"].splitlines(True), "current", "proposed"))
        _update(fid, status="pending" if data["kind"] != "none" else "no_action", proposal=data, diff=diff, base_playbook=current)
    except Exception as e:  # noqa: BLE001
        _update(fid, status="error", error=str(e))


def approve(fid: str, admin: str) -> dict:
    it = _get(fid)
    if not it or it.get("status") != "pending":
        raise ValueError("nothing to approve")
    prop = it["proposal"]
    out = {}
    if prop["kind"] in ("playbook", "both") and prop["playbook_new"].strip():
        if store.read_playbook() != it.get("base_playbook"):
            raise ValueError("the playbook changed since this proposal was drafted; re-analyze it first")
        out["playbook_version"] = store.write_playbook(prop["playbook_new"], admin, f"feedback from {it['author']}: {prop['summary']}")
    if prop["kind"] in ("code", "both"):
        out["issue"] = open_issue(prop["code_request_title"], prop["code_request_body"], it)
    _update(fid, status="approved", approved_by=admin, result=out)
    return out


def reject(fid: str, admin: str, note: str = ""):
    _update(fid, status="rejected", rejected_by=admin, note=note)


def reanalyze(fid: str):
    _update(fid, status="analyzing", proposal=None, diff="")
    threading.Thread(target=_analyze, args=(fid,), daemon=True).start()


def open_issue(title: str, body: str, it: dict) -> dict:
    repo = os.environ.get("GITHUB_REPO")
    token = os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return {"opened": False, "note": "Set GITHUB_REPO and GITHUB_TOKEN to open code-change issues automatically."}
    text = (f"{body}\n\n---\nOriginal feedback from {it['author']}:\n> " + it["text"].replace("\n", "\n> ") +
            "\n\n@claude please implement this change and open a pull request. Keep the existing tests passing and add one for the change.")
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues", method="POST",
                                 data=json.dumps({"title": title, "body": text, "labels": ["team-feedback"]}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "model-starter"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return {"opened": True, "url": data.get("html_url"), "number": data.get("number")}
