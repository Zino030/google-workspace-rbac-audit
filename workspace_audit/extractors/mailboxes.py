"""
Extractor: Shared Mailbox Delegations.

For every user in the organisation, this module calls
  gmail.users().settings().delegates().list(userId=<mailbox>)
using the mailbox address itself as the impersonated subject (required by Gmail API).

Delegation is binary — any delegate gets 'Editor / Schreibrechte'.
Only mailboxes with at least one delegate produce a row in the matrix.

NOTE: delegates.list does not accept gmail.settings.sharing. This module uses
      gmail.settings.basic, the narrowest accepted scope without message access.
      Google provides no read-only variant. This module calls list() only.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from ..auth import build_service
from ..core.normalize import to_matrix_role
from ..core.matrix import Grant

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    from googleapiclient.errors import HttpError
    return isinstance(exc, HttpError) and exc.resp.status in (429, 500, 503)


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _list_delegates(gmail_svc, mailbox: str) -> list[dict]:
    return (
        gmail_svc.users()
        .settings()
        .delegates()
        .list(userId=mailbox)
        .execute()
        .get("delegates", [])
    )


def extract(
    key_file: Path,
    users: list,  # list[OrgUser]
    internal_domains: set[str],
    dry_run: bool = False,
) -> tuple[list[Grant], list[str]]:
    """
    Returns (grants, errors).
    errors: list of human-readable error strings for mailboxes that failed.
    """
    grants: list[Grant] = []
    errors: list[str] = []

    logger.info("Extracting mailbox delegations for %d users...", len(users))

    for user in users:
        mailbox = user.primary_email
        if dry_run:
            logger.debug("[dry-run] Would check delegates for %s", mailbox)
            continue

        try:
            # Request only the Gmail scope — mixing admin directory scopes
            # in the same token for a non-admin subject causes Google to drop
            # the Gmail scope from the issued token.
            gmail_svc = build_service(
                "gmail", "v1", key_file, impersonate=mailbox,
                scopes=["https://www.googleapis.com/auth/gmail.settings.basic"],
            )
            delegates = _list_delegates(gmail_svc, mailbox)
        except Exception as exc:
            logger.warning("Skipping mailbox %s: %s", mailbox, exc)
            errors.append(f"mailbox:{mailbox} — {exc}")
            continue

        for delegate in delegates:
            delegate_email = delegate.get("delegateEmail", "").lower()
            status = delegate.get("verificationStatus", "")
            if not delegate_email:
                continue

            domain = delegate_email.split("@")[-1] if "@" in delegate_email else ""
            grantee_type = "Gruppe" if _is_group_email(delegate_email) else (
                "Person" if domain in internal_domains else "extern"
            )

            grants.append(Grant(
                resource_type="Geteilte Postfächer",
                resource=mailbox,
                grantee=delegate_email,
                grantee_type=grantee_type,
                native_role=f"delegate:{status}",
                matrix_role=to_matrix_role("mailbox", "delegate"),
            ))
            logger.debug("  %s → delegate %s (%s)", mailbox, delegate_email, status)

    logger.info("  Found %d delegation grant(s), %d error(s)", len(grants), len(errors))
    return grants, errors


def _is_group_email(email: str) -> bool:
    """Heuristic: we cannot know for sure without a Directory lookup, so the
    caller (orchestrator) will re-classify using the discovered groups set."""
    return False
