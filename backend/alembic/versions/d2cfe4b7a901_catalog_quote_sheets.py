"""Structured catalog, request positions and append-only quote lines.

Revision ID: d2cfe4b7a901
Revises: 3f6d74c810d2
"""

import re
from datetime import datetime, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from alembic import op

revision = 'd2cfe4b7a901'
down_revision = '3f6d74c810d2'
branch_labels = None
depends_on = None


def _id(kind: str, key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f'crm-v2/{kind}/{key}'))


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
    ]


def _legacy_packing(package_quantity: Decimal | None, unit: str | None, packaging: str | None) -> tuple[Decimal, str, str, bool]:
    label = (packaging or '').strip()
    if package_quantity is not None and package_quantity > 0 and unit:
        value = Decimal(str(package_quantity))
        normalized = format(value, 'f')
        if '.' in normalized:
            normalized = normalized.rstrip('0').rstrip('.')
        generated = f'{normalized} {unit}'
        return value, unit[:30], (label or generated)[:100], bool(label and label != generated)
    match = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*([^\d\s]+)', label)
    if match:
        value = Decimal(match.group(1).replace(',', '.'))
        if value > 0:
            return value, match.group(2)[:30], label[:100], False
    return Decimal('1'), 'pcs', (label or '1 pcs')[:100], bool(label)


def upgrade() -> None:
    op.add_column('chats', sa.Column('description', sa.Text(), nullable=False, server_default=''))
    op.create_table(
        'product_groups',
        sa.Column('name', sa.String(150), nullable=False, unique=True),
        sa.Column('slug', sa.String(80), nullable=False, unique=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_base_columns(),
    )
    op.create_table(
        'currencies',
        sa.Column('code', sa.String(3), nullable=False, unique=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_base_columns(),
    )
    op.create_table(
        'countries',
        sa.Column('name', sa.String(150), nullable=False, unique=True),
        sa.Column('iso2', sa.String(2), nullable=False, unique=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_base_columns(),
    )
    op.create_table(
        'expense_types',
        sa.Column('name', sa.String(120), nullable=False, unique=True),
        sa.Column('calculation_type', sa.String(20), nullable=False),
        sa.Column('default_value', sa.Numeric(24, 8), nullable=False),
        sa.Column('currency_id', sa.String(36), sa.ForeignKey('currencies.id'), nullable=False),
        sa.Column('distribution_method', sa.String(30), nullable=False),
        sa.Column('stage', sa.String(30), nullable=False),
        sa.Column('percent_base', sa.String(30)),
        sa.Column('brackets', sa.JSON(), nullable=False),
        sa.Column('include_in_cost', sa.Boolean(), nullable=False),
        sa.Column('include_in_cash', sa.Boolean(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_base_columns(),
    )
    op.create_table(
        'nomenclatures',
        sa.Column('name', sa.String(250), nullable=False),
        sa.Column('article', sa.String(150)),
        sa.Column('product_group_id', sa.String(36), sa.ForeignKey('product_groups.id')),
        sa.Column('cas', sa.String(30)),
        sa.Column('linear_formula', sa.String(500)),
        sa.Column('description', sa.Text()),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        *_base_columns(),
    )
    op.create_index('ix_nomenclatures_name', 'nomenclatures', ['name'])
    op.create_index('ix_nomenclatures_article', 'nomenclatures', ['article'])
    op.create_index('ix_nomenclatures_cas', 'nomenclatures', ['cas'])
    op.create_index('ix_nomenclatures_product_group_id', 'nomenclatures', ['product_group_id'])
    op.create_table(
        'packings',
        sa.Column('nomenclature_id', sa.String(36), sa.ForeignKey('nomenclatures.id'), nullable=False),
        sa.Column('value', sa.Numeric(24, 6), nullable=False),
        sa.Column('unit', sa.String(30), nullable=False),
        sa.Column('display_name', sa.String(100), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        *_base_columns(),
        sa.CheckConstraint('value > 0'),
        sa.UniqueConstraint('nomenclature_id', 'value', 'unit'),
    )
    op.create_index('ix_packings_nomenclature_id', 'packings', ['nomenclature_id'])
    with op.batch_alter_table('request_items') as batch_op:
        batch_op.add_column(sa.Column('product_group_id', sa.String(36)))
        batch_op.add_column(sa.Column('nomenclature_id', sa.String(36)))
        batch_op.add_column(sa.Column('packing_id', sa.String(36)))
        batch_op.add_column(sa.Column('article', sa.String(150)))
        batch_op.add_column(sa.Column('supplier_id', sa.String(36)))
        batch_op.add_column(sa.Column('supplier_country_id', sa.String(36)))
        batch_op.add_column(sa.Column('purchase_price', sa.Numeric(24, 8)))
        batch_op.add_column(sa.Column('purchase_currency_id', sa.String(36)))
        batch_op.create_foreign_key('fk_request_items_product_group_id', 'product_groups', ['product_group_id'], ['id'])
        batch_op.create_foreign_key('fk_request_items_nomenclature_id', 'nomenclatures', ['nomenclature_id'], ['id'])
        batch_op.create_foreign_key('fk_request_items_packing_id', 'packings', ['packing_id'], ['id'])
        batch_op.create_foreign_key('fk_request_items_supplier_id', 'counterparties', ['supplier_id'], ['id'])
        batch_op.create_foreign_key('fk_request_items_supplier_country_id', 'countries', ['supplier_country_id'], ['id'])
        batch_op.create_foreign_key('fk_request_items_purchase_currency_id', 'currencies', ['purchase_currency_id'], ['id'])
        batch_op.create_index('ix_request_items_product_group_id', ['product_group_id'])
        batch_op.create_index('ix_request_items_nomenclature_id', ['nomenclature_id'])
        batch_op.create_index('ix_request_items_packing_id', ['packing_id'])
    op.create_table(
        'quote_sheets',
        sa.Column('number', sa.String(80), nullable=False, unique=True),
        sa.Column('request_id', sa.String(36), sa.ForeignKey('requests.id'), nullable=False),
        sa.Column('supplier_id', sa.String(36), sa.ForeignKey('counterparties.id'), nullable=False),
        sa.Column('supplier_request_id', sa.String(36), sa.ForeignKey('supplier_requests.id')),
        sa.Column('author_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        *_base_columns(),
    )
    op.create_index('ix_quote_sheets_request_id', 'quote_sheets', ['request_id'])
    op.create_index('ix_quote_sheets_supplier_id', 'quote_sheets', ['supplier_id'])
    op.create_table(
        'quote_items',
        sa.Column('quote_id', sa.String(36), sa.ForeignKey('quote_sheets.id'), nullable=False),
        sa.Column('supplier_id', sa.String(36), sa.ForeignKey('counterparties.id'), nullable=False),
        sa.Column('nomenclature_id', sa.String(36), sa.ForeignKey('nomenclatures.id'), nullable=False),
        sa.Column('packing_id', sa.String(36), sa.ForeignKey('packings.id'), nullable=False),
        sa.Column('quantity', sa.Numeric(24, 6), nullable=False),
        sa.Column('unit_price', sa.Numeric(24, 8), nullable=False),
        sa.Column('currency_id', sa.String(36), sa.ForeignKey('currencies.id'), nullable=False),
        sa.Column('delivery_days', sa.Integer()),
        sa.Column('quoted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('valid_until', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source_request_item_id', sa.String(36), sa.ForeignKey('request_items.id'), nullable=False),
        sa.Column('price_source_id', sa.String(36), sa.ForeignKey('quote_items.id')),
        sa.Column('author_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        *_base_columns(),
        sa.CheckConstraint('quantity > 0 AND unit_price >= 0'),
        sa.CheckConstraint('delivery_days IS NULL OR delivery_days >= 0'),
    )
    for column in ('quote_id', 'supplier_id', 'nomenclature_id', 'packing_id', 'currency_id'):
        op.create_index(f'ix_quote_items_{column}', 'quote_items', [column])
    op.create_index(
        'ix_quote_items_price_lookup', 'quote_items',
        ['supplier_id', 'nomenclature_id', 'packing_id', 'currency_id', 'quoted_at'],
    )
    if op.get_bind().dialect.name == 'postgresql':
        for table in ('quote_sheets', 'quote_items'):
            op.execute(
                f'CREATE TRIGGER crm_history_row BEFORE UPDATE OR DELETE ON {table} '
                'FOR EACH ROW EXECUTE FUNCTION crm_protect_history()'
            )
            op.execute(
                f'CREATE TRIGGER crm_history_truncate BEFORE TRUNCATE ON {table} '
                'FOR EACH STATEMENT EXECUTE FUNCTION crm_protect_history()'
            )
    with op.batch_alter_table('executions') as batch_op:
        batch_op.alter_column('quote_id', existing_type=sa.String(36), nullable=True)
        batch_op.add_column(sa.Column('quote_item_id', sa.String(36)))
        batch_op.create_foreign_key('fk_executions_quote_item_id', 'quote_items', ['quote_item_id'], ['id'])
        batch_op.create_index('ix_executions_quote_item_id', ['quote_item_id'])

    now = datetime.now(timezone.utc)
    product_groups = sa.table(
        'product_groups', sa.column('id', sa.String), sa.column('name', sa.String),
        sa.column('slug', sa.String), sa.column('active', sa.Boolean),
        sa.column('created_at', sa.DateTime(timezone=True)), sa.column('version', sa.Integer),
    )
    op.bulk_insert(product_groups, [
        {'id': _id('product_group', slug), 'name': name, 'slug': slug, 'active': True, 'created_at': now, 'version': 1}
        for slug, name in (
            ('reference_standards', 'Стандартные образцы'),
            ('reagents', 'Реактивы'),
            ('columns', 'Колонки'),
            ('lab_glassware', 'Лабораторная посуда'),
            ('other', 'Другое'),
        )
    ])
    currencies = sa.table(
        'currencies', sa.column('id', sa.String), sa.column('code', sa.String),
        sa.column('name', sa.String), sa.column('active', sa.Boolean),
        sa.column('created_at', sa.DateTime(timezone=True)), sa.column('version', sa.Integer),
    )
    op.bulk_insert(currencies, [
        {'id': _id('currency', code), 'code': code, 'name': name, 'active': True, 'created_at': now, 'version': 1}
        for code, name in (
            ('INR', 'Индийская рупия'), ('USD', 'Доллар США'),
            ('EUR', 'Евро'), ('CNY', 'Китайский юань'), ('RUB', 'Российский рубль'),
        )
    ])
    countries = sa.table(
        'countries', sa.column('id', sa.String), sa.column('name', sa.String),
        sa.column('iso2', sa.String), sa.column('active', sa.Boolean),
        sa.column('created_at', sa.DateTime(timezone=True)), sa.column('version', sa.Integer),
    )
    op.bulk_insert(countries, [
        {'id': _id('country', code), 'name': name, 'iso2': code, 'active': True, 'created_at': now, 'version': 1}
        for code, name in (
            ('RU', 'Россия'), ('IN', 'Индия'), ('CN', 'Китай'),
            ('US', 'США'), ('DE', 'Германия'),
        )
    ])

    # Keep existing product cards visible in the new catalog. Ambiguous old
    # packaging remains readable and can be corrected in the new dictionary.
    connection = op.get_bind()
    old_products = connection.execute(sa.text('''
        SELECT p.id, p.name, p.article, p.packaging, p.unit, p.package_quantity,
               p.created_at, s.cas
        FROM products AS p LEFT JOIN substances AS s ON s.id = p.substance_id
        ORDER BY p.id
    ''')).mappings().all()
    nomenclatures = sa.table(
        'nomenclatures', sa.column('id', sa.String), sa.column('name', sa.String),
        sa.column('article', sa.String), sa.column('product_group_id', sa.String),
        sa.column('cas', sa.String), sa.column('linear_formula', sa.String),
        sa.column('description', sa.Text), sa.column('active', sa.Boolean),
        sa.column('updated_at', sa.DateTime(timezone=True)),
        sa.column('created_at', sa.DateTime(timezone=True)), sa.column('version', sa.Integer),
    )
    packings = sa.table(
        'packings', sa.column('id', sa.String), sa.column('nomenclature_id', sa.String),
        sa.column('value', sa.Numeric(24, 6)), sa.column('unit', sa.String),
        sa.column('display_name', sa.String), sa.column('active', sa.Boolean),
        sa.column('created_at', sa.DateTime(timezone=True)), sa.column('version', sa.Integer),
    )
    for product in old_products:
        value, unit, display_name, ambiguous = _legacy_packing(
            product['package_quantity'], product['unit'], product['packaging'],
        )
        nomenclature_id = _id('legacy_product', product['id'])
        created_at = product['created_at'] or now
        connection.execute(nomenclatures.insert().values(
            id=nomenclature_id,
            name=product['name'],
            article=product['article'] or None,
            product_group_id=_id('product_group', 'other'),
            cas=product['cas'],
            linear_formula=None,
            description=f'Проверьте фасовку: {product["packaging"] or display_name}' if ambiguous else None,
            active=True,
            updated_at=now,
            created_at=created_at,
            version=1,
        ))
        connection.execute(packings.insert().values(
            id=_id('legacy_packing', product['id']),
            nomenclature_id=nomenclature_id,
            value=value,
            unit=unit,
            display_name=display_name,
            active=True,
            created_at=created_at,
            version=1,
        ))


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        for table in ('quote_items', 'quote_sheets'):
            op.execute(f'DROP TRIGGER crm_history_truncate ON {table}')
            op.execute(f'DROP TRIGGER crm_history_row ON {table}')
    op.drop_column('chats', 'description')
    with op.batch_alter_table('executions') as batch_op:
        batch_op.drop_index('ix_executions_quote_item_id')
        batch_op.drop_constraint('fk_executions_quote_item_id', type_='foreignkey')
        batch_op.drop_column('quote_item_id')
        batch_op.alter_column('quote_id', existing_type=sa.String(36), nullable=False)
    op.drop_index('ix_quote_items_price_lookup', table_name='quote_items')
    for column in ('quote_id', 'supplier_id', 'nomenclature_id', 'packing_id', 'currency_id'):
        op.drop_index(f'ix_quote_items_{column}', table_name='quote_items')
    op.drop_table('quote_items')
    op.drop_index('ix_quote_sheets_supplier_id', table_name='quote_sheets')
    op.drop_index('ix_quote_sheets_request_id', table_name='quote_sheets')
    op.drop_table('quote_sheets')
    with op.batch_alter_table('request_items') as batch_op:
        for column in ('product_group_id', 'nomenclature_id', 'packing_id'):
            batch_op.drop_index(f'ix_request_items_{column}')
        for column in (
            'product_group_id', 'nomenclature_id', 'packing_id', 'supplier_id',
            'supplier_country_id', 'purchase_currency_id',
        ):
            batch_op.drop_constraint(f'fk_request_items_{column}', type_='foreignkey')
        for column in (
            'product_group_id', 'nomenclature_id', 'packing_id', 'article',
            'supplier_id', 'supplier_country_id', 'purchase_price', 'purchase_currency_id',
        ):
            batch_op.drop_column(column)
    op.drop_index('ix_packings_nomenclature_id', table_name='packings')
    op.drop_table('packings')
    for column in ('product_group_id', 'name', 'article', 'cas'):
        op.drop_index(f'ix_nomenclatures_{column}', table_name='nomenclatures')
    op.drop_table('nomenclatures')
    op.drop_table('expense_types')
    op.drop_table('countries')
    op.drop_table('currencies')
    op.drop_table('product_groups')
