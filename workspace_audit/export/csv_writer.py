"""
CSV export — RFC-4180 compliant, UTF-8-BOM for correct Excel rendering.

Every row has exactly as many columns as the header (no ragged rows).
Ressourcentyp is written in full in every data row (no merged cells in CSV).
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from ..core.matrix import AuditMatrix
from ..core.normalize import NONE

logger = logging.getLogger(__name__)


def write(matrix: AuditMatrix, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    header = ["Ressourcentyp", "Ressource"] + matrix.grantee_order

    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(header)

        for row in matrix.rows:
            cells = [row.resource_type, row.resource]
            for grantee in matrix.grantee_order:
                cells.append(row.permissions.get(grantee, NONE))
            assert len(cells) == len(header), \
                f"Row length mismatch: {len(cells)} vs header {len(header)}"
            writer.writerow(cells)

    logger.info("CSV written: %s", out_path)
    _validate(out_path, len(header))


def _validate(path: Path, expected_cols: int) -> None:
    """Re-read and assert every row has the expected column count."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            assert len(row) == expected_cols, \
                f"CSV validation failed at row {i}: expected {expected_cols} cols, got {len(row)}"
    logger.debug("CSV self-validation passed (%s)", path.name)
