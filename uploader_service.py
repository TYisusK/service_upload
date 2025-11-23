import os
import time
import threading
from typing import Optional, Dict

from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv

import cloudinary
import cloudinary.uploader

# -------- .env ----------
load_dotenv()

CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
API_KEY = os.getenv("CLOUDINARY_API_KEY")
API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
UPLOAD_PRESET = os.getenv("CLOUDINARY_UPLOAD_PRESET", "mindfulpro")

# OneSignal
ONESIGNAL_APP_ID = os.getenv("ONESIGNAL_APP_ID", "4d37d5f3-d9ca-41f5-9db4-b0a9c57125fa")

if not (CLOUD_NAME and API_KEY and API_SECRET):
    raise RuntimeError("Faltan CLOUDINARY_CLOUD_NAME / CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET en el entorno.")

cloudinary.config(
    cloud_name=CLOUD_NAME,
    api_key=API_KEY,
    api_secret=API_SECRET,
    secure=True,
)

app = FastAPI(title="Mindful Service")
templates = Jinja2Templates(directory="templates")

# ---------- Headers para iframe embebido ----------
class FrameHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "ALLOWALL"
        response.headers["Content-Security-Policy"] = "frame-ancestors *"
        return response

app.add_middleware(FrameHeadersMiddleware)

# ---------- CORS ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ajusta en prod a tu dominio de Flet/Render
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Sesiones en memoria ----------
_sessions: Dict[str, Dict] = {}
_lock = threading.Lock()

def session_set(session_id: str, key: str, value):
    with _lock:
        _sessions.setdefault(session_id, {"ts": time.time()})
        _sessions[session_id][key] = value
        _sessions[session_id]["ts"] = time.time()

def session_get(session_id: str, key: str):
    with _lock:
        return (_sessions.get(session_id) or {}).get(key)

def touch_session(session_id: str):
    with _lock:
        _sessions.setdefault(session_id, {"ts": time.time()})
        _sessions[session_id]["ts"] = time.time()

def janitor():
    while True:
        time.sleep(60)
        cutoff = time.time() - 60*30
        with _lock:
            for k in list(_sessions.keys()):
                if _sessions[k]["ts"] < cutoff:
                    del _sessions[k]

threading.Thread(target=janitor, daemon=True).start()

# ---------- Rutas básicas ----------
@app.get("/health")
def health():
    return {"ok": True}

# ---------- Servir los Service Workers en RAÍZ ----------
SW_ROOT = os.path.dirname(os.path.abspath(__file__))

@app.get("/OneSignalSDKWorker.js", include_in_schema=False)
def onesignal_sw():
    path = os.path.join(SW_ROOT, "OneSignalSDKWorker.js")
    return FileResponse(path, media_type="application/javascript")

@app.get("/OneSignalSDKUpdaterWorker.js", include_in_schema=False)
def onesignal_sw_updater():
    path = os.path.join(SW_ROOT, "OneSignalSDKUpdaterWorker.js")
    return FileResponse(path, media_type="application/javascript")

# ---------- Uploader ----------
@app.get("/uploader", response_class=HTMLResponse)
def uploader_form(request: Request, session: str, folder: str = "mindful/profesionistas"):
    touch_session(session)
    return templates.TemplateResponse(
        "upload.html",
        {
            "request": request,
            "cloud_name": CLOUD_NAME,
            "upload_preset": UPLOAD_PRESET,
            "session": session,
            "folder": folder,
        },
    )

@app.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    session: Optional[str] = Form(default=None),
    folder: Optional[str] = Form(default="mindful/profesionistas"),
    public_id: Optional[str] = Form(default=None),
    overwrite: Optional[bool] = Form(default=True),
):
    try:
        contents = await file.read()
        res = cloudinary.uploader.upload(
            contents,
            folder=folder,
            public_id=public_id,
            overwrite=overwrite,
            upload_preset=UPLOAD_PRESET,
            resource_type="image",
        )
        url = res.get("secure_url")
        if session and url:
            session_set(session, "url", url)
        return {"ok": True, "secure_url": url, "public_id": res.get("public_id")}
    except Exception as ex:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(ex)})

@app.get("/poll")
def poll(session: str):
    url = session_get(session, "url")
    return {"ok": True, "url": url}

# ---------- NOTIFICACIONES: OneSignal ----------
@app.get("/notify", response_class=HTMLResponse)
def notify_page(request: Request, session: str, uid: Optional[str] = None, role: Optional[str] = None):
    if not ONESIGNAL_APP_ID:
        return HTMLResponse("<h3>Falta ONESIGNAL_APP_ID en el servidor.</h3>", status_code=500)

    touch_session(session)
    return templates.TemplateResponse(
        "notify.html",
        {
            "request": request,
            "onesignal_app_id": ONESIGNAL_APP_ID,
            "session": session,
            "uid": uid or "",
            "role": role or "",
        },
    )

@app.post("/notify/ok")
def notify_ok(session: str):
    session_set(session, "push_ready", True)
    return {"ok": True}

@app.get("/notify/poll")
def notify_poll(session: str):
    ready = bool(session_get(session, "push_ready"))
    return {"ok": True, "ready": ready}
