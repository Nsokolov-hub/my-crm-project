"""List endpoints issue a fixed number of queries, however many rows a page holds."""

from datetime import timedelta
from decimal import Decimal
from itertools import product
from string import ascii_uppercase

from app.core.db import utcnow
from app.core.models import PermissionGrant, Role, User
from app.crm.models import (
    Counterparty,
    Currency,
    Nomenclature,
    Packing,
    ProductGroup,
    QuoteItem,
    QuoteSheet,
    Request,
    RequestItem,
)
from tests.support.crm import login
from tests.support.queries import query_count

CURRENCY_CODES = ("".join(letters) for letters in product(ascii_uppercase, repeat=3))


def catalogue(env, count, tag):
    """`count` request lines that each point to their own supplier, group, nomenclature and currency."""
    owner = env["admin"].id
    with env["sessions"].begin() as db:
        client = Counterparty(name=f"Клиент {tag}", owner_id=owner)
        db.add(client)
        db.flush()
        request = Request(number=f"N1-{tag}", title=f"Заявка {tag}", client_id=client.id, owner_id=owner)
        db.add(request)
        db.flush()
        for index in range(count):
            supplier = Counterparty(name=f"Поставщик {tag}-{index}", kind="supplier", owner_id=owner)
            group = ProductGroup(name=f"Группа {tag}-{index}", slug=f"group-{tag}-{index}")
            currency = Currency(code=next(CURRENCY_CODES), name=f"Валюта {tag}-{index}")
            db.add_all([supplier, group, currency])
            db.flush()
            nomenclature = Nomenclature(name=f"Товар {tag}-{index}", product_group_id=group.id)
            db.add(nomenclature)
            db.flush()
            packing = Packing(nomenclature_id=nomenclature.id, value=Decimal("1"), unit="pcs", display_name="1 шт.")
            db.add(packing)
            db.flush()
            item = RequestItem(request_id=request.id, description=nomenclature.name, nomenclature_id=nomenclature.id,
                               packing_id=packing.id, product_group_id=group.id, supplier_id=supplier.id,
                               purchase_currency_id=currency.id, purchase_price=Decimal("10"),
                               quantity=Decimal("1"), unit="pcs")
            db.add(item)
            db.flush()
            sheet = QuoteSheet(number=f"Q-{tag}-{index}", request_id=request.id, supplier_id=supplier.id,
                               author_id=owner)
            db.add(sheet)
            db.flush()
            db.add(QuoteItem(quote_id=sheet.id, supplier_id=supplier.id, nomenclature_id=nomenclature.id,
                             packing_id=packing.id, quantity=Decimal("1"), unit_price=Decimal("10"),
                             currency_id=currency.id, delivery_days=7, quoted_at=utcnow(),
                             valid_until=utcnow() + timedelta(days=21), source_request_item_id=item.id,
                             author_id=owner))
        return request.id


def queries_for(env, count, tag, path_of):
    request_id = catalogue(env, count, tag)
    return query_count(env, path_of(request_id))


def test_request_items_and_quotes_do_not_query_per_line(crm):
    login(crm)
    for path_of in (lambda rid: f"/requests/{rid}/items",
                    lambda rid: f"/requests/{rid}/quote-items",
                    lambda rid: f"/requests/{rid}/quote-items?all=true",
                    lambda rid: f"/requests/{rid}/quote-sheets"):
        few = queries_for(crm, 2, f"a{id(path_of)}", path_of)
        many = queries_for(crm, 7, f"b{id(path_of)}", path_of)
        assert many == few, path_of("<id>")


def test_request_list_does_not_query_per_request(crm):
    login(crm)
    catalogue(crm, 1, "one")
    few = query_count(crm, "/requests")
    for index in range(6):
        catalogue(crm, 1, f"more{index}")
    assert query_count(crm, "/requests") == few


def test_catalogue_and_people_lists_do_not_query_per_row(crm):
    login(crm)
    catalogue(crm, 2, "c")
    few = {path: query_count(crm, path) for path in ("/nomenclatures", "/admin/users", "/admin/roles")}
    catalogue(crm, 6, "d")
    with crm["sessions"].begin() as db:
        for index in range(6):
            user = User(email=f"user{index}@example.com", name=f"Сотрудник {index}", password_hash="x")
            role = Role(name=f"Роль {index}")
            db.add_all([user, role])
            db.flush()
            db.add(PermissionGrant(role_id=role.id, code="clients.read", scope="all"))
            db.add(PermissionGrant(user_id=user.id, code="tasks.read", scope="own"))
    many = {path: query_count(crm, path) for path in few}
    assert many == few
