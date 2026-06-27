#!/bin/bash
# Script de test complet pour les corrections CMSFP
set -e
BASE="http://localhost:8000"
AUTH="-H Content-Type:application/x-www-form-urlencoded"

echo "============================================"
echo "  TEST 1: /health SANS auth (public)"
echo "============================================"
curl -s -w " [HTTP %{http_code}]\n" "$BASE/health"

echo ""
echo "============================================"
echo "  TEST 2: /docs + /openapi.json SANS auth"
echo "============================================"
curl -s -o /dev/null -w "/docs -> HTTP %{http_code}\n" "$BASE/docs"
curl -s -o /dev/null -w "/openapi.json -> HTTP %{http_code}\n" "$BASE/openapi.json"

echo ""
echo "============================================"
echo "  TEST 3: Login admin/admin123"
echo "============================================"
TOKEN=$(curl -s -X POST "$BASE/api/v1/auth/login" \
  -d "username=admin&password=admin123" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "Token: ${TOKEN:0:40}..."

echo ""
echo "============================================"
echo "  TEST 4: Endpoints SANS token -> 401"
echo "============================================"
for ep in "/api/v1/patients" "/api/v1/consultations" "/api/v1/paiements" "/api/v1/caisse/journal" "/api/v1/tarifs/regles"; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE${ep}")
  echo "  GET ${ep} -> HTTP ${CODE}"
done

echo ""
echo "============================================"
echo "  TEST 5: Endpoints AVEC token -> 200"
echo "============================================"
for ep in "/api/v1/patients" "/api/v1/consultations" "/api/v1/paiements" "/api/v1/caisse/journal" "/api/v1/tarifs/regles"; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE${ep}" -H "Authorization: Bearer $TOKEN")
  echo "  GET ${ep} -> HTTP ${CODE}"
done

echo ""
echo "============================================"
echo "  TEST 6: Créer patient (avec token)"
echo "============================================"
PATIENT_RESP=$(curl -s -X POST "$BASE/api/v1/patients" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"nom":"Test","prenom":"Patient","matricule":"T001","status":"fonctionnaire"}')
echo "$PATIENT_RESP" | python3 -m json.tool
PATIENT_ID=$(echo "$PATIENT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Patient ID: $PATIENT_ID"

echo ""
echo "============================================"
echo "  TEST 7: Créer consultation (montant Integer)"
echo "============================================"
CONSULT_RESP=$(curl -s -X POST "$BASE/api/v1/consultations" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"patient_id\":$PATIENT_ID,\"type\":\"ophtalmologique\"}")
echo "$CONSULT_RESP" | python3 -m json.tool
CONSULT_ID=$(echo "$CONSULT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

echo ""
echo "  Vérification: montant est bien un entier (pas un float)"
MONTANT=$(echo "$CONSULT_RESP" | python3 -c "import sys,json; m=json.load(sys.stdin)['montant']; print(type(m).__name__)")
echo "  Type de montant: $MONTANT"

echo ""
echo "============================================"
echo "  TEST 8: Créer paiement (montant Integer)"
echo "============================================"
PAIEMENT_RESP=$(curl -s -X POST "$BASE/api/v1/paiements" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"patient_id\":$PATIENT_ID,\"consultation_id\":$CONSULT_ID,\"montant\":1000}")
echo "$PAIEMENT_RESP" | python3 -m json.tool

echo ""
echo "  Vérification: montant est bien un entier"
PMONTANT=$(echo "$PAIEMENT_RESP" | python3 -c "import sys,json; m=json.load(sys.stdin)['montant']; print(type(m).__name__)")
echo "  Type de montant: $PMONTANT"

echo ""
echo "============================================"
echo "  TEST 9: GET /paiements (debug 500)"
echo "============================================"
PAIEMENTS_LIST=$(curl -s -w "\nHTTP_CODE:%{http_code}" "$BASE/api/v1/paiements" -H "Authorization: Bearer $TOKEN")
echo "$PAIEMENTS_LIST"

echo ""
echo "============================================"
echo "  TEST 10: Créer opération caisse (ouverture)"
echo "============================================"
CAISSE_RESP=$(curl -s -X POST "$BASE/api/v1/caisse/journal" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"type_operation":"ouverture","montant_ouverture":50000,"operateur":"Test"}')
echo "$CAISSE_RESP" | python3 -m json.tool

echo ""
echo "  Vérification: montant_ouverture est un entier"
OMONTANT=$(echo "$CAISSE_RESP" | python3 -c "import sys,json; m=json.load(sys.stdin)['montant_ouverture']; print(type(m).__name__)")
echo "  Type de montant_ouverture: $OMONTANT"

echo ""
echo "============================================"
echo "  TEST 11: Suppression patient avec caisse_ops"
echo "  (doit échouer -> 409)"
echo "============================================"
# D'abord, créer une opération de caisse liée au patient
curl -s -X POST "$BASE/api/v1/caisse/journal" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"type_operation\":\"encaissement\",\"montant\":1000,\"patient_id\":$PATIENT_ID}" > /dev/null

DELETE_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$BASE/api/v1/patients/$PATIENT_ID" \
  -H "Authorization: Bearer $TOKEN")
echo "  DELETE /patients/$PATIENT_ID -> HTTP $DELETE_CODE (attendu: 409)"

echo ""
echo "============================================"
echo "  TEST 12: Vérifier chaîne de hachage"
echo "============================================"
curl -s "$BASE/api/v1/caisse/journal/verifier" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

echo ""
echo "============================================"
echo "  TEST 13: Token invalide -> 401"
echo "============================================"
CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/v1/patients" -H "Authorization: Bearer invalidtoken123")
echo "  GET /patients avec token invalide -> HTTP $CODE"

echo ""
echo "============================================"
echo "  TOUS LES TESTS SONT TERMINÉS"
echo "============================================"
