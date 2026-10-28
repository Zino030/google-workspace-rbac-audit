"""
XLSX export — primary output format.

Tabs:
  Matrix    — full permission matrix with colour coding and merged type cells
  Rohdaten  — raw grants (one row per grant)
  Übersicht — per-grantee summary counts
"""

from __future__ import annotations

import logging
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import (
    Alignment, Font, PatternFill, Border, Side
)
from openpyxl.utils import get_column_letter

from ..core.matrix import AuditMatrix, Grant
from ..core.normalize import NONE, EDITOR, READER

logger = logging.getLogger(__name__)

# Colours
_GREEN = "C6EFCE"   # Editor / Schreibrechte
_YELLOW = "FFEB9C"  # Leserechte
_GREY = "F2F2F2"    # Keine Berechtigungen
_HEADER_BG = "4472C4"
_HEADER_FG = "FFFFFF"
_TYPE_BG = "D9E1F2"

_ROLE_FILL = {
    EDITOR: PatternFill("solid", fgColor=_GREEN),
    READER: PatternFill("solid", fgColor=_YELLOW),
    NONE: PatternFill("solid", fgColor=_GREY),
}

_THIN = Side(style="thin", color="CCCCCC")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def write(matrix: AuditMatrix, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    _write_matrix_tab(wb, matrix)
    _write_raw_tab(wb, matrix.raw_grants)
    _write_summary_tab(wb, matrix)

    # Remove default empty sheet if present
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    wb.save(str(out_path))
    logger.info("XLSX written: %s", out_path)
    _validate(out_path)


def _write_matrix_tab(wb: Workbook, matrix: AuditMatrix) -> None:
    ws = wb.create_sheet("Matrix")

    grantees = matrix.grantee_order
    n_fixed = 2  # Ressourcentyp, Ressource

    # --- Header row ---
    header_fill = PatternFill("solid", fgColor=_HEADER_BG)
    header_font = Font(bold=True, color=_HEADER_FG)

    ws.cell(1, 1, "Ressourcentyp").font = header_font
    ws.cell(1, 1).fill = header_fill
    ws.cell(1, 2, "Ressource").font = header_font
    ws.cell(1, 2).fill = header_fill

    for col_idx, grantee in enumerate(grantees, start=n_fixed + 1):
        cell = ws.cell(1, col_idx, grantee)
        cell.font = Font(bold=True, color=_HEADER_FG)
        cell.fill = header_fill
        cell.alignment = Alignment(
            text_rotation=90, wrap_text=False, vertical="bottom", horizontal="center"
        )

    # Freeze panes at C2
    ws.freeze_panes = "C2"

    # --- Data rows ---
    from ..core.matrix import RESOURCE_TYPE_ORDER
    type_block_start: dict[str, int] = {}
    type_block_end: dict[str, int] = {}

    row_num = 2
    for row in matrix.rows:
        rt = row.resource_type
        if rt not in type_block_start:
            type_block_start[rt] = row_num

        type_cell = ws.cell(row_num, 1, rt)
        type_cell.fill = PatternFill("solid", fgColor=_TYPE_BG)
        type_cell.font = Font(bold=True)
        type_cell.alignment = Alignment(horizontal="center", vertical="center")

        res_cell = ws.cell(row_num, 2, row.resource)
        res_cell.alignment = Alignment(horizontal="left")

        for col_idx, grantee in enumerate(grantees, start=n_fixed + 1):
            role = row.permissions.get(grantee, NONE)
            cell = ws.cell(row_num, col_idx, role)
            cell.fill = _ROLE_FILL.get(role, _ROLE_FILL[NONE])
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = _BORDER

        type_block_end[rt] = row_num
        row_num += 1

    # Merge Ressourcentyp cells per block
    for rt in RESOURCE_TYPE_ORDER:
        if rt in type_block_start:
            start = type_block_start[rt]
            end = type_block_end[rt]
            if end > start:
                ws.merge_cells(
                    start_row=start, start_column=1, end_row=end, end_column=1
                )

    # --- Column widths ---
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 35
    for col_idx in range(n_fixed + 1, n_fixed + 1 + len(grantees)):
        ws.column_dimensions[get_column_letter(col_idx)].width = 5

    # Row height for header
    ws.row_dimensions[1].height = 100


def _write_raw_tab(wb: Workbook, grants: list[Grant]) -> None:
    ws = wb.create_sheet("Rohdaten")
    headers = [
        "Ressourcentyp", "Ressource", "Grantee (primär)",
        "Grantee-Typ", "Native Rolle", "Normalisierte Rolle"
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor=_HEADER_BG)
        cell.font = Font(bold=True, color=_HEADER_FG)

    for g in grants:
        ws.append([
            g.resource_type, g.resource, g.grantee,
            g.grantee_type, g.native_role, g.matrix_role,
        ])

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 35
    ws.column_dimensions["C"].width = 35
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 20
    ws.column_dimensions["F"].width = 22


def _write_summary_tab(wb: Workbook, matrix: AuditMatrix) -> None:
    ws = wb.create_sheet("Übersicht je Grantee")
    headers = ["Grantee", "Typ", "Gesamt Ressourcen", EDITOR, READER, NONE]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color=_HEADER_FG)
        cell.fill = PatternFill("solid", fgColor=_HEADER_BG)

    for grantee in matrix.grantee_order:
        gt = matrix.grantee_meta.get(grantee, "extern")
        editor_count = reader_count = none_count = 0
        for row in matrix.rows:
            role = row.permissions.get(grantee, NONE)
            if role == EDITOR:
                editor_count += 1
            elif role == READER:
                reader_count += 1
            else:
                none_count += 1

        non_none = editor_count + reader_count
        ws.append([grantee, gt, non_none, editor_count, reader_count, none_count])

    ws.column_dimensions["A"].width = 35
    ws.column_dimensions["B"].width = 12
    for col in ["C", "D", "E", "F"]:
        ws.column_dimensions[col].width = 20


def _validate(path: Path) -> None:
    """Re-open the file to ensure it's not corrupt."""
    wb = load_workbook(str(path))
    assert "Matrix" in wb.sheetnames, "XLSX missing 'Matrix' tab"
    ws = wb["Matrix"]
    # Verify all data rows have the same width as the header
    max_col = ws.max_column
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        assert len(row) == max_col, f"Ragged row in XLSX at row {row[0].row}"
    logger.debug("XLSX self-validation passed (%s)", path.name)
