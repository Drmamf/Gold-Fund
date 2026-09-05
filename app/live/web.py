from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from app.database import SessionLocal
from app.live import ops


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DASH_USER = os.getenv("DASHBOARD_USER", "admin").strip() or "admin"
DASH_PASS = os.getenv("DASHBOARD_PASSWORD", "admin")
SESSION_SECRET = os.getenv("DASHBOARD_SECRET") or secrets.token_hex(32)
STATIC_DIR = PROJECT_ROOT / "app" / "live" / "web_static"

app = FastAPI(title="GoldFund Live Console", docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=False,
    max_age=60 * 60 * 24 * 7,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:43123",
        "http://localhost:43123",
        "http://127.0.0.1:43147",
        "http://localhost:43147",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginBody(BaseModel):
    username: str
    password: str


class CapitalBody(BaseModel):
    max_toman: int = Field(..., ge=1, le=5_000_000_000)


class EnabledBody(BaseModel):
    enabled: bool


class BrokerBody(BaseModel):
    national_id: str | None = None
    password: str | None = None


def _authed(request: Request) -> None:
    if request.session.get("user") != DASH_USER:
        raise HTTPException(status_code=401, detail="وارد شوید")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "karamad-live-web"}


@app.post("/api/login")
def login(body: LoginBody, request: Request):
    if not secrets.compare_digest(body.username, DASH_USER) or not secrets.compare_digest(
        body.password, DASH_PASS
    ):
        raise HTTPException(status_code=401, detail="نام کاربری یا رمز اشتباه است")
    request.session["user"] = DASH_USER
    return {"ok": True, "user": DASH_USER}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/session")
def session_get(request: Request):
    user = request.session.get("user")
    return {"authenticated": user == DASH_USER, "user": user if user == DASH_USER else None}


@app.get("/api/overview")
def overview(request: Request):
    _authed(request)
    with SessionLocal() as session:
        return ops.overview(session)


@app.get("/api/signals")
def signals(request: Request):
    _authed(request)
    with SessionLocal() as session:
        return {"signals": ops.active_signals(session)}


@app.get("/api/history")
def history(request: Request):
    _authed(request)
    with SessionLocal() as session:
        return {"orders": ops.order_history(session)}


@app.post("/api/live/enabled")
def set_enabled(body: EnabledBody, request: Request):
    _authed(request)
    enabled = ops.set_live_enabled(body.enabled)
    return {"ok": True, "live_enabled": enabled}


@app.post("/api/capital")
def set_capital(body: CapitalBody, request: Request):
    _authed(request)
    toman = ops.set_capital_max_toman(body.max_toman)
    return {"ok": True, "capital_toman": toman}


@app.post("/api/broker")
def set_broker(body: BrokerBody, request: Request):
    _authed(request)
    try:
        data = ops.set_broker(national_id=body.national_id, password=body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "broker": data}


@app.exception_handler(ValueError)
def _value_error(_, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
