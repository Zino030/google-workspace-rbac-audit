"""
Extractor: Google Group Memberships.

For each group in the organisation, lists its direct members.
Sub-groups are NOT resolved — a member of type GROUP appears as its own grantee column.

All membership roles (OWNER / MANAGER / MEMBER) map to 'Editor / Schreibrechte'.
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
def _list_members_page(dir_svc, group_key: str, page_token: str | None) -> dict:
    req = dir_svc.members().list(
        groupKey=group_key,
        maxResults=200,
        **({"pageToken": page_token} if page_token else {}),
    )
    return req.execute()


def extract(
    key_file: Path,
    admin_email: str,
    groups: list,  # list[OrgGroup]
    internal_domains: set[str],
    dry_run: bool = False,
) -> tuple[list[Grant], list[str]]:
    grants: list[Grant] = []
    errors: list[str] = []

    dir_svc = build_service("admin", "directory_v1", key_file, admin_email)
    logger.info("Extracting group memberships for %d groups...", len(groups))

    for group in groups:
        if dry_run:
            logger.debug("[dry-run] Would list members of %s", group.email)
            continue

        page_token = None
        try:
            while True:
                page = _list_members_page(dir_svc, group.email, page_token)
                for member in page.get("members", []):
                    member_email = member.get("email", "").lower()
                    if not member_email:
                        continue
                    member_type_raw = member.get("type", "USER").upper()

                    if member_type_raw == "GROUP":
                        grantee_type = "Gruppe"
                    else:
                        domain = member_email.split("@")[-1] if "@" in member_email else ""
                        grantee_type = "Person" if domain in internal_domains else "extern"

                    grants.append(Grant(
                        resource_type="Google Gruppe",
                        resource=group.email,
                        grantee=member_email,
                        grantee_type=grantee_type,
                        native_role=member.get("role", "MEMBER"),
                        matrix_role=to_matrix_role("group", member.get("role", "MEMBER")),
                    ))

                page_token = page.get("nextPageToken")
                if not page_token:
                    break

        except Exception as exc:
            logger.warning("Skipping group %s: %s", group.email, exc)
            errors.append(f"group:{group.email} — {exc}")

    logger.info("  Found %d membership grant(s), %d error(s)", len(grants), len(errors))
    return grants, errors
