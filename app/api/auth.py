# Authentification JWT — CMSFP Platform
# ---------------------------------------------------------------------------
# Système d'authentification par JWT (HS256) avec :
#   - OWASP A07 : aucun credential hardcodé — l'admin de bootstrap est chargé
#     depuis les variables d'environnement (.env).
#   - OWASP A07 : validation JWT stricte (vérification de signature + claims
#     obligatoires exp/sub, refus de l'algorithme "none").
#   - OWASP A01 : RBAC via `require_role(roles)` pour séparer
#     admin / comptable / medecin.
#   - OWASP A01 : rate limiting sur /login (slowapi) contre le brute-force.
#   - OWASP A07 : access token 60 min + refresh token 7 jours.
#   - OWASP A09 : audit log des tentatives de login (succès/échec).
#
# Endpoint POST /api/v1/auth/login  -> access_token + refresh_token
# Endpoint POST /api/v1/auth/refresh -> renouvelle l'access_token
# Dépendance `get_current_user` à injecter sur tous les endpoints protégés.
# ---------------------------------------------------------------------------
import bcrypt
from datetime import datetime, timedelta
from typing import Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings
from app.core.audit import audit_log

router = APIRouter(prefix="/api/v1/auth", tags=["Authentification"])

# --- Schémas ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# Rate limiter pour le endpoint de login (A01 — protection brute-force).
limiter = Limiter(key_func=get_remote_address)


class Token(BaseModel):
    """Jeton d'accès retourné par /login."""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    role: str
    expires_in: int  # secondes


class RefreshRequest(BaseModel):
    """Corps de la requête POST /refresh."""
    refresh_token: str


class TokenData(BaseModel):
    """Données décodées du JWT (utilisées par la dépendance)."""
    username: Optional[str] = None
    role: Optional[str] = None


# --- Hachage / vérification des mots de passe ---
def _hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# --- Base d'utilisateurs ---
# OWASP A07 : les credentials de bootstrap sont chargés exclusivement depuis
# les variables d'environnement (settings). Aucun mot de passe n'est codé en
# dur dans le code source. En production, remplacer par une table utilisateurs
# persistée en base de données.
_BOOTSTRAP_USERNAME = settings.CMSFP_BOOTSTRAP_ADMIN_USERNAME
_BOOTSTRAP_PASSWORD = settings.CMSFP_BOOTSTRAP_ADMIN_PASSWORD
_BOOTSTRAP_ROLE = settings.CMSFP_BOOTSTRAP_ADMIN_ROLE or "admin"

_USERS: dict[str, dict] = {
    _BOOTSTRAP_USERNAME: {
        "username": _BOOTSTRAP_USERNAME,
        "hashed_password": _hash_password(_BOOTSTRAP_PASSWORD),
        "role": _BOOTSTRAP_ROLE,
    },
}


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Vérifie username/password. Retourne l'utilisateur ou None."""
    user = _USERS.get(username)
    if not user:
        return None
    if not _verify_password(password, user["hashed_password"]):
        return None
    return user


# --- Création des tokens ---
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Crée un JWT signé (type=access) contenant `sub` (username) et `role`."""
    to_encode = data.copy()
    to_encode["type"] = "access"
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """Crée un JWT signé (type=refresh) de longue durée."""
    to_encode = data.copy()
    to_encode["type"] = "refresh"
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenData:
    """
    Dépendance FastAPI : valide le JWT Bearer et retourne les informations
    de l'utilisateur authentifié.

    OWASP A07 : validation stricte — vérification de signature activée,
    claims `exp` et `sub` obligatoires, algorithme restreint à `settings.ALGORITHM`
    (refuse implicitement `alg=none` car seul HS256 est accepté).
    Le token doit être de type "access" (les refresh tokens ne sont pas
    acceptés pour l'authentification des endpoints).

    Lève 401 si le token est absent, invalide, expiré, ou de mauvais type.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token invalide ou expiré",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={
                "verify_signature": True,
                "verify_exp": True,
                "require": ["exp", "sub"],
            },
        )
        username: Optional[str] = payload.get("sub")
        role: Optional[str] = payload.get("role")
        token_type: Optional[str] = payload.get("type")
        if username is None:
            raise credentials_exception
        # Un refresh token ne doit pas être utilisé pour accéder aux endpoints.
        if token_type == "refresh":
            raise credentials_exception
        return TokenData(username=username, role=role)
    except JWTError:
        raise credentials_exception


# Alias sémantique pour protéger les routes financières
require_auth = get_current_user


def require_role(allowed_roles: Set[str]):
    """
    Dépendance FastAPI pour le contrôle d'accès basé sur les rôles (RBAC).

    OWASP A01 : permet de restreindre un endpoint à un ou plusieurs rôles.
    L'utilisateur authentifié doit posséder un rôle présent dans
    `allowed_roles`, sinon une erreur 403 est levée.

    Usage :
        @router.post("/budget", dependencies=[Depends(require_role({"admin"}))])
        async def definir_budget(...): ...

    Rôles conventionnels : admin, comptable, medecin.
    """
    def _check_role(current_user: TokenData = Depends(get_current_user)) -> TokenData:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Accès refusé : rôle '{current_user.role}' insuffisant. "
                    f"Rôles requis : {', '.join(sorted(allowed_roles))}."
                ),
            )
        return current_user

    return _check_role


# --- Endpoint de login ---
@router.post("/login", response_model=Token, summary="Authentification")
@limiter.limit(settings.LOGIN_RATE_LIMIT)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """
    Authentifie un utilisateur et retourne un jeton JWT (Bearer).

    Format : OAuth2 Password Flow (form-encoded `username` + `password`).

    **Sécurité :**
    - Rate limiting (A01) : 5 tentatives par minute par IP.
    - Audit log (A09) : chaque tentative (succès/échec) est journalisée.
    - Access token 60 min + refresh token 7 jours (A07).

    Le compte administrateur de bootstrap est configuré via les variables
    d'environnement `CMSFP_BOOTSTRAP_ADMIN_USERNAME` / `_PASSWORD`.
    """
    client_ip = get_remote_address(request)
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        # Audit : échec de login (sans logger le mot de passe).
        audit_log(
            "login",
            user=form_data.username,
            success=False,
            ip=client_ip,
            detail="credentials invalides",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nom d'utilisateur ou mot de passe incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Audit : login réussi.
    audit_log("login", user=user["username"], success=True, ip=client_ip)

    access_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["username"], "role": user["role"]},
        expires_delta=access_expires,
    )
    refresh_token = create_refresh_token(
        data={"sub": user["username"], "role": user["role"]},
    )
    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        role=user["role"],
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# --- Endpoint de refresh ---
@router.post("/refresh", response_model=Token, summary="Renouvellement du jeton d'accès")
async def refresh_token(payload: RefreshRequest):
    """
    Renouvelle l'access token à partir d'un refresh token valide.

    Le refresh token doit être de type "refresh" et non expiré.
    Retourne un nouvel access token (et un nouveau refresh token pour
    la rotation).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Refresh token invalide ou expiré",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token_payload = jwt.decode(
            payload.refresh_token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={
                "verify_signature": True,
                "verify_exp": True,
                "require": ["exp", "sub"],
            },
        )
        if token_payload.get("type") != "refresh":
            raise credentials_exception
        username: Optional[str] = token_payload.get("sub")
        role: Optional[str] = token_payload.get("role")
        if username is None or role is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    access_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    new_access = create_access_token(
        data={"sub": username, "role": role},
        expires_delta=access_expires,
    )
    new_refresh = create_refresh_token(data={"sub": username, "role": role})

    return Token(
        access_token=new_access,
        refresh_token=new_refresh,
        token_type="bearer",
        role=role,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
