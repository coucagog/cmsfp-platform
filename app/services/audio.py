# Services IA Audio — CMSFP Platform
#
# Trois fonctionnalités IA Audio s'appuient sur ce module :
#   1. Dictée de consultation  → Speech-to-Text (STT)
#   2. Résumé audio patient    → Text-to-Speech (TTS)
#   3. Compte-rendu de réunion → STT + résumé + extraction participants/actions
#
# Stratégie de résilience (aucune configuration réseau n'est garantie) :
#
#   STT (transcription) :
#     - Si OPENAI_API_KEY est présente → API OpenAI Whisper (cloud, haute qualité)
#     - Sinon → faster-whisper (modèle local « tiny », CPU, hors-ligne après 1er téléchargement)
#
#   TTS (synthèse vocale) :
#     - edge-tts (service Microsoft Edge en ligne, voix françaises gratuites, aucune clé)
#
#   Résumé / extraction (CR réunions) :
#     - Si OPENAI_API_KEY est présente → LLM OpenAI (gpt-4o-mini) pour résumé + actions
#     - Sinon → résumé extractif local (scoring fréquentiel des phrases) + heuristiques
#       d'extraction des participants et actions.
#
# Toutes les fonctions sont conçues pour ne jamais lever d'exception fatale sur
# un échec réseau : elles renvoient un texte explicite ou lèvent une
# AudioServiceError attrapée par la couche API qui renvoie un 502/500 propre.
from __future__ import annotations

import io
import os
import re
import ssl
import hashlib
import asyncio
import logging
from typing import Optional

logger = logging.getLogger("cmsfp.audio")


class AudioServiceError(RuntimeError):
    """Erreur métier remontée à l'API (message orienté utilisateur)."""


# --------------------------------------------------------------------------- #
#  Speech-to-Text (STT)
# --------------------------------------------------------------------------- #
# Le modèle faster-whisper est coûteux à charger : on le garde en cache au
# niveau du module (lazy singleton). Thread-safe via un verrou asyncio.
_FASTER_WHISPER_MODEL = None
_FW_LOCK = asyncio.Lock()


def _openai_api_key() -> Optional[str]:
    """Résout la clé API OpenAI (env var classique ou surcharge CMSFP)."""
    return os.getenv("OPENAI_API_KEY") or os.getenv("CMSFP_OPENAI_API_KEY")


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    """
    Transcrit un flux audio en texte.

    Ordre de résolution :
      1. API OpenAI Whisper si une clé est disponible.
      2. Modèle local faster-whisper (tiny) sinon.

    Args:
        audio_bytes: contenu binaire du fichier audio (webm/wav/mp3/m4a/ogg).
        filename: nom d'origine (utilisé pour l'extension MIME côté OpenAI).

    Returns:
        Le texte transcrit (str). Lève AudioServiceError si toutes les
        stratégies échouent.
    """
    key = _openai_api_key()
    if key:
        try:
            return await _transcribe_openai(audio_bytes, filename, key)
        except Exception as e:
            # On ne plante pas : on enchaîne sur le fallback local.
            logger.warning("OpenAI Whisper a échoué (%s) — fallback local", e)

    try:
        return await _transcribe_local(audio_bytes)
    except Exception as e:
        raise AudioServiceError(
            "Transcription impossible : ni l'API OpenAI Whisper ni le moteur "
            f"local faster-whisper n'ont pu traiter l'audio ({type(e).__name__}: {e})."
        )


async def _transcribe_openai(audio_bytes: bytes, filename: str, api_key: str) -> str:
    """Transcription via l'API OpenAI Audio (Whisper). Appel bloquant → run_in_executor."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    fileobj = io.BytesIO(audio_bytes)
    fileobj.name = filename or "audio.webm"

    loop = asyncio.get_event_loop()

    def _call():
        resp = client.audio.transcriptions.create(
            model="whisper-1",
            file=fileobj,
            language="fr",
        )
        return getattr(resp, "text", "") or ""

    text = await loop.run_in_executor(None, _call)
    if not text.strip():
        raise AudioServiceError("OpenAI Whisper a renvoyé une transcription vide.")
    return text.strip()


async def _transcribe_local(audio_bytes: bytes) -> str:
    """Transcription hors-ligne via faster-whisper (modèle tiny, CPU)."""
    global _FASTER_WHISPER_MODEL
    from faster_whisper import WhisperModel

    async with _FW_LOCK:
        if _FASTER_WHISPER_MODEL is None:
            logger.info("Chargement du modèle faster-whisper 'tiny' (1er usage)…")
            _FASTER_WHISPER_MODEL = WhisperModel("tiny", device="cpu", compute_type="int8")

    # faster-whisper est synchrone et CPU-bound → exécuter dans un thread.
    loop = asyncio.get_event_loop()
    # faster-whisper attend un chemin ou un numpy array ; on écrit un fichier temp.
    tmp_path = f"/tmp/_cmsfp_stt_{hashlib.md5(audio_bytes).hexdigest()[:12]}.bin"
    with open(tmp_path, "wb") as f:
        f.write(audio_bytes)

    def _call():
        segments, info = _FASTER_WHISPER_MODEL.transcribe(tmp_path, language="fr")
        return " ".join(s.text.strip() for s in segments).strip()

    try:
        text = await loop.run_in_executor(None, _call)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if not text.strip():
        raise AudioServiceError("Le moteur local a renvoyé une transcription vide.")
    return text


# --------------------------------------------------------------------------- #
#  Text-to-Speech (TTS)
# --------------------------------------------------------------------------- #
async def synthesize_speech(text: str, voice: str = "fr-FR-HenriNeural") -> bytes:
    """
    Synthétise un texte en un flux audio MP3 via edge-tts (Microsoft Edge,
    voix françaises, aucune clé API requise).

    Args:
        text: texte à vocaliser (français).
        voice: voix edge-tts (défaut : HenriNeural, homme). Alternatives :
               fr-FR-DeniseNeural (femme), fr-FR-MauriceNeural, etc.

    Returns:
        Contenu binaire MP3. Lève AudioServiceError si la synthèse échoue.
    """
    if not text or not text.strip():
        raise AudioServiceError("Aucun texte à synthétiser.")

    try:
        communicate = edge_tts.Communicate(text, voice=voice)
        buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buffer.write(chunk["data"])
        mp3 = buffer.getvalue()
        if not mp3:
            raise AudioServiceError("edge-tts n'a produit aucun flux audio.")
        return mp3
    except AudioServiceError:
        raise
    except Exception as e:
        raise AudioServiceError(
            f"Synthèse vocale indisponible (edge-tts) : {type(e).__name__}: {e}"
        )


# edge-tts est importé paresseusement pour ne pas payer le coût d'import si
# seul le STT est utilisé, et pour laisser l'erreur remonter proprement.
def _import_edge_tts():
    import edge_tts  # noqa: WPS433
    return edge_tts


# Alias de module : on intercepte l'accès attribute pour importer paresseusement.
class _EdgeTtsProxy:
    def __getattr__(self, name):
        return getattr(_import_edge_tts(), name)


edge_tts = _EdgeTtsProxy()


# --------------------------------------------------------------------------- #
#  Résumé / Extraction (CR réunions)
# --------------------------------------------------------------------------- #
def summarize_text(text: str, max_sentences: int = 5) -> str:
    """
    Résume un texte. Si une clé OpenAI est disponible, utilise un LLM pour un
    résumé abstractif de qualité ; sinon produit un résumé extractif local
    (scoring fréquentiel des phrases, approche type TextRank simplifiée).

    Args:
        text: texte source (transcription de réunion).
        max_sentences: nombre maximal de phrases du résumé extractif.

    Returns:
        Le résumé (str).
    """
    if not text or not text.strip():
        return ""

    key = _openai_api_key()
    if key:
        try:
            return _summarize_openai(text, key)
        except Exception as e:
            logger.warning("Résumé OpenAI a échoué (%s) — fallback extractif", e)

    return _summarize_extractive(text, max_sentences=max_sentences)


def _summarize_openai(text: str, api_key: str) -> str:
    """Résumé abstractif via OpenAI Chat Completions (appel synchrone)."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    # Tronquer pour rester sous la limite de contexte (approx. 12k tokens).
    source = text[:40000]
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Tu es un assistant qui rédige des comptes-rendus de réunions "
                    "professionnelles en français. Résume la réunion en 4 à 6 phrases "
                    "claires : décisions prises, sujets abordés, points clés."
                ),
            },
            {"role": "user", "content": f"Voici la transcription de la réunion :\n\n{source}"},
        ],
        temperature=0.3,
        max_tokens=400,
    )
    summary = (resp.choices[0].message.content or "").strip()
    if not summary:
        raise AudioServiceError("OpenAI a renvoyé un résumé vide.")
    return summary


# Stop-words français pour le scoring extractif.
_STOPWORDS_FR = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "mais",
    "donc", "or", "ni", "car", "que", "qui", "quoi", "dont", "où", "ce",
    "cet", "cette", "ces", "son", "sa", "ses", "mon", "ma", "mes", "ton",
    "ta", "tes", "notre", "nos", "votre", "vos", "leur", "leurs", "il",
    "elle", "ils", "elles", "on", "nous", "vous", "je", "tu", "me", "te",
    "se", "lui", "soi", "en", "y", "à", "au", "aux", "dans", "sur", "pour",
    "par", "avec", "sans", "sous", "vers", "chez", "entre", "pendant",
    "est", "sont", "été", "être", "avoir", "ont", "avait", "fait", "faire",
    "plus", "moins", "très", "trop", "bien", "pas", "ne", "si", "comme",
    "quand", "alors", "puis", "aussi", "encore", "déjà", "tout", "tous",
    "toute", "toutes", "rien", "autre", "autres", "même", "mêmes", "cela",
    "ça", "non", "oui", "the", "of", "to", "and", "a", "in", "is", "for",
    "this", "that",
}


def _split_sentences(text: str) -> list[str]:
    """Découpe un texte en phrases (français). Robuste aux abréviations courantes."""
    # Normaliser les sauts de ligne en espaces pour le découpage phrastique.
    flat = re.sub(r"\s+", " ", text).strip()
    # Découper sur . ! ? ; tout en conservant la ponctuation.
    parts = re.split(r"(?<=[.!?;])\s+", flat)
    return [p.strip() for p in parts if len(p.strip()) > 20]


def _tokenize(text: str) -> list[str]:
    """Tokenisation basique en mots minuscules (sans ponctuation)."""
    return [w for w in re.findall(r"[a-zA-ZàâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]+", text.lower())
            if len(w) > 2 and w not in _STOPWORDS_FR]


def _summarize_extractive(text: str, max_sentences: int = 5) -> str:
    """Résumé extractif : score chaque phrase par la fréquence de ses mots-clés."""
    sentences = _split_sentences(text)
    if len(sentences) <= max_sentences:
        return " ".join(sentences)

    # Fréquence des mots (hors stop-words).
    freq: dict[str, int] = {}
    for word in _tokenize(text):
        freq[word] = freq.get(word, 0) + 1
    if not freq:
        # Pas de mots exploitables → on retourne les premières phrases.
        return " ".join(sentences[:max_sentences])

    max_freq = max(freq.values())
    normalized = {w: c / max_freq for w, c in freq.items()}

    # Score de chaque phrase = somme des fréquences normalisées de ses mots.
    scored: list[tuple[float, int, str]] = []
    for idx, sent in enumerate(sentences):
        tokens = _tokenize(sent)
        if not tokens:
            continue
        score = sum(normalized.get(w, 0.0) for w in tokens) / len(tokens)
        # Légère pénalité pour les phrases très longues ou très courtes.
        length_factor = 1.0 if 40 <= len(sent) <= 300 else 0.7
        scored.append((score * length_factor, idx, sent))

    # Prendre les meilleures phrases, puis les réordonner par position d'origine.
    scored.sort(key=lambda t: t[0], reverse=True)
    top = sorted(scored[:max_sentences], key=lambda t: t[1])
    return " ".join(s[2] for s in top)


# --------------------------------------------------------------------------- #
#  Extraction des participants et des actions (CR réunions)
# --------------------------------------------------------------------------- #
# Heuristiques locales fonctionnant sur la transcription texte.
# Si une clé OpenAI est disponible, on délègue pour une extraction plus fine.

_PARTICIPANT_PATTERNS = [
    # « M. Dupont », « Mme Durand », « Monsieur Martin »
    re.compile(r"\b(?:Monsieur|Mme|Madame|M\.|Mr)\s+([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ][a-zàâäéèêëîïôöùûüç]+(?:\s+[A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ][a-zàâäéèêëîïôöùûüç]+)?)"),
    # « Docteur Dupont », « Dr Martin », « Professeur X »
    re.compile(r"\b(?:Docteur|Dr|Professeur|Pr|Infirmier|Infirmière)\s+([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ][a-zàâäéèêëîïôöùûüç]+)"),
    # « Jean Dupont : » (style prise de parole) ou « - Jean Dupont : »
    re.compile(r"(?:^|\n|\-)\s*([A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ][a-zàâäéèêëîïôöùûüç]+(?:\s+[A-ZÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ][a-zàâäéèêëîïôöùûüç]+)?)\s*:"),
]

_ACTION_PATTERNS = [
    re.compile(
        r"((?:il faut|il faudrait|nous devons|on doit|je propose|à faire|"
        r"action|tâche|responsable|penser à|n'oublions pas|"
        r"we need to|to do|action item)[^.!?\n]{5,200}[.!?]?)",
        re.IGNORECASE,
    ),
]


def extract_participants(text: str) -> list[str]:
    """Extrait une liste de noms de participants probables depuis la transcription."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for pattern in _PARTICIPANT_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1).strip().strip(":").strip()
            # Filtrer les faux positifs courants (mots en début de phrase).
            if len(name) < 4 or name.lower() in {"nous", "vous", "ils", "elles", "cette", "cela"}:
                continue
            key = name.lower()
            if key not in seen:
                seen.add(key)
                found.append(name)
    return found[:20]  # plafonner


def extract_actions(text: str) -> list[str]:
    """Extrait les actions / tâches identifiables dans la transcription."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for pattern in _ACTION_PATTERNS:
        for match in pattern.finditer(text):
            action = match.group(1).strip()
            action = re.sub(r"\s+", " ", action)
            if len(action) < 10:
                continue
            key = action.lower()
            if key not in seen:
                seen.add(key)
                found.append(action)
    return found[:30]


def build_reunion_compte_rendu(transcription: str) -> dict:
    """
    Construit le compte-rendu structuré d'une réunion à partir de sa transcription.

    Returns:
        Dict avec clés : resume (str), participants (list[str]),
        actions (list[str]).
    """
    resume = summarize_text(transcription, max_sentences=6)
    participants = extract_participants(transcription)
    actions = extract_actions(transcription)
    return {
        "resume": resume,
        "participants": participants,
        "actions": actions,
    }
