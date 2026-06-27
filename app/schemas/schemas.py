# Pydantic schemas - CMSFP Platform
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime
from enum import Enum
from app.models.models import PatientStatus, ConsultationType


# --------------------------------------------------------------------------- #
#  Enums partagés (alignés sur les modèles)
# --------------------------------------------------------------------------- #
class StatutPaiement(str, Enum):
    EFFECTUE = "effectue"
    REMBOURSE = "rembourse"
    DIFFERE = "differe"


class ModePaiement(str, Enum):
    ESPECES = "especes"
    MOBILE_MONEY = "mobile_money"
    CHEQUE = "cheque"
    VIREMENT = "virement"
    CARTE = "carte"


# --------------------------------------------------------------------------- #
#  Patient
# --------------------------------------------------------------------------- #
class PatientBase(BaseModel):
    nom: str = Field(..., min_length=1, max_length=100, description="Nom du patient")
    prenom: str = Field(..., min_length=1, max_length=100, description="Prénom du patient")
    matricule: Optional[str] = Field(None, max_length=50, description="Matricule administratif")
    telephone: Optional[str] = Field(None, max_length=20, description="Numéro de téléphone")
    status: PatientStatus = Field(
        default=PatientStatus.NON_AYANT_DROIT,
        description="Statut: fonctionnaire | ayant_droit | non_ayant_droit",
    )
    qr_code: Optional[str] = Field(None, max_length=255, description="Code QR d'identification")
    date_naissance: Optional[datetime] = Field(None, description="Date de naissance")
    adresse: Optional[str] = Field(None, description="Adresse postale")


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    nom: Optional[str] = Field(None, min_length=1, max_length=100)
    prenom: Optional[str] = Field(None, min_length=1, max_length=100)
    matricule: Optional[str] = Field(None, max_length=50)
    telephone: Optional[str] = Field(None, max_length=20)
    status: Optional[PatientStatus] = None
    qr_code: Optional[str] = Field(None, max_length=255)
    date_naissance: Optional[datetime] = None
    adresse: Optional[str] = None


class PatientResponse(PatientBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class PatientListResponse(BaseModel):
    """Réponse paginée pour la liste des patients."""
    total: int
    skip: int
    limit: int
    patients: List[PatientResponse]


class QRCodeResponse(BaseModel):
    """Réponse de génération de QR code pour un patient."""
    patient_id: int
    qr_code: str = Field(..., description="Code UUID unique attribué au patient")
    image_base64: str = Field(..., description="Image PNG du QR code encodée en base64")
    image_format: str = Field("png", description="Format de l'image")
    generated: bool = Field(..., description="True si un nouveau code a été généré, False si l'existante a été retournée")


class PatientSearchResult(BaseModel):
    """Résultat de recherche dynamique de patients (typeahead)."""
    total: int
    query: str
    patients: List[PatientResponse]


# --------------------------------------------------------------------------- #
#  Consultation
# --------------------------------------------------------------------------- #
class ConsultationCreate(BaseModel):
    patient_id: int = Field(..., description="Identifiant du patient concerné")
    type: ConsultationType = Field(..., description="Type de prestation")
    notes: Optional[str] = Field(None, description="Notes cliniques")
    dictee_audio: Optional[str] = Field(None, description="Transcription audio / dictée")
    # Overrides optionnels — sinon calcul automatique via le moteur tarifaire
    montant_override: Optional[int] = Field(
        None, ge=0, description="Forcer le montant (outrepasse le moteur tarifaire)"
    )
    remise_override: Optional[int] = Field(None, ge=0, description="Forcer la remise")
    forcer_gratuit: Optional[bool] = Field(None, description="Forcer la gratuité")


class ConsultationUpdate(BaseModel):
    type: Optional[ConsultationType] = None
    notes: Optional[str] = None
    dictee_audio: Optional[str] = None
    remise: Optional[int] = Field(None, ge=0)
    montant: Optional[int] = Field(None, ge=0)
    gratuit: Optional[bool] = None


class ConsultationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    type: ConsultationType
    montant: int
    remise: int
    gratuit: bool
    notes: Optional[str] = None
    dictee_audio: Optional[str] = None
    created_at: datetime


class ConsultationWithTarifResponse(ConsultationResponse):
    """Réponse enrichie incluant le détail du calcul tarifaire."""
    details_tarif: Optional[str] = None


class ConsultationListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    consultations: List[ConsultationResponse]


class TarifPreviewRequest(BaseModel):
    """Aperçu du tarif sans créer de consultation."""
    patient_id: int
    type: ConsultationType


# --------------------------------------------------------------------------- #
#  Paiement
# --------------------------------------------------------------------------- #
class PaiementCreate(BaseModel):
    patient_id: int = Field(..., description="Patient concerné")
    consultation_id: Optional[int] = Field(None, description="Consultation associée (optionnel)")
    montant: int = Field(..., ge=0, description="Montant encaissé (FCFA — entier)")
    mode: ModePaiement = Field(default=ModePaiement.ESPECES, description="Mode de paiement")
    statut: StatutPaiement = Field(
        default=StatutPaiement.EFFECTUE, description="Statut initial du paiement"
    )


class PaiementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: int
    consultation_id: Optional[int] = None
    montant: int
    remise_appliquee: int
    mode: str
    statut: str
    motif_remboursement: Optional[str] = None
    date_paiement: datetime


class PaiementListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    paiements: List[PaiementResponse]


class RemboursementRequest(BaseModel):
    motif: str = Field(..., min_length=3, max_length=255, description="Motif du remboursement")


class DiffererRequest(BaseModel):
    motif: Optional[str] = Field(None, max_length=255, description="Motif du report (optionnel)")


class PaiementStatistiques(BaseModel):
    """Statistiques agrégées des paiements."""
    total_encaisse: int
    total_rembourse: int
    total_differe: int
    nombre_effectues: int
    nombre_rembourses: int
    nombre_differes: int
    nombre_total: int


# --------------------------------------------------------------------------- #
#  Caisse traçable (Défi 3)
# --------------------------------------------------------------------------- #
from app.models.models import TypeOperationCaisse, CasParticulierCaisse


class CaisseOperationCreate(BaseModel):
    """Création d'une entrée dans le journal de caisse (immuable)."""
    type_operation: TypeOperationCaisse = Field(
        ...,
        description="ouverture | encaissement | remboursement | cloture | renonciation | regularisation_differe",
    )
    montant: int = Field(0, ge=0, description="Montant de l'opération (FCFA — entier)")

    # Champs spécifiques à l'ouverture / clôture
    montant_ouverture: Optional[int] = Field(
        None, ge=0, description="Fond de caisse à l'ouverture (type=ouverture)"
    )
    montant_cloture: Optional[int] = Field(
        None, ge=0, description="Montant réellement compté à la clôture (type=cloture)"
    )

    # Traçabilité métier
    patient_id: Optional[int] = Field(None, description="Patient concerné (optionnel)")
    paiement_id: Optional[int] = Field(None, description="Paiement associé (optionnel)")
    cas_particulier: CasParticulierCaisse = Field(
        default=CasParticulierCaisse.AUCUN,
        description="aucun | renonciation_apres_paiement | remboursement_panne | paiement_differe",
    )
    motif: Optional[str] = Field(None, max_length=255, description="Motif / justification")
    operateur: Optional[str] = Field(None, max_length=100, description="Identité du caissier")
    notes: Optional[str] = Field(None, description="Notes libres")


class CaisseOperationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    type_operation: TypeOperationCaisse
    montant: int
    montant_ouverture: Optional[int] = None
    montant_theorique: Optional[int] = None
    montant_cloture: Optional[int] = None
    operations: int
    ecarts: int
    patient_id: Optional[int] = None
    paiement_id: Optional[int] = None
    cas_particulier: CasParticulierCaisse
    motif: Optional[str] = None
    operateur: Optional[str] = None
    notes: Optional[str] = None
    hash_precedent: Optional[str] = None
    hash_courant: str
    horodatage: datetime


class CaisseJournalListResponse(BaseModel):
    """Réponse paginée du journal de caisse."""
    total: int
    skip: int
    limit: int
    operations: List[CaisseOperationResponse]


class CaisseJournalDuJourResponse(BaseModel):
    """Journal de caisse du jour avec synthèse."""
    date: str
    total_operations: int
    montant_ouverture: Optional[int] = None
    montant_cloture: Optional[int] = None
    montant_theorique: Optional[int] = None
    ecarts: int
    total_encaisse: int
    total_rembourse: int
    operations: List[CaisseOperationResponse]


class CaisseVerificationResponse(BaseModel):
    """Résultat de la vérification d'intégrité de la chaîne de hachage."""
    integre: bool
    total_verifiees: int
    premier_hash_precedent: Optional[str] = None
    derniere_operation_id: Optional[int] = None
    anomalies: List[str] = []


# --------------------------------------------------------------------------- #
#  Circuit Conseil de santé (Défi 4)
# --------------------------------------------------------------------------- #
from app.models.models import StatutDossierConseil


class DossierConseilSanteCreate(BaseModel):
    """Création d'un nouveau dossier du circuit Conseil de santé."""
    patient_id: Optional[int] = Field(None, description="Patient enregistré (optionnel)")
    nom_patient: str = Field(..., min_length=1, max_length=100, description="Nom du patient")
    prenom_patient: str = Field(..., min_length=1, max_length=100, description="Prénom du patient")
    matricule_patient: Optional[str] = Field(None, max_length=50, description="Matricule de l'agent")
    motif: str = Field(..., min_length=3, description="Motif de la demande d'évacuation")
    specialiste: Optional[str] = Field(None, max_length=200, description="Spécialiste assigné")
    destination: Optional[str] = Field(None, max_length=200, description="Pays/ville de prise en charge")
    montant_estime: Optional[int] = Field(None, ge=0, description="Estimation du coût (FCFA)")
    observations: Optional[str] = Field(None, description="Observations libres")


class DossierConseilSanteResponse(BaseModel):
    """Réponse détaillée d'un dossier Conseil de santé."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    numero_dossier: str
    patient_id: Optional[int] = None
    nom_patient: str
    prenom_patient: str
    matricule_patient: Optional[str] = None
    motif: str
    specialiste: Optional[str] = None
    destination: Optional[str] = None
    statut: StatutDossierConseil
    date_soumission: Optional[datetime] = None
    date_etude: Optional[datetime] = None
    date_ventilation: Optional[datetime] = None
    date_evacuation: Optional[datetime] = None
    date_retour: Optional[datetime] = None
    date_controle_tresor: Optional[datetime] = None
    date_cloture: Optional[datetime] = None
    montant_estime: Optional[int] = None
    montant_reel: Optional[int] = None
    observations: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DossierConseilSanteListResponse(BaseModel):
    """Réponse paginée de la liste des dossiers Conseil de santé."""
    total: int
    skip: int
    limit: int
    dossiers: List[DossierConseilSanteResponse]


class TransitionStatutRequest(BaseModel):
    """
    Demande de transition de statut dans le circuit Conseil de santé.

    Champs optionnels permettant de renseigner les informations métier
    requises à certaines étapes du circuit (spécialiste, destination,
    montant réel, etc.).
    """
    nouveau_statut: StatutDossierConseil = Field(..., description="Nouveau statut cible")
    specialiste: Optional[str] = Field(None, max_length=200, description="Spécialiste (requis pour ventilation)")
    destination: Optional[str] = Field(None, max_length=200, description="Destination (requis pour évacuation)")
    montant_reel: Optional[int] = Field(None, ge=0, description="Montant réel contrôlé par le Trésor (FCFA)")
    observations: Optional[str] = Field(None, description="Observations ajoutées à la transition")


# --------------------------------------------------------------------------- #
#  Dashboard de pilotage budgétaire
# --------------------------------------------------------------------------- #
class RecettePoint(BaseModel):
    """Un point de la série des recettes."""
    periode: str = Field(..., description="Libellé de la période (date ISO, semaine, mois)")
    total: int = Field(..., description="Somme des paiements encaissés sur la période (FCFA)")
    nombre: int = Field(..., description="Nombre de paiements sur la période")


class RecettesResponse(BaseModel):
    """Recettes agrégées par jour / semaine / mois."""
    periode: str = Field(..., description="Granularité: jour | semaine | mois")
    date_debut: Optional[str] = None
    date_fin: Optional[str] = None
    total_general: int = Field(..., description="Total des recettes sur la plage (FCFA)")
    nombre_paiements: int = Field(..., description="Nombre total de paiements encaissés")
    series: List[RecettePoint] = Field(default_factory=list)


class ConsultationTypeStat(BaseModel):
    """Statistiques d'un type de consultation."""
    type: str
    nombre: int
    montant_total: int = Field(..., description="Somme des montants (FCFA)")


class ConsultationsDashboardResponse(BaseModel):
    """Nombre de consultations par type."""
    total_consultations: int
    par_type: List[ConsultationTypeStat]


class BudgetSetRequest(BaseModel):
    """Définition du budget alloué pour une année."""
    annee: int = Field(..., description="Année (ex. 2026)")
    montant_alloue: int = Field(..., ge=0, description="Budget alloué (FCFA entiers)")
    description: Optional[str] = Field(None, max_length=255)


class BudgetResponse(BaseModel):
    """Budget alloué vs recettes cumulées pour une année."""
    annee: int
    budget_alloue: int = Field(..., description="Budget alloué (FCFA) — 0 si non défini")
    recettes_cumulees: int = Field(..., description="Recettes encaissées sur l'année (FCFA)")
    taux_realisation: float = Field(..., description="Recettes / budget * 100 (0 si pas de budget)")
    ecart: int = Field(..., description="recettes_cumulees - budget_alloue (FCFA)")
    depassement: bool = Field(..., description="True si recettes > budget")
    budget_defini: bool = Field(..., description="True si un budget est enregistré pour l'année")


class CaisseClotureInfo(BaseModel):
    """Synthèse d'une clôture de caisse."""
    id: int
    horodatage: datetime
    montant_ouverture: Optional[int] = None
    montant_theorique: Optional[int] = None
    montant_cloture: Optional[int] = None
    ecart: int


class CaisseDashboardResponse(BaseModel):
    """Synthèse caisse pour le dashboard."""
    session_ouverte: bool
    montant_ouverture: Optional[int] = None
    operations_cumul: int = 0
    montant_theorique: Optional[int] = None
    derniere_cloture: Optional[CaisseClotureInfo] = None
    total_encaisse_session: int = 0
    total_rembourse_session: int = 0
    integrite_journal: bool = True
    total_anomalies: int = 0
    anomalies: List[str] = Field(default_factory=list)
    clotures_recentes: List[CaisseClotureInfo] = Field(default_factory=list)


class AlerteItem(BaseModel):
    """Une alerte du dashboard."""
    niveau: str = Field(..., description="critique | attention | info")
    categorie: str = Field(..., description="budget | caisse | paiements | systeme")
    titre: str
    message: str
    valeur: Optional[int] = None


class AlertesResponse(BaseModel):
    """Ensemble des alertes du dashboard."""
    total: int
    alertes: List[AlerteItem]


# --------------------------------------------------------------------------- #
#  IA Audio — Dictée consultation, Résumé patient, CR réunions
# --------------------------------------------------------------------------- #


class DicteeConsultationResponse(BaseModel):
    """Résultat de la dictée vocale d'une consultation (STT)."""
    consultation_id: int
    dictee_audio: str = Field(..., description="Transcription générée par STT")
    transcribeur: str = Field(..., description="openai_whisper | faster_whisper_local")
    taille_audio_octets: int
    ancienne_valeur: Optional[str] = Field(
        None, description="Ancienne transcription écrasée (si elle existait)"
    )


class ResumePatientRequest(BaseModel):
    """Options de génération du résumé audio d'un patient."""
    voix: Optional[str] = Field(
        "fr-FR-HenriNeural",
        description="Voix edge-tts (fr-FR-HenriNeural homme, fr-FR-DeniseNeural femme, …)",
    )


class ResumePatientResponse(BaseModel):
    """Synthèse vocale du dossier patient (texte + disponibilité audio)."""
    patient_id: int
    nom: str
    prenom: str
    status: str
    resume_texte: str = Field(..., description="Texte synthétisé décrivant le patient")
    audio_disponible: bool = Field(..., description="True si l'audio MP3 peut être généré")
    audio_url: Optional[str] = Field(
        None, description="URL relative pour récupérer le flux MP3 (si audio_disponible)"
    )
    nombre_consultations: int
    total_paye: int = Field(..., description="Total encaissé pour ce patient (FCFA)")


class ReunionResponse(BaseModel):
    """Réunion enregistrée avec son compte-rendu IA."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    titre: str
    date_reunion: datetime
    participants: Optional[str] = Field(
        None, description="Participants (liste.join ', ') — extraits ou fournis"
    )
    transcription_audio: Optional[str] = Field(None, description="Transcription STT brute")
    compte_rendu: Optional[str] = Field(
        None, description="Compte-rendu structuré (résumé + actions)"
    )
    created_at: datetime


class ReunionListResponse(BaseModel):
    """Liste paginée des réunions."""
    total: int
    skip: int
    limit: int
    reunions: List[ReunionResponse]


class ReunionDetailResponse(ReunionResponse):
    """Détail enrichi d'une réunion (participants et actions parsés)."""
    participants_list: List[str] = Field(default_factory=list)
    actions_list: List[str] = Field(default_factory=list)
    resume: Optional[str] = Field(None, description="Résumé extrait du compte-rendu")
    transcribeur: Optional[str] = Field(None, description="Moteur STT utilisé")


class ReunionCreateResponse(BaseModel):
    """Réponse à la création d'une réunion (upload audio + traitement IA)."""
    reunion: ReunionDetailResponse
    transcribeur: str
    taille_audio_octets: int
    resume: str
    participants: List[str]
    actions: List[str]
    note: Optional[str] = Field(
        None, description="Information sur les stratégies utilisées / dégradations"
    )
