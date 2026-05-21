"""Build or append to llm-config-results.ods.

Usage:
    python build_results_ods.py <run_data.json>

The JSON file is a flat dict of {column_name: value}. The script appends a row
to bench/logs/llm-config-results.ods (creating it with headers if missing).
Override the destination with the BENCH_ODS_PATH env var.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from odf import table, text, style
from odf.opendocument import OpenDocumentSpreadsheet, load
from odf.table import Table, TableCell, TableColumn, TableRow
from odf.text import P
from odf.style import (
    Style, TableColumnProperties, TextProperties, TableCellProperties,
    ParagraphProperties,
)


SECTIONS = [
    ("Run", [
        "run_id", "timestamp_start", "timestamp_end",
        "duration_total_sec", "phase_a_sec", "phase_b_sec", "step_3_sec",
    ]),
    ("Models", [
        "primary_model", "extraction_model", "relationship_model",
        "primary_base", "extraction_base", "relationship_base",
    ]),
    ("Reasoning", [
        "extraction_reasoning_mode", "relationship_reasoning_mode",
        "default_reasoning_mode", "reasoning_overrides",
    ]),
    ("Pipeline Stats", [
        "documents", "chunks", "entities", "relationships_total",
        "per_chunk_relationships", "cross_doc_relationships",
        "err", "communities",
    ]),
    ("Entity Types", [
        "type_concept", "type_person", "type_product", "type_organization",
        "type_technology", "type_event", "type_process", "type_location",
        "type_system", "type_document",
    ]),
    ("Log Signals", [
        "doc_summaries_ok", "entity_batches_ok", "raw_entities_extracted",
        "extraction_timeouts",
        "relationship_batches", "relationships_from_phase2",
        "candidate_scans_ok", "candidate_scan_empty", "candidate_pairs_total",
        "gleaning_passes", "empty_content_length", "empty_content_stop",
        "communities_named", "community_parse_fallback", "per_chunk_retries",
    ]),
    ("Analysis", [
        "verdict", "observations", "failure_patterns",
        "performance_notes", "quality_notes", "recommendation",
        "vs_previous_run",
    ]),
]

ALL_COLUMNS: list[str] = [c for _, cols in SECTIONS for c in cols]


def _add_styles(doc: OpenDocumentSpreadsheet) -> dict[str, Style]:
    """Register cell/column styles, return name → style for use in cells."""
    styles: dict[str, Style] = {}

    # Header row style: bold + light grey background
    s = Style(name="Header", family="table-cell")
    s.addElement(TextProperties(fontweight="bold"))
    s.addElement(TableCellProperties(backgroundcolor="#d9d9d9"))
    doc.automaticstyles.addElement(s)
    styles["header"] = s

    # Section label style: bold + accent background
    s = Style(name="Section", family="table-cell")
    s.addElement(TextProperties(fontweight="bold", color="#ffffff"))
    s.addElement(TableCellProperties(backgroundcolor="#1f4e79"))
    doc.automaticstyles.addElement(s)
    styles["section"] = s

    # Default body cell — top-aligned (long notes look better)
    s = Style(name="Body", family="table-cell")
    s.addElement(TableCellProperties(verticalalign="top"))
    doc.automaticstyles.addElement(s)
    styles["body"] = s

    # Column width preset (~24mm)
    col_default = Style(name="ColDefault", family="table-column")
    col_default.addElement(TableColumnProperties(columnwidth="28mm"))
    doc.automaticstyles.addElement(col_default)
    styles["col_default"] = col_default

    # Wide column for notes (~80mm)
    col_wide = Style(name="ColWide", family="table-column")
    col_wide.addElement(TableColumnProperties(columnwidth="80mm"))
    doc.automaticstyles.addElement(col_wide)
    styles["col_wide"] = col_wide

    return styles


def _cell(value, *, styles: dict[str, Style], style_key: str = "body") -> TableCell:
    if isinstance(value, bool):
        c = TableCell(valuetype="boolean", booleanvalue=str(value).lower(),
                      stylename=styles[style_key])
        c.addElement(P(text=str(value)))
        return c
    if isinstance(value, (int, float)):
        c = TableCell(valuetype="float", value=str(value),
                      stylename=styles[style_key])
        c.addElement(P(text=str(value)))
        return c
    text_value = "" if value is None else str(value)
    c = TableCell(valuetype="string", stylename=styles[style_key])
    c.addElement(P(text=text_value))
    return c


def _build_header_rows(styles) -> list[TableRow]:
    """Two header rows: section labels + column names."""
    section_row = TableRow()
    name_row = TableRow()
    for section_name, cols in SECTIONS:
        # Section label spans len(cols) columns
        cell = TableCell(stylename=styles["section"],
                        numbercolumnsspanned=len(cols))
        cell.addElement(P(text=section_name))
        section_row.addElement(cell)
        # Covered cells (placeholders for the span)
        for _ in range(len(cols) - 1):
            section_row.addElement(TableCell())
        # Column-name header row
        for col in cols:
            c = TableCell(stylename=styles["header"])
            c.addElement(P(text=col))
            name_row.addElement(c)
    return [section_row, name_row]


def _new_doc(path: Path) -> None:
    doc = OpenDocumentSpreadsheet()
    styles = _add_styles(doc)

    tbl = Table(name="Runs")
    # Add columns with width hints
    wide_cols = {
        "reasoning_overrides", "primary_base", "extraction_base",
        "relationship_base", "observations", "failure_patterns",
        "performance_notes", "quality_notes", "recommendation",
        "vs_previous_run",
    }
    for col in ALL_COLUMNS:
        tbl.addElement(TableColumn(
            stylename=styles["col_wide" if col in wide_cols else "col_default"]
        ))
    for row in _build_header_rows(styles):
        tbl.addElement(row)

    doc.spreadsheet.addElement(tbl)
    doc.save(str(path))


def _append_row(path: Path, row_data: dict) -> None:
    """Append a row of data to an existing doc."""
    doc = load(str(path))
    styles = _add_styles(doc)  # re-register so we can reuse styling
    tbl = doc.spreadsheet.getElementsByType(Table)[0]
    row = TableRow()
    for col in ALL_COLUMNS:
        row.addElement(_cell(row_data.get(col), styles=styles))
    tbl.addElement(row)
    doc.save(str(path))


def main():
    if len(sys.argv) != 2:
        print("Usage: build_results_ods.py <run_data.json>", file=sys.stderr)
        sys.exit(2)

    run_data_path = Path(sys.argv[1])
    run_data = json.loads(run_data_path.read_text())

    # Default output: bench/logs/llm-config-results.ods (alongside per-run JSONs).
    # Override with BENCH_ODS_PATH env var if needed.
    default_ods = Path(__file__).parent / "logs" / "llm-config-results.ods"
    ods_path = Path(os.environ.get("BENCH_ODS_PATH", str(default_ods)))
    ods_path.parent.mkdir(parents=True, exist_ok=True)
    if not ods_path.exists():
        _new_doc(ods_path)
        print(f"Created {ods_path}")
    _append_row(ods_path, run_data)
    print(f"Appended row to {ods_path}")


if __name__ == "__main__":
    main()
