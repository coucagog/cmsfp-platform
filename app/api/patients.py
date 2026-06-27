# API endpoints - Gestion des Patients
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import uuid
import io
import base64

import qrcode

from app.core.database import get_db
from app.models.models import Patient, PatientStatus, Consultation, Paiement, CaisseOperation
from app.api.auth import get_current_user, TokenData
from app.core.audit import audit_log
from app.schemas.schemas import (
    PatientCreate,
    PatientUpdate,
    PatientResponse,
    PatientListResponse,
    QRCodeResponse,
    PatientSearchResult,
)

router = APIRouter(
    prefix="/api/v1/patients",
    tags=["Patients"],
    dependencies=[Depends(get_current_user)],
)


# --------------------------------------------------------------------------- #
#  Création
# --------------------------------------------------------------------------- #
@router.post("", response_model=PatientResponse, status_code=201)
async def creer_patient(
    payload: PatientCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(get_current_user),
):
    """Crée un nouveau patient. Vérifie l'unicité du matricule et du QR code."""
    # Unicité du matricule
    if payload.matricule:
        existing = await db.execute(
            select(Patient).where(Patient.matricule == payload.matricule)
        )
        if existing.scalars().first():
            raise HTTPException(409, f"Un patient avec le matricule '{payload.matricule}' existe déjà")

    # Unicité du QR code
    if payload.qr_code:
        existing = await db.execute(
            select(Patient).where(Patient.qr_code == payload.qr_code)
        )
        if existing.scalars().first():
            raise HTTPException(409, f"Le QR code '{payload.qr_code}' est déjà attribué")

    patient = Patient(**payload.model_dump())
    db.add(patient)
    await db.commit()
    await db.refresh(patient)
    # A09 — audit log de la création de patient.
    audit_log(
        "patient.create",
        user=current_user.username,
        resource_type="patient",
        resource_id=patient.id,
        detail=f"matricule={payload.matricule or 'N/A'}",
    )
    return patient


# --------------------------------------------------------------------------- #
#  Recherche dynamique (typeahead — temps réel pendant la frappe)
# --------------------------------------------------------------------------- #
@router.get("/search", response_model=PatientSearchResult)
async def rechercher_patients(
    q: str = Query(..., min_length=1, description="Terme de recherche (nom, prénom, téléphone, matricule)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """
    Recherche dynamique multi-critères optimisée pour le typeahead.
    - Insensible à la casse (ILIKE).
    - Multi-mots : chaque mot doit apparaître dans au moins un champ
      (nom, prénom, téléphone, matricule).
    - Les espaces superflus sont ignorés.
    """
    q = q.strip()
    if not q:
        raise HTTPException(422, "Le terme de recherche ne peut pas être vide")

    # Découpage en mots pour la recherche multi-critères
    mots = q.split()
    filtres_mots = []
    for mot in mots:
        pattern = f"%{mot}%"
        filtres_mots.append(
            or_(
                Patient.nom.ilike(pattern),
                Patient.prenom.ilike(pattern),
                Patient.telephone.ilike(pattern),
                Patient.matricule.ilike(pattern),
            )
        )
    # Tous les mots doivent matcher (AND entre mots, OR entre champs)
    filtre = and_(*filtres_mots)

    total_q = await db.execute(select(func.count(Patient.id)).where(filtre))
    total = total_q.scalar_one()

    rows = await db.execute(
        select(Patient).where(filtre).order_by(Patient.nom, Patient.prenom).offset(skip).limit(limit)
    )
    patients = rows.scalars().all()

    return PatientSearchResult(
        total=total,
        query=q,
        patients=[PatientResponse.model_validate(p) for p in patients],
    )


# --------------------------------------------------------------------------- #
#  Liste paginée
# --------------------------------------------------------------------------- #
@router.get("", response_model=PatientListResponse)
async def lister_patients(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[PatientStatus] = Query(None, description="Filtrer par statut"),
    db: AsyncSession = Depends(get_db),
):
    """Liste paginée des patients, avec filtre optionnel par statut."""
    query = select(Patient)
    count_query = select(func.count(Patient.id))

    if status is not None:
        query = query.where(Patient.status == status)
        count_query = count_query.where(Patient.status == status)

    total = (await db.execute(count_query)).scalar_one()
    rows = await db.execute(
        query.order_by(Patient.created_at.desc()).offset(skip).limit(limit)
    )
    patients = rows.scalars().all()

    return PatientListResponse(
        total=total, skip=skip, limit=limit, patients=[PatientResponse.model_validate(p) for p in patients]
    )


# --------------------------------------------------------------------------- #
#  Recherche par QR code
# --------------------------------------------------------------------------- #
@router.get("/qr/{code}", response_model=PatientResponse)
async def retrouver_par_qr(code: str, db: AsyncSession = Depends(get_db)):
    """Retrouve un patient à partir de son QR code unique (UUID)."""
    row = await db.execute(select(Patient).where(Patient.qr_code == code))
    patient = row.scalars().first()
    if not patient:
        raise HTTPException(404, f"Aucun patient trouvé avec le QR code '{code}'")
    return patient


# --------------------------------------------------------------------------- #
#  Détail
# --------------------------------------------------------------------------- #
@router.get("/{patient_id}", response_model=PatientResponse)
async def obtenir_patient(patient_id: int, db: AsyncSession = Depends(get_db)):
    """Récupère un patient par son identifiant."""
    row = await db.execute(select(Patient).where(Patient.id == patient_id))
    patient = row.scalars().first()
    if not patient:
        raise HTTPException(404, f"Patient #{patient_id} introuvable")
    return patient


# --------------------------------------------------------------------------- #
#  Génération de QR code
# --------------------------------------------------------------------------- #
@router.post("/{patient_id}/qr-code", response_model=QRCodeResponse)
async def generer_qr_code(
    patient_id: int,
    regenerate: bool = Query(
        False, description="Forcer la régénération d'un nouveau QR code même si un existe déjà"
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Génère un QR code unique (UUID v4) pour un patient et le sauvegarde en base.
    Si le patient a déjà un QR code et regenerate=False, le code existant est retourné.
    L'image PNG du QR code est renvoyée encodée en base64.
    """
    row = await db.execute(select(Patient).where(Patient.id == patient_id))
    patient = row.scalars().first()
    if not patient:
        raise HTTPException(404, f"Patient #{patient_id} introuvable")

    generated = False

    if patient.qr_code and not regenerate:
        code = patient.qr_code
    else:
        # Génération d'un UUID v4 unique
        code = str(uuid.uuid4())
        # Vérification d'unicité (collision extrêmement improbable mais défensive)
        for _ in range(10):
            existing = await db.execute(select(Patient).where(Patient.qr_code == code, Patient.id != patient_id))
            if not existing.scalars().first():
                break
            code = str(uuid.uuid4())

        patient.qr_code = code
        await db.commit()
        await db.refresh(patient)
        generated = True

    # Génération de l'image PNG du QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    return QRCodeResponse(
        patient_id=patient.id,
        qr_code=code,
        image_base64=img_b64,
        image_format="png",
        generated=generated,
    )


# --------------------------------------------------------------------------- #
#  Mise à jour
# --------------------------------------------------------------------------- #
@router.put("/{patient_id}", response_model=PatientResponse)
async def modifier_patient(
    patient_id: int, payload: PatientUpdate, db: AsyncSession = Depends(get_db)
):
    """Met à jour les informations d'un patient."""
    row = await db.execute(select(Patient).where(Patient.id == patient_id))
    patient = row.scalars().first()
    if not patient:
        raise HTTPException(404, f"Patient #{patient_id} introuvable")

    data = payload.model_dump(exclude_unset=True)

    # Vérifier l'unicité du matricule si modifié
    if "matricule" in data and data["matricule"] is not None:
        dup = await db.execute(
            select(Patient).where(Patient.matricule == data["matricule"], Patient.id != patient_id)
        )
        if dup.scalars().first():
            raise HTTPException(409, f"Le matricule '{data['matricule']}' appartient déjà à un autre patient")

    # Vérifier l'unicité du QR code si modifié
    if "qr_code" in data and data["qr_code"] is not None:
        dup = await db.execute(
            select(Patient).where(Patient.qr_code == data["qr_code"], Patient.id != patient_id)
        )
        if dup.scalars().first():
            raise HTTPException(409, f"Le QR code '{data['qr_code']}' appartient déjà à un autre patient")

    for champ, valeur in data.items():
        setattr(patient, champ, valeur)

    await db.commit()
    await db.refresh(patient)
    return patient


# --------------------------------------------------------------------------- #
#  Suppression
# --------------------------------------------------------------------------- #
@router.delete("/{patient_id}", status_code=204)
async def supprimer_patient(patient_id: int, db: AsyncSession = Depends(get_db)):
    """Supprime un patient. Refuse la suppression si des consultations ou paiements existent."""
    row = await db.execute(select(Patient).where(Patient.id == patient_id))
    patient = row.scalars().first()
    if not patient:
        raise HTTPException(404, f"Patient #{patient_id} introuvable")

    # Vérifier l'absence de dépendances (requêtes explicites — pas de lazy load en async)
    nb_consultations = (
        await db.execute(select(func.count(Consultation.id)).where(Consultation.patient_id == patient_id))
    ).scalar_one()
    if nb_consultations:
        raise HTTPException(
            409,
            f"Impossible de supprimer: {nb_consultations} consultation(s) liée(s). "
            "Supprimez d'abord les consultations.",
        )

    nb_paiements = (
        await db.execute(select(func.count(Paiement.id)).where(Paiement.patient_id == patient_id))
    ).scalar_one()
    if nb_paiements:
        raise HTTPException(
            409,
            f"Impossible de supprimer: {nb_paiements} paiement(s) lié(s).",
        )

    # Vérifier aussi les opérations de caisse (journal immuable)
    nb_caisse_ops = (
        await db.execute(select(func.count(CaisseOperation.id)).where(CaisseOperation.patient_id == patient_id))
    ).scalar_one()
    if nb_caisse_ops:
        raise HTTPException(
            409,
            f"Impossible de supprimer: {nb_caisse_ops} opération(s) de caisse liée(s) "
            "au journal immuable. Supprimez d'abord ces opérations.",
        )

    await db.delete(patient)
    await db.commit()
    return None
