"""
Authentication helpers for workspace_audit.

READ-ONLY GUARANTEE
-------------------
All credentials are scoped to read-only (or the narrowest available) scopes.
The build_service() helper wraps every API call through a guard that raises
an exception if the HTTP method is anything other than GET, preventing any
accidental write operation from ever reaching Google's servers.

gmail.settings.basic scope note
-----------------------------------
`users.settings.delegates.list` does not accept `gmail.settings.sharing`.
Google allows only gmail.settings.basic, gmail.readonly, gmail.modify or
mail.google.com for it. The narrowest of these that does not expose message
content is `gmail.settings.basic`. It has no read-only variant and grants
read and write access to basic mail settings, but this tool calls *only*
`users.settings.delegates.list` (a GET request). The read-only guard
enforces this at runtime.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httplib2
import google_auth_httplib2
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import HttpRequest

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.group.member.readonly",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.domain.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    # delegates.list requires gmail.settings.basic (no readonly variant
    # without message access) - only list() is called.
    "https://www.googleapis.com/auth/gmail.settings.basic",
]


class ReadOnlyViolation(RuntimeError):
    """Raised when a non-GET HTTP method would be sent to the API."""


class _GuardedHttp:
    """
    Wraps google_auth_httplib2.AuthorizedHttp and blocks any non-GET request
    before it reaches Google's servers.
    """

    def __init__(self, authorized_http: google_auth_httplib2.AuthorizedHttp) -> None:
        self._inner = authorized_http

    def request(self, uri: str, method: str = "GET", *args: Any, **kwargs: Any) -> Any:
        if method.upper() != "GET":
            raise ReadOnlyViolation(
                f"Blocked non-GET request: {method} {uri}\n"
                "workspace_audit is strictly read-only."
            )
        return self._inner.request(uri, method, *args, **kwargs)

    # Proxy all other attribute access to the inner object so googleapiclient
    # can reach .connections, .follow_redirects, etc.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _credentials(
    key_file: Path,
    subject: str,
    scopes: list[str] | None = None,
) -> service_account.Credentials:
    creds = service_account.Credentials.from_service_account_file(
        str(key_file),
        scopes=scopes if scopes is not None else SCOPES,
    )
    return creds.with_subject(subject)


def build_service(
    api_name: str,
    version: str,
    key_file: Path,
    impersonate: str,
    scopes: list[str] | None = None,
) -> Any:
    """Return a Google API service client that blocks all non-GET calls.

    Pass `scopes` to request a specific subset of SCOPES (e.g. for Gmail
    impersonation of non-admin users, where mixing admin scopes in the same
    token can cause Google to drop the Gmail scope).
    """
    creds = _credentials(key_file, impersonate, scopes=scopes)
    authorized_http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http())
    guarded_http = _GuardedHttp(authorized_http)
    service = build(api_name, version, http=guarded_http, cache_discovery=False)
    logger.debug(
        "Built service %s %s impersonating %s (scopes: %s)",
        api_name, version, impersonate,
        scopes if scopes else "all",
    )
    return service


def validate_key_file(key_file: Path) -> None:
    """Basic sanity-check that the file is a valid service-account JSON."""
    if not key_file.exists():
        raise FileNotFoundError(f"Service account key not found: {key_file}")
    with key_file.open() as f:
        data = json.load(f)
    required = {"type", "project_id", "private_key", "client_email"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Key file is missing fields: {missing}")
    if data.get("type") != "service_account":
        raise ValueError(f"Expected type=service_account, got {data.get('type')!r}")
