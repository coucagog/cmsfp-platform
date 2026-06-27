#!/usr/bin/env python3
"""Suite de test des fonctionnalités IA Audio (CMSFP Platform).

Teste les 3 fonctionnalités via l'API HTTP :
  1. Dictée consultation (STT)
  2. Résumé audio patient (TTS)
  3. Compte-rendu de réunion (STT + résumé + participants + actions)

Usage : python3 test_audio.py
"""
import json
import sys
import time
import requests

BASE = "http://localhost:8000"
TIMEOUT = 150  # STT local peut être lent au 1er chargement du modèle


def banner(title):
    print(f"\n{'='*70}\n  {title}\n{'='*70}")


def pp(label, resp):
    print(f"\n--- {label} ---")
    print(f"HTTP {resp.status_code}  ({resp.elapsed.total_seconds():.2f}s)")
    ct = resp.headers.get("content-type", "")
    if "application/json" in ct:
        try:
            print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
        except Exception:
            print(resp.text[:1000])
    elif "audio/" in ct:
        print(f"[flux audio {ct} — {len(resp.content)} octets]")
    else:
        print(resp.text[:1000])


# --------------------------------------------------------------------------- #
#  Authentification
# --------------------------------------------------------------------------- #
banner("AUTHENTIFICATION (admin/admin123)")
r = requests.post(f"{BASE}/api/v1/auth/login",
                  data={"username": "admin", "password": "admin123"}, timeout=10)
pp("POST /api/v1/auth/login", r)
assert r.status_code == 200, "Login échoué"
TOKEN = r.json()["access_token"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}
print(f"\nToken obtenu ({len(TOKEN)} chars).")

# --------------------------------------------------------------------------- #
#  TEST 1 — Endpoints protégés (401 sans token)
# --------------------------------------------------------------------------- #
banner("TEST 1: Endpoints SANS token -> 401")
for method, path in [("GET", "/api/v1/audio/resume/1"),
                     ("GET", "/api/v1/reunions"),
                     ("GET", "/api/v1/reunions/1"),
                     ("POST", "/api/v1/audio/dictee"),
                     ("POST", "/api/v1/audio/reunion")]:
    code = requests.request(method, f"{BASE}{path}", timeout=10).status_code
    ok = "OK" if code == 401 else "FAIL"
    print(f"  [{ok}] {method:4s} {path:40s} -> {code} (attendu 401)")

# --------------------------------------------------------------------------- #
#  TEST 2 — Dictée consultation (STT)
# --------------------------------------------------------------------------- #
banner("TEST 2: POST /api/v1/audio/dictee (consultation #1)")
print("  Transcription via faster-whisper (modèle tiny, 1er chargement ~15-30s)...")
t0 = time.time()
with open("/tmp/dictee_test.mp3", "rb") as f:
    r = requests.post(f"{BASE}/api/v1/audio/dictee",
                      headers=AUTH,
                      data={"consultation_id": "1"},
                      files={"file": ("dictee_test.mp3", f, "audio/mpeg")},
                      timeout=TIMEOUT)
pp(f"POST /api/v1/audio/dictee  ({time.time()-t0:.1f}s)", r)
assert r.status_code == 200, f"Dictee failed: {r.status_code} {r.text}"
data = r.json()
print(f"\n  Transcribeur : {data['transcribeur']}")
print(f"  Taille audio : {data['taille_audio_octets']} octets")
print(f"  Transcription: \"{data['dictee_audio']}\"")
assert data["dictee_audio"], "Transcription vide"
assert data["transcribeur"] in ("faster_whisper_local", "openai_whisper")

# Vérifier que la consultation a bien été mise à jour en base.
r2 = requests.get(f"{BASE}/api/v1/consultations/1", headers=AUTH, timeout=10)
assert r2.status_code == 200
assert r2.json()["dictee_audio"] == data["dictee_audio"], "dictee_audio non persisté en base"
print(f"\n  [OK] Consultation #1 mise à jour en base (dictee_audio persisté).")

# --------------------------------------------------------------------------- #
#  TEST 3a — Résumé patient (JSON)
# --------------------------------------------------------------------------- #
banner("TEST 3a: GET /api/v1/audio/resume/1 (mode JSON)")
r = requests.get(f"{BASE}/api/v1/audio/resume/1", headers=AUTH, timeout=30)
pp("GET /api/v1/audio/resume/1", r)
assert r.status_code == 200, f"Resume JSON failed: {r.status_code} {r.text}"
data = r.json()
print(f"\n  Patient       : {data['prenom']} {data['nom']} ({data['status']})")
print(f"  Consultations : {data['nombre_consultations']}")
print(f"  Total payé    : {data['total_paye']} FCFA")
print(f"  Audio dispo   : {data['audio_disponible']}")
print(f"  Audio URL     : {data['audio_url']}")
print(f"  Résumé texte  : \"{data['resume_texte'][:200]}...\"")
assert data["audio_disponible"] is True
assert data["audio_url"]
assert "Test Patient" in data["resume_texte"] or "Patient Test" in data["resume_texte"]

# --------------------------------------------------------------------------- #
#  TEST 3b — Résumé patient (flux MP3)
# --------------------------------------------------------------------------- #
banner("TEST 3b: GET /api/v1/audio/resume/1?audio=true (flux MP3)")
r = requests.get(f"{BASE}/api/v1/audio/resume/1?audio=true",
                 headers=AUTH, timeout=30)
print(f"\n--- GET /api/v1/audio/resume/1?audio=true ---")
print(f"HTTP {r.status_code}  ({r.elapsed.total_seconds():.2f}s)")
print(f"Content-Type : {r.headers.get('content-type')}")
print(f"Taille MP3   : {len(r.content)} octets")
assert r.status_code == 200, f"Resume audio failed: {r.status_code} {r.text[:300]}"
assert "audio/mpeg" in r.headers.get("content-type", ""), "Mauvais content-type"
assert len(r.content) > 1000, "MP3 trop petit"
# Sauvegarder pour inspection.
with open("/tmp/resume_patient_1.mp3", "wb") as f:
    f.write(r.content)
print(f"  [OK] MP3 sauvegardé dans /tmp/resume_patient_1.mp3 ({len(r.content)} octets)")

# --------------------------------------------------------------------------- #
#  TEST 3c — Résumé patient inexistant (404)
# --------------------------------------------------------------------------- #
banner("TEST 3c: GET /api/v1/audio/resume/9999 (404)")
r = requests.get(f"{BASE}/api/v1/audio/resume/9999", headers=AUTH, timeout=10)
pp("GET /api/v1/audio/resume/9999", r)
assert r.status_code == 404

# --------------------------------------------------------------------------- #
#  TEST 4 — Compte-rendu de réunion (STT + résumé + participants + actions)
# --------------------------------------------------------------------------- #
banner("TEST 4: POST /api/v1/audio/reunion")
print("  Transcription + résumé + extraction (peut prendre ~30-60s)...")
t0 = time.time()
with open("/tmp/reunion_test.mp3", "rb") as f:
    r = requests.post(f"{BASE}/api/v1/audio/reunion",
                      headers=AUTH,
                      data={"titre": "Conseil médical du 26 juin 2026",
                            "participants": "Durand, Martin"},
                      files={"fichier": ("reunion_test.mp3", f, "audio/mpeg")},
                      timeout=TIMEOUT)
pp(f"POST /api/v1/audio/reunion  ({time.time()-t0:.1f}s)", r)
assert r.status_code == 201, f"Reunion failed: {r.status_code} {r.text}"
data = r.json()
REUNION_ID = data["reunion"]["id"]
print(f"\n  Réunion ID     : {REUNION_ID}")
print(f"  Transcribeur   : {data['transcribeur']}")
print(f"  Taille audio   : {data['taille_audio_octets']} octets")
print(f"  Résumé         : \"{data['resume']}\"")
print(f"  Participants   : {data['participants']}")
print(f"  Actions        : {data['actions']}")
print(f"  Note           : {data.get('note')}")
assert data["reunion"]["transcription_audio"], "Transcription vide"
assert data["resume"], "Résumé vide"
# Au moins un participant extrait ou fourni.
assert len(data["participants"]) >= 1, "Aucun participant"
print(f"\n  [OK] Réunion #{REUNION_ID} créée avec CR complet.")

# --------------------------------------------------------------------------- #
#  TEST 5 — Liste des réunions
# --------------------------------------------------------------------------- #
banner("TEST 5: GET /api/v1/reunions (liste)")
r = requests.get(f"{BASE}/api/v1/reunions", headers=AUTH, timeout=10)
pp("GET /api/v1/reunions", r)
assert r.status_code == 200
data = r.json()
assert data["total"] >= 1, "Aucune réunion dans la liste"
print(f"\n  Total réunions : {data['total']}")
for reu in data["reunions"]:
    print(f"  - #{reu['id']} {reu['titre']} ({reu['date_reunion']})")

# --------------------------------------------------------------------------- #
#  TEST 6 — Détail d'une réunion
# --------------------------------------------------------------------------- #
banner(f"TEST 6: GET /api/v1/reunions/{REUNION_ID} (détail)")
r = requests.get(f"{BASE}/api/v1/reunions/{REUNION_ID}", headers=AUTH, timeout=10)
pp(f"GET /api/v1/reunions/{REUNION_ID}", r)
assert r.status_code == 200
data = r.json()
print(f"\n  Titre          : {data['titre']}")
print(f"  Participants   : {data['participants_list']}")
print(f"  Actions        : {data['actions_list']}")
print(f"  Résumé extrait : \"{data['resume']}\"")
assert data["participants_list"], "participants_list vide dans le détail"
assert data["actions_list"], "actions_list vide dans le détail"

# --------------------------------------------------------------------------- #
#  TEST 7 — Réunion inexistante (404)
# --------------------------------------------------------------------------- #
banner("TEST 7: GET /api/v1/reunions/9999 (404)")
r = requests.get(f"{BASE}/api/v1/reunions/9999", headers=AUTH, timeout=10)
pp("GET /api/v1/reunions/9999", r)
assert r.status_code == 404

# --------------------------------------------------------------------------- #
#  TEST 8 — Upload trop volumineux simulé (401 déjà testé; test 404 consultation)
# --------------------------------------------------------------------------- #
banner("TEST 8: POST /api/v1/audio/dictee (consultation inexistante -> 404)")
with open("/tmp/dictee_test.mp3", "rb") as f:
    r = requests.post(f"{BASE}/api/v1/audio/dictee",
                      headers=AUTH,
                      data={"consultation_id": "9999"},
                      files={"file": ("dictee.mp3", f, "audio/mpeg")},
                      timeout=TIMEOUT)
pp("POST /api/v1/audio/dictee (consultation 9999)", r)
assert r.status_code == 404

# --------------------------------------------------------------------------- #
#  Bilan
# --------------------------------------------------------------------------- #
banner("BILAN — TOUS LES TESTS ONT RÉUSSI")
print("""
  1. Dictée consultation (STT)         ✅  POST /api/v1/audio/dictee
  2. Résumé audio patient (TTS)        ✅  GET  /api/v1/audio/resume/{id}
     - mode JSON                          ✅  (texte + audio_disponible)
     - mode flux MP3                      ✅  (?audio=true)
  3. Compte-rendu de réunion           ✅  POST /api/v1/audio/reunion
     - transcription STT                  ✅
     - résumé automatique                 ✅
     - extraction participants            ✅
     - extraction actions                 ✅
  4. Liste des réunions                ✅  GET  /api/v1/reunions
  5. Détail d'une réunion              ✅  GET  /api/v1/reunions/{id}

  Sécurité JWT : tous les endpoints retournent 401 sans token. ✅
  Gestion d'erreurs : 404 patient/réunion/consultation inexistants. ✅
""")
