# API endpoints - Circuit Conseil de santé (Défi 4)
# Gestion des dossiers d'évacuation sanitaire des agents de l'État.
#
# Circuit métier (machine à états linéaire) :
#   soumis → étude → ventilé → évacué → retour → contrôle_trésor → clôturé
#
# Chaque transition est validée : le statut source doit être le prédécesseur
# direct du statut cible, et certaines informations métier doivent être
# présentes (spécialiste pour la ventilation, destination pour l'évacuation,
# montant réel pour le contrôle du Trésor).
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime, date
import uuid

from app.core.database import get_db
from app.models.models import (
    DossierConseilSante,
    StatutDossierConseil,
    Patient,
)
from app.api.auth import get_current_user
from app.schemas.schemas import (
    DossierConseilSanteCreate,
    DossierConseilSanteResponse,
    DossierConseilSanteListResponse,
    TransitionStatutRequest,
)

router = APIRouter(
    prefix="/api/v1/conseil-sante",
    tags=["Circuit Conseil de santé"],
    dependencies=[Depends(get_current_user)],
)

# --------------------------------------------------------------------------- #
#  Machine à états : transitions autorisées
# --------------------------------------------------------------------------- #
TRANSITIONS_AUTORISEES: dict[StatutDossierConseil, StatutDossierConseil] = {
    StatutDossierConseil.SOUMIS: StatutDossierConseil.ETUDE,
    StatutDossierConseil.ETUDE: StatutDossierConseil.VENTILE,
    StatutDossierConseil.VENTILE: StatutDossierConseil.EVACUE,
    StatutDossierConseil.EVACUE: StatutDossierConseil.RETOUR,
    StatutDossierConseil.RETOUR: StatutDossierConseil.CONTROLE_TRESOR,
    StatutDossierConseil.CONTROLE_TRESOR: StatutDossierConseil.CLOTURE,
}

# Date à remplir pour chaque transition
DATE_PAR_TRANSITION: dict[StatutDossierConseil, str] = {
    StatutDossierConseil.ETUDE: "date_etude",
    StatutDossierConseil.VENTILE: "date_ventilation",
    StatutDossierConseil.EVACUE: "date_evacuation",
    StatutDossierConseil.RETOUR: "date_retour",
    StatutDossierConseil.CONTROLE_TRESOR: "date_controle_tresor",
    StatutDossierConseil.CLOTURE: "date_cloture",
}


def _generer_numero_dossier() -> str:
    """Génère un numéro de dossier unique au format CS-YYYY-XXXXXX."""
    annee = datetime.utcnow().year
    suffixe = uuid.uuid4().hex[:6].upper()
    return f"CS-{annee}-{suffixe}"


async def _get_dossier_or_404(db: AsyncSession, dossier_id: int) -> DossierConseilSante:
    row = await db.execute(
        select(DossierConseilSante).where(DossierConseilSante.id == dossier_id)
    )
    dossier = row.scalars().first()
    if not dossier:
        raise HTTPException(404, f"Dossier Conseil de santé #{dossier_id} introuvable")
    return dossier


# --------------------------------------------------------------------------- #
#  POST /api/v1/conseil-sante — Nouveau dossier
# --------------------------------------------------------------------------- #
@router.post("", response_model=DossierConseilSanteResponse, status_code=201)
async def creer_dossier(
    payload: DossierConseilSanteCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    Crée un nouveau dossier du circuit Conseil de santé.

    Le dossier est créé avec le statut **soumis** (demande adressée au
    Ministre de la Fonction publique). Un numéro de dossier unique est
    généré automatiquement.
    """
    # Vérifier le patient si un patient_id est fourni
    if payload.patient_id is not None:
        row = await db.execute(
            select(Patient).where(Patient.id == payload.patient_id)
        )
        if not row.scalars().first():
            raise HTTPException(404, f"Patient #{payload.patient_id} introuvable")

    # Générer un numéro de dossier unique
    numero = _generer_numero_dossier()
    for _ in range(10):
        existing = await db.execute(
            select(DossierConseilSante).where(
                DossierConseilSante.numero_dossier == numero
            )
        )
        if not existing.scalars().first():
            break
        numero = _generer_numero_dossier()

    data = payload.model_dump()
    data["numero_dossier"] = numero
    data["statut"] = StatutDossierConseil.SOUMIS
    data["date_soumission"] = datetime.utcnow()

    dossier = DossierConseilSante(**data)
    db.add(dossier)
    await db.commit()
    await db.refresh(dossier)
    return dossier


# --------------------------------------------------------------------------- #
#  GET /api/v1/conseil-sante — Liste paginée avec filtres
# --------------------------------------------------------------------------- #
@router.get("", response_model=DossierConseilSanteListResponse)
async def lister_dossiers(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    statut: Optional[StatutDossierConseil] = Query(
        None, description="Filtrer par statut"
    ),
    date_debut: Optional[date] = Query(
        None, description="Filtrer par date de soumission (à partir de)"
    ),
    date_fin: Optional[date] = Query(
        None, description="Filtrer par date de soumission (jusqu'à)"
    ),
    patient_id: Optional[int] = Query(None, description="Filtrer par patient"),
    db: AsyncSession = Depends(get_db),
):
    """
    Liste paginée des dossiers Conseil de santé.

    Filtres disponibles :
    - **statut** : filtrer par statut du circuit
    - **date_debut / date_fin** : plage de dates de soumission
    - **patient_id** : dossiers liés à un patient spécifique
    """
    query = select(DossierConseilSante)
    count_query = select(func.count(DossierConseilSante.id))

    conditions = []
    if statut is not None:
        conditions.append(DossierConseilSante.statut == statut)
    if patient_id is not None:
        conditions.append(DossierConseilSante.patient_id == patient_id)
    if date_debut is not None:
        conditions.append(DossierConseilSante.date_soumission >= datetime.combine(date_debut, datetime.min.time()))
    if date_fin is not None:
        conditions.append(
            DossierConseilSante.date_soumission
            <= datetime.combine(date_fin, datetime.max.time())
        )

    if conditions:
        filtre = and_(*conditions)
        query = query.where(filtre)
        count_query = count_query.where(filtre)

    total = (await db.execute(count_query)).scalar_one()
    rows = await db.execute(
        query.order_by(DossierConseilSante.date_soumission.desc())
        .offset(skip)
        .limit(limit)
    )
    dossiers = rows.scalars().all()

    return DossierConseilSanteListResponse(
        total=total,
        skip=skip,
        limit=limit,
        dossiers=[
            DossierConseilSanteResponse.model_validate(d) for d in dossiers
        ],
    )


# --------------------------------------------------------------------------- #
#  GET /api/v1/conseil-sante/controle-tresor — Dossiers en attente de contrôle
# --------------------------------------------------------------------------- #
@router.get(
    "/controle-tresor",
    response_model=DossierConseilSanteListResponse,
    summary="Dossiers en attente de contrôle du Trésor",
)
async def dossiers_controle_tresor(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """
    Retourne les dossiers dont le statut est **retour** (en attente de
    transmission au Trésor) ou **contrôle_trésor** (en cours de contrôle).

    Ces dossiers représentent les patients de retour d'évacuation dont les
    paiements doivent être vérifiés et validés par le Trésor public.
    """
    statuts_cibles = [
        StatutDossierConseil.RETOUR,
        StatutDossierConseil.CONTROLE_TRESOR,
    ]
    filtre = DossierConseilSante.statut.in_(statuts_cibles)

    total = (
        await db.execute(
            select(func.count(DossierConseilSante.id)).where(filtre)
        )
    ).scalar_one()

    rows = await db.execute(
        select(DossierConseilSante)
        .where(filtre)
        .order_by(DossierConseilSante.date_retour.asc().nullslast())
        .offset(skip)
        .limit(limit)
    )
    dossiers = rows.scalars().all()

    return DossierConseilSanteListResponse(
        total=total,
        skip=skip,
        limit=limit,
        dossiers=[
            DossierConseilSanteResponse.model_validate(d) for d in dossiers
        ],
    )


# --------------------------------------------------------------------------- #
#  GET /api/v1/conseil-sante/{id} — Détail
# --------------------------------------------------------------------------- #
@router.get("/{dossier_id}", response_model=DossierConseilSanteResponse)
async def obtenir_dossier(
    dossier_id: int, db: AsyncSession = Depends(get_db)
):
    """Récupère le détail d'un dossier Conseil de santé par son identifiant."""
    return await _get_dossier_or_404(db, dossier_id)


# --------------------------------------------------------------------------- #
#  PUT /api/v1/conseil-sante/{id}/statut — Transition de statut
# --------------------------------------------------------------------------- #
@router.put(
    "/{dossier_id}/statut",
    response_model=DossierConseilSanteResponse,
    summary="Transition de statut du circuit",
)
async def transition_statut(
    dossier_id: int,
    payload: TransitionStatutRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Effectue une transition de statut dans le circuit Conseil de santé.

    Le circuit est linéaire : chaque statut ne peut passer que vers son
    successeur direct :

    ```
    soumis → étude → ventilé → évacué → retour → contrôle_trésor → clôturé
    ```

    **Validations par transition :**
    - **ventilation** (→ ventilé) : un spécialiste doit être renseigné
    - **évacuation** (→ évacué) : une destination doit être renseignée
    - **contrôle du Trésor** (→ contrôle_trésor) : un montant réel doit être fourni

    Les champs optionnels (`specialiste`, `destination`, `montant_reel`,
    `observations`) peuvent être fournis dans le corps de la requête pour
    mettre à jour le dossier en même temps que la transition.
    """
    dossier = await _get_dossier_or_404(db, dossier_id)
    statut_cible = payload.nouveau_statut
    statut_actuel = dossier.statut

    # Vérifier que la transition est autorisée
    cible_attendue = TRANSITIONS_AUTORISEES.get(statut_actuel)
    if cible_attendue is None:
        raise HTTPException(
            409,
            f"Le dossier #{dossier.id} est au statut '{statut_actuel.value}' "
            f"(clôturé) — aucune transition n'est possible.",
        )
    if statut_cible != cible_attendue:
        raise HTTPException(
            409,
            f"Transition non autorisée : '{statut_actuel.value}' → "
            f"'{statut_cible.value}'. La transition attendue depuis "
            f"'{statut_actuel.value}' est vers '{cible_attendue.value}'.",
        )

    maintenant = datetime.utcnow()

    # Validations spécifiques par transition
    if statut_cible == StatutDossierConseil.VENTILE:
        # La ventilation nécessite un spécialiste
        specialiste = payload.specialiste or dossier.specialiste
        if not specialiste:
            raise HTTPException(
                422,
                "La ventilation vers un spécialiste nécessite de renseigner "
                "le champ 'specialiste'.",
            )
        dossier.specialiste = specialiste

    if statut_cible == StatutDossierConseil.EVACUE:
        # L'évacuation nécessite une destination
        destination = payload.destination or dossier.destination
        if not destination:
            raise HTTPException(
                422,
                "L'évacuation nécessite de renseigner le champ 'destination' "
                "(pays/ville de prise en charge à l'étranger).",
            )
        dossier.destination = destination

    if statut_cible == StatutDossierConseil.CONTROLE_TRESOR:
        # Le contrôle du Trésor nécessite un montant réel
        if payload.montant_reel is None and dossier.montant_reel is None:
            raise HTTPException(
                422,
                "Le contrôle du Trésor nécessite de renseigner le champ "
                "'montant_reel' (coût réel en FCFA).",
            )
        if payload.montant_reel is not None:
            dossier.montant_reel = payload.montant_reel

    # Mettre à jour les observations si fournies
    if payload.observations:
        obs_existantes = dossier.observations or ""
        dossier.observations = (
            f"{obs_existantes}\n[{maintenant.isoformat()}] "
            f"({statut_actuel.value}→{statut_cible.value}) "
            f"{payload.observations}"
            if obs_existantes
            else f"[{maintenant.isoformat()}] "
            f"({statut_actuel.value}→{statut_cible.value}) "
            f"{payload.observations}"
        )

    # Appliquer la transition
    dossier.statut = statut_cible
    champ_date = DATE_PAR_TRANSITION.get(statut_cible)
    if champ_date:
        setattr(dossier, champ_date, maintenant)

    await db.commit()
    await db.refresh(dossier)
    return dossier
