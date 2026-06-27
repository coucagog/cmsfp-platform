# Rapport de Sécurité OWASP — CMSFP Platform

## Résumé

| Champ | Valeur |
|-------|--------|
| Date de l'audit | 26 juin 2026 |
| Version audité | **1.1.0** (`app/main.py`, `app/core/config.py`) |
| Cible | Plateforme de gestion financière du Centre Médico-Social de la Fonction Publique |
| Statut | **14 vulnérabilités corrigées sur 14 identifiées** |
| Pile technique | FastAPI · SQLAlchemy (async) · python-jose (JWT HS256) · slowapi · Pydantic v2 |

### Synthèse de la couverture OWASP Top 10 (2021)

| Catégorie | Statut | Détail |
|-----------|--------|--------|
| A01 — Broken Access Control | ✅ | RBAC, rate limiting, /dashboard protégé |
| A02 — Cryptographic Failures | ✅ | SECRET_KEY env, JWT strict, tokens courts |
| A03 — Injection | ✅ | ORM paramétré, validation Pydantic |
| A04 — Insecure Design | ✅ | Audit log, exposition des tokens réduite |
| A05 — Security Misconfiguration | ✅ | CORS, security headers, create_all conditionnel |
| A06 — Vulnerable Components | ✅ | Versions figées, CVE python-jose corrigée |
| A07 — Identification & Auth Failures | ✅ | Credentials env, rate limiting, refresh token |
| A08 — Software Integrity | ⚠️ Partiel | SBOM/checksums à planifier |
| A09 — Logging & Monitoring | ✅ | Audit log + middleware de logging |
| A10 — SSRF | ✅ | Pre-check taille uploads audio |

---

## OWASP Top 10 — Analyse par catégorie

### A01: Broken Access Control ✅

**Corrections apportées :**
- RBAC via `require_role()` — possibilité de séparer les rôles `admin` / `comptable` / `medecin`.
- Rate limiting sur `/login` (slowapi, 5 requêtes/min) — protection anti-brute-force.
- `/dashboard` désormais protégé par JWT (`Depends(auth.get_current_user)`).

**Restant à traiter :**
- Rotation et révocation explicite des tokens (liste noire / denylist côté serveur).
- Vérification de propriété ressource-par-ressource (object-level authorization).

### A02: Cryptographic Failures ✅

**Corrections apportées :**
- `SECRET_KEY` retiré du code source : fourni exclusivement via `.env` / variables d'environnement, **sans valeur par défaut** (l'application refuse de démarrer sans lui).
- JWT `alg=none` bloqué : `verify_signature=True` + vérification explicite de `exp` et `sub`.
- Expiration du token d'accès réduite à **60 min** (était 480 min).
- Refresh token avec rotation (**7 jours**).

**Restant à traiter :**
- Migration de HS256 vers **RS256** (clé asymétrique) pour permettre la vérification publique sans partager le secret de signature.
- Hachage des refresh tokens en base (actuellement stateless).

### A03: Injection ✅ (déjà conforme)

- SQLAlchemy ORM avec requêtes paramétrées — pas de concaténation SQL.
- Validation Pydantic sur toutes les entrées (schémas de requête).

### A04: Insecure Design ✅

**Corrections apportées :**
- Audit log sur toutes les opérations critiques (création patient, encaissement, opération caisse, login).
- Réduction du temps d'exposition des tokens (60 min + refresh).

**Restant à traiter :**
- Authentification multifacteur (MFA).
- Whitelist IP pour les endpoints d'administration.

### A05: Security Misconfiguration ✅

**Corrections apportées :**
- `CORSMiddleware` avec origines configurables via `CORS_ORIGINS` (`.env`).
- Security headers via `SecurityHeadersMiddleware` (6 headers) :
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`
  - `Content-Security-Policy` (restrictive, frame-ancestors 'none')
  - `Referrer-Policy: strict-origin-when-cross-origin`
- `create_all` conditionnel via `AUTO_CREATE_TABLES` (désactivable en production → migrations Alembic).

**Vérification HTTP effectuée (26/06/2026) :** tous les headers de sécurité sont présents sur les réponses (y compris sur `/health`).

**Restant à traiter :**
- HTTPS / TLS au niveau du reverse proxy (HSTS n'est pleinement effectif qu'en HTTPS).

### A06: Vulnerable Components ✅

**Corrections apportées :**
- Versions **figées** (minimums) dans `requirements.txt`.
- CVE python-jose corrigée (version ≥ 3.4).

**Restant à traiter :**
- Audit de sécurité régulier des dépendances (`pip-audit`, `safety`, Dependabot).
- Scan continu des vulnérabilités connues.

### A07: Identification & Auth Failures ✅

**Corrections apportées :**
- Suppression des credentials hardcodés : bootstrap admin exclusivement via `CMSFP_BOOTSTRAP_ADMIN_*` (`.env`).
- Rate limiting (5 req/min sur `/login`) — mitigation brute-force.
- Refresh token avec rotation (7 jours).
- Token d'accès **60 min** au lieu de 480 min.

**Vérification effectuée (26/06/2026) :** `POST /api/v1/auth/login` renvoie `access_token` + `refresh_token` + `role` + `expires_in: 3600`.

**Restant à traiter :**
- Politique de mot de passe (longueur minimale, complexité).
- Verrouillage de compte après N échecs (account lockout).
- MFA.

### A08: Software Integrity ⚠️ Partiel

**Restant à traiter :**
- Checksum des dépendances (`hashin` / `pip-require-hashes`).
- SBOM (Software Bill of Materials) pour la chaîne d'approvisionnement.
- Signature et vérification des artefacts de build.

### A09: Logging & Monitoring ✅

**Corrections apportées :**
- Audit log structuré (`app/core/audit.py` — fonction `audit_log()`).
- `RequestLoggingMiddleware` : journalise méthode, chemin, code de statut, durée, IP client.
- Traçage des événements sensibles : login (succès/échec), création patient, encaissements, opérations de caisse.

**Restant à traiter :**
- Intégration SIEM (centralisation des logs).
- Alerting temps réel (détection d'anomalies).

### A10: SSRF ✅

**Corrections apportées :**
- Vérification de la taille (`AUDIO_MAX_UPLOAD_BYTES`) **avant** lecture des uploads audio.
- Seuls les traitements locaux sont autorisés (pas de récupération de ressources par URL externe).

---

## Correctifs Détaillés

| # | Vulnérabilité | Fichier | Correctif |
|---|---------------|---------|-----------|
| 1 | Secret hardcodé | `app/core/config.py` | Suppression valeur défaut + validation env (obligatoire) |
| 2 | Credentials démo hardcodés | `app/api/auth.py` | Bootstrap admin depuis `.env` uniquement |
| 3 | CORS absent | `app/main.py` | `CORSMiddleware` configurable via `CORS_ORIGINS` |
| 4 | Headers de sécurité absents | `app/main.py` | `SecurityHeadersMiddleware` (6 headers) |
| 5 | Rate limiting absent | `app/api/auth.py` | slowapi — 5 req/min sur `/login` |
| 6 | RBAC manquant | `app/api/auth.py` | `require_role()` avec rôles |
| 7 | JWT `alg=none` accepté | `app/api/auth.py` | `verify_signature=True` + `exp`/`sub` requis |
| 8 | Versions `>=` non figées | `requirements.txt` | Versions minimales figées |
| 9 | Audit log absent | `app/core/audit.py` + 4 routers | Module `audit_log()` + appels |
| 10 | `create_all` automatique | `app/main.py` | `AUTO_CREATE_TABLES` configurable |
| 11 | Token 8 h | `app/core/config.py` | 60 min + refresh token (7 jours) |
| 12 | `/dashboard` public | `app/main.py` | Protégé par `get_current_user` |
| 13 | Upload sans pre-check | `app/api/audio.py` | Vérification taille avant lecture |
| 14 | Logging requêtes absent | `app/main.py` | `RequestLoggingMiddleware` |

---

## Vérifications de non-régression (26 juin 2026)

| Test | Résultat |
|------|----------|
| Démarrage du serveur (`uvicorn app.main:app`) | ✅ Application startup complete |
| `GET /health` | ✅ `200` — `{"status":"ok","version":"1.1.0",...}` |
| Security headers sur `/health` (HTTP) | ✅ `X-Content-Type-Options`, `X-Frame-Options`, `Strict-Transport-Security`, `Content-Security-Policy`, `Referrer-Policy` présents |
| `POST /api/v1/auth/login` (admin) | ✅ `200` — `access_token` + `refresh_token` + `role:"admin"` + `expires_in:3600` |
| `tests/test_corrections.py` (14 tests) | ✅ PASS — login, 401/200 auth, JWT, CRUD patient/consultation/paiement/caisse, chaîne de hachage, token invalide, FK |
| `tests/test_supplementaire.py` | ✅ PASS — FK au niveau DB (PRAGMA foreign_keys=ON), immuabilité caisse_ops, scénarios token (absent/malformé/vide/mauvais schème → 401) |
| `tests/test_audio.py` | ✅ PASS — STT (faster-whisper), TTS, CR réunion, **401 sans token** sur tous les endpoints audio, 404 patient/réunion/consultation |

> **Note sur l'exécution des tests** : les fichiers `tests/test_*.py` sont des **scripts
> d'intégration HTTP** (assertions au niveau module, sans fonctions `test_`) conçus pour
> être lancés directement (`python3 tests/test_corrections.py`, etc.) contre un serveur
> actif et une base propre. `pytest tests/` les importe (exécution du code de premier
> niveau lors de la collecte) mais ne reconnaît pas de fonctions de test (`0 items
> collected`) et souffre de l'ordre de collecte alphabétique (`test_audio` s'exécute avant
> que `test_corrections` ne crée la consultation #1 dont il dépend). Les trois suites ont
> donc été validées par exécution directe sur base fraîche — **toutes passent**.

---

## Recommandations Futures

- Migration de **HS256 vers RS256** pour les JWT (clé asymétrique).
- Implémentation de la **MFA** sur les comptes administrateurs.
- **Hachage des refresh tokens** en base + révocation.
- Compteurs d'échecs + **account lockout** temporisé.
- Politique de **mot de passe** (longueur, complexité, rotation).
- **SBOM** + checksums des dépendances (`pip-audit`, `hashin`).
- Revue de code régulière (**SAST/DAST**) et tests de pénétration trimestriels.
- Centralisation des logs dans un **SIEM** + alerting temps réel.
- **HTTPS/TLS** au niveau du reverse proxy (HSTS déjà en place côté application).
