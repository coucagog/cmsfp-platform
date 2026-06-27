# API endpoints - Gestion des Paiements
# Encaissement, remboursement et paiement différé.
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime

from app.core.database import get_db
from app.models.models import Patient, Consultation, Paiement
from app.api.auth import get_current_user, TokenData
from app.core.audit import audit_log
from app.schemas.schemas import (
    PaiementCreate,
    PaiementResponse,
    PaiementListResponse,
    RemboursementRequest,
    DiffererRequest,
    PaiementStatistiques,
    StatutPaiement,
    ModePaiement,
)

router = APIRouter(
    prefix="/api/v1/paiements",
    tags=["Paiements"],
    dependencies=[Depends(get_current_user)],
)


# --------------------------------------------------------------------------- #
#  Encaissement (création d'un paiement)
# --------------------------------------------------------------------------- #
@router.post("", response_model=PaiementResponse, status_code=201)
async def encaisser_paiement(
    payload: PaiementCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """
    Enregistre un encaissement. Si une consultation est liée, la remise appliquée
    est récupérée automatiquement depuis la consultation.
    """
    # Vérifier le patient
    row = await db.execute(select(Patient).where(Patient.id == payload.patient_id))
    patient = row.scalars().first()
    if not patient:
        raise HTTPException(404, f"Patient #{payload.patient_id} introuvable")

    remise_appliquee = 0

    # Lier à une consultation (optionnel)
    if payload.consultation_id is not None:
        crow = await db.execute(
            select(Consultation).where(Consultation.id == payload.consultation_id)
        )
        consultation = crow.scalars().first()
        if not consultation:
            raise HTTPException(404, f"Consultation #{payload.consultation_id} introuvable")
        if consultation.patient_id != payload.patient_id:
            raise HTTPException(
                422,
                f"La consultation #{payload.consultation_id} n'appartient pas au patient #{payload.patient_id}",
            )
        remise_appliquee = int(consultation.remise or 0)

    # Validation du statut
    if payload.statut == StatutPaiement.DIFFERE and payload.montant == 0:
        raise HTTPException(422, "Un paiement différé doit avoir un montant > 0")

    paiement = Paiement(
        patient_id=payload.patient_id,
        consultation_id=payload.consultation_id,
        montant=int(payload.montant),
        remise_appliquee=remise_appliquee,
        mode=payload.mode.value,
        statut=payload.statut.value,
        date_paiement=datetime.utcnow(),
    )
    db.add(paiement)
    await db.commit()
    await db.refresh(paiement)
    # A09 — audit log de l'encaissement.
    audit_log(
        "paiement.encaissement",
        user=current_user.username,
        resource_type="paiement",
        resource_id=paiement.id,
        detail=f"patient_id={payload.patient_id} montant={payload.montant} mode={payload.mode.value}",
    )
    return paiement


# --------------------------------------------------------------------------- #
#  Liste paginée
# --------------------------------------------------------------------------- #
@router.get("", response_model=PaiementListResponse)
async def lister_paiements(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    patient_id: Optional[int] = Query(None, description="Filtrer par patient"),
    statut: Optional[StatutPaiement] = Query(None, description="Filtrer par statut"),
    mode: Optional[ModePaiement] = Query(None, description="Filtrer par mode"),
    db: AsyncSession = Depends(get_db),
):
    """Liste paginée des paiements avec filtres optionnels."""
    query = select(Paiement)
    count_query = select(func.count(Paiement.id))

    if patient_id is not None:
        query = query.where(Paiement.patient_id == patient_id)
        count_query = count_query.where(Paiement.patient_id == patient_id)
    if statut is not None:
        query = query.where(Paiement.statut == statut.value)
        count_query = count_query.where(Paiement.statut == statut.value)
    if mode is not None:
        query = query.where(Paiement.mode == mode.value)
        count_query = count_query.where(Paiement.mode == mode.value)

    total = (await db.execute(count_query)).scalar_one()
    rows = await db.execute(
        query.order_by(Paiement.date_paiement.desc()).offset(skip).limit(limit)
    )
    paiements = rows.scalars().all()

    return PaiementListResponse(
        total=total, skip=skip, limit=limit,
        paiements=[PaiementResponse.model_validate(p) for p in paiements],
    )


# --------------------------------------------------------------------------- #
#  Détail
# --------------------------------------------------------------------------- #
@router.get("/{paiement_id}", response_model=PaiementResponse)
async def obtenir_paiement(paiement_id: int, db: AsyncSession = Depends(get_db)):
    """Récupère un paiement par son identifiant."""
    row = await db.execute(select(Paiement).where(Paiement.id == paiement_id))
    paiement = row.scalars().first()
    if not paiement:
        raise HTTPException(404, f"Paiement #{paiement_id} introuvable")
    return paiement


# --------------------------------------------------------------------------- #
#  Remboursement
# --------------------------------------------------------------------------- #
@router.post("/{paiement_id}/rembourser", response_model=PaiementResponse)
async def rembourser_paiement(
    paiement_id: int, payload: RemboursementRequest, db: AsyncSession = Depends(get_db)
):
    """
    Marque un paiement comme remboursé. Un motif est obligatoire.
    Seuls les paiements au statut 'effectue' ou 'differe' peuvent être remboursés.
    """
    row = await db.execute(select(Paiement).where(Paiement.id == paiement_id))
    paiement = row.scalars().first()
    if not paiement:
        raise HTTPException(404, f"Paiement #{paiement_id} introuvable")

    if paiement.statut == StatutPaiement.REMBOURSE.value:
        raise HTTPException(409, "Ce paiement est déjà remboursé")
    if paiement.statut != StatutPaiement.EFFECTUE.value and paiement.statut != StatutPaiement.DIFFERE.value:
        raise HTTPException(422, f"Impossible de rembourser un paiement au statut '{paiement.statut}'")

    paiement.statut = StatutPaiement.REMBOURSE.value
    paiement.motif_remboursement = payload.motif
    await db.commit()
    await db.refresh(paiement)
    return paiement


# --------------------------------------------------------------------------- #
#  Paiement différé (passage en différé ou réglement d'un différé)
# --------------------------------------------------------------------------- #
@router.post("/{paiement_id}/differer", response_model=PaiementResponse)
async def differer_paiement(
    paiement_id: int, payload: DiffererRequest, db: AsyncSession = Depends(get_db)
):
    """
    Diffère un paiement (reporte l'encaissement).
    Seuls les paiements au statut 'effectue' peuvent être différés.
    """
    row = await db.execute(select(Paiement).where(Paiement.id == paiement_id))
    paiement = row.scalars().first()
    if not paiement:
        raise HTTPException(404, f"Paiement #{paiement_id} introuvable")

    if paiement.statut == StatutPaiement.DIFFERE.value:
        raise HTTPException(409, "Ce paiement est déjà différé")
    if paiement.statut == StatutPaiement.REMBOURSE.value:
        raise HTTPException(422, "Impossible de différer un paiement remboursé")

    paiement.statut = StatutPaiement.DIFFERE.value
    if payload.motif:
        paiement.motif_remboursement = payload.motif
    await db.commit()
    await db.refresh(paiement)
    return paiement


@router.post("/{paiement_id}/regulariser", response_model=PaiementResponse)
async def regulariser_paiement_differe(
    paiement_id: int, db: AsyncSession = Depends(get_db)
):
    """
    Régularise un paiement différé (le repasse au statut 'effectue').
    Sert à enregistrer l'encaissement effectif d'un paiement qui était différé.
    """
    row = await db.execute(select(Paiement).where(Paiement.id == paiement_id))
    paiement = row.scalars().first()
    if not paiement:
        raise HTTPException(404, f"Paiement #{paiement_id} introuvable")

    if paiement.statut != StatutPaiement.DIFFERE.value:
        raise HTTPException(422, f"Le paiement #{paiement_id} n'est pas différé (statut: '{paiement.statut}')")

    paiement.statut = StatutPaiement.EFFECTUE.value
    paiement.date_paiement = datetime.utcnow()
    await db.commit()
    await db.refresh(paiement)
    return paiement


# --------------------------------------------------------------------------- #
#  Statistiques
# --------------------------------------------------------------------------- #
@router.get("/statistiques/summary", response_model=PaiementStatistiques)
async def statistiques_paiements(db: AsyncSession = Depends(get_db)):
    """
    Statistiques agrégées: montants encaissés / remboursés / différés
    et décomptes par statut.
    """
    # Montants par statut
    montant_q = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (Paiement.statut == StatutPaiement.EFFECTUE.value, Paiement.montant),
                        else_=0,
                    )
                ),
                0,
            ).label("total_encaisse"),
            func.coalesce(
                func.sum(
                    case(
                        (Paiement.statut == StatutPaiement.REMBOURSE.value, Paiement.montant),
                        else_=0,
                    )
                ),
                0,
            ).label("total_rembourse"),
            func.coalesce(
                func.sum(
                    case(
                        (Paiement.statut == StatutPaiement.DIFFERE.value, Paiement.montant),
                        else_=0,
                    )
                ),
                0,
            ).label("total_differe"),
        )
    )
    montants = montant_q.one()

    # Décomptes par statut
    count_q = await db.execute(
        select(Paiement.statut, func.count(Paiement.id)).group_by(Paiement.statut)
    )
    counts = {statut: cnt for statut, cnt in count_q.all()}

    nombre_total = sum(counts.values())

    return PaiementStatistiques(
        total_encaisse=float(montants.total_encaisse or 0),
        total_rembourse=float(montants.total_rembourse or 0),
        total_differe=float(montants.total_differe or 0),
        nombre_effectues=counts.get(StatutPaiement.EFFECTUE.value, 0),
        nombre_rembourses=counts.get(StatutPaiement.REMBOURSE.value, 0),
        nombre_differes=counts.get(StatutPaiement.DIFFERE.value, 0),
        nombre_total=nombre_total,
    )


# --------------------------------------------------------------------------- #
#  Mise à jour
# --------------------------------------------------------------------------- #
@router.put("/{paiement_id}", response_model=PaiementResponse)
async def modifier_paiement(
    paiement_id: int,
    montant: Optional[int] = Query(None, ge=0, description="Nouveau montant"),
    mode: Optional[ModePaiement] = Query(None, description="Nouveau mode"),
    db: AsyncSession = Depends(get_db),
):
    """Met à jour le montant ou le mode d'un paiement (hors changement de statut)."""
    row = await db.execute(select(Paiement).where(Paiement.id == paiement_id))
    paiement = row.scalars().first()
    if not paiement:
        raise HTTPException(404, f"Paiement #{paiement_id} introuvable")

    if montant is not None:
        paiement.montant = int(montant)
    if mode is not None:
        paiement.mode = mode.value

    await db.commit()
    await db.refresh(paiement)
    return paiement


# --------------------------------------------------------------------------- #
#  Suppression
# --------------------------------------------------------------------------- #
@router.delete("/{paiement_id}", status_code=204)
async def supprimer_paiement(paiement_id: int, db: AsyncSession = Depends(get_db)):
    """Supprime un paiement."""
    row = await db.execute(select(Paiement).where(Paiement.id == paiement_id))
    paiement = row.scalars().first()
    if not paiement:
        raise HTTPException(404, f"Paiement #{paiement_id} introuvable")

    await db.delete(paiement)
    await db.commit()
    return None
