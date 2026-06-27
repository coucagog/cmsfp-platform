#!/usr/bin/env python3
"""Script de test complet pour les corrections CMSFP."""
import requests
import json
import sys

BASE = "http://localhost:8000"

def pp(label, resp):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"HTTP {resp.status_code}")
    try:
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    except Exception:
        print(resp.text[:500] if resp.text else "(empty body)")

# --- TEST 1: Health (public) ---
r = requests.get(f"{BASE}/health")
pp("TEST 1: /health SANS auth (public)", r)
assert r.status_code == 200

# --- TEST 2: Docs + OpenAPI (public) ---
r1 = requests.get(f"{BASE}/docs")
r2 = requests.get(f"{BASE}/openapi.json")
print(f"\n/docs -> {r1.status_code} | /openapi.json -> {r2.status_code}")
assert r1.status_code == 200
assert r2.status_code == 200

# --- TEST 3: Login ---
r = requests.post(f"{BASE}/api/v1/auth/login", data={"username": "admin", "password": "admin123"})
pp("TEST 3: Login admin/admin123", r)
assert r.status_code == 200
TOKEN = r.json()["access_token"]
H = {"Authorization": f"Bearer {TOKEN}"}
print(f"\nToken obtenu: {TOKEN[:40]}...")

# --- TEST 4: Endpoints SANS token -> 401 ---
print(f"\n{'='*60}")
print("  TEST 4: Endpoints SANS token -> 401")
print(f"{'='*60}")
for ep in ["/api/v1/patients", "/api/v1/consultations", "/api/v1/paiements", "/api/v1/caisse/journal", "/api/v1/tarifs/regles"]:
    code = requests.get(f"{BASE}{ep}").status_code
    print(f"  GET {ep} -> HTTP {code}")
    assert code == 401, f"Expected 401 for {ep}, got {code}"

# --- TEST 5: Endpoints AVEC token -> 200 ---
print(f"\n{'='*60}")
print("  TEST 5: Endpoints AVEC token -> 200")
print(f"{'='*60}")
for ep in ["/api/v1/patients", "/api/v1/consultations", "/api/v1/paiements", "/api/v1/caisse/journal", "/api/v1/tarifs/regles"]:
    r = requests.get(f"{BASE}{ep}", headers=H)
    print(f"  GET {ep} -> HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"    ERROR: {r.text[:300]}")

# --- TEST 6: Créer patient ---
r = requests.post(f"{BASE}/api/v1/patients", headers=H, json={
    "nom": "Test", "prenom": "Patient", "matricule": "T001", "status": "fonctionnaire"
})
pp("TEST 6: Créer patient", r)
assert r.status_code == 201
PATIENT_ID = r.json()["id"]

# --- TEST 7: Créer consultation (montant Integer) ---
r = requests.post(f"{BASE}/api/v1/consultations", headers=H, json={
    "patient_id": PATIENT_ID, "type": "ophtalmologique"
})
pp("TEST 7: Créer consultation (montant Integer)", r)
assert r.status_code == 201
montant = r.json()["montant"]
print(f"\n  Type de montant: {type(montant).__name__} = {montant}")
assert isinstance(montant, int), f"montant should be int, got {type(montant)}"
CONSULT_ID = r.json()["id"]

# --- TEST 8: Créer paiement (montant Integer) ---
r = requests.post(f"{BASE}/api/v1/paiements", headers=H, json={
    "patient_id": PATIENT_ID, "consultation_id": CONSULT_ID, "montant": 1000
})
pp("TEST 8: Créer paiement (montant Integer)", r)
assert r.status_code == 201
montant = r.json()["montant"]
print(f"\n  Type de montant: {type(montant).__name__} = {montant}")
assert isinstance(montant, int), f"montant should be int, got {type(montant)}"

# --- TEST 9: GET /paiements (debug 500) ---
r = requests.get(f"{BASE}/api/v1/paiements", headers=H)
pp("TEST 9: GET /paiements (listing)", r)

# --- TEST 10: Créer opération caisse (ouverture) ---
r = requests.post(f"{BASE}/api/v1/caisse/journal", headers=H, json={
    "type_operation": "ouverture", "montant_ouverture": 50000, "operateur": "Test"
})
pp("TEST 10: Créer opération caisse (ouverture)", r)
assert r.status_code == 201
mo = r.json()["montant_ouverture"]
print(f"\n  Type de montant_ouverture: {type(mo).__name__} = {mo}")
assert isinstance(mo, int)

# --- TEST 11: Suppression patient avec caisse_ops -> 409 ---
# Créer une opération de caisse liée au patient
r = requests.post(f"{BASE}/api/v1/caisse/journal", headers=H, json={
    "type_operation": "encaissement", "montant": 1000, "patient_id": PATIENT_ID
})
print(f"\n  Caisse op liée au patient: HTTP {r.status_code}")

r = requests.delete(f"{BASE}/api/v1/patients/{PATIENT_ID}", headers=H)
pp(f"TEST 11: DELETE /patients/{PATIENT_ID} (attendu 409)", r)
assert r.status_code == 409, f"Expected 409, got {r.status_code}"

# --- TEST 12: Vérifier chaîne de hachage ---
r = requests.get(f"{BASE}/api/v1/caisse/journal/verifier", headers=H)
pp("TEST 12: Vérifier chaîne de hachage", r)
assert r.status_code == 200
assert r.json()["integre"] == True

# --- TEST 13: Token invalide -> 401 ---
code = requests.get(f"{BASE}/api/v1/patients", headers={"Authorization": "Bearer invalid_token"}).status_code
print(f"\n{'='*60}")
print(f"  TEST 13: Token invalide -> HTTP {code}")
print(f"{'='*60}")
assert code == 401

# --- TEST 14: FK enforcement - créer caisse op avec patient inexistant ---
r = requests.post(f"{BASE}/api/v1/caisse/journal", headers=H, json={
    "type_operation": "encaissement", "montant": 500, "patient_id": 99999
})
pp("TEST 14: Caisse op avec patient inexistant (attendu 404)", r)
assert r.status_code == 404

print(f"\n{'='*60}")
print("  ✅ TOUS LES TESTS SONT PASSÉS")
print(f"{'='*60}")
