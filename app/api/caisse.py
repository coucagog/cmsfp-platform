# API endpoints - Caisse traçable (Défi 3)
# Journal immuable des opérations de caisse (ouverture, encaissement,
# remboursement, clôture) sécurisé par une chaîne de hachage SHA-256.
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime, date, timedelta
import hashlib
import json

from app.core.database import get_db
from app.models.models import (
    CaisseOperation,
    TypeOperationCaisse,
    CasParticulierCaisse,
    Patient,
    Paiement,
)
from app.api.auth import get_current_user, TokenData
from app.core.audit import audit_log
from app.schemas.schemas import (
    CaisseOperationCreate,
    CaisseOperationResponse,
    CaisseJournalListResponse,
    CaisseJournalDuJourResponse,
    CaisseVerificationResponse,
)

router = APIRouter(
    prefix="/api/v1/caisse",
    tags=["Caisse traçable"],
    dependencies=[Depends(get_current_user)],
)

GENESIS_HASH = "0" * 64  # hash_precedent du tout premier enregistrement


# --------------------------------------------------------------------------- #
#  Utilitaires — chaîne de hachage
# --------------------------------------------------------------------------- #
def _calculer_hash(payload: dict) -> str:
    """
    Calcule le SHA-256 d'un payload canonique (clés triées, JSON compact).
    Sert à la fois pour hash_courant (insertion) et la vérification.
    """
    canonique = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonique.encode("utf-8")).hexdigest()


def _payload_hachage(op: CaisseOperation) -> dict:
    """Champs critiques qui entrent dans le calcul du hash."""
    return {
        "id": op.id,
        "type_operation": op.type_operation.value if op.type_operation else None,
        "montant": int(op.montant or 0),
        "montant_ouverture": op.montant_ouverture,
        "montant_theorique": op.montant_theorique,
        "montant_cloture": op.montant_cloture,
        "operations": op.operations,
        "ecarts": int(op.ecarts or 0),
        "patient_id": op.patient_id,
        "paiement_id": op.paiement_id,
        "cas_particulier": (
            op.cas_particulier.value if op.cas_particulier else None
        ),
        "motif": op.motif,
        "operateur": op.operateur,
        "horodatage": op.horodatage.isoformat() if op.horodatage else None,
        "hash_precedent": op.hash_precedent,
    }


async def _derniere_operation(db: AsyncSession) -> Optional[CaisseOperation]:
    """Récupère la dernière opération du journal (pour le chaînage)."""
    row = await db.execute(
        select(CaisseOperation).order_by(CaisseOperation.id.desc()).limit(1)
    )
    return row.scalars().first()


async def _derniere_ouverture(
    db: AsyncSession, avant_id: Optional[int] = None
) -> Optional[CaisseOperation]:
    """Récupère la dernière opération d'OUVERTURE (borne la séance courante)."""
    q = select(CaisseOperation).where(
        CaisseOperation.type_operation == TypeOperationCaisse.OUVERTURE
    )
    if avant_id is not None:
        q = q.where(CaisseOperation.id < avant_id)
    q = q.order_by(CaisseOperation.id.desc()).limit(1)
    row = await db.execute(q)
    return row.scalars().first()


async def _recalculer_contexte(db: AsyncSession) -> tuple:
    """
    Calcule le contexte courant de la caisse :
      - montant_ouverture (fond de caisse de la séance en cours)
      - operations_cumul (nombre d'opérations depuis la dernière ouverture)
      - montant_theorique (fond + encaissements - remboursements depuis l'ouverture)
    Retourne (montant_ouverture, operations_cumul, montant_theorique).
    """
    ouverture = await _derniere_ouverture(db)
    montant_ouverture = int(ouverture.montant_ouverture or 0) if ouverture else 0
    operations_cumul = 0
    montant_theorique = montant_ouverture

    if ouverture:
        # Toutes les opérations postérieures à l'ouverture
        q = select(CaisseOperation).where(
            CaisseOperation.id > ouverture.id,
            CaisseOperation.type_operation.in_(
                [
                    TypeOperationCaisse.ENCAISSEMENT,
                    TypeOperationCaisse.REMBOURSEMENT,
                    TypeOperationCaisse.RENONCIATION,
                    TypeOperationCaisse.REGULARISATION_DIFFERE,
                ]
            ),
        )
        rows = await db.execute(q.order_by(CaisseOperation.id.asc()))
        for op in rows.scalars().all():
            operations_cumul += 1
            if op.type_operation == TypeOperationCaisse.ENCAISSEMENT:
                montant_theorique += int(op.montant or 0)
            elif op.type_operation == TypeOperationCaisse.REMBOURSEMENT:
                montant_theorique -= int(op.montant or 0)
            elif op.type_operation == TypeOperationCaisse.REGULARISATION_DIFFERE:
                montant_theorique += int(op.montant or 0)
            # RENONCIATION : pas d'impact sur le cash (déjà encaissé, on note juste)

    return montant_ouverture, operations_cumul, montant_theorique


# --------------------------------------------------------------------------- #
#  POST /api/v1/caisse/journal — enregistrement immuable
# --------------------------------------------------------------------------- #
@router.post("/journal", response_model=CaisseOperationResponse, status_code=201)
async def enregistrer_operation(
    payload: CaisseOperationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """
    Enregistre une opération de caisse dans le journal immuable.

    L'opération est chaînée à la précédente via un hash SHA-256, ce qui rend
    toute modification ultérieure détectable (cf. GET /journal/verifier).

    Règles métier :
      - OUVERTURE : initialise le fond de caisse (montant_ouverture obligatoire).
        Une nouvelle ouverture clôture implicitement la séance précédente si
        elle ne l'a pas été.
      - ENCAISSEMENT / REMBOURSEMENT : tracer le flux de trésorerie.
      - CLOTURE : calcule automatiquement le montant_théorique et l'écart
        (montant_cloture - montant_theorique). montant_cloture obligatoire.
      - RENONCIATION : cas particulier (renonciation après paiement) —
        enregistre la renonciation du patient après encaissement.
      - REGULARISATION_DIFFERE : régularisation d'un paiement différé.

    Cas particuliers gérés :
      - renonciation_apres_paiement : type=RENONCIATION
      - remboursement_panne : type=REMBOURSEMENT avec motif
      - paiement_differe : type=REGULARISATION_DIFFERE
    """
    t = payload.type_operation

    # ---- Validations spécifiques au type ----
    if t == TypeOperationCaisse.OUVERTURE and payload.montant_ouverture is None:
        raise HTTPException(
            422, "Une ouverture de caisse nécessite 'montant_ouverture'"
        )
    if t == TypeOperationCaisse.CLOTURE and payload.montant_cloture is None:
        raise HTTPException(
            422, "Une clôture de caisse nécessite 'montant_cloture' (montant compté)"
        )

    # ---- Validations des entités liées ----
    if payload.patient_id is not None:
        prow = await db.execute(select(Patient).where(Patient.id == payload.patient_id))
        if not prow.scalars().first():
            raise HTTPException(404, f"Patient #{payload.patient_id} introuvable")
    if payload.paiement_id is not None:
        parow = await db.execute(select(Paiement).where(Paiement.id == payload.paiement_id))
        paiement = parow.scalars().first()
        if not paiement:
            raise HTTPException(404, f"Paiement #{payload.paiement_id} introuvable")

    # ---- Contexte de la séance courante ----
    montant_ouverture_ctx, operations_cumul, montant_theorique_ctx = (
        await _recalculer_contexte(db)
    )

    # Pour une OUVERTURE, le montant_ouverture vient du payload
    if t == TypeOperationCaisse.OUVERTURE:
        montant_ouverture_ctx = int(payload.montant_ouverture)
        operations_cumul = 0
        montant_theorique_ctx = montant_ouverture_ctx

    # Pour une CLÔTURE, on calcule l'écart
    ecart = 0
    montant_cloture_val = None
    montant_theorique_val = montant_theorique_ctx
    if t == TypeOperationCaisse.CLOTURE:
        montant_cloture_val = int(payload.montant_cloture)
        ecart = montant_cloture_val - montant_theorique_ctx
        # On n'incrémente pas le compteur pour la clôture elle-même
    else:
        operations_cumul += 1

    # ---- Récupération du hash précédent (chaînage) ----
    derniere = await _derniere_operation(db)
    hash_precedent = derniere.hash_courant if derniere else GENESIS_HASH

    # ---- Cas particuliers : cohérence type <-> cas_particulier ----
    cas = payload.cas_particulier
    if cas == CasParticulierCaisse.RENONCIATION_APRES_PAIEMENT and t not in (
        TypeOperationCaisse.RENONCIATION,
        TypeOperationCaisse.REMBOURSEMENT,
    ):
        raise HTTPException(
            422,
            "Le cas 'renonciation_apres_paiement' doit utiliser type_operation "
            "'renonciation' ou 'remboursement'",
        )
    if cas == CasParticulierCaisse.REMBOURSEMENT_PANNE and t != TypeOperationCaisse.REMBOURSEMENT:
        raise HTTPException(
            422,
            "Le cas 'remboursement_panne' doit utiliser type_operation 'remboursement'",
        )
    if cas == CasParticulierCaisse.PAIEMENT_DIFFERE and t not in (
        TypeOperationCaisse.REGULARISATION_DIFFERE,
        TypeOperationCaisse.ENCAISSEMENT,
    ):
        raise HTTPException(
            422,
            "Le cas 'paiement_differe' doit utiliser type_operation "
            "'regularisation_differe' ou 'encaissement'",
        )

    # Remboursement pour panne : motif obligatoire
    if cas == CasParticulierCaisse.REMBOURSEMENT_PANNE and not payload.motif:
        raise HTTPException(
            422, "Un remboursement pour panne nécessite un 'motif' descriptif"
        )

    # ---- Construction de l'opération (sans hash_courant pour l'instant) ----
    now = datetime.utcnow()
    operation = CaisseOperation(
        type_operation=t,
        montant=int(payload.montant or 0),
        montant_ouverture=montant_ouverture_ctx,
        montant_theorique=montant_theorique_val if t == TypeOperationCaisse.CLOTURE else None,
        montant_cloture=montant_cloture_val,
        operations=operations_cumul,
        ecarts=ecart,
        patient_id=payload.patient_id,
        paiement_id=payload.paiement_id,
        cas_particulier=cas,
        motif=payload.motif,
        operateur=payload.operateur,
        notes=payload.notes,
        hash_precedent=hash_precedent,
        hash_courant="pending",  # placeholder, recalculé après flush (id connu)
        horodatage=now,
    )
    db.add(operation)
    await db.flush()  # obtient operation.id sans commit

    # ---- Calcul du hash_courant avec l'ID définitif ----
    payload_hash = _payload_hachage(operation)
    operation.hash_courant = _calculer_hash(payload_hash)

    await db.commit()
    await db.refresh(operation)
    # A09 — audit log de l'opération de caisse (journal immuable).
    audit_log(
        "caisse.operation",
        user=current_user.username,
        resource_type="caisse_operation",
        resource_id=operation.id,
        detail=(
            f"type={t.value} montant={int(payload.montant or 0)} "
            f"operateur={payload.operateur or 'N/A'}"
        ),
    )
    return operation


# --------------------------------------------------------------------------- #
#  GET /api/v1/caisse/journal — historique paginé
# --------------------------------------------------------------------------- #
@router.get("/journal", response_model=CaisseJournalListResponse)
async def consulter_journal(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    type_operation: Optional[TypeOperationCaisse] = Query(
        None, description="Filtrer par type d'opération"
    ),
    patient_id: Optional[int] = Query(None, description="Filtrer par patient"),
    cas_particulier: Optional[CasParticulierCaisse] = Query(
        None, description="Filtrer par cas particulier"
    ),
    date_debut: Optional[datetime] = Query(None, description="Filtrer depuis (ISO)"),
    date_fin: Optional[datetime] = Query(None, description="Filtrer jusqu'à (ISO)"),
    db: AsyncSession = Depends(get_db),
):
    """Consulte l'historique complet du journal de caisse (filtres optionnels)."""
    query = select(CaisseOperation)
    count_query = select(func.count(CaisseOperation.id))

    if type_operation is not None:
        query = query.where(CaisseOperation.type_operation == type_operation)
        count_query = count_query.where(
            CaisseOperation.type_operation == type_operation
        )
    if patient_id is not None:
        query = query.where(CaisseOperation.patient_id == patient_id)
        count_query = count_query.where(CaisseOperation.patient_id == patient_id)
    if cas_particulier is not None:
        query = query.where(CaisseOperation.cas_particulier == cas_particulier)
        count_query = count_query.where(
            CaisseOperation.cas_particulier == cas_particulier
        )
    if date_debut is not None:
        query = query.where(CaisseOperation.horodatage >= date_debut)
        count_query = count_query.where(CaisseOperation.horodatage >= date_debut)
    if date_fin is not None:
        query = query.where(CaisseOperation.horodatage <= date_fin)
        count_query = count_query.where(CaisseOperation.horodatage <= date_fin)

    total = (await db.execute(count_query)).scalar_one()
    rows = await db.execute(
        query.order_by(CaisseOperation.id.asc()).offset(skip).limit(limit)
    )
    operations = rows.scalars().all()

    return CaisseJournalListResponse(
        total=total,
        skip=skip,
        limit=limit,
        operations=[CaisseOperationResponse.model_validate(o) for o in operations],
    )


# --------------------------------------------------------------------------- #
#  GET /api/v1/caisse/journal/du-jour — journal du jour
# --------------------------------------------------------------------------- #
@router.get("/journal/du-jour", response_model=CaisseJournalDuJourResponse)
async def journal_du_jour(db: AsyncSession = Depends(get_db)):
    """
    Retourne toutes les opérations de caisse enregistrées aujourd'hui,
    avec une synthèse : fond d'ouverture, montant théorique, montant compté,
    écart, total encaissé et total remboursé de la journée.
    """
    today = date.today()
    debut = datetime.combine(today, datetime.min.time())
    fin = datetime.combine(today, datetime.max.time())

    rows = await db.execute(
        select(CaisseOperation)
        .where(
            and_(CaisseOperation.horodatage >= debut, CaisseOperation.horodatage <= fin)
        )
        .order_by(CaisseOperation.id.asc())
    )
    operations = rows.scalars().all()

    montant_ouverture = None
    montant_cloture = None
    montant_theorique = None
    ecart = 0
    total_encaisse = 0
    total_rembourse = 0

    for op in operations:
        if op.type_operation == TypeOperationCaisse.OUVERTURE:
            montant_ouverture = int(op.montant_ouverture or 0)
        elif op.type_operation == TypeOperationCaisse.CLOTURE:
            montant_cloture = op.montant_cloture
            montant_theorique = op.montant_theorique
            ecart = int(op.ecarts or 0)
        elif op.type_operation == TypeOperationCaisse.ENCAISSEMENT:
            total_encaisse += int(op.montant or 0)
        elif op.type_operation == TypeOperationCaisse.REMBOURSEMENT:
            total_rembourse += int(op.montant or 0)
        elif op.type_operation == TypeOperationCaisse.REGULARISATION_DIFFERE:
            total_encaisse += int(op.montant or 0)

    return CaisseJournalDuJourResponse(
        date=today.isoformat(),
        total_operations=len(operations),
        montant_ouverture=montant_ouverture,
        montant_cloture=montant_cloture,
        montant_theorique=montant_theorique,
        ecarts=ecart,
        total_encaisse=total_encaisse,
        total_rembourse=total_rembourse,
        operations=[CaisseOperationResponse.model_validate(o) for o in operations],
    )


# --------------------------------------------------------------------------- #
#  GET /api/v1/caisse/journal/verifier — intégrité de la chaîne
# --------------------------------------------------------------------------- #
@router.get("/journal/verifier", response_model=CaisseVerificationResponse)
async def verifier_integrite_journal(db: AsyncSession = Depends(get_db)):
    """
    Vérifie l'intégrité de la chaîne de hachage du journal.
    Recalcule chaque hash_courant et s'assure que hash_precedent de l'opé N+1
    correspond bien au hash_courant de l'opé N. Toute anomalie est listée.
    """
    rows = await db.execute(select(CaisseOperation).order_by(CaisseOperation.id.asc()))
    operations = rows.scalars().all()

    anomalies: list[str] = []
    hash_attendu_precedent = GENESIS_HASH
    dernier_id = None

    for op in operations:
        dernier_id = op.id
        # Le hash_precedent doit correspondre au hash_courant de la précédente
        if op.hash_precedent != hash_attendu_precedent:
            anomalies.append(
                f"Opération #{op.id}: hash_precedent incorrect "
                f"(attendu {hash_attendu_precedent[:12]}…, trouvé {(op.hash_precedent or 'None')[:12]}…)"
            )
        # Recalcul du hash_courant
        payload = _payload_hachage(op)
        hash_recalcule = _calculer_hash(payload)
        if op.hash_courant != hash_recalcule:
            anomalies.append(
                f"Opération #{op.id}: hash_courant invalide "
                f"(attendu {hash_recalcule[:12]}…, trouvé {(op.hash_courant or 'None')[:12]}…)"
            )
        hash_attendu_precedent = op.hash_courant

    return CaisseVerificationResponse(
        integre=len(anomalies) == 0,
        total_verifiees=len(operations),
        premier_hash_precedent=operations[0].hash_precedent if operations else None,
        derniere_operation_id=dernier_id,
        anomalies=anomalies,
    )


# --------------------------------------------------------------------------- #
#  GET /api/v1/caisse/journal/{operation_id} — détail
# --------------------------------------------------------------------------- #
@router.get("/journal/{operation_id}", response_model=CaisseOperationResponse)
async def detail_operation(operation_id: int, db: AsyncSession = Depends(get_db)):
    """Récupère une opération de caisse par son identifiant."""
    row = await db.execute(
        select(CaisseOperation).where(CaisseOperation.id == operation_id)
    )
    op = row.scalars().first()
    if not op:
        raise HTTPException(404, f"Opération de caisse #{operation_id} introuvable")
    return op


# --------------------------------------------------------------------------- #
#  GET /api/v1/caisse/synthese — synthèse de la séance courante
# --------------------------------------------------------------------------- #
@router.get("/synthese")
async def synthese_caisse(db: AsyncSession = Depends(get_db)):
    """
    Synthèse en temps réel de la séance de caisse courante :
    fond d'ouverture, opérations cumulées, montant théorique attendu.
    """
    montant_ouverture, operations_cumul, montant_theorique = (
        await _recalculer_contexte(db)
    )
    ouverture = await _derniere_ouverture(db)
    cloture = None
    # Vérifier si la séance a été clôturée
    if ouverture:
        crow = await db.execute(
            select(CaisseOperation)
            .where(
                and_(
                    CaisseOperation.id > ouverture.id,
                    CaisseOperation.type_operation == TypeOperationCaisse.CLOTURE,
                )
            )
            .order_by(CaisseOperation.id.desc())
            .limit(1)
        )
        cloture = crow.scalars().first()

    return {
        "ouverte": cloture is None and ouverture is not None,
        "montant_ouverture": montant_ouverture,
        "operations_cumul": operations_cumul,
        "montant_theorique": montant_theorique,
        "ouverture_id": ouverture.id if ouverture else None,
        "cloture_id": cloture.id if cloture else None,
        "ecart_cloture": int(cloture.ecarts) if cloture else None,
    }
