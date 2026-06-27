# Database models - CMSFP Platform
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Enum as SAEnum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.core.database import Base

class PatientStatus(str, enum.Enum):
    FONCTIONNAIRE = "fonctionnaire"
    AYANT_DROIT = "ayant_droit"
    NON_AYANT_DROIT = "non_ayant_droit"

class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    nom = Column(String(100), nullable=False)
    prenom = Column(String(100), nullable=False)
    matricule = Column(String(50), unique=True, nullable=True)
    telephone = Column(String(20), nullable=True)
    status = Column(SAEnum(PatientStatus), default=PatientStatus.NON_AYANT_DROIT)
    qr_code = Column(String(255), unique=True, nullable=True)
    date_naissance = Column(DateTime, nullable=True)
    adresse = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    consultations = relationship("Consultation", back_populates="patient")
    paiements = relationship("Paiement", back_populates="patient")

class ConsultationType(str, enum.Enum):
    GENERALE = "generale"
    OPHTALMO = "ophtalmologique"
    ODONTO = "odontologique"
    CARDIOLOGIE = "cardiologie"
    ANALYSE = "analyse"
    IMAGERIE = "imagerie"

class Consultation(Base):
    __tablename__ = "consultations"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    type = Column(SAEnum(ConsultationType), nullable=False)
    # Montants en FCFA — Integer (pas de décimales sur la monnaie locale)
    montant = Column(Integer, default=0)
    remise = Column(Integer, default=0)
    gratuit = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)
    dictee_audio = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="consultations")

class Paiement(Base):
    __tablename__ = "paiements"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    consultation_id = Column(Integer, ForeignKey("consultations.id"), nullable=True)
    # Montants en FCFA — Integer
    montant = Column(Integer, nullable=False)
    remise_appliquee = Column(Integer, default=0)
    mode = Column(String(50), default="especes")
    statut = Column(String(20), default="effectue")  # effectue, rembourse, differe
    motif_remboursement = Column(String(255), nullable=True)
    date_paiement = Column(DateTime, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="paiements")

# --------------------------------------------------------------------------- #
#  Circuit Conseil de santé (Défi 4)
#  Gestion des dossiers de patients (agents de l'État) à évacuer à l'étranger
#  pour des soins non disponibles au Sénégal.
#
#  Circuit :
#    1. Demande adressée au Ministre (voie hiérarchique)     → soumis
#    2. Dossier étudié puis ventilé vers spécialistes         → étude → ventilé
#    3. Patient orienté vers soins à l'étranger               → évacué
#    4. Au retour : retrouve poste / réaffectation / quitte FP → retour
#    5. Dossiers contrôlés par le Trésor (versement paiements) → contrôle_trésor
#    6. Suivi financier : collecte, enregistrement, conservation → clôturé
# --------------------------------------------------------------------------- #
class StatutDossierConseil(str, enum.Enum):
    """Statuts du circuit Conseil de santé (machine à états linéaire)."""
    SOUMIS = "soumis"
    ETUDE = "etude"
    VENTILE = "ventile"
    EVACUE = "evacue"
    RETOUR = "retour"
    CONTROLE_TRESOR = "controle_tresor"
    CLOTURE = "cloture"


class DossierConseilSante(Base):
    """
    Dossier du circuit Conseil de santé.

    Représente le parcours complet d'un agent de l'État depuis la demande
    d'évacuation sanitaire jusqu'au contrôle financier par le Trésor et la
    clôture du dossier.
    """
    __tablename__ = "dossiers_conseil_sante"

    id = Column(Integer, primary_key=True, index=True)
    numero_dossier = Column(String(50), unique=True, nullable=False, index=True)

    # Lien optionnel vers un patient enregistré dans la plateforme
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True, index=True)

    # Informations du patient (portées directement pour les dossiers
    # où le patient n'est pas encore enregistré comme Patient)
    nom_patient = Column(String(100), nullable=False)
    prenom_patient = Column(String(100), nullable=False)
    matricule_patient = Column(String(50), nullable=True)

    # --- Circuit métier ---
    motif = Column(Text, nullable=False, doc="Motif de la demande d'évacuation")
    specialiste = Column(String(200), nullable=True, doc="Spécialiste compétent assigné après ventilation")
    destination = Column(String(200), nullable=True, doc="Pays/ville de prise en charge à l'étranger")

    # Statut courant dans le circuit
    statut = Column(SAEnum(StatutDossierConseil), default=StatutDossierConseil.SOUMIS, nullable=False, index=True)

    # --- Dates clés du circuit (remplies au fur et à mesure des transitions) ---
    date_soumission = Column(DateTime, default=datetime.utcnow, doc="Demande adressée au Ministre")
    date_etude = Column(DateTime, nullable=True, doc="Début d'étude du dossier")
    date_ventilation = Column(DateTime, nullable=True, doc="Ventilation vers spécialiste")
    date_evacuation = Column(DateTime, nullable=True, doc="Évacuation vers l'étranger")
    date_retour = Column(DateTime, nullable=True, doc="Retour du patient")
    date_controle_tresor = Column(DateTime, nullable=True, doc="Contrôle par le Trésor")
    date_cloture = Column(DateTime, nullable=True, doc="Clôture du dossier")

    # --- Suivi financier (montants en FCFA entiers) ---
    montant_estime = Column(Integer, nullable=True, doc="Estimation du coût d'évacuation (FCFA)")
    montant_reel = Column(Integer, nullable=True, doc="Coût réel après contrôle du Trésor (FCFA)")

    # --- Observations / notes libres ---
    observations = Column(Text, nullable=True)

    # --- Métadonnées ---
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relation (lazy="select" — pas de chargement automatique en async)
    patient = relationship("Patient")

class Reunion(Base):
    __tablename__ = "reunions"

    id = Column(Integer, primary_key=True, index=True)
    titre = Column(String(200), nullable=False)
    date_reunion = Column(DateTime, default=datetime.utcnow)
    participants = Column(Text, nullable=True)
    transcription_audio = Column(Text, nullable=True)
    compte_rendu = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------- #
#  Caisse traçable (Défi 3)
#  Journal immuable des opérations de caisse utilisant une chaîne de hachage
#  (hash_precedent -> hash_courant) qui rend toute altération détectable.
# --------------------------------------------------------------------------- #
class TypeOperationCaisse(str, enum.Enum):
    """Types d'opération enregistrés dans le journal de caisse."""
    OUVERTURE = "ouverture"
    ENCAISSEMENT = "encaissement"
    REMBOURSEMENT = "remboursement"
    CLOTURE = "cloture"
    RENONCIATION = "renonciation"
    REGULARISATION_DIFFERE = "regularisation_differe"


class CasParticulierCaisse(str, enum.Enum):
    """Cas particuliers de gestion financière tracés explicitement."""
    AUCUN = "aucun"
    RENONCIATION_APRES_PAIEMENT = "renonciation_apres_paiement"
    REMBOURSEMENT_PANNE = "remboursement_panne"
    PAIEMENT_DIFFERE = "paiement_differe"


class CaisseOperation(Base):
    """
    Journal immuable de la caisse.

    Chaque ligne est liée à la précédente par `hash_precedent` (SHA-256 du
    record précédent). `hash_courant` est calculé à l'insertion à partir des
    champs critiques de l'opération + du hash précédent. Toute modification
    ultérieure d'un enregistrement casse la chaîne et devient détectable par
    l'endpoint GET /api/v1/caisse/journal/verifier.

    Champs demandés par le cahier des charges :
      - montant_ouverture : fond de caisse au démarrage de la séance
      - montant_cloture   : montant réellement compté à la clôture
      - operations        : nombre cumulé d'opérations depuis l'ouverture
      - ecarts            : écart (réel - théorique) constaté à la clôture

    Tous les montants sont en FCFA entiers (Integer) — pas de décimales.
    """
    __tablename__ = "caisse_operations"

    id = Column(Integer, primary_key=True, index=True)
    type_operation = Column(SAEnum(TypeOperationCaisse), nullable=False, index=True)

    # Montant de l'opération courante (encaissement / remboursement / etc.) — FCFA entiers
    montant = Column(Integer, default=0)

    # Fond de caisse — rempli lors d'une OUVERTURE, recopié pour le suivi
    montant_ouverture = Column(Integer, nullable=True)
    # Montant théoriquement attendu en caisse (calculé à la clôture)
    montant_theorique = Column(Integer, nullable=True)
    # Montant réellement compté à la clôture
    montant_cloture = Column(Integer, nullable=True)

    # Cumul des opérations depuis la dernière ouverture
    operations = Column(Integer, default=0)
    # Écart constaté (montant_cloture - montant_theorique)
    ecarts = Column(Integer, default=0)

    # Traçabilité métier
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True)
    paiement_id = Column(Integer, ForeignKey("paiements.id"), nullable=True)
    cas_particulier = Column(
        SAEnum(CasParticulierCaisse), default=CasParticulierCaisse.AUCUN
    )
    motif = Column(String(255), nullable=True)
    operateur = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)

    # Chaîne de hachage pour l'immutabilité
    hash_precedent = Column(String(64), nullable=True, index=True)
    hash_courant = Column(String(64), nullable=False, unique=True, index=True)

    horodatage = Column(DateTime, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# --------------------------------------------------------------------------- #
#  Budget alloué (pilotage budgétaire — Dashboard)
#  Montants en FCFA entiers. Un budget est alloué pour une année donnée.
# --------------------------------------------------------------------------- #
class Budget(Base):
    """Budget alloué à l'établissement pour une année (FCFA entiers)."""
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, index=True)
    annee = Column(Integer, nullable=False, index=True, unique=True)
    montant_alloue = Column(Integer, nullable=False, doc="Budget alloué pour l'année (FCFA)")
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
