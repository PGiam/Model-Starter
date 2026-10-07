"""Mirror the site's data to a branch of the GitHub repo so it survives restarts.

Free hosting plans wipe the local disk whenever the server sleeps or redeploys. When
DATA_REPO (a PRIVATE repo, e.g. "you/model-starter-data") and GITHUB_TOKEN are set (token
needs Contents: read and write on it), the playbook, feedback, and each chat's messages and
Excel models are committed to its DATA_BRANCH branch (default "site-data") and restored
from it at startup. Downloaded filings are not mirrored; Claude can fetch them again.
There is deliberately no fallback to GITHUB_REPO: the code repo may be public.
"""
from __future__ import annotations

import base64
import fnmatch
import json
import os
import threading
import time
import urllib.error
import urllib.request

from . import store

REPO = os.environ.get("DATA_REPO", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
BRANCH = os.environ.get("DATA_BRANCH", "site-data")
API = "https://api.github.com"

# Paths (relative to DATA_DIR) worth keeping across restarts.
KEEP = ["playbook/*", "feedback/*", "sessions/*/meta.json", "sessions/*/events.jsonl",
        "sessions/*/messages.json", "sessions/*/spec.json", "sessions/*/files/*.xlsx"]

_pending: set[str] = set()
_cv = threading.Condition()
_worker: threading.Thread | None = None
status = {"enabled": bool(REPO and TOKEN), "last_sync": None, "error": None}


def enabled() -> bool:
    return bool(REPO and TOKEN) and not status.get("refused")


def _api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(f"{API}/repos/{REPO}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
                                          "User-Agent": "model-starter", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"null")


def _kept(rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pat) for pat in KEEP)


def _branch_head() -> str | None:
    try:
        return _api("GET", f"/git/ref/heads/{BRANCH}")["object"]["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def restore():
    """Download the mirrored files that are missing locally. Called once at startup."""
    if not enabled():
        return
    try:
        if not _api("GET", "").get("private"):
            status.update(enabled=False, refused=True, error=f"{REPO} is public; saving is off so chats and models stay private")
            return
        head = _branch_head()
        if not head:
            return
        tree_sha = _api("GET", f"/git/commits/{head}")["tree"]["sha"]
        tree = _api("GET", f"/git/trees/{tree_sha}?recursive=1")
        n = 0
        for item in tree.get("tree", []):
            rel = item["path"]
            if item["type"] != "blob" or not _kept(rel) or ".." in rel.split("/"):
                continue
            dest = os.path.join(store.DATA_DIR, *rel.split("/"))
            if os.path.exists(dest):
                continue
            blob = _api("GET", f"/git/blobs/{item['sha']}")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(base64.b64decode(blob["content"]))
            n += 1
        status.update(last_sync=time.time(), error=None, restored=n)
    except Exception as e:  # noqa: BLE001 - the site still runs without the mirror
        status["error"] = f"restore failed: {e}"


def save(*paths: str):
    """Queue files (absolute or DATA_DIR-relative) to be committed to the data branch."""
    if not enabled():
        return
    global _worker
    root = os.path.realpath(store.DATA_DIR)
    with _cv:
        for p in paths:
            rel = os.path.relpath(os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p)), root).replace(os.sep, "/")
            if _kept(rel):
                _pending.add(rel)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, daemon=True)
            _worker.start()
        _cv.notify()


def save_session(sid: str):
    d = store.session_dir(sid)
    files = [os.path.join(d, n) for n in ("meta.json", "events.jsonl", "messages.json", "spec.json")]
    fdir = os.path.join(d, "files")
    if os.path.isdir(fdir):
        files += [os.path.join(fdir, n) for n in os.listdir(fdir) if n.endswith(".xlsx")]
    save(*[f for f in files if os.path.exists(f)])


def _run():
    while True:
        with _cv:
            while not _pending:
                _cv.wait()
        time.sleep(3)  # gather a burst of writes into one commit
        with _cv:
            batch = sorted(_pending)
            _pending.clear()
        for attempt in range(4):
            try:
                _commit(batch)
                status.update(last_sync=time.time(), error=None)
                break
            except Exception as e:  # noqa: BLE001
                status["error"] = f"sync failed: {e}"
                time.sleep(2 ** (attempt + 1))
        else:
            with _cv:
                _pending.update(batch)  # try again with the next write


def _commit(rels: list[str]):
    rels = [r for r in rels if os.path.exists(os.path.join(store.DATA_DIR, *r.split("/")))]
    if not rels:
        return
    head = _branch_head()
    if head is None:  # start the data branch with an empty history of its own
        parents, base_tree = [], None
    else:
        parents, base_tree = [head], _api("GET", f"/git/commits/{head}")["tree"]["sha"]
    entries = []
    for rel in rels:
        with open(os.path.join(store.DATA_DIR, *rel.split("/")), "rb") as f:
            blob = _api("POST", "/git/blobs", {"content": base64.b64encode(f.read()).decode(), "encoding": "base64"})
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = _api("POST", "/git/trees", {"tree": entries, **({"base_tree": base_tree} if base_tree else {})})
    commit = _api("POST", "/git/commits", {"message": f"Save site data ({len(entries)} files)", "tree": tree["sha"], "parents": parents})
    if head is None:
        _api("POST", "/git/refs", {"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})
    else:
        _api("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": commit["sha"]})
