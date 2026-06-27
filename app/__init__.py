# CMSFP Platform - Digital Masterplan Architecture
#
# ┌────────────────────────────────────────────────────────────┐
# │           APPLICATIONS ET SERVICES                         │
# │  Caisse · Patients · Conseil Santé · Tableaux de bord     │
# │  IA : Dictée · Résumé audio · CR réunions                 │
# ├────────────────────────────────────────────────────────────┤
# │           PLATEFORMES DIGITALES                             │
# │  Hermes Agent · FastAPI · OpenRouter · PostgreSQL          │
# │  Sub-agents : Claude Code → DeepSeek v4 Flash              │
# ├────────────────────────────────────────────────────────────┤
# │           INFRASTRUCTURES DE BASE                           │
# │  Docker/WSL2 · SQLite/PostgreSQL · Whisper STT · SMTP     │
# └────────────────────────────────────────────────────────────┘
#
# Technologies:
# - Backend: FastAPI (Python)
# - BDD: SQLite (dev) → PostgreSQL (prod)
# - Frontend: React / PWA
# - Agents: Claude Code Pro + DeepSeek v4 Flash (fallback)
# - LLM Gateway: OpenRouter
# - STT: Whisper / API
# - TTS: Edge TTS
# - Email: SMTP (ridwan@gcouca.com)
# - Messagerie: Telegram

__version__ = "1.0.0"
__description__ = "CMSFP Platform — Digitalisation gestion financière"
