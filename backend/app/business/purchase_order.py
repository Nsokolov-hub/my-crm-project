import io
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app.commerce.files import safe_cell


def purchase_order(order):
    book = Workbook()
    sheet = book.active
    sheet.title = "Purchase order"
    customer, supplier = order.snapshot["customer"], order.snapshot["supplier"]

    def address(party):
        details = party.get("details") or {}
        return details.get("legal_address") or details.get("Юридический адрес", "")

    rows = [
        ["Customer", "", "Purchase order №", order.number],
        [customer["name"], "", "Purchase Order Date", order.created_at.date().isoformat()],
        [address(customer), "", "Delivery terms", order.delivery_terms],
        ["", "", "Contract", order.contract],
        ["", "", "Payment conditions", order.payment_terms],
        ["", "", "Expected delivery date", order.expected_date.isoformat()],
        [],
        ["Supplier"],
        [supplier["name"]],
        [address(supplier)],
        [],
        [
            "Name",
            "Manufacturer",
            "Packaging",
            "Cat.",
            "Qty",
            f"Price ({order.currency})",
            f"Total ({order.currency})",
        ],
    ]
    for row in order.snapshot["lines"]:
        rows.append(
            [
                row["name"],
                row.get("manufacturer"),
                row.get("packaging"),
                row.get("article"),
                Decimal(row["quantity"]),
                Decimal(row["unit_price"]),
                Decimal(row["quantity"]) * Decimal(row["unit_price"]),
            ]
        )
    rows.append(["", "", "", "", f"Total ({order.currency})", "", order.total])
    for row in rows:
        sheet.append([safe_cell(value) for value in row])
    for index in (1, 8, 12, len(rows)):
        for cell in sheet[index]:
            cell.font = Font(name="Arial", size=11, bold=True)
            cell.fill = PatternFill("solid", fgColor="E8F0EB")
    for column, width in zip("ABCDEFG", [47, 27, 22, 24, 16, 22, 24]):
        sheet.column_dimensions[column].width = width
    border = Border(*(Side(style="thin", color="C6D3CC") for _ in range(4)))
    for row in sheet:
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if cell.row >= 12:
                cell.border = border
            if cell.row > 12 and cell.column >= 5:
                cell.number_format = "#,##0.00"
    sheet.row_dimensions[3].height = 64
    sheet.row_dimensions[10].height = 64
    sheet.freeze_panes = "A13"
    sheet.print_title_rows = "12:12"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
