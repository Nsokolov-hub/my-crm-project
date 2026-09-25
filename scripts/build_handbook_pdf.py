#!/usr/bin/env python3
"""Render the Markdown user handbook as a compact branded PDF.

This optional documentation tool needs reportlab. The resulting PDF is a
distribution artifact; the editable source remains docs/user-handbook.md.
"""

from __future__ import annotations

import argparse
import re
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "user-handbook.md"
DEFAULT_OUTPUT = ROOT / "output" / "pdf" / "reaktiv-crm-handbook.pdf"
NAVY = colors.HexColor("#202233")
INK = colors.HexColor("#242638")
MUTED = colors.HexColor("#666a7a")
PURPLE = colors.HexColor("#7960d6")
LIME = colors.HexColor("#c7ed89")
PAPER = colors.HexColor("#f6f7fa")


def register_fonts() -> None:
    candidates = [
        (
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial Italic.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
        ),
        (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/ariali.ttf"),
        ),
    ]
    selected = next((item for item in candidates if all(path.exists() for path in item)), None)
    if selected is None:
        raise SystemExit("Не найден Arial или DejaVu Sans TTF с кириллицей для PDF")
    paths = dict(zip(("Handbook", "Handbook-Bold", "Handbook-Italic"), selected))
    for name, path in paths.items():
        pdfmetrics.registerFont(TTFont(name, str(path)))
    pdfmetrics.registerFontFamily(
        "Handbook", normal="Handbook", bold="Handbook-Bold", italic="Handbook-Italic"
    )


def clean_text(value: str) -> str:
    # ReportLab's TTF output is most predictable with ordinary ASCII hyphens.
    return value.replace("—", " - ").replace("–", "-").replace("‑", "-")


def inline(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = escape(value)
    value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"`([^`]+)`", r'<font color="#5d49a8">\1</font>', value)
    return value


def styles() -> dict[str, ParagraphStyle]:
    base = {"fontName": "Handbook", "textColor": INK, "allowWidows": 0, "allowOrphans": 0}
    return {
        "body": ParagraphStyle(
            "Body", fontSize=9.2, leading=14.3, spaceAfter=8, alignment=TA_LEFT, **base
        ),
        "h2": ParagraphStyle(
            "Section", fontName="Handbook-Bold", fontSize=16, leading=20,
            textColor=NAVY, spaceBefore=17, spaceAfter=9, keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "Subsection", fontName="Handbook-Bold", fontSize=11.4, leading=15,
            textColor=PURPLE, spaceBefore=12, spaceAfter=6, keepWithNext=True,
        ),
        "list": ParagraphStyle(
            "List", fontSize=9, leading=13.8, leftIndent=18, firstLineIndent=-15,
            spaceAfter=5, **base
        ),
        "table": ParagraphStyle(
            "Table", fontSize=8.3, leading=11.5, spaceAfter=0, **base
        ),
        "table_head": ParagraphStyle(
            "TableHead", fontName="Handbook-Bold", fontSize=8.4,
            leading=11.5, textColor=colors.white,
        ),
        "note": ParagraphStyle(
            "Note", fontSize=8.4, leading=12.6, textColor=MUTED,
            spaceAfter=7, **{k: v for k, v in base.items() if k != "textColor"}
        ),
    }


def table_flowable(lines: list[str], s: dict[str, ParagraphStyle], width: float) -> Table:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    rows = [row for row in rows if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in row)]
    if not rows:
        return Table([])
    count = max(len(row) for row in rows)
    # The handbook has two-column glossary/navigation tables. A wider second
    # column leaves room for the explanations without shrinking the type.
    widths = [width * 0.27, width * 0.73] if count == 2 else [width / count] * count
    cells = []
    for index, row in enumerate(rows):
        cells.append([
            Paragraph(inline(row[i]) if i < len(row) else "", s["table_head" if index == 0 else "table"])
            for i in range(count)
        ])
    table = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#e9eaf0")),
    ]))
    return table


def palette_flowable(width: float) -> Table:
    labels = [
        ("Навигация", "#202233", NAVY, colors.white),
        ("Акцент", "#7960d6", PURPLE, colors.white),
        ("Призыв к действию", "#c7ed89", LIME, NAVY),
        ("Фон", "#f6f7fa", PAPER, NAVY),
    ]
    cells = []
    for label, code, _, foreground in labels:
        cells.append(Paragraph(
            f'<font color="{foreground.hexval().replace("0x", "#")}"><b>{label}</b><br/>{code}</font>',
            ParagraphStyle("Swatch", fontName="Handbook", fontSize=7.7, leading=11),
        ))
    table = Table([cells], colWidths=[width / 4] * 4, hAlign="LEFT")
    table.setStyle(TableStyle([
        *[("BACKGROUND", (i, 0), (i, 0), item[2]) for i, item in enumerate(labels)],
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, 0), 8),
        ("RIGHTPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 11),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 11),
    ]))
    return table


def content(source: str, width: float) -> list:
    s = styles()
    lines = source.splitlines()
    story: list = [Spacer(1, 680), PageBreak()]
    paragraph: list[str] = []
    index = 0

    def flush() -> None:
        if paragraph:
            story.append(Paragraph(inline(" ".join(paragraph)), s["body"]))
            paragraph.clear()

    while index < len(lines):
        line = lines[index].strip()
        if not line:
            flush()
            index += 1
            continue
        if line.startswith("# "):
            index += 1
            continue  # The title is on the designed cover.
        if line.startswith("## "):
            flush()
            heading = line[3:]
            story.append(Paragraph(inline(heading), s["h2"]))
            story.append(HRFlowable(width="100%", thickness=1, color=LIME, spaceAfter=7))
            index += 1
            continue
        if line.startswith("### "):
            flush()
            heading = line[4:]
            story.append(Paragraph(inline(heading), s["h3"]))
            if heading == "Визуальный язык текущего интерфейса":
                story.append(palette_flowable(width))
                story.append(Spacer(1, 8))
            index += 1
            continue
        if line.startswith("|"):
            flush()
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            story.append(table_flowable(table_lines, s, width))
            story.append(Spacer(1, 9))
            continue
        ordered = re.match(r"^(\d+)\.\s+(.*)$", line)
        if ordered:
            flush()
            story.append(Paragraph(
                f'<font color="#7960d6"><b>{ordered.group(1)}.</b></font> {inline(ordered.group(2))}',
                s["list"],
            ))
            index += 1
            continue
        if line.startswith("- "):
            flush()
            story.append(Paragraph(f'<font color="#7960d6"><b>•</b></font>&nbsp;{inline(line[2:])}', s["list"]))
            index += 1
            continue
        paragraph.append(line)
        index += 1
    flush()
    return story


def cover(canvas, doc) -> None:
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, width, height, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#2e3045"))
    for x, y, radius in [(460, 685, 175), (565, 205, 165), (135, 145, 95)]:
        canvas.circle(x, y, radius, fill=1, stroke=0)
    canvas.setFillColor(LIME)
    canvas.roundRect(48, height - 112, 47, 47, 14, fill=1, stroke=0)
    canvas.setStrokeColor(NAVY)
    canvas.setLineWidth(2.4)
    canvas.line(65, height - 76, 79, height - 76)
    canvas.line(68, height - 76, 68, height - 87)
    canvas.line(76, height - 76, 76, height - 87)
    canvas.line(68, height - 87, 59, height - 102)
    canvas.line(76, height - 87, 85, height - 102)
    canvas.line(59, height - 102, 85, height - 102)
    canvas.setFillColor(colors.white)
    canvas.setFont("Handbook-Bold", 40)
    canvas.drawString(48, height - 225, "Реактив CRM")
    canvas.setFillColor(LIME)
    canvas.setFont("Handbook-Bold", 18)
    canvas.drawString(49, height - 258, "Руководство пользователя")
    canvas.setFillColor(colors.HexColor("#d8d9e7"))
    canvas.setFont("Handbook", 12)
    for offset, text in enumerate([
        "Для владельца компании, сотрудников и команды внедрения",
        "Продукт • роли • сквозной процесс • передача",
    ]):
        canvas.drawString(49, height - 315 - offset * 25, text)
    canvas.setStrokeColor(LIME)
    canvas.setLineWidth(3)
    canvas.line(49, 190, width - 49, 190)
    canvas.setFillColor(colors.white)
    canvas.setFont("Handbook-Bold", 11)
    canvas.drawString(49, 155, "ВЕРСИЯ ДЛЯ РУЧНОЙ ПРИЁМКИ")
    canvas.setFont("Handbook", 10)
    canvas.drawString(49, 132, "25 сентября 2026 г.  |  Текущий интерфейс и стартовые роли")


def page(canvas, doc) -> None:
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 36, width, 36, fill=1, stroke=0)
    canvas.setFillColor(LIME)
    canvas.setFont("Handbook-Bold", 9)
    canvas.drawString(47, height - 23, "РЕАКТИВ CRM")
    canvas.setFillColor(colors.white)
    canvas.setFont("Handbook", 8)
    canvas.drawRightString(width - 47, height - 23, "Руководство пользователя")
    canvas.setStrokeColor(colors.HexColor("#dadce5"))
    canvas.setLineWidth(0.5)
    canvas.line(47, 41, width - 47, 41)
    canvas.setFont("Handbook", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(47, 27, "Текущая версия продукта • 25.09.2026")
    canvas.drawRightString(width - 47, 27, str(doc.page - 1))


def main() -> None:
    parser = argparse.ArgumentParser(description="Собрать PDF-руководство Реактив CRM")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    register_fonts()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(args.output), pagesize=A4, leftMargin=47, rightMargin=47,
        topMargin=57, bottomMargin=57, title="Реактив CRM - руководство пользователя",
        author="Команда проекта Реактив CRM", subject="Руководство по CRM и ролям",
    )
    story = content(SOURCE.read_text(encoding="utf-8"), A4[0] - 94)
    doc.build(story, onFirstPage=cover, onLaterPages=page)
    print(args.output)


if __name__ == "__main__":
    main()
