"""Customer-facing formatting must leave immutable document amounts intact."""

import io

from openpyxl import load_workbook

from app.commerce.files import document_files, format_details, party_details, pdf_decimal, pdf_unit, read_file
from app.core.config import settings


def test_pdf_number_and_requisites_formatting():
    assert pdf_decimal("49077.00000000", money=True) == "49\u00a0077,00"
    assert pdf_decimal("0.12345678", money=True) == "0,12345678"
    assert pdf_decimal("123456789012.50", money=True) == "123\u00a0456\u00a0789\u00a0012,50"
    assert pdf_decimal("2.000000") == "2"
    assert pdf_unit("pcs") == "шт."
    assert format_details({"tax_id": "7700000000", "registration_code": "770001001"}) == (
        "ИНН: 7700000000 · КПП: 770001001"
    )
    assert party_details({"details": {}, "tax_id": "7800000000"}) == "ИНН: 7800000000"


def test_proposal_and_invoice_pdf_export_preserve_xlsx_amounts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    for title, line_count in (("Коммерческое предложение", 45), ("Счёт на оплату", 2)):
        lines = [{
            "description": "Реагент с длинным наименованием для проверки переноса строки " * 2,
            "cas": "64-17-5", "quantity": "2.000000", "unit": "pcs",
            "unit_price": "49077.00000000", "net": "80454.00", "tax": "17700.00",
            "total": "98154.00",
        } for _ in range(line_count)]
        snapshot = {
            "title": title, "number": f"QA-{line_count}", "date": "2026-09-30",
            "currency": "RUB", "request_number": "QA-REQUEST",
            "seller": {"name": "Тестовый продавец", "details": {"tax_id": "TEST"}},
            "client": {"name": "Тестовый клиент", "details": {}},
            "lines": lines, "totals": {"net": str(80454 * line_count),
                                       "tax": str(17700 * line_count),
                                       "total": str(98154 * line_count)},
            "terms": "Предоплата", "valid_until": "2026-10-15",
        }
        files = document_files(snapshot)
        pdf = read_file(files["pdf"])
        assert pdf.startswith(b"%PDF-") and len(pdf) > 2000
        book = load_workbook(io.BytesIO(read_file(files["xlsx"])), read_only=True)
        assert any(cell.value == "49077.00000000" for row in book.active for cell in row)
        assert any(cell.value == str(98154 * line_count) for row in book.active for cell in row)
        book.close()
