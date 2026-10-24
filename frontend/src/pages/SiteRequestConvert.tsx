import { useId, useState } from 'react';
import { Badge, Button, ErrorBox, Loading, Modal, Section } from '../components/ui';
import { units } from '../lib/format';
import { useApi, useCommand, useDirtyProtection } from '../lib/hooks';
import '../styles/site-requests.scss';

/**
 * «Создать заявку» из заявки с сайта (ТЗ сайта 9.4.1): всё в одном окне, одним сотрудником.
 * CRM ищет клиента по ИНН, телефону, e-mail и названию; не нашла — карточка нового клиента уже заполнена.
 * Позиции переносятся 1:1 и правятся до сохранения. Одна кнопка создаёт клиента, контакт и заявку.
 */

type ContactRef = {
  id: string;
  name: string;
  phone?: string | null;
  email?: string | null;
  position?: string | null;
};
type Match = {
  id: string;
  name: string;
  internal_code: number;
  tax_id?: string | null;
  phone?: string | null;
  email?: string | null;
  city?: string | null;
  client_base: string;
  owner_name?: string | null;
  reasons: string[];
  contacts: ContactRef[];
  matched_contact_id?: string | null;
};
type ClientCard = {
  name: string;
  tax_id: string | null;
  country: string | null;
  city: string | null;
  phone: string | null;
  email: string | null;
};
type ContactCard = {
  name: string;
  position: string | null;
  phone: string | null;
  email: string | null;
};
type Line = {
  site_item_id: string | null;
  description: string;
  article: string | null;
  cas: string | null;
  packaging: string | null;
  quantity: string | null;
  unit: string | null;
  allow_analogue: boolean;
  comment: string | null;
};
type Conversion = {
  matches: Match[];
  hidden: { reasons: string[]; owner_name?: string | null }[];
  proposal: { client: ClientCard; contact: ContactCard; title: string; items: Line[] };
  owner?: { id: string; name: string } | null;
};

const NEW = 'new';
const blank = (value: string | null | undefined) => (value && value.trim() ? value.trim() : null);

function TextField({
  label,
  value,
  onChange,
  required,
  wide,
  type = 'text',
}: {
  label: string;
  value: string | null;
  onChange: (value: string) => void;
  required?: boolean;
  wide?: boolean;
  type?: string;
}) {
  const id = useId();
  return (
    <div className={`field ${wide ? 'wide' : ''}`}>
      <label htmlFor={id}>
        {label}
        {required && <span> *</span>}
      </label>
      <input
        id={id}
        type={type}
        value={value ?? ''}
        required={required}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function ConvertForm({
  siteRequestId,
  version,
  data,
  onClose,
  onDone,
}: {
  siteRequestId: string;
  version: number;
  data: Conversion;
  onClose: () => void;
  onDone: () => void;
}) {
  const strongest = data.matches.find((match) => match.reasons.includes('ИНН'));
  const [clientChoice, setClientChoice] = useState<string>(strongest?.id ?? NEW);
  const [client, setClient] = useState<ClientCard>(data.proposal.client);
  const chosen = data.matches.find((match) => match.id === clientChoice);
  const [contactChoice, setContactChoice] = useState<string>(strongest?.matched_contact_id ?? NEW);
  const [contact, setContact] = useState<ContactCard>(data.proposal.contact);
  const [title, setTitle] = useState(data.proposal.title);
  const [lines, setLines] = useState(
    data.proposal.items.map((line) => ({ ...line, include: true })),
  );
  const [dirty, setDirty] = useState(false);
  const operation = useCommand();
  useDirtyProtection(dirty);

  function pickClient(id: string) {
    setClientChoice(id);
    const match = data.matches.find((row) => row.id === id);
    setContactChoice(match?.matched_contact_id ?? NEW);
    setDirty(true);
  }
  function editLine(index: number, patch: Partial<Line & { include: boolean }>) {
    setLines((rows) => rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
    setDirty(true);
  }
  function close() {
    if (!dirty || window.confirm('Закрыть окно без создания заявки?')) onClose();
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const body = {
      version,
      ...(clientChoice === NEW
        ? {
            client: {
              name: client.name.trim(),
              tax_id: blank(client.tax_id),
              country: blank(client.country),
              city: blank(client.city),
              phone: blank(client.phone),
              email: blank(client.email),
            },
          }
        : { client_id: clientChoice }),
      ...(contactChoice === NEW || clientChoice === NEW
        ? {
            contact: {
              name: contact.name.trim(),
              position: blank(contact.position),
              phone: blank(contact.phone),
              email: blank(contact.email),
            },
          }
        : { contact_id: contactChoice }),
      title: title.trim(),
      items: lines
        .filter((line) => line.include)
        .map((line) => ({
          site_item_id: line.site_item_id,
          description: line.description.trim(),
          article: blank(line.article),
          cas: blank(line.cas),
          packaging: blank(line.packaging),
          quantity: blank(line.quantity?.replace(',', '.')),
          unit: line.unit || null,
          allow_analogue: line.allow_analogue,
          comment: blank(line.comment),
        })),
    };
    try {
      await operation.run(`/site-requests/${siteRequestId}/convert`, body);
      setDirty(false);
      onDone();
    } catch {
      /* the form keeps its values and the operation key */
    }
  }

  const contacts = chosen?.contacts ?? [];
  return (
    <form className="convert-form" onSubmit={submit}>
      <div className="form-body">
        <ErrorBox error={operation.error} />
        <div className="info-note">
          Ответственный за заявку — {data.owner?.name ?? 'вы'}, источник — «Сайт». Вложения и
          источник трафика перейдут в заявку.
        </div>
        <Section title="Клиент" description="Совпадения по ИНН, телефону, e-mail и названию">
          {data.hidden.length > 0 && (
            <div className="convert-warning" role="status">
              {data.hidden.map((row, index) => (
                <p key={index}>
                  Есть совпадение ({row.reasons.join(', ')}) с клиентом другого сотрудника
                  {row.owner_name ? ` — ${row.owner_name}` : ''}. Уточните у руководителя, прежде
                  чем создавать нового клиента.
                </p>
              ))}
            </div>
          )}
          <fieldset className="convert-options">
            <legend className="convert-legend">Клиент заявки</legend>
            {data.matches.map((match) => (
              <label key={match.id} className="convert-option">
                <input
                  type="radio"
                  name="client"
                  checked={clientChoice === match.id}
                  onChange={() => pickClient(match.id)}
                />
                <span>
                  <strong>{match.name}</strong>
                  {match.reasons.map((reason) => (
                    <Badge key={reason} value={reason} tone="purple" />
                  ))}
                  {match.client_base === 'cold' && <Badge value="База обзвона" tone="amber" />}
                </span>
                <small>
                  {[
                    `Код ${match.internal_code}`,
                    match.tax_id && `ИНН ${match.tax_id}`,
                    match.phone,
                    match.email,
                    match.city,
                    match.owner_name && `ответственный: ${match.owner_name}`,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </small>
              </label>
            ))}
            <label className="convert-option">
              <input
                type="radio"
                name="client"
                checked={clientChoice === NEW}
                onChange={() => pickClient(NEW)}
              />
              <span>
                <strong>Новый клиент</strong>
              </span>
              <small>Карточка заполнена из заявки — проверьте и дополните.</small>
            </label>
          </fieldset>
          {clientChoice === NEW && (
            <div className="form-grid">
              <TextField
                label="Название"
                required
                wide
                value={client.name}
                onChange={(name) => {
                  setClient({ ...client, name });
                  setDirty(true);
                }}
              />
              {(['tax_id', 'city', 'phone', 'email', 'country'] as const).map((key) => (
                <TextField
                  key={key}
                  label={
                    {
                      tax_id: 'ИНН',
                      city: 'Город',
                      phone: 'Телефон',
                      email: 'E-mail',
                      country: 'Страна',
                    }[key]
                  }
                  type={key === 'email' ? 'email' : 'text'}
                  value={client[key]}
                  onChange={(value) => {
                    setClient({ ...client, [key]: value });
                    setDirty(true);
                  }}
                />
              ))}
            </div>
          )}
        </Section>
        <Section title="Контакт">
          {clientChoice !== NEW && contacts.length > 0 && (
            <fieldset className="convert-options">
              <legend className="convert-legend">Контакт клиента</legend>
              {contacts.map((row) => (
                <label key={row.id} className="convert-option">
                  <input
                    type="radio"
                    name="contact"
                    checked={contactChoice === row.id}
                    onChange={() => {
                      setContactChoice(row.id);
                      setDirty(true);
                    }}
                  />
                  <span>
                    <strong>{row.name}</strong>
                    {chosen?.matched_contact_id === row.id && (
                      <Badge value="Совпадает" tone="green" />
                    )}
                  </span>
                  <small>
                    {[row.position, row.phone, row.email].filter(Boolean).join(' · ') || '—'}
                  </small>
                </label>
              ))}
              <label className="convert-option">
                <input
                  type="radio"
                  name="contact"
                  checked={contactChoice === NEW}
                  onChange={() => {
                    setContactChoice(NEW);
                    setDirty(true);
                  }}
                />
                <span>
                  <strong>Новый контакт</strong>
                </span>
                <small>Добавится к выбранному клиенту.</small>
              </label>
            </fieldset>
          )}
          {(clientChoice === NEW || contactChoice === NEW || contacts.length === 0) && (
            <div className="form-grid">
              {(['name', 'position', 'phone', 'email'] as const).map((key) => (
                <TextField
                  key={key}
                  label={
                    { name: 'ФИО', position: 'Должность', phone: 'Телефон', email: 'E-mail' }[key]
                  }
                  required={key === 'name'}
                  type={key === 'email' ? 'email' : 'text'}
                  value={contact[key]}
                  onChange={(value) => {
                    setContact({ ...contact, [key]: value });
                    setDirty(true);
                  }}
                />
              ))}
            </div>
          )}
        </Section>
        <Section title="Заявка">
          <div className="form-grid">
            <TextField
              label="Название заявки"
              required
              wide
              value={title}
              onChange={(value) => {
                setTitle(value);
                setDirty(true);
              }}
            />
          </div>
        </Section>
        {lines.length > 0 && (
          <Section
            title={`Позиции (${lines.filter((line) => line.include).length} из ${lines.length})`}
          >
            <div className="table-scroll">
              <table className="convert-lines">
                <thead>
                  <tr>
                    <th>Перенести</th>
                    <th>Описание</th>
                    <th>Артикул</th>
                    <th>CAS</th>
                    <th>Фасовка</th>
                    <th>Кол-во</th>
                    <th>Ед.</th>
                    <th>Аналог допустим</th>
                    <th>Комментарий</th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((line, index) => (
                    <tr key={line.site_item_id ?? index}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Перенести позицию ${index + 1}`}
                          checked={line.include}
                          onChange={(e) => editLine(index, { include: e.target.checked })}
                        />
                      </td>
                      <td className="wide-cell">
                        <textarea
                          aria-label={`Описание позиции ${index + 1}`}
                          required={line.include}
                          value={line.description}
                          onChange={(e) => editLine(index, { description: e.target.value })}
                        />
                      </td>
                      {(['article', 'cas', 'packaging'] as const).map((key) => (
                        <td key={key}>
                          <input
                            aria-label={`${{ article: 'Артикул', cas: 'CAS', packaging: 'Фасовка' }[key]} позиции ${index + 1}`}
                            value={line[key] ?? ''}
                            onChange={(e) => editLine(index, { [key]: e.target.value })}
                          />
                        </td>
                      ))}
                      <td className="narrow-cell">
                        <input
                          aria-label={`Количество позиции ${index + 1}`}
                          inputMode="decimal"
                          pattern="[0-9]+([.,][0-9]{1,6})?"
                          value={line.quantity ?? ''}
                          onChange={(e) => editLine(index, { quantity: e.target.value })}
                        />
                      </td>
                      <td className="narrow-cell">
                        <select
                          aria-label={`Единица позиции ${index + 1}`}
                          value={line.unit ?? ''}
                          onChange={(e) => editLine(index, { unit: e.target.value || null })}
                        >
                          <option value="">—</option>
                          {units.map((unit) => (
                            <option key={unit.value} value={unit.value}>
                              {unit.label}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Аналог допустим, позиция ${index + 1}`}
                          checked={line.allow_analogue}
                          onChange={(e) => editLine(index, { allow_analogue: e.target.checked })}
                        />
                      </td>
                      <td className="wide-cell">
                        <textarea
                          aria-label={`Комментарий позиции ${index + 1}`}
                          value={line.comment ?? ''}
                          onChange={(e) => editLine(index, { comment: e.target.value })}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        )}
      </div>
      <div className="modal-footer">
        <Button variant="secondary" type="button" onClick={close}>
          Отмена
        </Button>
        <Button type="submit" busy={operation.busy}>
          {clientChoice === NEW ? 'Создать клиента, контакт и заявку' : 'Создать заявку'}
        </Button>
      </div>
    </form>
  );
}

export function SiteRequestConvert({
  siteRequestId,
  version,
  onClose,
  onDone,
}: {
  siteRequestId: string;
  version: number;
  onClose: () => void;
  onDone: () => void;
}) {
  const conversion = useApi<Conversion>(`/site-requests/${siteRequestId}/conversion`);
  return (
    <Modal title="Создать заявку" wide onClose={onClose}>
      {conversion.loading ? (
        <div className="form-body">
          <Loading />
        </div>
      ) : conversion.data ? (
        <ConvertForm
          siteRequestId={siteRequestId}
          version={version}
          data={conversion.data}
          onClose={onClose}
          onDone={onDone}
        />
      ) : (
        <div className="form-body">
          <ErrorBox error={conversion.error} retry={conversion.refresh} />
        </div>
      )}
    </Modal>
  );
}
