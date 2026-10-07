"""File-based storage under DATA_DIR (a persistent disk in production)."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import tempfile
import threading

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
_ev_lock = threading.Lock()
_pb_lock = threading.Lock()
HERE = os.path.dirname(__file__)


def _p(*parts):
    path = os.path.join(DATA_DIR, *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, default=str)
    os.replace(tmp, path)


def valid_id(s: str) -> bool:
    return bool(re.fullmatch(r"[a-f0-9]{12,32}", s or ""))


def session_dir(sid: str) -> str:
    if not valid_id(sid):
        raise ValueError("bad session id")
    d = os.path.join(DATA_DIR, "sessions", sid)
    os.makedirs(d, exist_ok=True)
    return d


def list_sessions():
    root = os.path.join(DATA_DIR, "sessions")
    out = []
    if os.path.isdir(root):
        for sid in os.listdir(root):
            if valid_id(sid):
                m = read_json(os.path.join(root, sid, "meta.json"), {})
                m["id"] = sid
                out.append(m)
    out.sort(key=lambda m: m.get("created", ""), reverse=True)
    return out


def append_event(sid: str, ev: dict):
    path = os.path.join(session_dir(sid), "events.jsonl")
    with _ev_lock, open(path, "a") as f:
        f.write(json.dumps(ev, default=str) + "\n")


def read_events(sid: str, after: int = 0):
    path = os.path.join(session_dir(sid), "events.jsonl")
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return [], 0
    return [json.loads(l) for l in lines[after:]], len(lines)


# ---------------------------------------------------------------- playbook (versioned)
def read_playbook() -> str:
    path = _p("playbook", "current.md")
    if not os.path.exists(path):
        shutil.copy(os.path.join(HERE, "playbook_default.md"), path)
    with open(path) as f:
        return f.read()


def write_playbook(text: str, author: str, reason: str) -> int:
    with _pb_lock:
        hist = read_json(_p("playbook", "history.json"), [])
        ver = (hist[-1]["version"] + 1) if hist else 1
        if not hist:  # keep the original as version 0
            hist.append({"version": 0, "at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(), "author": "default", "reason": "initial playbook", "file": "v0.md"})
            with open(_p("playbook", "v0.md"), "w") as f:
                f.write(read_playbook())
            ver = 1
        with open(_p("playbook", f"v{ver}.md"), "w") as f:
            f.write(text)
        with open(_p("playbook", "current.md"), "w") as f:
            f.write(text)
        hist.append({"version": ver, "at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(), "author": author, "reason": reason, "file": f"v{ver}.md"})
        write_json(_p("playbook", "history.json"), hist)
        return ver


def playbook_history():
    return read_json(_p("playbook", "history.json"), [])


def playbook_version_text(ver: int) -> str:
    with open(_p("playbook", f"v{int(ver)}.md")) as f:
        return f.read()


# ---------------------------------------------------------------- feedback & proposals
def feedback_path():
    return _p("feedback", "items.json")


def list_feedback():
    return read_json(feedback_path(), [])


def save_feedback(items):
    write_json(feedback_path(), items)
