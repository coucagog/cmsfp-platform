#!/usr/bin/env python3
"""Tests supplémentaires: caisse_operations check + FK enforcement DB-level."""
import requests
import json
import sqlite3

BASE = "http://localhost:8000"

# Login
r = requests.post(f"{BASE}/api/v1/auth/login", data={"username": "admin", "password": "admin123"})
TOKEN = r.json()["access_token"]
H = {"Authorization": f"Bearer {TOKEN}"}

# --- TEST A: Patient avec UNIQUEMENT caisse_operations ---
print("=" * 60)
print("  TEST A: Suppression patient avec caisse_ops seulement")
print("=" * 60)

# Créer un patient sans consultation ni paiement
r = requests.post(f"{BASE}/api/v1/patients", headers=H, json={
    "nom": "CaisseOnly", "prenom": "Test", "matricule": "C001", "status": "non_ayant_droit"
})
pid = r.json()["id"]
print(f"Patient créé: ID={pid}")

# Créer une opération de caisse liée (encaissement)
r = requests.post(f"{BASE}/api/v1/caisse/journal", headers=H, json={
    "type_operation": "encaissement", "montant": 2000, "patient_id": pid
})
print(f"Caisse op créée: HTTP {r.status_code}")

# Tenter de supprimer le patient -> doit échouer avec 409 (caisse_operations)
r = requests.delete(f"{BASE}/api/v1/patients/{pid}", headers=H)
print(f"DELETE patient -> HTTP {r.status_code}")
print(f"Response: {r.text}")
assert r.status_code == 409, f"Expected 409, got {r.status_code}"
assert "caisse" in r.text.lower(), "Should mention caisse operations"
print("✅ PASS: Suppression bloquée par caisse_operations")

# --- TEST B: FK enforcement au niveau base de données ---
print("\n" + "=" * 60)
print("  TEST B: FK enforcement au niveau DB (PRAGMA foreign_keys=ON)")
print("=" * 60)

conn = sqlite3.connect("cmsfp.db")
cur = conn.cursor()

# Activer FK pour cette connexion (simule le comportement de SQLAlchemy)
cur.execute("PRAGMA foreign_keys=ON")
cur.execute("PRAGMA foreign_keys")
fk_status = cur.fetchone()[0]
print(f"PRAGMA foreign_keys = {fk_status}")
assert fk_status == 1, "Foreign keys should be ON"

# Tenter d'insérer une caisse_operation avec patient_id inexistant
try:
    cur.execute(
        "INSERT INTO caisse_operations (type_operation, montant, hash_courant, horodatage, patient_id) "
        "VALUES ('encaissement', 500, 'test_fk_hash', '2026-01-01 00:00:00', 99999)"
    )
    print("❌ FAIL: Insert avec FK invalide a réussi (ne devrait pas)")
    conn.rollback()
except sqlite3.IntegrityError as e:
    print(f"✅ PASS: IntegrityError levée: {e}")
    conn.rollback()

# Tenter d'insérer une consultation avec patient_id inexistant
try:
    cur.execute(
        "INSERT INTO consultations (patient_id, type, montant, remise, gratuit) "
        "VALUES (99999, 'generale', 0, 0, 1)"
    )
    print("❌ FAIL: Insert consultation avec FK invalide a réussi")
    conn.rollback()
except sqlite3.IntegrityError as e:
    print(f"✅ PASS: IntegrityError levée: {e}")
    conn.rollback()

conn.close()

# --- TEST C: Token expiré/invalide format ---
print("\n" + "=" * 60)
print("  TEST C: Différents scénarios de token")
print("=" * 60)

# Pas de header Authorization
code = requests.get(f"{BASE}/api/v1/patients").status_code
print(f"Pas de header Authorization -> {code}")
assert code == 401

# Token malformé
code = requests.get(f"{BASE}/api/v1/patients", headers={"Authorization": "Bearer not.a.jwt"}).status_code
print(f"Token malformé -> {code}")
assert code == 401

# Token vide
code = requests.get(f"{BASE}/api/v1/patients", headers={"Authorization": "Bearer "}).status_code
print(f"Token vide -> {code}")
assert code == 401

# Mauvais schéma
code = requests.get(f"{BASE}/api/v1/patients", headers={"Authorization": "Basic abc"}).status_code
print(f"Mauvais schéma (Basic) -> {code}")
assert code == 401

print("✅ PASS: Tous les scénarios de token")

print("\n" + "=" * 60)
print("  ✅ TOUS LES TESTS SUPPLÉMENTAIRES SONT PASSÉS")
print("=" * 60)
