"""
Auto-discovery of all organisation resources via customer='my_customer'.

No manual lists required. Everything is fetched from the Directory and Drive APIs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from .auth import build_service

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    from googleapiclient.errors import HttpError
    if isinstance(exc, HttpError):
        return exc.resp.status in (429, 500, 503)
    return False


retry_policy = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)


@dataclass
class OrgUser:
    primary_email: str
    full_name: str = ""
    is_suspended: bool = False


@dataclass
class OrgGroup:
    email: str
    name: str
    group_id: str


@dataclass
class OrgDrive:
    drive_id: str
    name: str


@dataclass
class OrgResources:
    domains: list[str] = field(default_factory=list)
    users: list[OrgUser] = field(default_factory=list)
    groups: list[OrgGroup] = field(default_factory=list)
    drives: list[OrgDrive] = field(default_factory=list)


def _paginate_list(list_method, **kwargs) -> Generator[dict, None, None]:
    """Paginate a Google API list method. Handles nextPageToken automatically."""
    page_token = None
    while True:
        kw = {**kwargs}
        if page_token:
            kw["pageToken"] = page_token
        response = list_method(**kw).execute()
        yield response
        page_token = response.get("nextPageToken")
        if not page_token:
            break


def discover_all(key_file: Path, admin_email: str) -> OrgResources:
    """Discover all domains, users, groups and shared drives in the organisation."""
    dir_svc = build_service("admin", "directory_v1", key_file, admin_email)
    drive_svc = build_service("drive", "v3", key_file, admin_email)

    resources = OrgResources()

    # --- Domains ---
    logger.info("Discovering domains...")
    try:
        resp = dir_svc.domains().list(customer="my_customer").execute()
        for d in resp.get("domains", []):
            resources.domains.append(d["domainName"])
        logger.info("  Found %d domain(s): %s", len(resources.domains), resources.domains)
    except Exception as exc:
        logger.warning("Could not fetch domains: %s", exc)

    # --- Users ---
    logger.info("Discovering users...")
    for page in _paginate_list(
        dir_svc.users().list, customer="my_customer", maxResults=500
    ):
        for u in page.get("users", []):
            resources.users.append(OrgUser(
                primary_email=u["primaryEmail"].lower(),
                full_name=u.get("name", {}).get("fullName", ""),
                is_suspended=u.get("suspended", False),
            ))
    logger.info("  Found %d user(s)", len(resources.users))

    # --- Groups ---
    logger.info("Discovering groups...")
    for page in _paginate_list(
        dir_svc.groups().list, customer="my_customer", maxResults=200
    ):
        for g in page.get("groups", []):
            resources.groups.append(OrgGroup(
                email=g["email"].lower(),
                name=g.get("name", g["email"]),
                group_id=g["id"],
            ))
    logger.info("  Found %d group(s)", len(resources.groups))

    # --- Shared Drives ---
    logger.info("Discovering shared drives...")
    for page in _paginate_list(
        drive_svc.drives().list, pageSize=100, useDomainAdminAccess=True
    ):
        for d in page.get("drives", []):
            resources.drives.append(OrgDrive(drive_id=d["id"], name=d["name"]))
    logger.info("  Found %d shared drive(s)", len(resources.drives))

    return resources
