# API endpoints - Gestion des Consultations
# Enregistre les consultations avec calcul automatique du tarif via le moteur tarifaire.
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.core.database import get_db
from app.models.models import Patient, Consultation, ConsultationType, PatientStatus
from app.services.tarif_engine import tarif_engine, StatutPatient, TypePrestation
from app.api.auth import get_current_user
from app.schemas.schemas import (
    ConsultationCreate,
    ConsultationUpdate,
    ConsultationResponse,
    ConsultationWithTarifResponse,
    ConsultationListResponse,
    TarifPreviewRequest,
)

router = APIRouter(
    prefix="/api/v1/consultations",
    tags=["Consultations"],
    dependencies=[Depends(get_current_user)],
)


# --------------------------------------------------------------------------- #
#  Mapping: ConsultationType (modèle)  →  TypePrestation (moteur tarifaire)
# --------------------------------------------------------------------------- #
_CONSULTATION_TO_PRESTATION: dict[ConsultationType, TypePrestation] = {
    ConsultationType.GENERALE: TypePrestation.CONSULTATION_GENERALE,
    ConsultationType.OPHTALMO: TypePrestation.CONSULTATION_OPHTALMO,
    ConsultationType.ODONTO: TypePrestation.CONSULTATION_ODONTO,
    ConsultationType.CARDIOLOGIE: TypePrestation.CARDIOLOGIE,
    ConsultationType.ANALYSE: TypePrestation.ANALYSE_BIOLOGIQUE,
    ConsultationType.IMAGERIE: TypePrestation.IMAGERIE,
}


def _statut_patient(status: PatientStatus) -> StatutPatient:
    """Convertit le PatientStatus du modèle vers le StatutPatient du moteur tarifaire.
    Les deux enums partagent les mêmes valeurs string, la conversion est directe."""
    return StatutPatient(status.value)


def _resoudre_prestation(type_consultation: ConsultationType) -> TypePrestation:
    """Résout le TypePrestation correspondant, lève une 400 si non tarifé."""
    prestation = _CONSULTATION_TO_PRESTATION.get(type_consultation)
    if prestation is None:
        raise HTTPException(
            400,
            f"Type de consultation '{type_consultation.value}' non pris en charge par le moteur tarifaire",
        )
    return prestation


# --------------------------------------------------------------------------- #
#  Aperçu tarifaire (sans création)
# --------------------------------------------------------------------------- #
@router.post("/preview-tarif", response_model=ConsultationWithTarifResponse)
async def preview_tarif(payload: TarifPreviewRequest, db: AsyncSession = Depends(get_db)):
    """Calcule le tarif applicable à une consultation SUIVANT le statut du patient, sans l'enregistrer."""
    row = await db.execute(select(Patient).where(Patient.id == payload.patient_id))
    patient = row.scalars().first()
    if not patient:
        raise HTTPException(404, f"Patient #{payload.patient_id} introuvable")

    prestation = _resoudre_prestation(payload.type)
    resultat = tarif_engine.calculer(_statut_patient(patient.status), prestation)

    return ConsultationWithTarifResponse(
        id=0,
        patient_id=patient.id,
        type=payload.type,
        montant=resultat["montant"],
        remise=resultat["remise"],
        gratuit=resultat["gratuit"],
        notes=None,
        dictee_audio=None,
        created_at=__import__("datetime").datetime.utcnow(),
        details_tarif=resultat["details"],
    )


# --------------------------------------------------------------------------- #
#  Création avec calcul automatique du tarif
# --------------------------------------------------------------------------- #
@router.post("", response_model=ConsultationWithTarifResponse, status_code=201)
async def creer_consultation(payload: ConsultationCreate, db: AsyncSession = Depends(get_db)):
    """
    Enregistre une consultation et calcule automatiquement le tarif via le moteur tarifaire
    (statut du patient × type de prestation).

    Des overrides optionnels permettent de forcer montant / remise / gratuité.
    """
    # 1. Vérifier l'existence du patient
    row = await db.execute(select(Patient).where(Patient.id == payload.patient_id))
    patient = row.scalars().first()
    if not patient:
        raise HTTPException(404, f"Patient #{payload.patient_id} introuvable")

    # 2. Calcul tarifaire automatique
    prestation = _resoudre_prestation(payload.type)
    tarif = tarif_engine.calculer(_statut_patient(patient.status), prestation)

    # 3. Appliquer les overrides éventuels
    gratuit = payload.forcer_gratuit if payload.forcer_gratuit is not None else tarif["gratuit"]
    montant = payload.montant_override if payload.montant_override is not None else tarif["montant"]
    remise = payload.remise_override if payload.remise_override is not None else tarif["remise"]

    if gratuit:
        montant = 0
        remise = 0

    # 4. Persister
    consultation = Consultation(
        patient_id=payload.patient_id,
        type=payload.type,
        montant=int(montant),
        remise=int(remise),
        gratuit=gratuit,
        notes=payload.notes,
        dictee_audio=payload.dictee_audio,
    )
    db.add(consultation)
    await db.commit()
    await db.refresh(consultation)

    return ConsultationWithTarifResponse(
        id=consultation.id,
        patient_id=consultation.patient_id,
        type=consultation.type,
        montant=consultation.montant,
        remise=consultation.remise,
        gratuit=consultation.gratuit,
        notes=consultation.notes,
        dictee_audio=consultation.dictee_audio,
        created_at=consultation.created_at,
        details_tarif=tarif["details"],
    )


# --------------------------------------------------------------------------- #
#  Liste paginée
# --------------------------------------------------------------------------- #
@router.get("", response_model=ConsultationListResponse)
async def lister_consultations(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    patient_id: Optional[int] = Query(None, description="Filtrer par patient"),
    type: Optional[ConsultationType] = Query(None, description="Filtrer par type"),
    db: AsyncSession = Depends(get_db),
):
    """Liste paginée des consultations, avec filtres optionnels."""
    query = select(Consultation)
    count_query = select(func.count(Consultation.id))

    if patient_id is not None:
        query = query.where(Consultation.patient_id == patient_id)
        count_query = count_query.where(Consultation.patient_id == patient_id)
    if type is not None:
        query = query.where(Consultation.type == type)
        count_query = count_query.where(Consultation.type == type)

    total = (await db.execute(count_query)).scalar_one()
    rows = await db.execute(
        query.order_by(Consultation.created_at.desc()).offset(skip).limit(limit)
    )
    consultations = rows.scalars().all()

    return ConsultationListResponse(
        total=total, skip=skip, limit=limit,
        consultations=[ConsultationResponse.model_validate(c) for c in consultations],
    )


# --------------------------------------------------------------------------- #
#  Détail
# --------------------------------------------------------------------------- #
@router.get("/{consultation_id}", response_model=ConsultationResponse)
async def obtenir_consultation(consultation_id: int, db: AsyncSession = Depends(get_db)):
    """Récupère une consultation par son identifiant."""
    row = await db.execute(select(Consultation).where(Consultation.id == consultation_id))
    consultation = row.scalars().first()
    if not consultation:
        raise HTTPException(404, f"Consultation #{consultation_id} introuvable")
    return consultation


# --------------------------------------------------------------------------- #
#  Mise à jour
# --------------------------------------------------------------------------- #
@router.put("/{consultation_id}", response_model=ConsultationResponse)
async def modifier_consultation(
    consultation_id: int, payload: ConsultationUpdate, db: AsyncSession = Depends(get_db)
):
    """Met à jour une consultation (notes, montant manuel, etc.)."""
    row = await db.execute(select(Consultation).where(Consultation.id == consultation_id))
    consultation = row.scalars().first()
    if not consultation:
        raise HTTPException(404, f"Consultation #{consultation_id} introuvable")

    data = payload.model_dump(exclude_unset=True)
    for champ, valeur in data.items():
        setattr(consultation, champ, valeur)

    await db.commit()
    await db.refresh(consultation)
    return consultation


# --------------------------------------------------------------------------- #
#  Suppression
# --------------------------------------------------------------------------- #
@router.delete("/{consultation_id}", status_code=204)
async def supprimer_consultation(consultation_id: int, db: AsyncSession = Depends(get_db)):
    """Supprime une consultation."""
    row = await db.execute(select(Consultation).where(Consultation.id == consultation_id))
    consultation = row.scalars().first()
    if not consultation:
        raise HTTPException(404, f"Consultation #{consultation_id} introuvable")

    await db.delete(consultation)
    await db.commit()
    return None
