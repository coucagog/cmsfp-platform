# Moteur de règles tarifaires — CMSFP
# Gère la tarification complexe selon statut patient × type de prestation

from enum import Enum
from dataclasses import dataclass
from typing import Optional

class StatutPatient(str, Enum):
    FONCTIONNAIRE = "fonctionnaire"
    AYANT_DROIT = "ayant_droit"
    NON_AYANT_DROIT = "non_ayant_droit"

class TypePrestation(str, Enum):
    CONSULTATION_GENERALE = "consultation_generale"
    CONSULTATION_OPHTALMO = "consultation_ophtalmo"
    CONSULTATION_ODONTO = "consultation_odonto"
    ANALYSE_BIOLOGIQUE = "analyse_biologique"
    IMAGERIE = "imagerie"
    APPAREILLAGE_DENTAIRE = "appareillage_dentaire"
    PLANIFICATION_FAMILIALE = "planification_familiale"
    CARDIOLOGIE = "cardiologie"

@dataclass
class RegleTarifaire:
    """Règle de tarification : statut × prestation → montant + règles spéciales"""
    statut: StatutPatient
    prestation: TypePrestation
    montant_base: int  # FCFA entiers
    coefficient: float = 1.0  # 1 = plein tarif
    gratuit: bool = False
    remise_pct: float = 0.0
    description: str = ""

class TarifEngine:
    """
    Moteur de règles tarifaires paramétrable.
    Applique les règles : statut patient × type de prestation.
    """

    def __init__(self):
        self._regles: dict[str, RegleTarifaire] = {}
        self._initialiser_regles()

    def _key(self, statut: StatutPatient, prestation: TypePrestation) -> str:
        return f"{statut.value}:{prestation.value}"

    def _initialiser_regles(self):
        """Règles issues du cahier des charges CMSFP"""
        regles = [
            # Consultations générales : gratuites par défaut
            RegleTarifaire(StatutPatient.FONCTIONNAIRE, TypePrestation.CONSULTATION_GENERALE, 0, gratuit=True),
            RegleTarifaire(StatutPatient.AYANT_DROIT, TypePrestation.CONSULTATION_GENERALE, 0, gratuit=True),
            RegleTarifaire(StatutPatient.NON_AYANT_DROIT, TypePrestation.CONSULTATION_GENERALE, 0, gratuit=True),
            # Consultations ophtalmologiques : payantes pour tous
            RegleTarifaire(StatutPatient.FONCTIONNAIRE, TypePrestation.CONSULTATION_OPHTALMO, 5000, coefficient=0.2, description="1/5 tarif"),
            RegleTarifaire(StatutPatient.AYANT_DROIT, TypePrestation.CONSULTATION_OPHTALMO, 5000, coefficient=0.2, description="1/5 tarif"),
            RegleTarifaire(StatutPatient.NON_AYANT_DROIT, TypePrestation.CONSULTATION_OPHTALMO, 5000, coefficient=1.0, description="Plein tarif"),
            # Consultations odontologiques : payantes pour non ayants droit seulement
            RegleTarifaire(StatutPatient.FONCTIONNAIRE, TypePrestation.CONSULTATION_ODONTO, 0, gratuit=True),
            RegleTarifaire(StatutPatient.AYANT_DROIT, TypePrestation.CONSULTATION_ODONTO, 0, gratuit=True),
            RegleTarifaire(StatutPatient.NON_AYANT_DROIT, TypePrestation.CONSULTATION_ODONTO, 8000, coefficient=1.0, description="Plein tarif"),
            # Analyses biologiques : payantes
            RegleTarifaire(StatutPatient.FONCTIONNAIRE, TypePrestation.ANALYSE_BIOLOGIQUE, 10000, coefficient=0.2),
            RegleTarifaire(StatutPatient.AYANT_DROIT, TypePrestation.ANALYSE_BIOLOGIQUE, 10000, coefficient=0.2),
            RegleTarifaire(StatutPatient.NON_AYANT_DROIT, TypePrestation.ANALYSE_BIOLOGIQUE, 10000, coefficient=1.0),
            # Appareillage dentaire : payants
            RegleTarifaire(StatutPatient.FONCTIONNAIRE, TypePrestation.APPAREILLAGE_DENTAIRE, 50000, coefficient=0.2),
            RegleTarifaire(StatutPatient.AYANT_DROIT, TypePrestation.APPAREILLAGE_DENTAIRE, 50000, coefficient=0.2),
            RegleTarifaire(StatutPatient.NON_AYANT_DROIT, TypePrestation.APPAREILLAGE_DENTAIRE, 50000, coefficient=1.0),
        ]
        for r in regles:
            self._regles[self._key(r.statut, r.prestation)] = r

    def calculer(self, statut: StatutPatient, prestation: TypePrestation) -> dict:
        """
        Calcule le montant à payer selon statut × prestation.
        Retourne : {montant, remise, gratuit, details}
        """
        regle = self._regles.get(self._key(statut, prestation))
        if not regle:
            return {"montant": 0, "remise": 0, "gratuit": True, "details": "Non défini"}

        if regle.gratuit:
            return {"montant": 0, "remise": 0, "gratuit": True, "details": "Gratuit"}

        montant = regle.montant_base * regle.coefficient
        remise = regle.montant_base - montant
        return {
            "montant": round(montant),
            "remise": round(remise),
            "gratuit": False,
            "details": f"{regle.description} — Base: {regle.montant_base}F CFA"
        }

    def ajouter_regle(self, regle: RegleTarifaire):
        """Ajoute ou modifie une règle (paramétrable)"""
        self._regles[self._key(regle.statut, regle.prestation)] = regle

    def lister_regles(self) -> list[dict]:
        return [
            {
                "statut": k.split(":")[0],
                "prestation": k.split(":")[1],
                "montant_base": v.montant_base,
                "coefficient": v.coefficient,
                "gratuit": v.gratuit,
                "description": v.description
            }
            for k, v in self._regles.items()
        ]

# Instance globale
tarif_engine = TarifEngine()
