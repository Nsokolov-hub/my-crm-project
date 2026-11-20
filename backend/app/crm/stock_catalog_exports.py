"""Branded literal XLSX snapshots. The customer builder accepts only public fields."""

import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.table import Table, TableStyleInfo

INK = '173F35'
GREEN = '82EE8D'
PALE = 'F3F8F2'
GRAY = '65746B'
LINE = 'DCE6DF'
ZONE = ZoneInfo('Europe/Moscow')
LOGO = Path(__file__).parents[1] / 'commerce' / 'assets' / 'ogk-chem.jpg'
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
HEAD_ROW = 7


@dataclass(frozen=True, slots=True)
class ClientCatalogRow:
    name: str
    article: str = ''
    cas: str = ''
    manufacturer: str = ''
    purity: str = ''
    packaging: str = ''
    checked_at: datetime | None = None


def local(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo('UTC'))
    return value.astimezone(ZONE).replace(tzinfo=None)


def literal(cell: Any, value: Any) -> None:
    if isinstance(value, str):
        cell.value = CONTROL.sub('', value)[:32767]
        # A literal type, rather than escaping only '=': imported text never becomes a formula.
        cell.data_type = 's'
        cell.number_format = '@'
    else:
        cell.value = value
    if isinstance(value, datetime):
        cell.number_format = 'dd/mm/yyyy hh:mm'


def setup(title: str, at: datetime, headers: list[str], widths: list[int], context: str,
          *, synthetic: bool = False) -> tuple[Workbook, Any]:
    book = Workbook()
    book.properties.creator = 'ОГК-ХИМ'
    book.properties.lastModifiedBy = 'ОГК-ХИМ'
    book.properties.title = title
    book.properties.subject = 'Каталог наличия'
    book.properties.description = 'Демонстрационные вымышленные данные' if synthetic else ''
    book.properties.keywords = ''
    sheet = book.active
    sheet.title = 'Каталог наличия' if title == 'Каталог наличия' else 'Внутренний каталог'
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 85
    sheet.sheet_properties.tabColor = INK
    for row in sheet.iter_rows(min_row=1, max_row=HEAD_ROW, max_col=len(headers)):
        for cell in row:
            cell.font = Font(name='Arial', size=10, color=INK)
            cell.alignment = Alignment(vertical='center')
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[sheet.cell(HEAD_ROW, index).column_letter].width = width
    literal(sheet.cell(2, 1), title)
    sheet.cell(2, 1).font = Font(name='Arial', size=16, bold=True, color=INK)
    sheet.row_dimensions[2].height = 26
    # The logo lives in reserved top-right space, outside titles and table headers.
    if LOGO.is_file():
        logo = Image(str(LOGO))
        logo.width, logo.height = 160, 53
        sheet.add_image(logo, f'{sheet.cell(1, len(headers) - 1).column_letter}1')
    literal(sheet.cell(3, 1), 'Срез')
    literal(sheet.cell(3, 2), local(at))
    literal(sheet.cell(3, 3), 'Москва (UTC+3)')
    literal(sheet.cell(4, 1), context)
    if synthetic:
        literal(sheet.cell(5, 1), 'ДЕМОНСТРАЦИЯ — вымышленные товары и остатки')
        sheet.cell(5, 1).font = Font(name='Arial', size=10, bold=True, color='9A6600')
    else:
        literal(sheet.cell(5, 1), 'Наличие меняется. Подтвердите количество и срок при заказе.')
        sheet.cell(5, 1).font = Font(name='Arial', size=10, italic=True, color=GRAY)
    for col in range(1, len(headers) + 1):
        sheet.cell(6, col).border = Border(bottom=Side(style='medium', color=GREEN))
    for index, text in enumerate(headers, 1):
        cell = sheet.cell(HEAD_ROW, index)
        literal(cell, text)
        cell.fill = PatternFill('solid', fgColor=INK)
        cell.font = Font(name='Arial', size=10, bold=True, color='FFFFFF')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(right=Side(style='thin', color='FFFFFF'))
    sheet.row_dimensions[HEAD_ROW].height = 32
    sheet.freeze_panes = 'D8'
    sheet.print_title_rows = f'{HEAD_ROW}:{HEAD_ROW}'
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3 if len(headers) > 10 else sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_options.horizontalCentered = True
    sheet.oddFooter.left.text = 'ОГК-ХИМ'
    sheet.oddFooter.right.text = 'Страница &P из &N'
    return book, sheet


def finish(book: Workbook, sheet: Any, headers: list[str], rows: list[list[Any]]) -> bytes:
    for index, values in enumerate(rows, HEAD_ROW + 1):
        height = 28
        for column, value in enumerate(values, 1):
            cell = sheet.cell(index, column)
            literal(cell, value)
            cell.font = Font(name='Arial', size=10, color=INK)
            cell.alignment = Alignment(vertical='center', wrap_text=True,
                                       horizontal='right' if isinstance(value, (int, float, datetime)) else 'left')
            if index % 2 == 0:
                cell.fill = PatternFill('solid', fgColor=PALE)
            width = sheet.column_dimensions[cell.column_letter].width or 20
            lines = sum(1 + len(line) // max(8, int(width - 2)) for line in str(value or '').split('\n'))
            height = max(height, 14 * lines)
        sheet.row_dimensions[index].height = min(409, height)
    last = HEAD_ROW + len(rows)
    end = sheet.cell(max(HEAD_ROW, last), len(headers)).coordinate
    if rows:
        table = Table(displayName='StockCatalog', ref=f'A{HEAD_ROW}:{end}')
        table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium4', showRowStripes=False,
                                             showColumnStripes=False)
        sheet.add_table(table)
    else:
        literal(sheet.cell(HEAD_ROW + 1, 2), 'По выбранным фильтрам позиций нет')
        sheet.cell(HEAD_ROW + 1, 2).font = Font(name='Arial', size=10, color=GRAY)
        sheet.auto_filter.ref = f'A{HEAD_ROW}:{end}'
        last += 1
    sheet.print_area = f'A1:{sheet.cell(last, len(headers)).coordinate}'
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def client_workbook(rows: list[ClientCatalogRow], at: datetime, *, q: str = '', brand: str = '',
                    synthetic: bool = False) -> bytes:
    headers = ['№', 'Наименование', 'Артикул производителя', 'CAS', 'Производитель', 'Чистота',
               'Фасовка', 'Наличие', 'Обновлено, Москва']
    context = '. '.join(value for value in (f'Поиск: {q}' if q else '',
                                            f'Производитель: {brand}' if brand else '') if value
                        ) or 'Все позиции в наличии'
    book, sheet = setup('Каталог наличия', at, headers, [6, 50, 25, 17, 24, 20, 22, 18, 23], context,
                        synthetic=synthetic)
    data = [[index, row.name, row.article, row.cas, row.manufacturer, row.purity, row.packaging,
             'В наличии', local(row.checked_at)] for index, row in enumerate(rows, 1)]
    return finish(book, sheet, headers, data)


def internal_workbook(rows: list[dict[str, Any]], at: datetime, *, mode: str = 'catalog', q: str = '',
                      brand: str = '', source: str = '', synthetic: bool = False) -> bytes:
    headers = ['№', 'Наименование', 'Артикул производителя', 'CAS', 'Производитель', 'Чистота',
               'Фасовка каталога', 'Наличие', 'Источник', 'Артикул источника', 'Фасовка источника',
               'Остаток источника', 'Получено, Москва', 'Актуально до, Москва', 'Проверка', 'Ссылка источника']
    context = '. '.join(value for value in ('На проверке менеджера' if mode == 'review' else 'Проверка наличия',
                                            f'Поиск: {q}' if q else '', f'Производитель: {brand}' if brand else '',
                                            f'Источник: {source}' if source else '') if value)
    book, sheet = setup('Внутренний каталог наличия', at, headers,
                        [6, 45, 25, 17, 23, 20, 22, 18, 24, 24, 22, 24, 23, 23, 36, 48], context,
                        synthetic=synthetic)
    data: list[list[Any]] = []
    for row in rows:
        offers = row.get('offers') or [{}]
        for offer in offers:
            until = offer.get('valid_until')
            fresh = row.get('fresh') is not False and until is not None and until > max(at, datetime.now(UTC))
            status = ('Требует сверки. ' + str(row.get('reason') or '')) if mode == 'review' else 'Точная фасовка'
            if offer and not fresh:
                status = 'Устарело. ' + status
            source_name = offer.get('source_name') or ('Каталог ОГК-ХИМ' if row.get('origin') == 'own' else 'Не указан')
            data.append([len(data) + 1, row['name'], row.get('article') or '', row.get('cas') or '',
                         row.get('manufacturer') or '', row.get('purity') or '', row.get('packaging') or '',
                         'В наличии' if row.get('availability') == 'in_stock' else 'Требует проверки',
                         source_name, offer.get('catalog_number') or '', offer.get('pack') or '',
                         offer.get('quantity') if offer.get('quantity') is not None else 'Не указан',
                         local(offer.get('seen_at') or row.get('checked_at')), local(until), status,
                         offer.get('url') or ''])
    content = finish(book, sheet, headers, data)
    # Hyperlinks are created only for the explicit internal source column.
    # Set them before saving once more; the customer code path never handles URLs.
    for index, values in enumerate(data, HEAD_ROW + 1):
        target = values[-1]
        if target and urlsplit(target).scheme in ('http', 'https'):
            cell = sheet.cell(index, len(headers))
            cell.hyperlink = target
            cell.font = Font(name='Arial', size=10, color='236C47', underline='single')
    if data:
        output = io.BytesIO()
        book.save(output)
        return output.getvalue()
    return content
