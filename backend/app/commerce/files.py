"""Immutable generated documents in protected content-addressed storage."""

import hashlib
import io
import os
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.config import settings
from app.core.errors import error

LOGO = Path(__file__).parent / 'assets' / 'ogk-chem.jpg'


def put_file(content: bytes, extension: str) -> dict:
    checksum = hashlib.sha256(content).hexdigest()
    key = f"generated/{checksum[:2]}/{checksum}.{extension}"
    target = settings.storage_dir / key
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        # Atomic publication; an interrupted write never replaces an existing document.
        temporary = target.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, target)
    return {"key": key, "sha256": checksum, "size": len(content), "format": extension}


def read_file(metadata: dict) -> bytes:
    target = (settings.storage_dir / metadata["key"]).resolve()
    if not target.is_relative_to(settings.storage_dir.resolve()):
        error("FILE_NOT_FOUND", "Файл не найден", 404)
    try:
        content = target.read_bytes()
    except OSError:
        error("FILE_UNAVAILABLE", "Файл временно недоступен. Обратитесь к администратору", 503)
    if hashlib.sha256(content).hexdigest() != metadata["sha256"]:
        error(
            "FILE_INTEGRITY",
            "Контрольная сумма документа не совпадает. Восстановите файл из резервной копии",
            503,
        )
    return content


def safe_cell(value):
    # Prevent spreadsheet formula injection from product names and user remarks.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def format_details(value: object) -> str:
    if isinstance(value, dict):
        labels = {
            "tax_id": "ИНН", "inn": "ИНН", "registration_code": "КПП", "kpp": "КПП",
            "legal_address": "Юридический адрес", "bank": "Банк",
            "bank_account": "Расчётный счёт", "account": "Расчётный счёт",
            "bank_code": "БИК", "bic": "БИК",
            "registration_number": "ОГРН", "ogrn": "ОГРН",
        }
        parts = [
            f"{labels.get(key, key)}: {item}"
            for key, item in value.items()
            if item is not None and str(item).strip()
        ]
        return " · ".join(parts) or "—"
    return str(value) if value else "—"


def party_details(party: dict) -> str:
    details = party.get("details") or {}
    keys = {"ИНН": ("ИНН", "inn", "tax_id"), "КПП": ("КПП", "kpp"),
            "Юридический адрес": ("Юридический адрес", "legal_address", "address"),
            "ОГРН": ("ОГРН", "ogrn", "registration_number")}
    legal = {label: next((details[key] for key in aliases if details.get(key)), None)
             for label, aliases in keys.items()}
    legal["ИНН"] = party.get("tax_id") or legal["ИНН"]
    return format_details(legal)


def payment_bank(snapshot):
    saved = snapshot.get("bank_details") or snapshot.get("seller", {}).get("details") or {}
    keys = {"Банк получателя": ("Банк", "bank", "bank_name"), "БИК": ("БИК", "bic", "bank_code"),
            "Корреспондентский счёт": ("Корреспондентский счёт", "correspondent_account", "correspondent"),
            "Расчётный счёт": ("Расчётный счёт", "settlement_account", "account", "bank_account"),
            "ИНН": ("ИНН", "inn", "tax_id"), "КПП": ("КПП", "kpp"),
            "Получатель": ("Получатель", "recipient")}
    result = {label: next((str(saved[key]) for key in aliases if saved.get(key)), "—")
              for label, aliases in keys.items()}
    if result["ИНН"] == "—":
        result["ИНН"] = snapshot["seller"].get("tax_id") or "—"
    if result["Получатель"] == "—":
        result["Получатель"] = snapshot["seller"]["name"]
    return result


def pdf_decimal(value: object, *, money: bool = False) -> str:
    """Format a numeric snapshot value for a customer document, without changing it."""
    number = Decimal(str(value))
    if money:
        fraction = format(number, "f").partition(".")[2].rstrip("0")
        places = max(2, len(fraction))
        formatted = f"{number:,.{places}f}"
    else:
        formatted = f"{number:,f}".rstrip("0").rstrip(".") if "." in f"{number:,f}" else f"{number:,f}"
    return formatted.replace(",", "\u00a0").replace(".", ",")


def pdf_unit(value: str) -> str:
    return {"pcs": "шт.", "kg": "кг", "g": "г", "l": "л", "ml": "мл"}.get(value, value)


def workbook(headers: list[str], rows: list[list], title: str) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = title[:31]
    sheet.append(headers)
    for row in rows:
        sheet.append([safe_cell(value) for value in row])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="213D37")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[1].height = 32
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = min(
            45, max(13, max(len(str(cell.value or "")) for cell in column) + 2)
        )
        for cell in column[1:]:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.number_format = "@"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def document_files(snapshot: dict) -> dict:
    is_proposal = snapshot.get("kind", "proposal" if "предложение" in snapshot["title"].lower() else "invoice") == "proposal"
    gross_prices = snapshot.get("price_includes_vat", True)
    price_label = "Цена за единицу с НДС" if gross_prices else "Цена за единицу без НДС"
    signature = snapshot.get("signature") or {}
    rows = [
        [
            str(index),
            row["description"],
            row.get("cas", ""),
            row["quantity"],
            row["unit"],
            row["unit_price"],
            row["net"],
            row["tax"],
            row["total"],
        ]
        for index, row in enumerate(snapshot["lines"], 1)
    ]
    headers = [
        "№",
        "Наименование и характеристики",
        "CAS",
        "Кол-во",
        "Ед.",
        "Цена без налога",
        "Без налога",
        "Налог",
        "Итого",
    ]
    if is_proposal:
        headers = ["№", "Наименование", "Кол-во", "Ед.", price_label, "Сумма с НДС"]
        rows = [[str(index), row["description"], row["quantity"], pdf_unit(row["unit"]), row["unit_price"], row["total"]]
                for index, row in enumerate(snapshot["lines"], 1)]
    else:
        headers[5] = price_label
    xlsx_rows = [
        [snapshot["title"], snapshot["number"], snapshot["date"]],
        ["Заявка", snapshot.get("request_number", "")],
        ["Продавец", snapshot["seller"]["name"], party_details(snapshot["seller"])],
        ["Клиент", snapshot["client"]["name"], party_details(snapshot["client"])],
        ["Валюта", snapshot["currency"]],
        [],
        headers,
        *rows,
        [],
        [
            "Итого",
            "",
            "",
            "",
            "",
            "",
            snapshot["totals"]["net"],
            snapshot["totals"]["tax"],
            snapshot["totals"]["total"],
        ],
        *([["Общий срок поставки, дней", snapshot["delivery_days"]]] if snapshot.get("delivery_days") is not None else []),
        ["Условия", snapshot["terms"]],
        ["Действует / оплатить до", snapshot["valid_until"]],
    ]
    if is_proposal:
        total_index = next(index for index, row in enumerate(xlsx_rows) if row and row[0] == "Итого")
        xlsx_rows[total_index] = ["Итого с НДС", "", "", "", "", snapshot["totals"]["total"]]
        xlsx_rows.insert(total_index + 1, ["В том числе НДС", snapshot["totals"]["tax"]])
    xlsx = workbook([snapshot["title"]], xlsx_rows, "Документ")
    book = load_workbook(io.BytesIO(xlsx))
    logo = ExcelImage(str(LOGO))
    logo.width, logo.height = 210, 70
    book.active.add_image(logo, "E1" if is_proposal else "G1")
    bank = payment_bank(snapshot) if not is_proposal else {}
    bank_start = book.active.max_row + 2
    for index, (key, value) in enumerate(bank.items(), bank_start):
        book.active.cell(index, 1, key)
        book.active.cell(index, 2, safe_cell(value))
        book.active.merge_cells(start_row=index, start_column=2, end_row=index, end_column=5)
        for cell in book.active[index][:5]:
            cell.border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
            cell.alignment = Alignment(wrap_text=True, vertical='top')
        book.active.row_dimensions[index].height = 30
    sheet = book.active
    for cell in sheet[8]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="213D37")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    sheet.row_dimensions[8].height = 32
    for index, row in enumerate(snapshot["lines"], 9):
        sheet.row_dimensions[index].height = max(28, 16 * ((len(row["description"]) + 39) // 40))
    signature_start = sheet.max_row + 3
    for offset, (key, value) in enumerate((
        ("Должность", signature.get("position") or "________________________"),
        ("ФИО", signature.get("name") or "________________________"),
        ("Подпись", "________________________"),
        ("Место печати", "М.П."),
    )):
        sheet.cell(signature_start + offset, 1, key)
        sheet.cell(signature_start + offset, 2, safe_cell(value))
        sheet.row_dimensions[signature_start + offset].height = 28
    sheet.print_options.horizontalCentered = True
    sheet.print_title_rows = "8:8"
    sheet.print_area = sheet.dimensions
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    book.active.page_setup.orientation = 'landscape'
    book.active.page_setup.fitToWidth = 1
    book.active.page_setup.fitToHeight = 0
    branded = io.BytesIO()
    book.save(branded)
    xlsx = branded.getvalue()
    font_name = "CRMUnicode"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        candidates = [
            os.getenv("CRM_PDF_FONT", ""),
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
        ]
        font = next((path for path in candidates if path and Path(path).is_file()), None)
        if not font:
            error("PDF_FONT_MISSING", "Не установлен шрифт Unicode для печатных форм", 503)
        pdfmetrics.registerFont(TTFont(font_name, font))
    buffer = io.BytesIO()
    money_values = [pdf_decimal(value, money=True) for row in snapshot["lines"]
                    for value in (row["unit_price"], row["net"], row["tax"], row["total"])]
    wide = any(pdfmetrics.stringWidth(value, font_name, 6.5) > 60 for value in money_values)
    widths = [20, 185, 55, 70, 70, 60, 75] if not wide else [25, 230, 80, 115, 105, 95, 125]
    if is_proposal:
        widths = [20, 270, 55, 90, 100] if wide else [20, 250, 55, 85, 125]
    pdf = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4) if wide else A4,
        rightMargin=30,
        leftMargin=30,
        topMargin=32,
        bottomMargin=32,
        title=f"{snapshot['title']} {snapshot['number']}",
    )
    style = ParagraphStyle("body", fontName=font_name, fontSize=8, leading=11, spaceAfter=8)
    title_style = ParagraphStyle("title", parent=style, fontSize=16, leading=20, spaceAfter=16)
    number_style = ParagraphStyle("number", parent=style, alignment=TA_RIGHT, splitLongWords=0)

    def p(text):
        return Paragraph(escape(str(text)), style)

    def number(value, width, *, money=False):
        value = pdf_decimal(value, money=money)
        available = width - 10
        size = min(8, max(5.5, 8 * available / max(pdfmetrics.stringWidth(value, font_name, 8), 1)))
        cell_style = ParagraphStyle("number-cell", parent=number_style, fontSize=size, leading=size + 2)
        return Paragraph(escape(value), cell_style)

    content = [
        Image(str(LOGO), width=190, height=190 * 425 / 1280, hAlign='LEFT'),
        Spacer(1, 14),
        Paragraph(escape(f"{snapshot['title']} № {snapshot['number']}"), title_style),
        p(f"Дата: {snapshot['date']} · Валюта: {snapshot['currency']}"),
        p(f"Заявка: {snapshot.get('request_number', '')}"),
        p(f"Продавец: {snapshot['seller']['name']}"),
        p(party_details(snapshot["seller"])),
        p(f"Клиент: {snapshot['client']['name']}"),
        p(party_details(snapshot["client"])),
        Spacer(1, 8),
    ]
    bank_table = Table([[p(key), p(value)] for key, value in payment_bank(snapshot).items()], colWidths=[sum(widths) * .35, sum(widths) * .65])
    bank_table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), .6, colors.HexColor('#596C61')), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
    if not is_proposal:
        content.insert(4, bank_table)
        content.insert(5, Spacer(1, 10))
    pdf_headers = ["№", "Наименование", "Кол-во / ед.", "Цена/ед.", "Без налога", "Налог", "Итого"]
    if is_proposal:
        pdf_headers = ["№", "Наименование", "Кол-во / ед.", price_label, "Сумма с НДС"]
    else:
        pdf_headers[3] = price_label
    table_rows = [[p(value) for value in pdf_headers]]
    for index, row in enumerate(snapshot["lines"], 1):
        title = row["description"]
        cells = [p(index), p(title), p(f"{pdf_decimal(row['quantity'])} {pdf_unit(row['unit'])}"),
                 number(row["unit_price"], widths[3], money=True)]
        cells += ([number(row["total"], widths[4], money=True)] if is_proposal else [
            number(row["net"], widths[4], money=True), number(row["tax"], widths[5], money=True),
            number(row["total"], widths[6], money=True)])
        table_rows.append(cells)
    table_rows.append([p(""), p("Итого с НДС"), p(""), p("") , number(snapshot["totals"]["total"], widths[4], money=True)] if is_proposal else [
        p(""), p("Итого"), p(""), p(""), number(snapshot["totals"]["net"], widths[4], money=True),
        number(snapshot["totals"]["tax"], widths[5], money=True), number(snapshot["totals"]["total"], widths[6], money=True),
    ])
    table = Table(table_rows, colWidths=widths, repeatRows=1)
    table_styles = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F0EB")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E8F0EB")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C6D3CC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]
    if len(table_rows) > 10:
        table_styles.append(("NOSPLIT", (0, len(table_rows) - 7), (-1, -1)))
    table.setStyle(TableStyle(table_styles))
    # Widths fit the printable area of A4 in either orientation.
    content.extend(
        [
            table,
            *([p(f"В том числе НДС: {pdf_decimal(snapshot['totals']['tax'], money=True)} {snapshot['currency']}")] if is_proposal else []),
            Spacer(1, 14),
            *([p(f"Общий срок поставки: {snapshot['delivery_days']} дней")] if snapshot.get("delivery_days") is not None else []),
            p(f"Условия: {snapshot['terms']}"),
            p(f"Действует / оплатить до: {snapshot['valid_until']}"),
        ]
    )
    content.append(KeepTogether([
        Spacer(1, 24),
        p(f"Должность: {signature.get('position') or '________________________'}"),
        p(f"ФИО: {signature.get('name') or '________________________'}"),
        p("Подпись: ________________________"),
        Spacer(1, 22), p("Место печати    М.П."), Spacer(1, 20),
    ]))
    pdf.build(content)
    return {"pdf": put_file(buffer.getvalue(), "pdf"), "xlsx": put_file(xlsx, "xlsx")}
