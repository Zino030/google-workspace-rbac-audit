"""
Extractor: Google Chat Spaces — MODULE SKELETON (not yet implemented).

This module is intentionally left as a skeleton for future implementation.

When implemented, it would use:
  - Chat API: spaces.search (or spaces.list with useAdminAccess=True)
  - Chat API: spaces.members.list (with useAdminAccess=True)

Required scopes (to be added to auth.py when implementing):
  https://www.googleapis.com/auth/chat.admin.spaces.readonly
  https://www.googleapis.com/auth/chat.admin.memberships.readonly

These scopes require Workspace Business Starter or higher and the
"Manage Chat" admin privilege.

Usage (future):
  from workspace_audit.extractors import chat_spaces
  grants, errors = chat_spaces.extract(key_file, admin_email, internal_domains)
"""

from __future__ import annotations

from pathlib import Path

from ..core.matrix import Grant


def extract(
    key_file: Path,
    admin_email: str,
    internal_domains: set[str],
    dry_run: bool = False,
) -> tuple[list[Grant], list[str]]:
    """Not implemented — returns empty results."""
    raise NotImplementedError(
        "Chat spaces extraction is not yet implemented. "
        "See module docstring for implementation notes."
    )
