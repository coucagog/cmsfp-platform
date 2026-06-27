# Main FastAPI app — CMSFP Platform
# ---------------------------------------------------------------------------
# Sécurité (OWASP Top 10) implémentée :
#   - A05 : middleware CORS restrictif (origines configurables via .env).
#   - A05 : headers de sécurité (X-Content-Type-Options, X-Frame-Options,
#           Strict-Transport-Security, Content-Security-Policy).
#   - A05 : /dashboard désormais protégé par JWT (était public).
#   - A05 : création automatique des tables conditionnelle
#           (AUTO_CREATE_TABLES — désactivable en production).
#   - A09 : middleware de logging structuré des requêtes HTTP.
#   - A01 : rate limiting via slowapi (configuré sur /login).
# ---------------------------------------------------------------------------
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import (
    tarifs,
    patients,
    consultations,
    paiements,
    caisse,
    auth,
    conseil_sante,
    dashboard,
    audio,
)
from app.core.config import settings
from app.core.database import engine, Base

# --- Logging (A09) ---
logger = logging.getLogger("cmsfp.app")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# --- Rate limiter global (A01) ---
# Enregistré sur l'app pour que le décorateur @limiter.limit fonctionne
# sur les endpoints individuels (notamment /login).
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Crée les tables au démarrage si AUTO_CREATE_TABLES est True (A05)."""
    if settings.AUTO_CREATE_TABLES:
        logger.info(
            "AUTO_CREATE_TABLES=true — création/vérification du schéma de base."
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        logger.info(
            "AUTO_CREATE_TABLES=false — schéma non créé automatiquement "
            "(utiliser des migrations Alembic en production)."
        )
    yield


app = FastAPI(
    title="CMSFP Platform",
    description=(
        "Plateforme de gestion financière du Centre Médico-Social "
        "de la Fonction Publique"
    ),
    version="1.1.0",
    lifespan=lifespan,
)

# --- SlowAPI : rate limiting global (A01) ---
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# --- CORS (A05) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)


# --- Security headers middleware (A05) ---
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Ajoute des headers de sécurité à toutes les réponses HTTP.

    - X-Content-Type-Options: nosniff — empêche le MIME-sniffing.
    - X-Frame-Options: DENY — protection contre le clickjacking.
    - Strict-Transport-Security — force HTTPS (respecté par les navigateurs
      dès la première connexion HTTPS ; ignoré en HTTP pur).
    - Content-Security-Policy — restreint les sources de contenu.
    - Referrer-Policy — limite la fuite d'URL référente.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )
        # CSP permissive pour le dashboard statique (scripts inline + styles)
        # mais restreignant les sources externes.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(SecurityHeadersMiddleware)


# --- Request logging middleware (A09) ---
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Journalise chaque requête HTTP : méthode, chemin, code de statut,
    durée, IP du client. Permet la traçabilité et la détection d'activité
    anormale.
    """

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        client_ip = get_remote_address(request)

        # On ne logue pas /health (sonde de liveness — trop bruyant).
        if request.url.path == "/health":
            return await call_next(request)

        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "request method=%s path=%s status=500 ip=%s duration=%.1fms "
                "(exception)",
                request.method,
                request.url.path,
                client_ip,
                elapsed_ms,
            )
            raise

        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "request method=%s path=%s status=%d ip=%s duration=%.1fms",
            request.method,
            request.url.path,
            response.status_code,
            client_ip,
            elapsed_ms,
        )
        return response


app.add_middleware(RequestLoggingMiddleware)


# Fichiers statiques (dashboard HTML, etc.)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Router d'authentification (POST /api/v1/auth/login) — public
app.include_router(auth.router)

# Routers métier — tous protégés par JWT via dependencies au niveau du router
app.include_router(tarifs.router)
app.include_router(patients.router)
app.include_router(consultations.router)
app.include_router(paiements.router)
app.include_router(caisse.router)
app.include_router(conseil_sante.router)
app.include_router(dashboard.router)

# Routers IA Audio (Défi 5) — dictée consultation, résumé patient, CR réunions.
# Tous protégés par JWT (dépendance au niveau du router).
app.include_router(audio.audio_router)
app.include_router(audio.reunions_router)


@app.get("/health")
async def health():
    """Sonde de liveness — publique (aucune donnée sensible)."""
    return {"status": "ok", "version": "1.1.0", "platform": "Hermes Agent CMSFP"}


@app.get("/dashboard", include_in_schema=False)
async def dashboard_page():
    """Sert la page HTML du tableau de bord (auth gérée côté JS via JWT)."""
    return FileResponse(str(STATIC_DIR / "dashboard.html"))


@app.get("/fiche-patient", include_in_schema=False)
async def fiche_patient_page():
    """Sert la page HTML de consultation / édition de fiche patient."""
    return FileResponse(str(STATIC_DIR / "fiche-patient.html"))
