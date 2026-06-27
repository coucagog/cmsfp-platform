# API endpoints — IA Audio (CMSFP Platform)
#
# Trois fonctionnalités IA Audio, toutes protégées par JWT :
#
#   1. Dictée de consultation  → POST /api/v1/audio/dictee
#      Upload audio (multipart) + consultation_id → transcription STT sauvegardée
#      dans Consultation.dictee_audio.
#
#   2. Résumé audio patient    → GET  /api/v1/audio/resume/{patient_id}
#      Synthétise le dossier du patient en texte + audio MP3 (edge-tts).
#      ?audio=true renvoie directement le flux MP3 ; sinon JSON avec audio_disponible.
#
#   3. Compte-rendu de réunion → POST /api/v1/audio/reunion
#      Upload audio de réunion → transcription + résumé + participants + actions,
#      enregistré dans la table Reunion.
#      + GET /api/v1/reunions        (liste paginée)
#      + GET /api/v1/reunions/{id}   (détail avec participants/actions parsés)
from __future__ import annotations

import os
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.models import Patient, Consultation, Paiement, Reunion
from app.api.auth import get_current_user
from app.schemas.schemas import (
    DicteeConsultationResponse,
    ResumePatientResponse,
    ReunionResponse,
    ReunionListResponse,
    ReunionDetailResponse,
    ReunionCreateResponse,
)
from app.services import audio as audio_service

logger = logging.getLogger("cmsfp.api.audio")

# Router principal — endpoints /audio/*
audio_router = APIRouter(
    prefix="/api/v1/audio",
    tags=["IA Audio"],
    dependencies=[Depends(get_current_user)],
)

# Router secondaire — endpoints /reunions/* (consultation des CR)
reunions_router = APIRouter(
    prefix="/api/v1/reunions",
    tags=["Réunions (CR IA)"],
    dependencies=[Depends(get_current_user)],
)

# Taille maximale d'upload audio — configurable via .env (A10).
MAX_AUDIO_BYTES = settings.AUDIO_MAX_UPLOAD_BYTES

# Extensions audio acceptées (vérification souple sur le nom de fichier).
_AUDIO_EXT = {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".oga", ".flac", ".opus", ".wma"}


def _detect_stt_engine() -> str:
    """Indique quel moteur STT sera utilisé (pour la transparence dans les réponses)."""
    if os.getenv("OPENAI_API_KEY") or os.getenv("CMSFP_OPENAI_API_KEY"):
        return "openai_whisper"
    return "faster_whisper_local"


def _validate_audio_upload(file: UploadFile, raw: bytes) -> None:
    """Vérifie la taille et l'extension d'un upload audio. Lève 413/400."""
    if len(raw) == 0:
        raise HTTPException(400, "Le fichier audio est vide.")
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(
            413,
            f"Fichier audio trop volumineux ({len(raw)} octets) ; maximum {MAX_AUDIO_BYTES} octets (50 Mo).",
        )
    name = (file.filename or "").lower()
    if not any(name.endswith(ext) for ext in _AUDIO_EXT):
        # On n'échoue pas durcement (le type MIME peut manquer) mais on avertit.
        logger.info("Upload audio avec extension inattendue : %s", file.filename)


def _pre_check_upload_size(file: UploadFile, request: Request) -> None:
    """
    OWASP A10 : vérifie la taille du fichier AVANT de lire tout le contenu
    en mémoire. Utilise l'attribut ``size`` de UploadFile (Starlette ≥ 0.36)
    puis l'en-tête Content-Length comme repli. Lève 413 si la taille dépasse
    le maximum autorisé.

    Cette vérification préventive évite l'épuisement mémoire (DoS) lorsqu'un
    client envoie un fichier volumineux.
    """
    declared_size: Optional[int] = None
    # Starlette >= 0.36 expose file.size (taille réelle du fichier temporaire).
    size_attr = getattr(file, "size", None)
    if size_attr is not None:
        declared_size = int(size_attr)
    if declared_size is None:
        cl = request.headers.get("content-length")
        if cl and cl.isdigit():
            declared_size = int(cl)
    if declared_size is not None and declared_size > MAX_AUDIO_BYTES:
        raise HTTPException(
            413,
            f"Fichier audio trop volumineux ({declared_size} octets) ; "
            f"maximum {MAX_AUDIO_BYTES} octets ({MAX_AUDIO_BYTES // (1024 * 1024)} Mo).",
        )


# --------------------------------------------------------------------------- #
#  1. DICTÉE CONSULTATION — POST /api/v1/audio/dictee
# --------------------------------------------------------------------------- #
@audio_router.post(
    "/dictee",
    response_model=DicteeConsultationResponse,
    summary="Dictée vocale d'une consultation (Speech-to-Text)",
)
async def dictee_consultation(
    request: Request,
    consultation_id: int = Form(..., description="Identifiant de la consultation à enrichir"),
    file: UploadFile = File(..., description="Fichier audio (webm/wav/mp3/m4a/ogg…)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Reçoit un fichier audio (dictée du praticien), le transcrit via Whisper
    (API OpenAI si clé configurée, sinon moteur local faster-whisper) et
    enregistre la transcription dans `Consultation.dictee_audio`.

    La transcription précédente, si elle existait, est écrasée.
    """
    # 1. Vérifier que la consultation existe.
    row = await db.execute(select(Consultation).where(Consultation.id == consultation_id))
    consultation = row.scalars().first()
    if not consultation:
        raise HTTPException(404, f"Consultation #{consultation_id} introuvable.")

    # 2. A10 — vérifier la taille AVANT de lire tout le fichier en mémoire.
    _pre_check_upload_size(file, request)

    # 3. Lire et valider l'audio.
    raw = await file.read()
    _validate_audio_upload(file, raw)

    # 4. Transcription STT.
    engine = _detect_stt_engine()
    try:
        transcription = await audio_service.transcribe_audio(raw, file.filename or "audio.webm")
    except audio_service.AudioServiceError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        logger.exception("Erreur STT inattendue")
        raise HTTPException(500, f"Erreur de transcription : {type(e).__name__}: {e}")

    # 5. Persister dans la consultation.
    ancienne = consultation.dictee_audio
    consultation.dictee_audio = transcription
    await db.commit()
    await db.refresh(consultation)

    return DicteeConsultationResponse(
        consultation_id=consultation.id,
        dictee_audio=transcription,
        transcribeur=engine,
        taille_audio_octets=len(raw),
        ancienne_valeur=ancienne,
    )


# --------------------------------------------------------------------------- #
#  2. RÉSUMÉ AUDIO PATIENT — GET /api/v1/audio/resume/{patient_id}
# --------------------------------------------------------------------------- #
def _build_patient_summary(patient: Patient, nb_consultations: int, total_paye: int,
                           derniere_consultation: Optional[Consultation]) -> str:
    """Construit le texte de synthèse du dossier patient (français)."""
    status_label = {
        "fonctionnaire": "fonctionnaire",
        "ayant_droit": "ayant droit",
        "non_ayant_droit": "non ayant droit",
    }.get(getattr(patient.status, "value", str(patient.status)), str(patient.status))

    parts = [
        f"Résumé du dossier patient. "
        f"Patient : {patient.prenom} {patient.nom}.",
        f"Statut : {status_label}.",
    ]
    if patient.matricule:
        parts.append(f"Matricule : {patient.matricule}.")
    if patient.telephone:
        parts.append(f"Téléphone : {patient.telephone}.")
    if patient.date_naissance:
        parts.append(f"Date de naissance : {patient.date_naissance.strftime('%d/%m/%Y')}.")

    parts.append(f"Nombre total de consultations enregistrées : {nb_consultations}.")
    if total_paye > 0:
        parts.append(f"Total encaissé pour ce patient : {total_paye} francs CFA.")

    if derniere_consultation is not None:
        type_label = getattr(derniere_consultation.type, "value", str(derniere_consultation.type))
        parts.append(
            f"Dernière consultation : {type_label}, "
            f"le {derniere_consultation.created_at.strftime('%d/%m/%Y')}."
        )
        if derniere_consultation.dictee_audio:
            # Inclure un extrait de la dernière dictée (limité pour la synthèse).
            extrait = derniere_consultation.dictee_audio.strip()
            if len(extrait) > 400:
                extrait = extrait[:400].rsplit(" ", 1)[0] + "…"
            parts.append(f"Notes de la dernière consultation : {extrait}.")
        elif derniere_consultation.notes:
            extrait = derniere_consultation.notes.strip()
            if len(extrait) > 400:
                extrait = extrait[:400].rsplit(" ", 1)[0] + "…"
            parts.append(f"Notes de la dernière consultation : {extrait}.")
    elif nb_consultations == 0:
        parts.append("Aucune consultation enregistrée à ce jour.")

    return " ".join(parts)


@audio_router.get(
    "/resume/{patient_id}",
    response_model=ResumePatientResponse,
    summary="Synthèse vocale du dossier patient",
)
async def resume_patient(
    patient_id: int,
    audio: bool = Query(False, description="Si true, renvoie directement le flux MP3 (audio/mpeg)"),
    voix: str = Query("fr-FR-HenriNeural", description="Voix edge-tts (fr-FR-HenriNeural, fr-FR-DeniseNeural…)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Synthétise les informations clés du patient en un résumé textuel, puis
    génère (optionnellement) un fichier audio MP3 via edge-tts.

    - Sans `?audio=true` : retourne un JSON `{ resume_texte, audio_disponible, audio_url }`.
    - Avec `?audio=true` : retourne directement le flux MP3 (`audio/mpeg`).
    """
    # 1. Charger le patient.
    row = await db.execute(select(Patient).where(Patient.id == patient_id))
    patient = row.scalars().first()
    if not patient:
        raise HTTPException(404, f"Patient #{patient_id} introuvable.")

    # 2. Agréger les données (nombre de consultations + total payé + dernière consultation).
    nb_cons = (await db.execute(
        select(func.count(Consultation.id)).where(Consultation.patient_id == patient_id)
    )).scalar_one()

    total_paye = (await db.execute(
        select(func.coalesce(func.sum(Paiement.montant), 0))
        .where(Paiement.patient_id == patient_id)
        .where(Paiement.statut == "effectue")
    )).scalar_one() or 0

    derniere = None
    if nb_cons > 0:
        r = await db.execute(
            select(Consultation)
            .where(Consultation.patient_id == patient_id)
            .order_by(Consultation.created_at.desc())
            .limit(1)
        )
        derniere = r.scalars().first()

    # 3. Construire le texte de synthèse.
    resume_texte = _build_patient_summary(patient, nb_cons, total_paye, derniere)

    # 4. Mode audio : générer et streamer le MP3.
    if audio:
        try:
            mp3 = await audio_service.synthesize_speech(resume_texte, voice=voix)
        except audio_service.AudioServiceError as e:
            raise HTTPException(502, str(e))
        except Exception as e:
            logger.exception("Erreur TTS inattendue")
            raise HTTPException(500, f"Erreur de synthèse vocale : {type(e).__name__}: {e}")
        return Response(
            content=mp3,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": f'inline; filename="resume_patient_{patient_id}.mp3"',
                "Accept-Ranges": "bytes",
            },
        )

    # 5. Mode JSON (par défaut).
    return ResumePatientResponse(
        patient_id=patient.id,
        nom=patient.nom,
        prenom=patient.prenom,
        status=getattr(patient.status, "value", str(patient.status)),
        resume_texte=resume_texte,
        audio_disponible=True,  # edge-tts est disponible (testé au démarrage du service)
        audio_url=f"/api/v1/audio/resume/{patient_id}?audio=true&voix={voix}",
        nombre_consultations=nb_cons,
        total_paye=int(total_paye),
    )


# --------------------------------------------------------------------------- #
#  3. COMPTE-RENDU DE RÉUNION — POST /api/v1/audio/reunion
# --------------------------------------------------------------------------- #
def _format_compte_rendu(resume: str, participants: list[str], actions: list[str]) -> str:
    """Formate le compte-rendu structuré stocké en base (Texte)."""
    lines = ["RÉSUMÉ:", resume.strip() or "(résumé indisponible)", ""]
    if participants:
        lines.append("PARTICIPANTS:")
        lines.extend(f"- {p}" for p in participants)
        lines.append("")
    else:
        lines.append("PARTICIPANTS: (non identifiés automatiquement)")
        lines.append("")
    if actions:
        lines.append("ACTIONS:")
        lines.extend(f"- {a}" for a in actions)
    else:
        lines.append("ACTIONS: (aucune action explicite détectée)")
    return "\n".join(lines)


@audio_router.post(
    "/reunion",
    response_model=ReunionCreateResponse,
    status_code=201,
    summary="Compte-rendu de réunion (transcription + résumé IA)",
)
async def creer_reunion(
    request: Request,
    titre: str = Form(..., min_length=3, max_length=200, description="Titre de la réunion"),
    fichier: UploadFile = File(..., description="Enregistrement audio de la réunion"),
    participants: Optional[str] = Form(
        None, description="Participants (liste séparée par virgules, optionnel — sinon extraction auto)"
    ),
    date_reunion: Optional[str] = Form(
        None, description="Date de la réunion (ISO 8601, ex. 2026-06-26T14:30). Défaut : maintenant."
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload d'un enregistrement audio de réunion :

    1. Transcription via Whisper (OpenAI ou faster-whisper local).
    2. Résumé automatique (LLM OpenAI si clé, sinon résumé extractif local).
    3. Extraction des participants (heuristiques sur la transcription) fusionnés
       avec la liste fournie manuellement.
    4. Extraction des actions / tâches identifiables.

    Le tout est persisté dans la table `Reunion` (transcription_audio,
    compte_rendu, participants).
    """
    # 1. A10 — vérifier la taille AVANT de lire tout le fichier en mémoire.
    _pre_check_upload_size(fichier, request)

    # 2. Lire et valider l'audio.
    raw = await fichier.read()
    _validate_audio_upload(fichier, raw)

    # 3. Transcription STT.
    engine = _detect_stt_engine()
    try:
        transcription = await audio_service.transcribe_audio(raw, fichier.filename or "reunion.webm")
    except audio_service.AudioServiceError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        logger.exception("Erreur STT réunion")
        raise HTTPException(500, f"Erreur de transcription : {type(e).__name__}: {e}")

    # 4. Résumé + extraction.
    try:
        cr = audio_service.build_reunion_compte_rendu(transcription)
    except Exception as e:
        logger.exception("Erreur construction compte-rendu")
        raise HTTPException(500, f"Erreur de synthèse du compte-rendu : {type(e).__name__}: {e}")

    resume = cr["resume"]
    extracted_participants = cr["participants"]
    actions = cr["actions"]

    # 4. Fusionner participants fournis manuellement + extraits.
    final_participants: list[str] = []
    seen = set()
    if participants and participants.strip():
        for p in participants.split(","):
            p = p.strip()
            if p and p.lower() not in seen:
                seen.add(p.lower())
                final_participants.append(p)
    for p in extracted_participants:
        if p.lower() not in seen:
            seen.add(p.lower())
            final_participants.append(p)

    # 5. Date de réunion.
    parsed_date: Optional[datetime] = None
    if date_reunion:
        try:
            parsed_date = datetime.fromisoformat(date_reunion.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, f"Format de date invalide : '{date_reunion}'. Attendu : ISO 8601 (ex. 2026-06-26T14:30).")

    # 6. Formater et persister.
    compte_rendu = _format_compte_rendu(resume, final_participants, actions)
    reunion = Reunion(
        titre=titre,
        date_reunion=parsed_date or datetime.utcnow(),
        participants=", ".join(final_participants) if final_participants else None,
        transcription_audio=transcription,
        compte_rendu=compte_rendu,
    )
    db.add(reunion)
    await db.commit()
    await db.refresh(reunion)

    # 7. Note de transparence sur les stratégies utilisées.
    notes = []
    if engine == "faster_whisper_local":
        notes.append("Transcription via moteur local faster-whisper (modèle tiny, hors-ligne).")
    else:
        notes.append("Transcription via API OpenAI Whisper.")
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("CMSFP_OPENAI_API_KEY")):
        notes.append("Résumé extractif local (aucune clé LLM configurée).")

    return ReunionCreateResponse(
        reunion=ReunionDetailResponse(
            id=reunion.id,
            titre=reunion.titre,
            date_reunion=reunion.date_reunion,
            participants=reunion.participants,
            transcription_audio=reunion.transcription_audio,
            compte_rendu=reunion.compte_rendu,
            created_at=reunion.created_at,
            participants_list=final_participants,
            actions_list=actions,
            resume=resume,
            transcribeur=engine,
        ),
        transcribeur=engine,
        taille_audio_octets=len(raw),
        resume=resume,
        participants=final_participants,
        actions=actions,
        note=" ".join(notes) if notes else None,
    )


# --------------------------------------------------------------------------- #
#  4 & 5. CONSULTATION DES RÉUNIONS — GET /api/v1/reunions
# --------------------------------------------------------------------------- #
def _parse_reunion_detail(reunion: Reunion) -> ReunionDetailResponse:
    """Reconstruit les listes participants/actions à partir de la transcription."""
    participants_list = audio_service.extract_participants(reunion.transcription_audio or "")
    actions_list = audio_service.extract_actions(reunion.transcription_audio or "")
    # Si les participants étaient fournis manuellement, les utiliser en priorité.
    if reunion.participants:
        participants_list = [p.strip() for p in reunion.participants.split(",") if p.strip()]

    # Extraire le résumé du compte-rendu formaté (section RÉSUMÉ:).
    resume = None
    if reunion.compte_rendu:
        cr_lines = reunion.compte_rendu.split("\n")
        if cr_lines and cr_lines[0].strip().upper().startswith("RÉSUMÉ"):
            # Les lignes entre "RÉSUMÉ:" et la prochaine section vide/section.
            resume_lines = []
            for line in cr_lines[1:]:
                if line.strip().upper() in ("PARTICIPANTS:", "ACTIONS:") or (
                    line.strip() == "" and resume_lines
                ):
                    break
                if line.strip():
                    resume_lines.append(line.strip())
            resume = " ".join(resume_lines) if resume_lines else None

    return ReunionDetailResponse(
        id=reunion.id,
        titre=reunion.titre,
        date_reunion=reunion.date_reunion,
        participants=reunion.participants,
        transcription_audio=reunion.transcription_audio,
        compte_rendu=reunion.compte_rendu,
        created_at=reunion.created_at,
        participants_list=participants_list,
        actions_list=actions_list,
        resume=resume,
        transcribeur=None,  # non stocké historiquement
    )


@reunions_router.get("", response_model=ReunionListResponse, summary="Liste des réunions")
async def lister_reunions(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Liste paginée des réunions (ordre antéchronologique)."""
    total = (await db.execute(select(func.count(Reunion.id)))).scalar_one()
    rows = await db.execute(
        select(Reunion).order_by(Reunion.date_reunion.desc()).offset(skip).limit(limit)
    )
    reunions = rows.scalars().all()
    return ReunionListResponse(
        total=total,
        skip=skip,
        limit=limit,
        reunions=[ReunionResponse.model_validate(r) for r in reunions],
    )


@reunions_router.get("/{reunion_id}", response_model=ReunionDetailResponse, summary="Détail d'une réunion")
async def detail_reunion(reunion_id: int, db: AsyncSession = Depends(get_db)):
    """Détail d'une réunion : transcription, compte-rendu, participants et actions parsés."""
    row = await db.execute(select(Reunion).where(Reunion.id == reunion_id))
    reunion = row.scalars().first()
    if not reunion:
        raise HTTPException(404, f"Réunion #{reunion_id} introuvable.")
    return _parse_reunion_detail(reunion)


@reunions_router.delete("/{reunion_id}", status_code=204, summary="Supprimer une réunion")
async def supprimer_reunion(reunion_id: int, db: AsyncSession = Depends(get_db)):
    """Supprime une réunion et son compte-rendu."""
    row = await db.execute(select(Reunion).where(Reunion.id == reunion_id))
    reunion = row.scalars().first()
    if not reunion:
        raise HTTPException(404, f"Réunion #{reunion_id} introuvable.")
    await db.delete(reunion)
    await db.commit()
    return None
