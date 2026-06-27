# Configuration CMSFP Platform
# ---------------------------------------------------------------------------
# Sécurité (OWASP A02) : SECRET_KEY et credentials admin n'ont AUCUNE valeur
# par défaut en dur. Ils DOIVENT être fournis via le fichier .env ou les
# variables d'environnement. L'application refuse de démarrer sans eux.
# ---------------------------------------------------------------------------
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "CMSFP Platform"
    VERSION: str = "1.1.0"
    DATABASE_URL: str = "sqlite+aiosqlite:///./cmsfp.db"

    # --- Sécurité / JWT (A02 — pas de valeur par défaut en dur) ---
    # OBLIGATOIRE. Générer avec :
    #   python3 -c "import secrets; print(secrets.token_urlsafe(64))"
    SECRET_KEY: str

    ALGORITHM: str = "HS256"

    # --- Durée des tokens (A07 — réduit de 8h à 1h + refresh token) ---
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # --- Bootstrap admin (A07 — credentials via env, jamais en dur) ---
    # Premier compte administrateur créé au démarrage si aucun utilisateur
    # n'existe encore. En production, utiliser un mot de passe fort.
    CMSFP_BOOTSTRAP_ADMIN_USERNAME: str
    CMSFP_BOOTSTRAP_ADMIN_PASSWORD: str
    CMSFP_BOOTSTRAP_ADMIN_ROLE: str = "admin"

    # --- CORS (A05) ---
    # Origines autorisées, séparées par des virgules dans .env.
    # Ex : CORS_ORIGINS=https://cmsfp.example.gov,https://admin.cmsfp.example.gov
    CORS_ORIGINS: str = "http://localhost:8000,http://127.0.0.1:8000"

    @property
    def cors_origins_list(self) -> list[str]:
        """Retourne la liste des origines CORS (parsée depuis la chaîne)."""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    # --- Environnement ---
    # dev | staging | production
    ENVIRONMENT: str = "dev"

    # --- Base de données (A05 — option pour désactiver create_all en prod) ---
    AUTO_CREATE_TABLES: bool = True

    # --- Rate limiting (A01 — protection brute-force sur /login) ---
    # Format slowapi, ex. "5/minute".
    LOGIN_RATE_LIMIT: str = "5/minute"

    # --- Upload audio (A10 — taille max en octets, défaut 50 Mo) ---
    AUDIO_MAX_UPLOAD_BYTES: int = 50 * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
