# upload_service.py
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


# -------------------- ENV --------------------
load_dotenv()

CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
API_KEY = os.getenv("CLOUDINARY_API_KEY")
API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
UPLOAD_PRESET = os.getenv("CLOUDINARY_UPLOAD_PRESET", "mindfulpro")

# OneSignal: usa tu App ID (puedes sobrescribir por env)
ONESIGNAL_APP_ID = os.getenv("ONESIGNAL_APP_ID", "38ca55f0-f29d-413e-afe1-b25cc2bf9505")

if not (CLOUD_NAME and API_KEY and API_SECRET):
    raise RuntimeError("Faltan CLOUDINARY_* en el entorno (CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET).")

cloudinary.config(
    cloud_name=CLOUD_NAME,
    api_key=API_KEY,
    api_secret=API_SECRET,
    secure=True,
)


# -------------------- APP --------------------
app = FastAPI(title="Mindful Service")
templates = Jinja2Templates(directory="templates")

# --- Middleware para permitir embeber en iframe (ajusta en prod) ---
class FrameHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "ALLOWALL"
        response.headers["Content-Security-Policy"] = "frame-ancestors *"
        return response

app.add_middleware(FrameHeadersMiddleware)

# --- CORS (ajústalo en producción a tus dominios) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------- SESIONES (para poll del uploader) --------------------
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

def _janitor():
    while True:
        time.sleep(60)
        cutoff = time.time() - (60 * 30)
        with _lock:
            for k in list(_sessions.keys()):
                if _sessions[k]["ts"] < cutoff:
                    del _sessions[k]

threading.Thread(target=_janitor, daemon=True).start()


# -------------------- RUTAS BÁSICAS --------------------
@app.get("/health")
def health():
    return {"ok": True}


# -------------------- UPLOADER --------------------
@app.get("/uploader", response_class=HTMLResponse)
def uploader_form(request: Request, session: str, folder: str = "mindful/profesionistas"):
    """
    Página HTML para subir imagen; al terminar, guarda la URL en la sesión
    para que el cliente haga poll a /poll.
    """
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
    """
    Endpoint JSON de subida. Si 'session' viene, guarda secure_url en la sesión.
    """
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
    """
    Devuelve la URL subida (si ya está disponible) para esa sesión.
    """
    url = session_get(session, "url")
    return {"ok": True, "url": url}


# -------------------- NOTIFICACIONES (OneSignal v16) --------------------
@app.get("/notify", response_class=HTMLResponse)
def notify_page(request: Request):
    """
    Página súper simple para activar push (sin externalId, sin tags).
    """
    return templates.TemplateResponse(
        "notify.html",
        {"request": request, "onesignal_app_id": ONESIGNAL_APP_ID},
    )


# -------------------- SERVICE WORKERS EN RAÍZ --------------------
# Sirve los SW desde /OneSignalSDKWorker.js y /OneSignalSDKUpdaterWorker.js
# Coloca los archivos en: static/OneSignalSDKWorker.js y static/OneSignalSDKUpdaterWorker.js
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

@app.get("/OneSignalSDKWorker.js")
def onesignal_worker():
    path = os.path.join(STATIC_DIR, "OneSignalSDKWorker.js")
    return FileResponse(path, media_type="application/javascript")

@app.get("/OneSignalSDKUpdaterWorker.js")
def onesignal_worker_updater():
    path = os.path.join(STATIC_DIR, "OneSignalSDKUpdaterWorker.js")
    return FileResponse(path, media_type="application/javascript")
