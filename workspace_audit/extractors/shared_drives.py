"""
Extractor: Shared Drive Permissions.

Uses Drive API v3 with useDomainAdminAccess=True to list permissions on every
shared drive discovered in the organisation.

Role mapping:
  organizer | fileOrganizer | writer  →  Editor / Schreibrechte
  commenter | reader                  →  Leserechte
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
def _list_permissions_page(drive_svc, drive_id: str, page_token: str | None) -> dict:
    return drive_svc.permissions().list(
        fileId=drive_id,
        supportsAllDrives=True,
        useDomainAdminAccess=True,
        pageSize=100,
        fields="permissions(id,type,emailAddress,role,domain,displayName),nextPageToken",
        **({"pageToken": page_token} if page_token else {}),
    ).execute()


def extract(
    key_file: Path,
    admin_email: str,
    drives: list,  # list[OrgDrive]
    internal_domains: set[str],
    group_emails: set[str],
    dry_run: bool = False,
) -> tuple[list[Grant], list[str]]:
    grants: list[Grant] = []
    errors: list[str] = []

    drive_svc = build_service("drive", "v3", key_file, admin_email)
    logger.info("Extracting permissions for %d shared drives...", len(drives))

    for drive in drives:
        if dry_run:
            logger.debug("[dry-run] Would list permissions of drive %s", drive.name)
            continue

        page_token = None
        try:
            while True:
                page = _list_permissions_page(drive_svc, drive.drive_id, page_token)
                for perm in page.get("permissions", []):
                    perm_type = perm.get("type", "")
                    email = perm.get("emailAddress", "").lower()

                    # skip 'anyone' or domain-wide permissions without a specific email
                    if perm_type in ("anyone",):
                        continue
                    if perm_type == "domain":
                        # domain-level permission — record as domain grantee
                        domain_name = perm.get("domain", "")
                        grantee = f"domain:{domain_name}"
                        grantee_type = "extern" if domain_name not in internal_domains else "Person"
                    else:
                        if not email:
                            continue
                        grantee = email
                        if email in group_emails or perm_type == "group":
                            grantee_type = "Gruppe"
                        else:
                            domain = email.split("@")[-1] if "@" in email else ""
                            grantee_type = "Person" if domain in internal_domains else "extern"

                    grants.append(Grant(
                        resource_type="Shared Drives",
                        resource=drive.name,
                        grantee=grantee,
                        grantee_type=grantee_type,
                        native_role=perm.get("role", ""),
                        matrix_role=to_matrix_role("drive", perm.get("role", "")),
                    ))

                page_token = page.get("nextPageToken")
                if not page_token:
                    break

        except Exception as exc:
            logger.warning("Skipping drive %s (%s): %s", drive.name, drive.drive_id, exc)
            errors.append(f"drive:{drive.name} — {exc}")

    logger.info("  Found %d drive permission grant(s), %d error(s)", len(grants), len(errors))
    return grants, errors
