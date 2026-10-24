"""«Создать заявку» from a site request (TZ of the site, 9.4.1): one window, one employee, one command.

The CRM looks for the client by INN, then by phone and e-mail (of the card and of its contacts), then by the
company name, and offers what it found. If nobody fits, the new client card is already filled from the request.
The lines move to the CRM request one to one and can be corrected before saving. One command creates the
client (if new), the contact and the request: responsible — the same employee, source — «Сайт»; attachments
are copied and the request keeps the link to its site request, so the traffic source stays visible.
"""

import re
from datetime import datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.communication.files import store_quarantine, verified_content
from app.communication.models import FileRecord
from app.core.config import settings
from app.core.db import DB, utcnow
from app.core.errors import DomainError
from app.core.models import OutboxEvent, User
from app.core.security import Actor, check_client, client_predicate, require_permission
from app.core.service import audit, check_version, idem, notify, prefetch, serialize
from app.crm.codes import next_request_number
from app.crm.common import active_user, contact_matches, save
from app.crm.models import Contact, Counterparty, Request, RequestItem, RequestItemRevision, Task
from app.crm.requests import structured_item_fields
from app.crm.text_search import normalize
from app.integrations.models import SiteRequest, SiteRequestItem
from app.integrations.site_contract import ConvertInput
from app.integrations.site_requests import OPEN_TASK_STATUSES, check_site_request, detail_view

router = APIRouter(tags=['Заявки с сайта'])

SOURCE = 'Сайт'
MATCH_LIMIT = 10
REASON_WEIGHTS = {'inn': 100, 'phone': 30, 'email': 30, 'contact': 30, 'name': 10}
REASON_LABELS = {'inn': 'ИНН', 'phone': 'Телефон', 'email': 'E-mail', 'contact': 'Контакт', 'name': 'Название'}
LEGAL_FORMS = {'ооо', 'оао', 'зао', 'пао', 'ао', 'ип', 'нао', 'фгуп', 'фгбу', 'фгбоу', 'гбу', 'гуп', 'муп', 'нии',
               'ltd', 'llc', 'gmbh', 'inc', 'ag', 'sa', 'co'}
UNITS = {'pack': 'pcs', 'pcs': 'pcs', 'mg': 'mg', 'g': 'g', 'kg': 'kg', 'ml': 'ml', 'l': 'l'}
ANALOGUE_LABELS = {'none': 'Только этот товар', 'allowed': 'Готовы рассмотреть аналоги',
                   'requested': 'Подберите аналог'}
# Columns of the site data shown next to the CRM fields of each line, as for imported tables.
SOURCE_COLUMNS = ['Бренд', 'Аналоги (сайт)']


def phone_key(value: str | None) -> str:
    """The last ten digits: «+7 (999) 123-45-67», «8 999 1234567» and «+79991234567» are one number."""
    digits = re.sub(r'\D', '', value or '')
    return digits[-10:] if len(digits) >= 10 else ''


def name_core(value: str | None) -> str:
    """«ООО «Пример-Фарма»» → «примерфарма»: legal form, quotes and punctuation do not matter."""
    words = re.split(r'[\s«»"\'.,()]+', (value or '').casefold())
    return normalize(' '.join(word for word in words if word and word not in LEGAL_FORMS))


def digits_sql(column: Any) -> Any:
    """The digits of a stored phone, in SQL that SQLite and PostgreSQL both run."""
    for char in (' ', '-', '(', ')', '+', '.'):
        column = func.replace(column, char, '')
    return column


def candidate_reasons(db: Session, row: SiteRequest) -> dict[str, set[str]]:
    """Counterparty id → why it matches the site request."""
    found: dict[str, set[str]] = {}

    def add(ids: Any, reason: str) -> None:
        for party_id in ids:
            found.setdefault(party_id, set()).add(reason)

    clients = select(Counterparty.id).where(Counterparty.kind.in_(('client', 'both')), Counterparty.archived.is_(False),
                                            Counterparty.merged_into_id.is_(None))
    if row.inn:
        add(db.scalars(clients.where(Counterparty.search_fields['tax_id'].as_string() == normalize(row.inn))), 'inn')
    if key := phone_key(row.phone):
        add(db.scalars(clients.where(Counterparty.search_fields['phone'].as_string().contains(key))), 'phone')
        add(db.scalars(select(Contact.client_id).where(Contact.archived.is_(False),
                                                       digits_sql(Contact.phone).like(f'%{key}'))), 'contact')
    if row.email:
        email = row.email.strip().casefold()
        add(db.scalars(clients.where(Counterparty.search_fields['email'].as_string() == normalize(email))), 'email')
        add(db.scalars(select(Contact.client_id).where(Contact.archived.is_(False),
                                                       func.lower(Contact.email) == email)), 'contact')
    if len(core := name_core(row.company)) >= 4:
        add(db.scalars(clients.where(Counterparty.search_fields['name'].as_string().contains(core))), 'name')
    # A contact may belong to an archived or merged card: only live client cards are offered.
    live = set(db.scalars(clients.where(Counterparty.id.in_(found)))) if found else set()
    return {party_id: reasons for party_id, reasons in found.items() if party_id in live}


def matched_contact(contacts: list[Contact], row: SiteRequest) -> str | None:
    key, email = phone_key(row.phone), (row.email or '').strip().casefold()
    for contact in contacts:
        if (email and (contact.email or '').strip().casefold() == email) or (key and phone_key(contact.phone) == key):
            return contact.id
    return None


def client_matches(db: Session, user: User, row: SiteRequest) -> dict[str, Any]:
    reasons = candidate_reasons(db, row)
    ranked = sorted(reasons, key=lambda party_id: -sum(REASON_WEIGHTS[r] for r in reasons[party_id]))
    visible = set(db.scalars(select(Counterparty.id).where(Counterparty.id.in_(ranked),
                                                           client_predicate(db, user)))) if ranked else set()
    parties = prefetch(db, Counterparty, ranked)
    owners = prefetch(db, User, (party.owner_id for party in parties.values()))
    contacts: dict[str, list[Contact]] = {}
    for contact in db.scalars(select(Contact).where(Contact.client_id.in_(visible), Contact.archived.is_(False))
                              .order_by(Contact.name)):
        contacts.setdefault(contact.client_id, []).append(contact)
    matches, hidden = [], []
    for party_id in ranked[:MATCH_LIMIT]:
        party = parties[party_id]
        labels = [REASON_LABELS[r] for r in REASON_WEIGHTS if r in reasons[party_id]]
        owner = owners.get(party.owner_id)
        if party_id not in visible:
            # Another manager's client: the employee learns a duplicate exists without seeing the card.
            hidden.append({'reasons': labels, 'owner_name': owner.name if owner else None})
            continue
        own = contacts.get(party_id, [])
        matches.append({
            'id': party.id, 'name': party.name, 'internal_code': party.internal_code, 'tax_id': party.tax_id,
            'phone': party.phone, 'email': party.email, 'city': (party.details or {}).get('city'),
            'client_base': party.client_base, 'owner_name': owner.name if owner else None, 'reasons': labels,
            'contacts': [{'id': c.id, 'name': c.name, 'phone': c.phone, 'email': c.email, 'position': c.position}
                         for c in own],
            'matched_contact_id': matched_contact(own, row),
        })
    return {'matches': matches, 'hidden': hidden}


def quantity_text(value: Decimal) -> str:
    return format(value.normalize(), 'f') if value == value.to_integral_value() else format(value, 'f').rstrip('0')


def proposal(db: Session, row: SiteRequest) -> dict[str, Any]:
    """The window's starting point: a new client card, a contact, a title and the lines."""
    items = db.scalars(select(SiteRequestItem).where(SiteRequestItem.site_request_id == row.id)
                       .order_by(SiteRequestItem.position)).all()
    lines = []
    for item in items:
        notes = [text for flag, text in ((item.analogue == 'requested', 'Клиент просит подобрать аналог'),
                                         (item.custom_packaging, 'Фасовка указана клиентом')) if flag]
        lines.append({
            'site_item_id': item.id,
            'description': f'{item.name} — {item.brand}' if item.brand else item.name,
            'article': item.catalog_number, 'cas': item.cas, 'packaging': item.packaging,
            'quantity': quantity_text(item.quantity), 'unit': UNITS.get(item.unit, 'pcs'),
            'allow_analogue': item.analogue in ('allowed', 'requested'),
            'comment': '\n'.join([*notes, *([item.comment] if item.comment else [])]) or None,
        })
    if row.kind == 'service' and row.service:
        title = f'Услуга «{row.service.get("name")}» — {row.company}'
    elif items:
        title = f'{items[0].name}' + (f' и ещё {len(items) - 1}' if len(items) > 1 else '') + f' — {row.company}'
    else:
        title = f'Запрос с сайта {row.site_number} — {row.company}'
    return {
        'client': {'name': row.company, 'tax_id': row.inn, 'country': None, 'city': row.city, 'phone': row.phone,
                   'email': row.email},
        'contact': {'name': row.customer_name, 'position': None, 'phone': row.phone, 'email': row.email},
        'title': title[:250],
        'items': lines,
    }


@router.get('/site-requests/{entity_id}/conversion')
def conversion(entity_id: str, user: Actor, db: DB) -> dict[str, Any]:
    """Данные окна «Создать заявку»: найденные клиенты с причинами совпадения и предложенная карточка."""
    require_permission(db, user, 'site_requests.write')
    row = check_site_request(db, user, entity_id)
    if row.status in ('converted', 'rejected'):
        raise DomainError('SITE_REQUEST_CLOSED', 'Заявка с сайта уже обработана', 409)
    owner = db.get(User, row.assignee_id) if row.assignee_id else user
    return {**client_matches(db, user, row), 'proposal': proposal(db, row),
            'owner': {'id': owner.id, 'name': owner.name} if owner else None}


def desired_moment(row: SiteRequest) -> datetime | None:
    if row.desired_date is None:
        return None
    return datetime.combine(row.desired_date, time(12), ZoneInfo(settings.company_timezone))


def copy_files(db: Session, user: User, row: SiteRequest, request: Request) -> int:
    """The customer's files go into the request as copies; the site request keeps what the customer sent."""
    copied = 0
    for original in db.scalars(select(FileRecord).where(FileRecord.site_request_id == row.id)
                               .order_by(FileRecord.created_at)):
        if original.status == 'infected':
            continue
        content = verified_content(original.storage_key, original.sha256, original.size)
        key, checksum = store_quarantine(content)
        clean = original.status == 'clean'
        copy = FileRecord(name=original.name, media_type=original.media_type, size=original.size, sha256=checksum,
                          storage_key=key, author_id=user.id, request_id=request.id,
                          status='clean' if clean else 'quarantined', scan_result='clean' if clean else None,
                          scanned_at=original.scanned_at if clean else None)
        db.add(copy)
        db.flush()
        if not clean:
            db.add(OutboxEvent(event_key=f'file-scan:{copy.id}', kind='file.scan', payload={'file_id': copy.id}))
        audit(db, user, 'file', copy.id, 'copied', after={'from_site_request': row.number, 'sha256': checksum})
        copied += 1
    return copied


def resolve_client(db: Session, user: User, body: ConvertInput, row: SiteRequest, owner: User) -> Counterparty:
    if body.client is not None:
        require_permission(db, user, 'clients.write')
        data = body.client.model_dump()
        city = data.pop('city')
        party = Counterparty(**data, kind='client', client_base='working', owner_id=owner.id, source=SOURCE,
                             details={'city': city} if city else {})
        save(db, user, party, 'counterparty')
        return party
    party = check_client(db, user, str(body.client_id))
    if party.archived or party.kind not in ('client', 'both') or party.merged_into_id:
        raise DomainError('WORKING_CLIENT_REQUIRED', 'Выберите действующего клиента', 422, 'client_id')
    if party.client_base != 'working':
        # A client from the call base who sent a request becomes a working client.
        check_client(db, user, party.id, 'clients.write')
        before = serialize(party)
        party.client_base = 'working'
        party.version += 1
        audit(db, user, 'counterparty', party.id, 'promoted', before, serialize(party),
              f'Заявка с сайта {row.number}')
    return party


def resolve_contact(db: Session, user: User, body: ConvertInput, party: Counterparty, row: SiteRequest) -> Contact:
    if body.contact is not None:
        check_client(db, user, party.id, 'clients.write')
        contact = Contact(client_id=party.id, comment=f'Из заявки с сайта {row.number}', **body.contact.model_dump())
        save(db, user, contact, 'contact')
        return contact
    contact_matches(db, body.contact_id, party.id)
    existing = db.get(Contact, body.contact_id)
    if existing is None:
        raise DomainError('CONTACT_INVALID', 'Контакт не относится к выбранному клиенту', 422, 'contact_id')
    return existing


@router.post('/site-requests/{entity_id}/convert')
def convert(entity_id: str, body: ConvertInput, user: Actor, db: DB,
            idempotency_key: str | None = Header(default=None)) -> dict[str, Any]:
    """Создать клиента (если новый), контакт и заявку CRM одной командой; заявка с сайта — «Преобразована»."""
    require_permission(db, user, 'site_requests.write')
    require_permission(db, user, 'requests.write')

    def operation() -> dict[str, Any]:
        row = check_site_request(db, user, entity_id, for_update=True)
        check_version(row, body.version)
        if row.status in ('converted', 'rejected'):
            raise DomainError('SITE_REQUEST_CLOSED', 'Заявка с сайта уже обработана', 409)
        owner = active_user(db, row.assignee_id or user.id)
        if owner.id != user.id:
            require_permission(db, user, 'requests.assign')
        party = resolve_client(db, user, body, row, owner)
        contact = resolve_contact(db, user, body, party, row)
        request = Request(title=body.title, client_id=party.id, contact_id=contact.id, owner_id=owner.id,
                          number=next_request_number(db), source=SOURCE)
        save(db, user, request, 'request')
        site_items = {item.id: item for item in db.scalars(
            select(SiteRequestItem).where(SiteRequestItem.site_request_id == row.id))}
        desired = desired_moment(row)
        for line in body.items:
            site_item = site_items.get(line.site_item_id or '')
            data = structured_item_fields(db, {**line.model_dump(exclude={'site_item_id'}), 'desired_at': desired})
            item = RequestItem(request_id=request.id, **data, source_columns=SOURCE_COLUMNS if site_item else [],
                               source_values=[site_item.brand or '', ANALOGUE_LABELS[site_item.analogue]]
                               if site_item else [])
            snapshot = save(db, user, item, 'request_item')
            db.add(RequestItemRevision(item_id=item.id, revision=1, snapshot=snapshot, author_id=user.id))
        files = copy_files(db, user, row, request)
        now = utcnow()
        before = {'status': row.status}
        row.status = 'converted'
        row.request_id = request.id
        row.converted_at = now
        if not row.assignee_id:
            row.assignee_id, row.assigned_at = owner.id, now
        row.version += 1
        for task in db.scalars(select(Task).where(Task.entity_type == 'site_request', Task.entity_id == row.id,
                                                  Task.status.in_(OPEN_TASK_STATUSES))):
            task.status, task.result, task.completed_at = 'completed', f'Создана заявка {request.number}', now
            task.version += 1
        audit(db, user, 'site_request', row.id, 'converted', before,
              {'status': row.status, 'request_number': request.number, 'client_id': party.id,
               'items': len(body.items), 'files': files})
        if owner.id != user.id:
            notify(db, owner.id, f'site-request-converted:{row.id}', f'Заявка {request.number} создана из заявки '
                   f'с сайта {row.number}', 'request', request.id)
        db.flush()
        return {**detail_view(db, user, row), 'created': {'request_id': request.id, 'request_number': request.number,
                                                          'client_id': party.id, 'contact_id': contact.id}}

    return idem(db, user, idempotency_key, f'site-request.convert:{entity_id}', body.model_dump(mode='json'),
                operation)


def site_source(db: Session, request_id: str) -> dict[str, Any] | None:
    """The site request a CRM request was created from and its traffic source, for the request card."""
    from app.integrations.models import SiteRequestAttribution

    row = db.scalar(select(SiteRequest).where(SiteRequest.request_id == request_id))
    if row is None:
        return None
    attribution = db.scalar(select(SiteRequestAttribution).where(SiteRequestAttribution.site_request_id == row.id))
    last = [attribution.last_utm_source, attribution.last_utm_medium, attribution.last_utm_campaign] \
        if attribution else []
    return {'id': row.id, 'number': row.number, 'site_number': row.site_number,
            'channel': attribution.channel if attribution else None,
            'last_touch': ' / '.join(value for value in last if value) or None}

