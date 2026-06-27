# API endpoints - Tarification
from fastapi import APIRouter, Depends, HTTPException
from app.services.tarif_engine import tarif_engine, StatutPatient, TypePrestation
from app.api.auth import get_current_user

router = APIRouter(
    prefix="/api/v1/tarifs",
    tags=["Tarification"],
    dependencies=[Depends(get_current_user)],
)

@router.get("/regles")
async def lister_regles():
    return {"regles": tarif_engine.lister_regles()}

@router.get("/calculer")
async def calculer_tarif(statut: str, prestation: str):
    try:
        s = StatutPatient(statut)
        p = TypePrestation(prestation)
    except ValueError:
        raise HTTPException(400, "Statut ou prestation invalide")
    return tarif_engine.calculer(s, p)

@router.get("/statuts")
async def lister_statuts():
    return {"statuts": [s.value for s in StatutPatient]}

@router.get("/prestations")
async def lister_prestations():
    return {"prestations": [p.value for p in TypePrestation]}
