#!/usr/bin/env python3
"""Generate the canonical Cortex QA spreadsheet (.ods) from a features JSON file.

Usage: python gen_ods.py features.json out.ods
JSON schema:
{
  "features": [
    {
      "id": "F-DOC-001", "name": "...", "area": "...",
      "user_story": "...", "expected": "...", "edge_cases": "...",
      "validation": "...", "dependencies": "...", "assumptions": "...",
      "test_cases": "...", "status": "...", "defect_count": 0,
      "severity": "...", "notes": "...", "last_tested": "..."
    }, ...
  ],
  "defects": [
    {"id":"D-001","feature_id":"F-...","repro":"...","expected":"...",
     "actual":"...","severity":"...","root_cause":"...","status":"..."}
  ]
}
"""
import json, sys
from odf.opendocument import OpenDocumentSpreadsheet
from odf.style import Style, TextProperties, TableColumnProperties, TableCellProperties, ParagraphProperties
from odf.table import Table, TableRow, TableCell, TableColumn
from odf.text import P

def build():
    src, out = sys.argv[1], sys.argv[2]
    data = json.load(open(src))
    doc = OpenDocumentSpreadsheet()

    # styles
    hdr = Style(name="hdr", family="table-cell")
    hdr.addElement(TextProperties(fontweight="bold", color="#ffffff"))
    hdr.addElement(TableCellProperties(backgroundcolor="#1f2933", wrapoption="wrap", verticalalign="middle"))
    doc.automaticstyles.addElement(hdr)
    cell = Style(name="cell", family="table-cell")
    cell.addElement(TableCellProperties(wrapoption="wrap", verticalalign="top"))
    doc.automaticstyles.addElement(cell)
    wide = Style(name="wcol", family="table-column")
    wide.addElement(TableColumnProperties(columnwidth="6cm"))
    doc.automaticstyles.addElement(wide)
    med = Style(name="mcol", family="table-column")
    med.addElement(TableColumnProperties(columnwidth="3cm"))
    doc.automaticstyles.addElement(med)
    narrow = Style(name="ncol", family="table-column")
    narrow.addElement(TableColumnProperties(columnwidth="2cm"))
    doc.automaticstyles.addElement(narrow)

    def add_cell(row, val, style=cell):
        c = TableCell(stylename=style, valuetype="string")
        for line in str(val).split("\n"):
            c.addElement(P(text=line))
        row.addElement(c)

    def add_sheet(name, headers, rows, colstyles):
        t = Table(name=name)
        for cs in colstyles:
            t.addElement(TableColumn(stylename=cs))
        hr = TableRow()
        for h in headers:
            add_cell(hr, h, hdr)
        t.addElement(hr)
        for r in rows:
            tr = TableRow()
            for v in r:
                add_cell(tr, v)
            t.addElement(tr)
        doc.spreadsheet.addElement(t)

    # Features sheet
    fheaders = ["Feature ID","Feature Name","Area","User Story","Expected Behaviour",
                "Edge Cases","Validation Rules","Dependencies","Assumptions","Test Cases",
                "Current Status","Defect Count","Severity","Notes","Last Tested Date"]
    frows = []
    for f in data.get("features", []):
        frows.append([
            f.get("id",""), f.get("name",""), f.get("area",""), f.get("user_story",""),
            f.get("expected",""), f.get("edge_cases",""), f.get("validation",""),
            f.get("dependencies",""), f.get("assumptions",""), f.get("test_cases",""),
            f.get("status",""), f.get("defect_count",0), f.get("severity",""),
            f.get("notes",""), f.get("last_tested",""),
        ])
    fcols = ["mcol","mcol","ncol","wcol","wcol","wcol","wcol","mcol","mcol","wcol","ncol","ncol","ncol","wcol","ncol"]
    add_sheet("Features", fheaders, frows, fcols)

    # Defects sheet
    dheaders = ["Defect ID","Feature ID","Reproduction Steps","Expected Result",
                "Actual Result","Severity","Root Cause Hypothesis","Status"]
    drows = []
    for d in data.get("defects", []):
        drows.append([d.get("id",""), d.get("feature_id",""), d.get("repro",""),
                      d.get("expected",""), d.get("actual",""), d.get("severity",""),
                      d.get("root_cause",""), d.get("status","")])
    dcols = ["mcol","mcol","wcol","wcol","wcol","ncol","wcol","ncol"]
    add_sheet("Defects", dheaders, drows, dcols)

    # Summary sheet
    feats = data.get("features", [])
    defs = data.get("defects", [])
    from collections import Counter
    by_area = Counter(f.get("area","?") for f in feats)
    by_status = Counter(f.get("status","?") for f in feats)
    sev = Counter(d.get("severity","?") for d in defs)
    srows = [["Total Features", len(feats)], ["Total Defects", len(defs)], ["", ""],
             ["--- Features by Area ---",""]]
    srows += [[k, v] for k, v in sorted(by_area.items())]
    srows += [["", ""], ["--- Features by Status ---",""]]
    srows += [[k, v] for k, v in sorted(by_status.items())]
    srows += [["", ""], ["--- Defects by Severity ---",""]]
    srows += [[k, v] for k, v in sorted(sev.items())]
    add_sheet("Summary", ["Metric","Value"], srows, ["wcol","mcol"])

    doc.save(out)
    print(f"Wrote {out}: {len(feats)} features, {len(defs)} defects")

if __name__ == "__main__":
    build()
