# API endpoints — Dashboard de pilotage budgétaire (CMSFP)
# Endpoints de données agrégées pour le tableau de bord de pilotage.
# Tous protégés par JWT (dépendance au niveau du router).
#
#   GET /api/v1/dashboard/recettes        — recettes par jour/semaine/mois
#   GET /api/v1/dashboard/consultations   — nombre de consultations par type
#   GET /api/v1/dashboard/budget          — budget alloué vs recettes cumulées
#   GET /api/v1/dashboard/caisse          — synthèse caisse (ouverture/clôture/écarts)
#   GET /api/v1/dashboard/alertes         — alertes (dépassement budget, écart caisse)
#   POST /api/v1/dashboard/budget         — définit le budget alloué d'une année
from datetime import datetime, date
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import (
    Paiement,
    Consultation,
    CaisseOperation,
    TypeOperationCaisse,
    Budget,
)
from app.api.auth import get_current_user, require_role
from app.api.caisse import (
    _recalculer_contexte,
    _derniere_ouverture,
    _calculer_hash,
    _payload_hachage,
    GENESIS_HASH,
)
from app.schemas.schemas import (
    RecettesResponse,
    RecettePoint,
    ConsultationsDashboardResponse,
    ConsultationTypeStat,
    BudgetResponse,
    BudgetSetRequest,
    CaisseDashboardResponse,
    CaisseClotureInfo,
    AlertesResponse,
    AlerteItem,
    StatutPaiement,
)

router = APIRouter(
    prefix="/api/v1/dashboard",
    tags=["Dashboard de pilotage"],
    dependencies=[Depends(get_current_user)],
)

# Seuil par défaut d'écart de caisse considéré comme "anormal" (FCFA).
SEUIL_ECART_CAISSE_DEFAUT = 1000


# --------------------------------------------------------------------------- #
#  GET /recettes — recettes par jour / semaine / mois
# --------------------------------------------------------------------------- #
@router.get("/recettes", response_model=RecettesResponse)
async def recettes(
    periode: str = Query(
        "jour",
        description="Granularité: jour | semaine | mois",
        pattern="^(jour|semaine|mois)$",
    ),
    date_debut: Optional[datetime] = Query(None, description="Filtrer depuis (ISO)"),
    date_fin: Optional[datetime] = Query(None, description="Filtrer jusqu'à (ISO)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Recettes encaissées agrégées par jour, semaine ou mois.

    Seuls les paiements au statut `effectue` sont comptabilisés (recettes réelles).
    Renvoie une série chronologique prête pour un graphique en ligne.
    """
    fmt = {
        "jour": "%Y-%m-%d",
        "semaine": "%Y-W%W",
        "mois": "%Y-%m",
    }[periode]

    bucket = func.strftime(fmt, Paiement.date_paiement).label("periode")
    query = (
        select(
            bucket,
            func.coalesce(func.sum(Paiement.montant), 0).label("total"),
            func.count(Paiement.id).label("nombre"),
        )
        .where(Paiement.statut == StatutPaiement.EFFECTUE.value)
        .group_by(bucket)
        .order_by(bucket)
    )

    if date_debut is not None:
        query = query.where(Paiement.date_paiement >= date_debut)
    if date_fin is not None:
        query = query.where(Paiement.date_paiement <= date_fin)

    rows = (await db.execute(query)).all()

    series = [
        RecettePoint(periode=r.periode, total=int(r.total or 0), nombre=int(r.nombre or 0))
        for r in rows
    ]

    total_general = sum(p.total for p in series)
    nombre_paiements = sum(p.nombre for p in series)

    return RecettesResponse(
        periode=periode,
        date_debut=date_debut.isoformat() if date_debut else None,
        date_fin=date_fin.isoformat() if date_fin else None,
        total_general=total_general,
        nombre_paiements=nombre_paiements,
        series=series,
    )


# --------------------------------------------------------------------------- #
#  GET /consultations — nombre de consultations par type
# --------------------------------------------------------------------------- #
@router.get("/consultations", response_model=ConsultationsDashboardResponse)
async def consultations_par_type(
    date_debut: Optional[datetime] = Query(None, description="Filtrer depuis (ISO)"),
    date_fin: Optional[datetime] = Query(None, description="Filtrer jusqu'à (ISO)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Nombre de consultations et montant total par type de prestation.
    Prêt pour un graphique en barres.
    """
    query = (
        select(
            Consultation.type,
            func.count(Consultation.id).label("nombre"),
            func.coalesce(func.sum(Consultation.montant), 0).label("montant_total"),
        )
        .group_by(Consultation.type)
        .order_by(func.count(Consultation.id).desc())
    )

    if date_debut is not None:
        query = query.where(Consultation.created_at >= date_debut)
    if date_fin is not None:
        query = query.where(Consultation.created_at <= date_fin)

    rows = (await db.execute(query)).all()
    par_type = [
        ConsultationTypeStat(
            type=r.type.value if hasattr(r.type, "value") else str(r.type),
            nombre=int(r.nombre or 0),
            montant_total=int(r.montant_total or 0),
        )
        for r in rows
    ]

    return ConsultationsDashboardResponse(
        total_consultations=sum(s.nombre for s in par_type),
        par_type=par_type,
    )


# --------------------------------------------------------------------------- #
#  GET /budget — budget alloué vs recettes cumulées
# --------------------------------------------------------------------------- #
@router.get("/budget", response_model=BudgetResponse)
async def budget_vs_recettes(
    annee: int = Query(default=date.today().year, description="Année (ex. 2026)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Compare le budget alloué d'une année aux recettes cumulées (paiements effectue
    de cette année). Renvoie le taux de réalisation et l'écart.
    """
    # Budget alloué pour l'année (s'il existe)
    brow = await db.execute(select(Budget).where(Budget.annee == annee))
    budget = brow.scalars().first()
    budget_alloue = int(budget.montant_alloue) if budget else 0

    # Recettes cumulées sur l'année (paiements effectue)
    annee_str = f"{annee}"
    rrow = await db.execute(
        select(func.coalesce(func.sum(Paiement.montant), 0)).where(
            and_(
                Paiement.statut == StatutPaiement.EFFECTUE.value,
                func.strftime("%Y", Paiement.date_paiement) == annee_str,
            )
        )
    )
    recettes_cumulees = int(rrow.scalar_one() or 0)

    ecart = recettes_cumulees - budget_alloue
    taux = round((recettes_cumulees / budget_alloue) * 100, 2) if budget_alloue > 0 else 0.0

    return BudgetResponse(
        annee=annee,
        budget_alloue=budget_alloue,
        recettes_cumulees=recettes_cumulees,
        taux_realisation=taux,
        ecart=ecart,
        depassement=recettes_cumulees > budget_alloue if budget_alloue > 0 else False,
        budget_defini=budget is not None,
    )


# --------------------------------------------------------------------------- #
#  POST /budget — définit le budget alloué d'une année
# --------------------------------------------------------------------------- #
@router.post(
    "/budget",
    response_model=BudgetResponse,
    status_code=201,
    # A01 — RBAC : seul un administrateur peut définir le budget alloué.
    dependencies=[Depends(require_role({"admin"}))],
)
async def definir_budget(payload: BudgetSetRequest, db: AsyncSession = Depends(get_db)):
    """
    Définit (ou met à jour) le budget alloué pour une année donnée.
    Permet au tableau de bord de calculer budget vs recettes.
    """
    row = await db.execute(select(Budget).where(Budget.annee == payload.annee))
    budget = row.scalars().first()
    if budget:
        budget.montant_alloue = int(payload.montant_alloue)
        budget.description = payload.description
    else:
        budget = Budget(
            annee=payload.annee,
            montant_alloue=int(payload.montant_alloue),
            description=payload.description,
        )
        db.add(budget)
    await db.commit()
    await db.refresh(budget)

    # Retourner la comparaison à jour (réutilise la logique GET)
    return await budget_vs_recettes(annee=payload.annee, db=db)


# --------------------------------------------------------------------------- #
#  GET /caisse — synthèse caisse (ouverture, clôture, écarts)
# --------------------------------------------------------------------------- #
@router.get("/caisse", response_model=CaisseDashboardResponse)
async def synthese_caisse_dashboard(db: AsyncSession = Depends(get_db)):
    """
    Synthèse de la caisse pour le dashboard :
      - état de la session courante (ouverte / clôturée),
      - fond d'ouverture, montant théorique, opérations cumulées,
      - dernière clôture et son écart,
      - totaux encaissements / remboursements de la session,
      - intégrité du journal de hachage,
      - clôtures récentes (pour repérer les écarts récurrents).
    """
    # --- Contexte de la séance courante ---
    montant_ouverture, operations_cumul, montant_theorique = (
        await _recalculer_contexte(db)
    )
    ouverture = await _derniere_ouverture(db)

    # Dernière clôture (postérieure à la dernière ouverture si elle existe)
    cloture = None
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

    # --- Totaux de la session courante ---
    total_encaisse_session = 0
    total_rembourse_session = 0
    if ouverture:
        srow = await db.execute(
            select(CaisseOperation).where(
                CaisseOperation.id > ouverture.id,
                CaisseOperation.type_operation.in_(
                    [
                        TypeOperationCaisse.ENCAISSEMENT,
                        TypeOperationCaisse.REMBOURSEMENT,
                        TypeOperationCaisse.REGULARISATION_DIFFERE,
                    ]
                ),
            )
        )
        for op in srow.scalars().all():
            if op.type_operation == TypeOperationCaisse.REMBOURSEMENT:
                total_rembourse_session += int(op.montant or 0)
            else:
                total_encaisse_session += int(op.montant or 0)

    # --- Intégrité de la chaîne de hachage ---
    anomalies = await _verifier_chaine(db)

    # --- Clôtures récentes ---
    rrow = await db.execute(
        select(CaisseOperation)
        .where(CaisseOperation.type_operation == TypeOperationCaisse.CLOTURE)
        .order_by(CaisseOperation.id.desc())
        .limit(5)
    )
    clotures_recentes = [
        CaisseClotureInfo(
            id=op.id,
            horodatage=op.horodatage,
            montant_ouverture=op.montant_ouverture,
            montant_theorique=op.montant_theorique,
            montant_cloture=op.montant_cloture,
            ecart=int(op.ecarts or 0),
        )
        for op in rrow.scalars().all()
    ]

    derniere_cloture = None
    if cloture:
        derniere_cloture = CaisseClotureInfo(
            id=cloture.id,
            horodatage=cloture.horodatage,
            montant_ouverture=cloture.montant_ouverture,
            montant_theorique=cloture.montant_theorique,
            montant_cloture=cloture.montant_cloture,
            ecart=int(cloture.ecarts or 0),
        )

    session_ouverte = cloture is None and ouverture is not None

    return CaisseDashboardResponse(
        session_ouverte=session_ouverte,
        montant_ouverture=montant_ouverture if ouverture else None,
        operations_cumul=operations_cumul,
        montant_theorique=montant_theorique if ouverture else None,
        derniere_cloture=derniere_cloture,
        total_encaisse_session=total_encaisse_session,
        total_rembourse_session=total_rembourse_session,
        integrite_journal=len(anomalies) == 0,
        total_anomalies=len(anomalies),
        anomalies=anomalies,
        clotures_recentes=clotures_recentes,
    )


# --------------------------------------------------------------------------- #
#  GET /alertes — alertes (dépassement budget, écart caisse anormal)
# --------------------------------------------------------------------------- #
@router.get("/alertes", response_model=AlertesResponse)
async def alertes(
    annee: int = Query(default=date.today().year, description="Année pour l'alerte budget"),
    seuil_ecart: int = Query(
        SEUIL_ECART_CAISSE_DEFAUT,
        ge=0,
        description="Seuil d'écart de caisse considéré comme anormal (FCFA)",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Calcule les alertes de pilotage :
      - **budget** : dépassement du budget alloué (recettes > budget) ou sous-réalisation,
      - **caisse** : écart de caisse anormal à la dernière clôture, ou journal altéré,
      - **paiements** : paiements différés en attente de régularisation.
    """
    alertes: List[AlerteItem] = []

    # --- Alerte budget ---
    budget_info = await budget_vs_recettes(annee=annee, db=db)
    if budget_info.budget_defini:
        if budget_info.depassement:
            alertes.append(
                AlerteItem(
                    niveau="attention",
                    categorie="budget",
                    titre="Dépassement de budget",
                    message=(
                        f"Les recettes ({budget_info.recettes_cumulees:,} FCFA) dépassent "
                        f"le budget alloué ({budget_info.budget_alloue:,} FCFA) de "
                        f"{abs(budget_info.ecart):,} FCFA pour {annee}."
                    ).replace(",", " "),
                    valeur=budget_info.ecart,
                )
            )
        elif budget_info.taux_realisation < 50.0:
            alertes.append(
                AlerteItem(
                    niveau="info",
                    categorie="budget",
                    titre="Recettes en dessous du budget",
                    message=(
                        f"Taux de réalisation de {budget_info.taux_realisation}% "
                        f"({budget_info.recettes_cumulees:,} / {budget_info.budget_alloue:,} FCFA) "
                        f"pour {annee}."
                    ).replace(",", " "),
                    valeur=budget_info.ecart,
                )
            )
    else:
        alertes.append(
            AlerteItem(
                niveau="info",
                categorie="budget",
                titre="Budget non défini",
                message=f"Aucun budget alloué n'est défini pour {annee}. Définissez-le pour activer le pilotage.",
                valeur=None,
            )
        )

    # --- Alertes caisse ---
    caisse_info = await synthese_caisse_dashboard(db=db)

    # Journal altéré -> critique
    if not caisse_info.integrite_journal:
        alertes.append(
            AlerteItem(
                niveau="critique",
                categorie="caisse",
                titre="Journal de caisse altéré",
                message=(
                    f"{caisse_info.total_anomalies} anomalie(s) détectée(s) dans la chaîne "
                    "de hachage du journal de caisse. Vérification requise."
                ),
                valeur=caisse_info.total_anomalies,
            )
        )

    # Écart de caisse anormal à la dernière clôture
    if caisse_info.derniere_cloture is not None:
        ecart = abs(caisse_info.derniere_cloture.ecart)
        if ecart >= seuil_ecart:
            niveau = "critique" if ecart >= seuil_ecart * 10 else "attention"
            alertes.append(
                AlerteItem(
                    niveau=niveau,
                    categorie="caisse",
                    titre="Écart de caisse anormal",
                    message=(
                        f"Écart de {caisse_info.derniere_cloture.ecart:+,} FCFA constaté "
                        f"à la dernière clôture (seuil: {seuil_ecart:,} FCFA)."
                    ).replace(",", " "),
                    valeur=caisse_info.derniere_cloture.ecart,
                )
            )

    # --- Alertes paiements différés ---
    drow = await db.execute(
        select(
            func.count(Paiement.id).label("nombre"),
            func.coalesce(func.sum(Paiement.montant), 0).label("montant"),
        ).where(Paiement.statut == StatutPaiement.DIFFERE.value)
    )
    diff = drow.one()
    nombre_differe = int(diff.nombre or 0)
    montant_differe = int(diff.montant or 0)
    if nombre_differe > 0:
        alertes.append(
            AlerteItem(
                niveau="info",
                categorie="paiements",
                titre="Paiements différés en attente",
                message=(
                    f"{nombre_differe} paiement(s) différé(s) représentant "
                    f"{montant_differe:,} FCFA en attente de régularisation."
                ).replace(",", " "),
                valeur=montant_differe,
            )
        )

    return AlertesResponse(total=len(alertes), alertes=alertes)


# --------------------------------------------------------------------------- #
#  Utilitaire — vérification de la chaîne de hachage (réutilise caisse.py)
# --------------------------------------------------------------------------- #
async def _verifier_chaine(db: AsyncSession) -> List[str]:
    """Recalcule la chaîne de hachage et retourne la liste des anomalies."""
    rows = await db.execute(
        select(CaisseOperation).order_by(CaisseOperation.id.asc())
    )
    operations = rows.scalars().all()

    anomalies: List[str] = []
    hash_attendu = GENESIS_HASH
    for op in operations:
        if op.hash_precedent != hash_attendu:
            anomalies.append(
                f"Opération #{op.id}: hash_precedent incorrect "
                f"(attendu {hash_attendu[:12]}…, "
                f"trouvé {(op.hash_precedent or 'None')[:12]}…)"
            )
        hash_recalcule = _calculer_hash(_payload_hachage(op))
        if op.hash_courant != hash_recalcule:
            anomalies.append(
                f"Opération #{op.id}: hash_courant invalide "
                f"(attendu {hash_recalcule[:12]}…, "
                f"trouvé {(op.hash_courant or 'None')[:12]}…)"
            )
        hash_attendu = op.hash_courant
    return anomalies
