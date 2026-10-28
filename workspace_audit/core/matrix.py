"""
In-memory representation of the audit matrix.

Invariant: every grantee (normalised primary e-mail) appears as exactly one
column. Enforced by an assertion in build_matrix().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from .normalize import NONE, EDITOR, merge_roles

logger = logging.getLogger(__name__)

RESOURCE_TYPE_ORDER = ["Geteilte Postfächer", "Google Gruppe", "Shared Drives"]


@dataclass
class Grant:
    resource_type: str   # one of RESOURCE_TYPE_ORDER values
    resource: str        # display name / address of the resource
    grantee: str         # normalised primary email
    grantee_type: str    # 'Gruppe' | 'Person' | 'extern'
    native_role: str
    matrix_role: str


@dataclass
class MatrixRow:
    resource_type: str
    resource: str
    # grantee -> matrix_role
    permissions: dict[str, str] = field(default_factory=dict)


@dataclass
class AuditMatrix:
    rows: list[MatrixRow]
    grantee_order: list[str]          # stable column order
    grantee_meta: dict[str, str]      # email -> grantee_type
    raw_grants: list[Grant]


def _grantee_sort_key(email: str, grantee_meta: dict[str, str]) -> tuple:
    gt = grantee_meta.get(email, "extern")
    order = {"Gruppe": 0, "Person": 1, "extern": 2}
    return (order.get(gt, 2), email)


def build_matrix(grants: list[Grant]) -> AuditMatrix:
    # --- collect all unique grantees (dedup invariant) ---
    grantee_meta: dict[str, str] = {}
    for g in grants:
        existing = grantee_meta.get(g.grantee)
        # If same email seen as both Gruppe and Person (shouldn't happen),
        # prefer Gruppe since the group takes precedence.
        if existing is None:
            grantee_meta[g.grantee] = g.grantee_type
        elif existing != g.grantee_type and existing != "Gruppe":
            grantee_meta[g.grantee] = g.grantee_type

    # Dedup invariant: each grantee appears exactly once
    assert len(set(grantee_meta.keys())) == len(grantee_meta), \
        "BUG: duplicate grantee keys in grantee_meta"

    grantee_order = sorted(
        grantee_meta.keys(),
        key=lambda e: _grantee_sort_key(e, grantee_meta),
    )

    # --- build row index ---
    row_index: dict[tuple[str, str], MatrixRow] = {}
    for rt in RESOURCE_TYPE_ORDER:
        for g in grants:
            if g.resource_type == rt:
                key = (g.resource_type, g.resource)
                if key not in row_index:
                    row_index[key] = MatrixRow(
                        resource_type=g.resource_type,
                        resource=g.resource,
                    )

    # --- fill cells (strongest role wins) ---
    for g in grants:
        key = (g.resource_type, g.resource)
        row = row_index[key]
        existing = row.permissions.get(g.grantee, NONE)
        row.permissions[g.grantee] = merge_roles(existing, g.matrix_role)

    # Order rows by resource type
    rows: list[MatrixRow] = []
    for rt in RESOURCE_TYPE_ORDER:
        rows.extend(r for (t, _), r in row_index.items() if t == rt)

    logger.info(
        "Matrix: %d rows, %d grantee columns (%d Gruppen, %d Personen, %d extern)",
        len(rows),
        len(grantee_order),
        sum(1 for v in grantee_meta.values() if v == "Gruppe"),
        sum(1 for v in grantee_meta.values() if v == "Person"),
        sum(1 for v in grantee_meta.values() if v == "extern"),
    )

    return AuditMatrix(
        rows=rows,
        grantee_order=grantee_order,
        grantee_meta=grantee_meta,
        raw_grants=grants,
    )
