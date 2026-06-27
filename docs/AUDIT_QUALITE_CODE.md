# Audit qualité du code — Plateforme CMSFP (FastAPI)

**Date :** 2026-06-26
**Auditeur :** Agent QA (Hermes)
**Périmètre :** `app/main.py`, `app/api/*.py` (tarifs, patients, consultations, paiements, caisse), `app/models/models.py`, `app/services/tarif_engine.py`, `app/schemas/schemas.py`, `app/core/*.py` (config, database), `requirements.txt`, `tests/test_caisse.sh`
**Méthode :** Relecture statique + exécution réelle (`compileall`, import de l'app, serveur uvicorn, requêtes HTTP, introspection SQLite).
**Note :** Aucun fichier source n'a été modifié. Les vérifications runtime ont ajouté puis supprimé des lignes de test dans la base de dev `cmsfp.db` (restaurée à son état initial : 3 patients, journal nettoyé).

---

## 1. Synthèse exécutive

Le code **compile et s'exécute** : `python3 -m compileall app/` ✔, `from app.main import app` ✔ (37 routes enregistrées), endpoints fonctionnels en runtime. L'architecture est cohérente (FastAPI + SQLAlchemy 2 async + Pydantic v2), les requêtes sont paramétrées (pas d'injection SQL), la validation Pydantic est présente, et la chaîne de hachage du journal de caisse fonctionne.

Cependant, sur une **plateforme financière**, plusieurs problèmes de sécurité et d'intégrité de données sont bloquants avant mise en production : **aucune authentification**, **intégrité référentielle non enforced** (orphelins dans le journal immuable), et **montants monétaires en `Float`**.

| Gravité | Nombre |
|---|---|
| Critique | 2 |
| Élevée | 2 |
| Moyenne | 5 |
| Mineure | 13 |

---

## 2. Points positifs

- **Pas d'injection SQL** : toutes les requêtes utilisent l'ORM SQLAlchemy / paramètres liés. La recherche `ilike(f"%{mot}%")` est parameter-bound (le `%` est dans la valeur, pas concaténé au SQL).
- **Pydantic v2** correctement utilisé (`ConfigDict(from_attributes=True)`, `Field(..., ge=, min_length=, max_length=)`).
- **Codes HTTP cohérents** : 201 (création), 204 (suppression), 404/409/422 (erreurs métier).
- **Pagination** systématique avec `skip`/`limit` plafonnés (`le=100`/`le=500`).
- **Pas de lazy-load en async** : `supprimer_patient` compte explicitement les dépendances (consultations, paiements) plutôt que de déclencher un chargement paresseux interdit en async.
- **Journal de caisse immuable** : chaîne SHA-256 (`hash_precedent` → `hash_courant`), endpoint `GET /journal/verifier` qui recalcule et détecte les altérations.
- **Gestion d'erreurs métier** détaillée (cas particuliers de caisse, transitions de statut de paiement).
- **Docstrings** de bonne qualité sur le module caisse.

---

## 3. Problèmes trouvés

### 🔴 CRITIQUES

**C1. Absence totale d'authentification / autorisation**
- Aucun code d'auth dans `app/` (vérifié : aucun `oauth2`, `jwt`, `jose`, `passlib`, `get_current_user`, `HTTPBearer`, `Security(...)`).
- `requirements.txt` déclare `python-jose` et `passlib` mais **aucun import** → dépendances mortes.
- `config.py` définit `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` mais ils ne sont **jamais utilisés**.
- Conséquence : tous les endpoints (patients, consultations, paiements, **journal de caisse immuable**) sont publiquement accessibles. `SECRET_KEY = "changer-en-production"` en dur. Inacceptable pour une plateforme financière.

**C2. Intégrité référentielle non enforced + suppression qui orpheline le journal immuable** *(confirmé en runtime)*
- `PRAGMA foreign_keys = 0` : SQLite n'applique pas les FK (le moteur n'active pas le pragma via un listener `connect`).
- `supprimer_patient` ne vérifie que `consultations` et `paiements`, **pas `caisse_operations`**.
- **Test runtime** : création d'un patient #6 + opération de caisse #12 le référençant, puis `DELETE /patients/6` → **HTTP 204**, patient supprimé, opération #12 conservée avec `patient_id=6` → **orphelin**. Le journal immuable pointe vers un patient inexistant : la traçabilité financière est cassée.
- À l'inverse, une FK enforced aurait levé une `IntegrityError` 500 non gérée (aucun `try/except` sur la suppression).

### 🟠 ÉLEVÉES

**E1. Montants monétaires en `Float`** *(confirmé en runtime)*
- Colonnes `montant`, `remise`, `montant_ouverture`, `montant_theorique`, `montant_cloture`, `ecarts`, `remise_appliquee` : type `Float`. `tarif_engine` fait de l'arithmétique `float` + `round()`.
- Les FCFA sont des unités entières ; le `Float` provoque des erreurs d'arrondi dans les agrégations (`statistiques/summary` fait un `SUM`, `_recalculer_contexte` accumule des `float`).
- **Test runtime** : `montant=100.10` stocké `100.1` ; `0.1+0.2` non représentable.
- Recommandation : `Integer` (FCFA entiers) ou `Numeric(12,2)` + `Decimal` dans le moteur tarifaire.

**E2. `hash_courant="pending"` à l'insertion → race sur contrainte `UNIQUE`**
- La colonne `hash_courant` est `unique=True, nullable=False`. L'insertion met `"pending"`, fait `flush()` (pour obtenir l'id), puis recalcule le vrai hash.
- Deux `POST /journal` **concurrents** flushed simultanément avec `"pending"` → `IntegrityError` 500. De plus, `"pending"` viole temporairement la sémantique de hachage.
- Recommandation : calculer le hash dans la transaction (séquence/CTE), ou retirer `UNIQUE` sur la colonne placeholder et s'appuyer sur l'endpoint `verifier`.

### 🟡 MOYENNES

**M1. Représentation incohérente des enums en base** *(confirmé en runtime)*
- Colonnes `SAEnum` (`patients.status`, `consultations.type`, `caisse.type_operation`, `caisse.cas_particulier`) stockent les **noms** (`FONCTIONNAIRE`, `OPHTALMO`, `OUVERTURE`…).
- Colonnes `String` (`paiements.statut`, `paiements.mode`) stockent les **valeurs** (`effectue`, `especes`).
- Schéma « split-brain » : toute comparaison directe SQL ou interopérabilité avec `.value` sur une colonne `SAEnum` échoue silencieusement. Utiliser `SAEnum(..., values_callable=lambda e: [m.value for m in e])` ou uniformiser en `Enum`.

**M2. Enums dupliqués entre couches**
- `tarif_engine` redéfinit `StatutPatient`/`TypePrestation` séparément de `models.PatientStatus`/`ConsultationType`, nécessitant les convertisseurs `_statut_patient()` + `_CONSULTATION_TO_PRESTATION`.
- `TypePrestation` contient `APPAREILLAGE_DENTAIRE` et `PLANIFICATION_FAMILIALE` **sans équivalent** dans `ConsultationType` → inatteignables via `/consultations`. Risque de dérive.

**M3. Chargements full-table non paginés**
- `GET /caisse/journal/verifier` et `GET /caisse/journal/du-jour` chargent **toutes** les opérations en mémoire. Sur un journal qui croît indéfiniment : risque mémoire / DoS.

**M4. Pas de middleware CORS**
- Le frontend (React/PWA indiqué dans `app/__init__.py`) sur une autre origine ne pourra pas appeler l'API. Ajouter `CORSMiddleware`.

**M5. Logique ternaire morte / trompeuse**
- `caisse.py` : `montant_ouverture=montant_ouverture_ctx if t == TypeOperationCaisse.OUVERTURE else montant_ouverture_ctx` — **les deux branches sont identiques**. *(confirmé : `montant_ouverture` non-null sur tous les types d'opération)*. Le solde d'ouverture est recopié sur chaque ligne ; code mort/mensonger.

### 🟢 MINEURES

- **m1.** `datetime.utcnow()` partout (défauts modèles, `consultations`, `paiements`, `caisse`) — **déprécié en Python 3.12**, renvoie du naïf UTC. Comparaisons avec `date.today()` (local) dans `journal_du_jour` → fenêtre « aujourd'hui » décalée. → `datetime.now(timezone.utc)`.
- **m2.** `__import__("datetime").datetime.utcnow()` dans `consultations.preview_tarif` — import inline laid ; `datetime` n'est pas importé en tête de fichier.
- **m3.** Import en milieu de fichier : `schemas.py` ligne 200 (`from app.models.models import TypeOperationCaisse, CasParticulierCaisse`) — PEP8 demande les imports en tête.
- **m4.** `PUT /paiements/{id}` prend `montant`/`mode` en **query params** au lieu d'un body Pydantic — non-REST et incohérent avec les autres endpoints de mise à jour. Permet en outre de modifier le montant d'un paiement quel que soit son statut (pas de garde métier).
- **m5.** `differer_paiement` stocke le motif de report dans `motif_remboursement` — sémantique de champ erronée.
- **m6.** Docstrings manquantes/incohérentes : endpoints de `tarifs.py` sans docstring ; certains helpers non documentés (alors que le module caisse est bien documenté).
- **m7.** `GET /tarifs/calculer` utilise `statut: str, prestation: str` + parsing `try/except` manuel au lieu des types `Enum` → perd la doc OpenAPI et la validation 422 automatique.
- **m8.** `tarif_engine.calculer()` renvoie `int` (`0`, `round()`) dans certains cas et `float` via les overrides — types numériques de retour incohérents.
- **m9.** Ordre des routes fragile : `/paiements/{paiement_id}` déclaré **avant** `/paiements/statistiques/summary`. Fonctionne uniquement parce que `paiement_id` est typé `int` (les chemins non numériques sont ignorés). Déclarer les routes spécifiques avant les paramétrées. *(confirmé : `/statistiques/summary` renvoie bien 200)*.
- **m10.** `round()` (banker's rounding) utilisé pour de l'argent — stratégie d'arrondi à expliciter.
- **m11.** `date_paiement=datetime.utcnow()` redondant dans `encaisser_paiement` alors que le modèle a déjà `default=datetime.utcnow`.
- **m12.** `migrations/` vide, `docs/` vide. Le `lifespan` utilise `create_all` (dev uniquement) : pas d'Alembic pour la migration PostgreSQL de production (prévue dans `__init__.py`).
- **m13.** Aucun logging structuré, pas de rate limiting, pas de middleware de validation au-delà de Pydantic.

---

## 4. Exécutabilité

| Vérification | Résultat |
|---|---|
| `python3 -m compileall app/` | ✔ PASS |
| `from app.main import app` | ✔ PASS — 37 routes |
| Serveur uvicorn + `/health` | ✔ 200 |
| CRUD patients, tarifs, consultations (auto-tarif), paiements, chaîne caisse | ✔ fonctionnels |
| `tests/test_caisse.sh` | ⚠ Le harness échoue (`KeyError: 'id'` au parsing) — les endpoints sous-jacents fonctionnent ; le script suppose un serveur lancé + DB propre. À migrer en `pytest` + `TestClient`. |

Imports : tous corrects et résolus ; aucune référence cassée.

---

## 5. Recommandations priorisées

1. **Authentification** : implémenter OAuth2/JWT (dépendances déjà présentes) + contrôle d'accès par rôle sur tous les endpoints ; charger `SECRET_KEY` depuis l'environnement.
2. **Intégrité référentielle** : activer `PRAGMA foreign_keys=ON` (listener `connect` sur l'engine) et étendre `supprimer_patient` à `caisse_operations` (ou `ON DELETE RESTRICT`). En PostgreSQL les FK sont enforced par défaut.
3. **Monnaie** : passer les colonnes monétaires en `Integer` (FCFA) ou `Numeric(12,2)` ; utiliser `Decimal` dans `tarif_engine`.
4. **Hachage** : calculer `hash_courant` avant insert (transaction/séquence) ou retirer `UNIQUE` sur le placeholder.
5. **Enums** : uniformiser le stockage (`values_callable`) et fusionner les enums `tarif_engine` avec ceux du modèle.
6. **API** : ajouter `CORSMiddleware`, paginer `verifier`/`du-jour`, convertir `PUT /paiements` en body Pydantic, réordonner les routes.
7. **Migration/Obs** : mettre en place Alembic ; ajouter logging structuré + rate limiting.
8. **Modernisation** : remplacer `datetime.utcnow()` par `datetime.now(timezone.utc)` ; nettoyer imports ; ajouter docstrings manquantes.
9. **Tests** : migrer `test_caisse.sh` vers une suite `pytest`/`TestClient` avec base de test isolée.
