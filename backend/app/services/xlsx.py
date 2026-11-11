"""Read and write small .xlsx workbooks with the standard library (zip + XML), no dependency.

Writing covers what reports need: several sheets, text and numbers, a bold header row,
column widths and a frozen first row. Text is stored as inline strings, so a
spreadsheet never runs a post's text as a formula.

Reading (stats import, scheduled-post import) returns every cell as text, as Excel
stored it: dates come back as Excel serial numbers (see `excel_serial_to_datetime`).
Uploaded files are untrusted, so the unzipped size is capped (zip bombs).
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape

from .errors import InvalidInput

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
# XML 1.0 forbids most control characters; they can't appear even escaped.
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
MAX_CELL = 32767   # Excel's limit for one cell's text
MAX_UNZIPPED = 40 * 1024 * 1024   # guards against zip bombs when reading uploads
NS = f"{{{_MAIN}}}"
REL_NS = f"{{{_REL}}}"
_EXCEL_EPOCH = datetime(1899, 12, 30)   # day 0 of Excel's (1900) date system

STYLES = f"""{_HEAD}<styleSheet xmlns="{_MAIN}">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>\
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


@dataclass
class Sheet:
    name: str
    rows: list[list[Any]]
    bold_rows: set[int] = field(default_factory=set)   # 0-based row numbers shown in bold
    widths: list[float] = field(default_factory=list)  # column widths, in characters
    freeze_header: bool = False


def _col(i: int) -> str:
    letters = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _cell(ref: str, value: Any, bold: bool) -> str:
    style = ' s="1"' if bold else ""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    text = escape(_ILLEGAL.sub("", str(value))[:MAX_CELL])
    return f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(sheet: Sheet) -> str:
    parts = [f'{_HEAD}<worksheet xmlns="{_MAIN}">']
    if sheet.freeze_header:
        parts.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
                     'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    if sheet.widths:
        parts.append("<cols>" + "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
                                        for i, w in enumerate(sheet.widths)) + "</cols>")
    parts.append("<sheetData>")
    for r, row in enumerate(sheet.rows):
        cells = "".join(_cell(f"{_col(c)}{r + 1}", v, r in sheet.bold_rows) for c, v in enumerate(row))
        parts.append(f'<row r="{r + 1}">{cells}</row>')
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def _safe_name(name: str, taken: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Sheet"
    candidate, n = base, 2
    while candidate.lower() in taken:
        candidate, n = f"{base[:28]} {n}", n + 1
    taken.add(candidate.lower())
    return candidate


def write_xlsx(sheets: list[Sheet]) -> bytes:
    taken: set[str] = set()
    names = [_safe_name(s.name, taken) for s in sheets]
    n = len(sheets)
    content_types = (
        f'{_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                  'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                  for i in range(n))
        + "</Types>")
    root_rels = (f'{_HEAD}<Relationships xmlns="{_PKG_REL}"><Relationship Id="rId1" '
                 f'Type="{_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    workbook = (f'{_HEAD}<workbook xmlns="{_MAIN}" xmlns:r="{_REL}"><sheets>'
                + "".join(f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                          for i, name in enumerate(names))
                + "</sheets></workbook>")
    workbook_rels = (f'{_HEAD}<Relationships xmlns="{_PKG_REL}">'
                     + "".join(f'<Relationship Id="rId{i + 1}" Type="{_REL}/worksheet" '
                               f'Target="worksheets/sheet{i + 1}.xml"/>' for i in range(n))
                     + f'<Relationship Id="rId{n + 1}" Type="{_REL}/styles" Target="styles.xml"/>'
                     + "</Relationships>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/styles.xml", STYLES)
        for i, sheet in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(sheet))
    return buf.getvalue()


# ---- reading ---------------------------------------------------------------------

def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _rows(sheet_xml: bytes, shared: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in ET.fromstring(sheet_xml).iter(f"{NS}row"):
        # Excel leaves empty rows out; pad them back so list positions are spreadsheet rows.
        number = int(row.get("r") or len(rows) + 1)
        while len(rows) < number - 1:
            rows.append([])
        cells: dict[int, str] = {}
        for c in row.findall(f"{NS}c"):
            kind, v = c.get("t"), c.find(f"{NS}v")
            if kind == "s" and v is not None:
                value = shared[int(v.text)]
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{NS}t"))
            else:
                value = v.text if v is not None and v.text else ""
            cells[_col_index(c.get("r", "A1"))] = value.strip()
        rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
    return rows


def read_xlsx(data: bytes) -> list[list[list[str]]]:
    """Every sheet of a workbook, in tab order, as rows of cell text."""
    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise InvalidInput("That is an old Excel file (.xls). In Excel choose Save As → Excel Workbook (.xlsx).")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise InvalidInput("That file isn't a readable Excel file.") from exc
    with z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
            raise InvalidInput("That file is too large to read.")
        names = set(z.namelist())
        if "xl/workbook.xml" not in names:
            raise InvalidInput("That file isn't a readable Excel file.")
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{NS}t")) for si in root.findall(f"{NS}si")]
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                rels[r.get("Id")] = r.get("Target", "")
        sheets = []
        for sheet in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{NS}sheet"):
            target = rels.get(sheet.get(f"{REL_NS}id"), "")
            path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
            if path in names:
                sheets.append(_rows(z.read(path), shared))
        return sheets


def excel_serial_to_datetime(value: float) -> datetime:
    """Excel stores a date and time as days since 1899-12-30; the result has no timezone,
    like the cell. Rounded to the minute, since 09:00 is stored as 0.375 (float noise)."""
    moment = _EXCEL_EPOCH + timedelta(days=value)
    return (moment + timedelta(seconds=30)).replace(second=0, microsecond=0)
