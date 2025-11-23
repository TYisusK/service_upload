import os
import time
import threading
from typing import Optional, Dict

from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv

import cloudinary
import cloudinary.uploader

# Carga variables de entorno (Render -> Dashboard -> Env Vars)
load_dotenv()

CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
API_KEY = os.getenv("CLOUDINARY_API_KEY")
API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
UPLOAD_PRESET = os.getenv("CLOUDINARY_UPLOAD_PRESET", "mindfulpro")

if not (CLOUD_NAME and API_KEY and API_SECRET):
    raise RuntimeError(
        "Faltan CLOUDINARY_CLOUD_NAME / CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET en el entorno."
    )

cloudinary.config(
    cloud_name=CLOUD_NAME,
    api_key=API_KEY,
    api_secret=API_SECRET,
    secure=True,
)

app = FastAPI(title="Mindful Uploader")
templates = Jinja2Templates(directory="templates")

# ---------- Permitir embebido en iframe (ajusta a tu dominio en prod) ----------
class FrameHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        # En producción, reemplaza '*' por tu dominio de la PWA.
        response.headers["X-Frame-Options"] = "ALLOWALL"
        response.headers["Content-Security-Policy"] = "frame-ancestors *"
        return response

app.add_middleware(FrameHeadersMiddleware)

# ---------- CORS ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # en prod, limita a tu dominio
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Sesiones en memoria para handoff URL ----------
_sessions: Dict[str, Dict] = {}
_lock = threading.Lock()

def set_session_url(session_id: str, url: str):
    with _lock:
        _sessions[session_id] = {"url": url, "ts": time.time()}

def get_session_url(session_id: str) -> Optional[str]:
    with _lock:
        item = _sessions.get(session_id)
        return item.get("url") if item else None

def touch_session(session_id: str):
    with _lock:
        _sessions.setdefault(session_id, {"url": None, "ts": time.time()})
        _sessions[session_id]["ts"] = time.time()

def janitor():
    # Limpia sesiones inactivas cada 60s
    while True:
        time.sleep(60)
        cutoff = time.time() - 60 * 30  # 30 min
        with _lock:
            for k in list(_sessions.keys()):
                if _sessions[k]["ts"] < cutoff:
                    del _sessions[k]

threading.Thread(target=janitor, daemon=True).start()

# ---------- Rutas ----------
@app.get("/", response_class=PlainTextResponse)
def root():
    # Render hace HEAD/GET a "/" para detectar el puerto: devolvemos 200 OK
    return "Mindful Uploader OK"

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/uploader", response_class=HTMLResponse)
def uploader_form(request: Request, session: str, folder: str = "mindful/profesionistas"):
    """
    Página de subida para abrir desde la PWA con ?session=XYZ.
    Al subir, guarda la URL bajo esa session para que la PWA la obtenga por /poll.
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
    Endpoint JSON para subir imágenes. Si 'session' viene, marca la URL en la sesión.
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
            set_session_url(session, url)
        return {"ok": True, "secure_url": url, "public_id": res.get("public_id")}
    except Exception as ex:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(ex)})

@app.get("/poll")
def poll(session: str):
    """
    La PWA consulta periódicamente si ya hay URL para esa sesión.
    """
    url = get_session_url(session)
    return {"ok": True, "url": url}
