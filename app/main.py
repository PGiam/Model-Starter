"""Web API and page server."""
from __future__ import annotations

import contextlib
import datetime as dt
import hmac
import io
import os
import time
import uuid
import zipfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from . import feedback, persist, store
from .agent import Session

TEAM_PASSWORD = os.environ.get("TEAM_PASSWORD", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET = os.environ.get("SESSION_SECRET") or uuid.uuid4().hex
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")

@contextlib.asynccontextmanager
async def lifespan(app):
    persist.restore()  # bring back data saved to GitHub before the server last restarted
    yield


app = FastAPI(title="Model Starter", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET, session_cookie="ms_session", max_age=14 * 24 * 3600,
                   same_site="lax", https_only=os.environ.get("COOKIE_SECURE", "1") == "1")

_sessions: dict[str, Session] = {}
_fails: dict[str, list[float]] = {}


def get_session(sid: str) -> Session:
    if not store.valid_id(sid) or not os.path.isdir(os.path.join(store.DATA_DIR, "sessions", sid)):
        raise HTTPException(404, "not found")
    if sid not in _sessions:
        _sessions[sid] = Session(sid)
    return _sessions[sid]


def user(req: Request) -> dict:
    u = req.session.get("user")
    if not u:
        raise HTTPException(401, "login required")
    return u


def admin(req: Request) -> dict:
    u = user(req)
    if not u.get("admin"):
        raise HTTPException(403, "admin only")
    return u


# ---------------------------------------------------------------- auth
class Login(BaseModel):
    password: str
    name: str = Field(min_length=1, max_length=60)


@app.post("/api/login")
def login(body: Login, req: Request):
    ip = req.headers.get("x-forwarded-for", req.client.host if req.client else "?").split(",")[0].strip()
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < 900]
    if len(recent) >= 10:
        raise HTTPException(429, "too many attempts; try again in 15 minutes")
    if not TEAM_PASSWORD:
        raise HTTPException(500, "TEAM_PASSWORD is not configured on the server")
    is_admin = bool(ADMIN_PASSWORD) and hmac.compare_digest(body.password, ADMIN_PASSWORD)
    if not (is_admin or hmac.compare_digest(body.password, TEAM_PASSWORD)):
        _fails[ip] = recent + [now]
        raise HTTPException(401, "wrong password")
    req.session["user"] = {"name": body.name.strip(), "admin": is_admin}
    return {"ok": True, "admin": is_admin}


@app.post("/api/logout")
def logout(req: Request):
    req.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(req: Request):
    return user(req)


@app.get("/api/health")
def health():
    return {"ok": True}


# ---------------------------------------------------------------- model sessions
class NewSession(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


@app.get("/api/sessions")
def sessions(req: Request):
    user(req)
    return [{k: m.get(k) for k in ("id", "ticker", "company", "created", "owner", "latest_model")} for m in store.list_sessions()[:200]]


@app.post("/api/sessions")
def new_session(body: NewSession, req: Request):
    u = user(req)
    sid = uuid.uuid4().hex[:16]
    s = get_session_create(sid)
    s.meta.update({"created": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(), "owner": u["name"], "ticker": body.ticker.upper().strip()})
    s.save()
    s.handle(f"Ticker: {body.ticker.upper().strip()}. Please start the model for this company following the playbook.", u["name"])
    return {"id": sid}


def get_session_create(sid):
    os.makedirs(store.session_dir(sid), exist_ok=True)
    return get_session(sid)


@app.post("/api/sessions/{sid}/messages")
def send(sid: str, body: Message, req: Request):
    u = user(req)
    s = get_session(sid)
    try:
        s.handle(body.text, u["name"])
    except RuntimeError:
        raise HTTPException(409, "Claude is still working on the last message")
    return {"ok": True}


@app.get("/api/sessions/{sid}/events")
def events(sid: str, req: Request, after: int = 0):
    user(req)
    s = get_session(sid)
    evs, n = store.read_events(sid, after)
    return {"events": evs, "next": n, "running": s.running, "meta": {k: s.meta.get(k) for k in ("ticker", "company", "latest_model", "owner")}}


@app.get("/api/sessions/{sid}/files")
def files(sid: str, req: Request):
    user(req)
    s = get_session(sid)
    out = []
    for name in sorted(os.listdir(s.files_dir)):
        p = os.path.join(s.files_dir, name)
        if os.path.isfile(p):
            out.append({"name": name, "size": os.path.getsize(p), "model": name.endswith(".xlsx")})
    return out


@app.get("/api/sessions/{sid}/files/{name}")
def download(sid: str, name: str, req: Request):
    user(req)
    s = get_session(sid)
    p = os.path.realpath(os.path.join(s.files_dir, name))
    if not p.startswith(os.path.realpath(s.files_dir) + os.sep) or not os.path.isfile(p):
        raise HTTPException(404, "not found")
    return FileResponse(p, filename=name)


@app.get("/api/sessions/{sid}/documents.zip")
def documents_zip(sid: str, req: Request):
    user(req)
    s = get_session(sid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(s.files_dir)):
            p = os.path.join(s.files_dir, name)
            if os.path.isfile(p) and not name.endswith(".xlsx"):
                z.write(p, name)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{s.meta.get("ticker", "documents")}_filings.zip"'})


# ---------------------------------------------------------------- feedback
class Feedback(BaseModel):
    text: str = Field(min_length=3, max_length=8000)
    session_id: str | None = None


@app.post("/api/feedback")
def post_feedback(body: Feedback, req: Request):
    u = user(req)
    sid = body.session_id if body.session_id and store.valid_id(body.session_id) else None
    it = feedback.submit(body.text, u["name"], sid)
    return {"id": it["id"]}


@app.get("/api/feedback/mine")
def my_feedback(req: Request):
    u = user(req)
    return [{k: it.get(k) for k in ("id", "at", "text", "status")} for it in store.list_feedback() if it.get("author") == u["name"]][:50]


# ---------------------------------------------------------------- admin
@app.get("/api/admin/feedback")
def admin_feedback(req: Request):
    admin(req)
    return store.list_feedback()


class Decision(BaseModel):
    note: str = ""


@app.post("/api/admin/feedback/{fid}/approve")
def approve(fid: str, req: Request):
    a = admin(req)
    try:
        return feedback.approve(fid, a["name"])
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/admin/feedback/{fid}/reject")
def reject(fid: str, body: Decision, req: Request):
    a = admin(req)
    feedback.reject(fid, a["name"], body.note)
    return {"ok": True}


@app.post("/api/admin/feedback/{fid}/reanalyze")
def reanalyze(fid: str, req: Request):
    admin(req)
    feedback.reanalyze(fid)
    return {"ok": True}


class PlaybookEdit(BaseModel):
    text: str = Field(min_length=10)
    reason: str = "manual edit"


@app.get("/api/admin/playbook")
def get_playbook(req: Request):
    admin(req)
    return {"text": store.read_playbook(), "history": store.playbook_history()}


@app.put("/api/admin/playbook")
def put_playbook(body: PlaybookEdit, req: Request):
    a = admin(req)
    return {"version": store.write_playbook(body.text, a["name"], body.reason)}


@app.post("/api/admin/playbook/revert/{ver}")
def revert(ver: int, req: Request):
    a = admin(req)
    try:
        text = store.playbook_version_text(ver)
    except FileNotFoundError:
        raise HTTPException(404, "no such version")
    return {"version": store.write_playbook(text, a["name"], f"revert to version {ver}")}


@app.get("/api/admin/usage")
def usage(req: Request):
    admin(req)
    rows = []
    for m in store.list_sessions():
        rows.append({k: m.get(k) for k in ("id", "ticker", "owner", "created", "cost_usd", "model_version")})
    return {"sessions": rows, "total_cost_usd": round(sum((r.get("cost_usd") or 0) for r in rows), 2), "storage": persist.status}


# ---------------------------------------------------------------- pages
@app.exception_handler(HTTPException)
async def http_err(req: Request, exc: HTTPException):
    if exc.status_code == 401 and not req.url.path.startswith("/api/"):
        return RedirectResponse("/login.html")
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
