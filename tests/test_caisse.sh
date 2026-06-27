#!/bin/bash
# Test complet du module Caisse traçable (Défi 3)
set -e
BASE="http://localhost:8000/api/v1"
echo "=========================================="
echo "  TESTS MODULE CAISSE TRAÇABLE (Défi 3)"
echo "=========================================="

echo ""
echo "[0] Préparation : créer un patient + un paiement de référence"
PATIENT=$(curl -s -X POST "$BASE/patients" \
  -H "Content-Type: application/json" \
  -d '{"nom":"Test","prenom":"Caisse","matricule":"CAISSE-TEST-001","status":"fonctionnaire"}')
PATIENT_ID=$(echo "$PATIENT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "  → Patient #$PATIENT_ID créé"

PAIEMENT=$(curl -s -X POST "$BASE/paiements" \
  -H "Content-Type: application/json" \
  -d "{\"patient_id\":$PATIENT_ID,\"montant\":15000,\"mode\":\"especes\",\"statut\":\"effectue\"}")
PAIEMENT_ID=$(echo "$PAIEMENT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "  → Paiement #$PAIEMENT_ID créé (15000 FCFA)"

echo ""
echo "[1] POST /caisse/journal — OUVERTURE de caisse (fond 50000 FCFA)"
OUVERTURE=$(curl -s -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d '{"type_operation":"ouverture","montant_ouverture":50000,"operateur":"M. Diallo","notes":"Ouverture séance du jour"}')
OUVERTURE_ID=$(echo "$OUVERTURE" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "$OUVERTURE" | python3 -m json.tool
echo "  ✓ hash_courant non vide: $(echo "$OUVERTURE" | python3 -c "import sys,json; h=json.load(sys.stdin)['hash_courant']; print('OUI' if h and h!='pending' else 'NON')")"
echo "  ✓ hash_precedent = genesis: $(echo "$OUVERTURE" | python3 -c "import sys,json; h=json.load(sys.stdin)['hash_precedent']; print('OUI' if h=='0'*64 else 'NON('+str(h)+')')")"

echo ""
echo "[2] POST /caisse/journal — ENCAISSEMENT (lien paiement, 15000 FCFA)"
ENC=$(curl -s -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d "{\"type_operation\":\"encaissement\",\"montant\":15000,\"patient_id\":$PATIENT_ID,\"paiement_id\":$PAIEMENT_ID,\"operateur\":\"M. Diallo\"}")
ENC_ID=$(echo "$ENC" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "$ENC" | python3 -m json.tool
echo "  ✓ operations cumul = 1: $(echo "$ENC" | python3 -c "import sys,json; print(json.load(sys.stdin)['operations'])")"

echo ""
echo "[3] POST /caisse/journal — ENCAISSEMENT simple (5000 FCFA)"
ENC2=$(curl -s -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d '{"type_operation":"encaissement","montant":5000,"operateur":"M. Diallo"}')
ENC2_ID=$(echo "$ENC2" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "  → Opération #$(echo "$ENC2" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])"), operations cumul=$(echo "$ENC2" | python3 -c "import sys,json; print(json.load(sys.stdin)['operations'])")"

echo ""
echo "[4] CAS PARTICULIER — Remboursement pour PANNE (2000 FCFA)"
PANNE=$(curl -s -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d '{"type_operation":"remboursement","montant":2000,"cas_particulier":"remboursement_panne","motif":"Panne échographe - examen annulé","patient_id":'$PATIENT_ID',"operateur":"M. Diallo"}')
echo "$PANNE" | python3 -m json.tool
echo "  ✓ cas_particulier: $(echo "$PANNE" | python3 -c "import sys,json; print(json.load(sys.stdin)['cas_particulier'])")"

echo ""
echo "[5] CAS PARTICULIER — Remboursement pour panne SANS motif (doit échouer 422)"
echo "  → Code HTTP attendu 422 :"
curl -s -o /dev/null -w "    HTTP %{http_code}\n" -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d '{"type_operation":"remboursement","montant":1000,"cas_particulier":"remboursement_panne"}'

echo ""
echo "[6] CAS PARTICULIER — RENONCIATION après paiement"
RENON=$(curl -s -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d "{\"type_operation\":\"renonciation\",\"montant\":0,\"cas_particulier\":\"renonciation_apres_paiement\",\"patient_id\":$PATIENT_ID,\"paiement_id\":$PAIEMENT_ID,\"motif\":\"Patient renonce après paiement\",\"operateur\":\"M. Diallo\"}")
echo "$RENON" | python3 -m json.tool

echo ""
echo "[7] CAS PARTICULIER — Régularisation PAIEMENT DIFFÉRÉ"
DIFF=$(curl -s -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d '{"type_operation":"regularisation_differe","montant":8000,"cas_particulier":"paiement_differe","motif":"Régularisation paiement différé #42","operateur":"M. Diallo"}')
echo "$DIFF" | python3 -m json.tool

echo ""
echo "[8] POST /caisse/journal — CLÔTURE (montant compté = 76000, théorique = 50000+15000+5000-2000+8000 = 76000)"
CLOTURE=$(curl -s -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d '{"type_operation":"cloture","montant_cloture":76000,"operateur":"M. Diallo","notes":"Clôture sans écart"}')
echo "$CLOTURE" | python3 -m json.tool
echo "  ✓ montant_theorique: $(echo "$CLOTURE" | python3 -c "import sys,json; print(json.load(sys.stdin)['montant_theorique'])")"
echo "  ✓ ecarts: $(echo "$CLOTURE" | python3 -c "import sys,json; print(json.load(sys.stdin)['ecarts'])") (attendu 0.0)"

echo ""
echo "[9] POST /caisse/journal — CLÔTURE avec écart (compté = 75950 → écart -50)"
CLOTURE2=$(curl -s -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d '{"type_operation":"ouverture","montant_ouverture":10000,"operateur":"M. Diallo"}')
# réouverture puis clôture avec écart
curl -s -X POST "$BASE/caisse/journal" -H "Content-Type: application/json" \
  -d '{"type_operation":"encaissement","montant":3000,"operateur":"M. Diallo"}' > /dev/null
CLOTURE_E=$(curl -s -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d '{"type_operation":"cloture","montant_cloture":12950,"operateur":"M. Diallo","notes":"Écart de -50 FCFA"}')
echo "$CLOTURE_E" | python3 -m json.tool
echo "  ✓ ecarts: $(echo "$CLOTURE_E" | python3 -c "import sys,json; print(json.load(sys.stdin)['ecarts'])") (attendu -50.0)"

echo ""
echo "[10] GET /caisse/journal — historique paginé"
curl -s "$BASE/caisse/journal?limit=5" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f'  total={d[\"total\"]}, skip={d[\"skip\"]}, limit={d[\"limit\"]}')
for o in d['operations']:
    print(f'  #{o[\"id\"]:>2} {o[\"type_operation\"]:>22} montant={o[\"montant\"]:>8} ops={o[\"operations\"]} hash={o[\"hash_courant\"][:16]}…')
"

echo ""
echo "[11] GET /caisse/journal — filtre par type_operation=ouverture"
curl -s "$BASE/caisse/journal?type_operation=ouverture" | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f'  total ouvertures = {d[\"total\"]}')
"

echo ""
echo "[12] GET /caisse/journal/du-jour — journal du jour"
curl -s "$BASE/caisse/journal/du-jour" | python3 -m json.tool

echo ""
echo "[13] GET /caisse/journal/verifier — intégrité de la chaîne"
curl -s "$BASE/caisse/journal/verifier" | python3 -m json.tool

echo ""
echo "[14] GET /caisse/journal/{id} — détail d'une opération"
curl -s "$BASE/caisse/journal/$OUVERTURE_ID" | python3 -m json.tool

echo ""
echo "[15] GET /caisse/synthese — séance courante"
curl -s "$BASE/caisse/synthese" | python3 -m json.tool

echo ""
echo "[16] Erreur 404 — opération inexistante"
curl -s -o /dev/null -w "  HTTP %{http_code} (attendu 404)\n" "$BASE/caisse/journal/999999"

echo ""
echo "[17] Erreur 422 — ouverture sans montant_ouverture"
curl -s -o /dev/null -w "  HTTP %{http_code} (attendu 422)\n" -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d '{"type_operation":"ouverture"}'

echo ""
echo "[18] Erreur 422 — clôture sans montant_cloture"
curl -s -o /dev/null -w "  HTTP %{http_code} (attendu 422)\n" -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d '{"type_operation":"cloture"}'

echo ""
echo "[19] Erreur 422 — cas_particulier incohérent (renonciation avec encaissement)"
curl -s -o /dev/null -w "  HTTP %{http_code} (attendu 422)\n" -X POST "$BASE/caisse/journal" \
  -H "Content-Type: application/json" \
  -d '{"type_operation":"encaissement","montant":100,"cas_particulier":"renonciation_apres_paiement"}'

echo ""
echo "=========================================="
echo "  FIN DES TESTS — module Caisse traçable"
echo "=========================================="