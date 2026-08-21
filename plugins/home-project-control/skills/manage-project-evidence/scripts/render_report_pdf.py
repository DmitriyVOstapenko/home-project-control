#!/usr/bin/env python3
"""Render a Markdown conclusion to a verified PDF and write the pair safely."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import uuid
from pathlib import Path

from inspect_project import is_linklike, require_ready_project


DASH_TRANSLATION = str.maketrans({
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u2212": "-",
})
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def require_pdf_dependencies() -> None:
    try:
        import reportlab  # noqa: F401
        import pypdf  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "PDF output requires Python packages reportlab and pypdf; install them before applying the report"
        ) from exc


def font_candidates() -> list[tuple[Path, Path | None]]:
    executable = Path(sys.executable).resolve()
    candidates = [
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
        (Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf")),
        (Path("/Library/Fonts/Arial Unicode.ttf"), None),
        (Path("/Library/Fonts/Arial.ttf"), Path("/Library/Fonts/Arial Bold.ttf")),
    ]
    for parent in executable.parents:
        candidates.append(
            (
                parent / "native" / "poppler" / "Library" / "share" / "fonts" / "DejaVuSans.ttf",
                parent / "native" / "poppler" / "Library" / "share" / "fonts" / "DejaVuSans-Bold.ttf",
            )
        )
    return candidates


def register_fonts() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for regular, bold in font_candidates():
        if not regular.is_file():
            continue
        regular_name = "HomeControlSans"
        bold_name = "HomeControlSansBold"
        if regular_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(regular_name, str(regular)))
        bold_path = bold if bold is not None and bold.is_file() else regular
        if bold_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
        return regular_name, bold_name
    raise RuntimeError("PDF output requires a Unicode TrueType font with Cyrillic support")


def normalized_text(value: str) -> str:
    return value.translate(DASH_TRANSLATION).replace("\u00a0", " ")


def inline_markup(value: str) -> str:
    value = normalized_text(value)
    output: list[str] = []
    cursor = 0
    for match in LINK_RE.finditer(value):
        output.append(html.escape(value[cursor:match.start()]))
        label = html.escape(match.group(1))
        href = html.escape(match.group(2), quote=True)
        output.append(f'<link href="{href}" color="#1F5A88">{label}</link>')
        cursor = match.end()
    output.append(html.escape(value[cursor:]))
    rendered = "".join(output)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", rendered)
    rendered = re.sub(r"`([^`]+)`", r'<font color="#4B5563">\1</font>', rendered)
    return rendered


def table_cells(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def markdown_story(markdown: str, title: str):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, Preformatted, Spacer, Table, TableStyle

    regular, bold = register_fonts()
    sample = getSampleStyleSheet()
    body = ParagraphStyle(
        "HomeControlBody",
        parent=sample["BodyText"],
        fontName=regular,
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#1F2937"),
        spaceAfter=2.5 * mm,
        splitLongWords=True,
    )
    styles = {
        1: ParagraphStyle(
            "HomeControlH1",
            parent=body,
            fontName=bold,
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#183B56"),
            spaceBefore=3 * mm,
            spaceAfter=5 * mm,
        ),
        2: ParagraphStyle(
            "HomeControlH2",
            parent=body,
            fontName=bold,
            fontSize=13,
            leading=17,
            textColor=colors.HexColor("#1F5A88"),
            spaceBefore=4 * mm,
            spaceAfter=2.5 * mm,
        ),
        3: ParagraphStyle(
            "HomeControlH3",
            parent=body,
            fontName=bold,
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#35566F"),
            spaceBefore=3 * mm,
            spaceAfter=2 * mm,
        ),
    }
    bullet = ParagraphStyle("HomeControlBullet", parent=body, leftIndent=6 * mm, firstLineIndent=-3 * mm)
    code = ParagraphStyle(
        "HomeControlCode",
        parent=body,
        fontName=regular,
        fontSize=8,
        leading=10,
        leftIndent=4 * mm,
        rightIndent=4 * mm,
        backColor=colors.HexColor("#F3F4F6"),
        borderPadding=3 * mm,
    )
    table_header = ParagraphStyle(
        "HomeControlTableHeader", parent=body, fontName=bold, fontSize=7.5, leading=9, textColor=colors.white
    )
    table_body = ParagraphStyle("HomeControlTableBody", parent=body, fontSize=7.5, leading=9, spaceAfter=0)

    lines = normalized_text(markdown).splitlines()
    story: list[object] = []
    if not any(line.startswith("# ") for line in lines):
        story.append(Paragraph(inline_markup(title), styles[1]))
    index = 0
    in_code = False
    code_lines: list[str] = []
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("```"):
            if in_code:
                story.append(Preformatted("\n".join(code_lines), code))
                code_lines = []
                in_code = False
            else:
                in_code = True
            index += 1
            continue
        if in_code:
            code_lines.append(line)
            index += 1
            continue
        if line.strip() == "\f":
            story.append(PageBreak())
            index += 1
            continue
        if "|" in line and index + 1 < len(lines) and TABLE_SEPARATOR_RE.match(lines[index + 1]):
            raw_rows = [table_cells(line)]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                raw_rows.append(table_cells(lines[index]))
                index += 1
            column_count = max(len(row) for row in raw_rows)
            rows = []
            for row_number, row in enumerate(raw_rows):
                row += [""] * (column_count - len(row))
                style = table_header if row_number == 0 else table_body
                rows.append([Paragraph(inline_markup(cell), style) for cell in row])
            available_width = A4[0] - 32 * mm
            table = Table(rows, colWidths=[available_width / column_count] * column_count, repeatRows=1, splitByRow=1)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F5A88")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.extend([table, Spacer(1, 3 * mm)])
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level = min(len(heading.group(1)), 3)
            story.append(Paragraph(inline_markup(heading.group(2)), styles[level]))
        elif re.match(r"^\s*[-*+]\s+", line):
            content = re.sub(r"^\s*[-*+]\s+", "", line)
            story.append(Paragraph("• " + inline_markup(content), bullet))
        elif re.match(r"^\s*\d+[.)]\s+", line):
            marker = re.match(r"^\s*(\d+[.)])\s+", line)
            content = line[marker.end():] if marker else line
            prefix = f"{marker.group(1)} " if marker else ""
            story.append(Paragraph(prefix + inline_markup(content), bullet))
        elif line.strip():
            story.append(Paragraph(inline_markup(line.strip()), body))
        elif story and not isinstance(story[-1], Spacer):
            story.append(Spacer(1, 1.5 * mm))
        index += 1
    if in_code:
        story.append(Preformatted("\n".join(code_lines), code))
    if not story:
        story.append(Paragraph(inline_markup(title), ParagraphStyle("EmptyTitle", parent=styles[1], alignment=TA_CENTER)))
    return story, regular


def render_pdf(markdown: str, output: Path, title: str) -> None:
    require_pdf_dependencies()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    story, regular_font = markdown_story(markdown, title)

    def page(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont(regular_font, 7.5)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(16 * mm, 10 * mm, "Контроль дома")
        canvas.drawRightString(A4[0] - 16 * mm, 10 * mm, f"Страница {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=17 * mm,
        title=normalized_text(title),
        author="Контроль дома",
        subject="Доказательное заключение по проекту",
    )
    document.build(story, onFirstPage=page, onLaterPages=page)
    validate_pdf(output)


def validate_pdf(path: Path) -> None:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if not reader.pages:
        raise RuntimeError("generated PDF has no pages")
    extracted = "".join((page.extract_text() or "") for page in reader.pages).strip()
    if not extracted:
        raise RuntimeError("generated PDF has no extractable text")


def write_report_pair(markdown_path: Path, markdown: str, title: str, replace: bool = True) -> tuple[Path, Path]:
    if markdown_path.suffix.lower() != ".md":
        raise ValueError("report target must use the .md extension")
    pdf_path = markdown_path.with_suffix(".pdf")
    for target in (markdown_path, pdf_path):
        if is_linklike(target) or (target.exists() and not target.is_file()):
            raise ValueError(f"unsafe report target: {target}")
        if target.exists() and not replace:
            raise ValueError(f"refusing to overwrite report target: {target}")
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    if is_linklike(markdown_path.parent):
        raise ValueError("unsafe report directory")

    token = uuid.uuid4().hex
    temporary_md = markdown_path.with_name(f".{markdown_path.name}.{token}.tmp")
    temporary_pdf = pdf_path.with_name(f".{pdf_path.name}.{token}.tmp.pdf")
    backup_md = markdown_path.with_name(f".{markdown_path.name}.{token}.bak")
    backup_pdf = pdf_path.with_name(f".{pdf_path.name}.{token}.bak")
    old_md = markdown_path.exists()
    old_pdf = pdf_path.exists()
    try:
        temporary_md.write_text(markdown.rstrip() + "\n", encoding="utf-8")
        render_pdf(markdown, temporary_pdf, title)
        if old_md:
            os.replace(markdown_path, backup_md)
        if old_pdf:
            os.replace(pdf_path, backup_pdf)
        os.replace(temporary_md, markdown_path)
        os.replace(temporary_pdf, pdf_path)
        validate_pdf(pdf_path)
        backup_md.unlink(missing_ok=True)
        backup_pdf.unlink(missing_ok=True)
    except Exception:
        if markdown_path.exists() and (not old_md or backup_md.exists()):
            markdown_path.unlink()
        if pdf_path.exists() and (not old_pdf or backup_pdf.exists()):
            pdf_path.unlink()
        if backup_md.exists():
            os.replace(backup_md, markdown_path)
        if backup_pdf.exists():
            os.replace(backup_pdf, pdf_path)
        raise
    finally:
        for path in (temporary_md, temporary_pdf, backup_md, backup_pdf):
            path.unlink(missing_ok=True)
    return markdown_path, pdf_path


def safe_report_target(root: Path, relative: str) -> Path:
    normalized = Path(relative)
    if normalized.is_absolute() or normalized.suffix.lower() != ".md" or ".." in normalized.parts:
        raise ValueError("target must be a safe relative .md path")
    reports = (root / ".home-control" / "reports").resolve()
    target = (reports / normalized).resolve()
    try:
        target.relative_to(reports)
    except ValueError as exc:
        raise ValueError("target escapes the reports directory") from exc
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("source_markdown", type=Path)
    parser.add_argument("--target", required=True, help="Relative path below .home-control/reports ending in .md")
    parser.add_argument("--title")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = require_ready_project(args.project_dir)
    if not args.source_markdown.is_file() or is_linklike(args.source_markdown):
        raise ValueError("source Markdown must be a regular file")
    markdown = args.source_markdown.read_text(encoding="utf-8")
    title = args.title or next(
        (line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")),
        args.source_markdown.stem,
    )
    target = safe_report_target(root, args.target)
    pdf_target = target.with_suffix(".pdf")
    if not args.replace and (target.exists() or pdf_target.exists()):
        raise ValueError("report target exists; preview again with --replace if replacement is intended")
    require_pdf_dependencies()
    result: dict[str, object] = {
        "mode": "preview",
        "source": str(args.source_markdown.resolve()),
        "would_create_or_replace": [str(target), str(pdf_target)],
        "title": title,
    }
    if args.apply:
        write_report_pair(target, markdown, title, replace=args.replace)
        result = {"mode": "applied", "created_or_replaced": [str(target), str(pdf_target)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        print(f"Report PDF failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
