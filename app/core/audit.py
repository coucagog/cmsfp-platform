# Audit logging — CMSFP Platform
# ---------------------------------------------------------------------------
# OWASP A09 : journalisation structurée des opérations critiques pour
# permettre la traçabilité et la détection d'incidents.
#
# Opérations journalisées :
#   - login (succès / échec)
#   - création de patient
#   - paiement (encaissement, remboursement)
#   - opération de caisse (journal immuable)
# ---------------------------------------------------------------------------
import logging
import sys
from datetime import datetime
from typing import Any, Optional

# Logger dédié à l'audit — configurable indépendamment du logger applicatif.
audit_logger = logging.getLogger("cmsfp.audit")
if not audit_logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | AUDIT | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    audit_logger.addHandler(_handler)
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False


def audit_log(
    action: str,
    user: Optional[str] = None,
    *,
    success: bool = True,
    resource_type: Optional[str] = None,
    resource_id: Optional[Any] = None,
    detail: Optional[str] = None,
    ip: Optional[str] = None,
) -> None:
    """
    Enregistre une entrée d'audit structurée.

    Parameters
    ----------
    action : str
        Nom court de l'action (ex. "login", "patient.create", "caisse.operation").
    user : str | None
        Identifiant de l'utilisateur ayant effectué l'action.
    success : bool
        Succès (True) ou échec (False) de l'opération.
    resource_type : str | None
        Type de ressource concernée (ex. "patient", "paiement", "caisse_operation").
    resource_id : Any | None
        Identifiant de la ressource.
    detail : str | None
        Information complémentaire libre (sans données sensibles).
    ip : str | None
        Adresse IP du client (si disponible).
    """
    parts = [
        f"action={action}",
        f"user={user or 'anonymous'}",
        f"result={'success' if success else 'failure'}",
    ]
    if resource_type:
        parts.append(f"resource={resource_type}")
    if resource_id is not None:
        parts.append(f"resource_id={resource_id}")
    if ip:
        parts.append(f"ip={ip}")
    if detail:
        # Échapper les pipes pour ne pas casser le parsing du log.
        parts.append(f'detail="{detail.replace("|", "/")}"')

    message = " | ".join(parts)
    if success:
        audit_logger.info(message)
    else:
        audit_logger.warning(message)
