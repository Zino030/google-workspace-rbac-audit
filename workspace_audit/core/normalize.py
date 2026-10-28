"""Role normalisation — maps native API roles to the three matrix values."""

from __future__ import annotations

EDITOR = "Editor / Schreibrechte"
READER = "Leserechte"
NONE = "Keine Berechtigungen"

ROLE_RANK = {EDITOR: 2, READER: 1, NONE: 0}

_DRIVE_EDITOR_ROLES = {"organizer", "fileorganizer", "writer"}
_DRIVE_READER_ROLES = {"commenter", "reader"}


def to_matrix_role(resource_type: str, native_role: str) -> str:
    """
    resource_type: 'mailbox' | 'group' | 'drive'
    native_role:   role string from the API, or '' / None for no access
    """
    if not native_role:
        return NONE

    rt = resource_type.lower()
    nr = native_role.lower().strip()

    if rt == "mailbox":
        return EDITOR  # all delegation is write

    if rt == "group":
        return EDITOR  # all group membership → write

    if rt == "drive":
        if nr in _DRIVE_EDITOR_ROLES:
            return EDITOR
        if nr in _DRIVE_READER_ROLES:
            return READER
        return NONE

    return NONE


def merge_roles(a: str, b: str) -> str:
    """Return the stronger of two normalised roles."""
    return a if ROLE_RANK.get(a, 0) >= ROLE_RANK.get(b, 0) else b
