import { Download, Plus, Send, Upload } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useAuth } from '../app/Auth';
import { TableImportDialog } from '../components/TableImportDialog';
import { Badge, Button, DataTable, ErrorBox, Loading, Modal, Section } from '../components/ui';
import { download } from '../lib/api';
import { date, decimal, today } from '../lib/format';
import { useApi, useCommand } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
import './RequestPriceReductions.scss';

function targetValues(items: Entity[], current: Record<string, string> = {}) {
  return Object.fromEntries(
    items.map((item) => [
      String(item.quote_item_id),
      current[String(item.quote_item_id)] ?? String(item.current_sale_unit_price || ''),
    ]),
  );
}

function ReductionCreateDialog({
  request,
  quoteItemIds,
  onClose,
  onCreated,
}: {
  request: Entity;
  quoteItemIds: string[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const query = new URLSearchParams({ quote_item_ids: quoteItemIds.join(',') });
  const options = useApi<Entity>(
    `/requests/${request.id}/price-reduction-options?${query.toString()}`,
  );
  const command = useCommand();
  const hintsCommand = useCommand();
  const [items, setItems] = useState<Entity[]>([]);
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [leaderId, setLeaderId] = useState('');
  const [managerId, setManagerId] = useState('');
  const [dueDate, setDueDate] = useState('');
  const [comment, setComment] = useState('');
  const [localError, setLocalError] = useState('');
  const [hintsLoaded, setHintsLoaded] = useState(false);
  useEffect(() => {
    const data = options.data;
    if (!data) return;
    const optionItems = (data.items || []) as Entity[];
    const leaders = (data.leaders || []) as Entity[];
    const managers = (data.managers || []) as Entity[];
    setItems(optionItems);
    setTargets((current) => targetValues(optionItems, current));
    setLeaderId((current) => current || String(leaders[0]?.id || ''));
    setManagerId((current) => current || String(data.default_manager_id || managers[0]?.id || ''));
    if (!dueDate) {
      const later = new Date();
      later.setDate(later.getDate() + 14);
      setDueDate(later.toISOString().slice(0, 10));
    }
  }, [options.data, dueDate]);

  function validTargets() {
    return items.every(
      (item) => Number(String(targets[String(item.quote_item_id)] || '').replace(',', '.')) > 0,
    );
  }
  async function showHints() {
    setLocalError('');
    if (!validTargets()) {
      setLocalError('Укажите положительную желаемую цену для каждой позиции.');
      return;
    }
    try {
      const result = await hintsCommand.run<Entity>(
        `/requests/${request.id}/price-reduction-hints`,
        {
          targets: items.map((item) => ({
            quote_item_id: item.quote_item_id,
            target_sale_price: targets[String(item.quote_item_id)].replace(',', '.'),
          })),
        },
      );
      if (result) {
        setItems((result.items || []) as Entity[]);
        setHintsLoaded(true);
      }
    } catch {
      /* ErrorBox displays the server error. */
    }
  }
  async function create(event: React.FormEvent) {
    event.preventDefault();
    setLocalError('');
    if (!leaderId || !managerId || !dueDate || comment.trim().length < 3 || !validTargets()) {
      setLocalError('Заполните согласующих, дату, комментарий и желаемые цены.');
      return;
    }
    try {
      await command.run(
        `/requests/${request.id}/price-reductions`,
        {
          request_version: request.version,
          quote_item_ids: quoteItemIds,
          leader_id: leaderId,
          manager_id: managerId,
          client_due_date: dueDate,
          comment: comment.trim(),
          targets: items.map((item) => ({
            quote_item_id: item.quote_item_id,
            target_sale_price: targets[String(item.quote_item_id)].replace(',', '.'),
          })),
        },
        'POST',
        true,
      );
      onCreated();
    } catch {
      /* ErrorBox displays the server error. */
    }
  }
  const leaders = (options.data?.leaders || []) as Entity[];
  const managers = (options.data?.managers || []) as Entity[];
  return (
    <Modal title="Создать запрос на снижение" wide onClose={onClose}>
      <form className="form-body" onSubmit={(event) => void create(event)}>
        <ErrorBox error={options.error || command.error || hintsCommand.error} />
        {localError && <p className="field-error">{localError}</p>}
        {options.loading ? (
          <Loading />
        ) : (
          <>
            <div className="form-grid">
              <label className="field">
                Руководитель продаж · первый этап
                <select
                  required
                  value={leaderId}
                  onChange={(event) => setLeaderId(event.target.value)}
                >
                  <option value="">Выберите руководителя</option>
                  {leaders.map((person) => (
                    <option key={person.id} value={person.id}>
                      {String(person.name)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Менеджер по заявкам · второй этап
                <select
                  required
                  value={managerId}
                  onChange={(event) => setManagerId(event.target.value)}
                >
                  <option value="">Выберите менеджера</option>
                  {managers.map((person) => (
                    <option key={person.id} value={person.id}>
                      {String(person.name)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Клиент готов ждать до
                <input
                  type="date"
                  min={today()}
                  required
                  value={dueDate}
                  onChange={(event) => setDueDate(event.target.value)}
                />
              </label>
            </div>
            <DataTable<Entity>
              rows={items.map((item) => ({ ...item, id: String(item.quote_item_id) }))}
              columns={[
                {
                  key: 'nomenclature_name',
                  label: 'Позиция квоты',
                  render: (item) => (
                    <span className="stacked">
                      <strong>{String(item.nomenclature_name)}</strong>
                      <small>
                        {String(item.supplier_name)} · {String(item.article || '—')} ·{' '}
                        {decimal(item.unit_price)} {String(item.currency_code)}
                      </small>
                    </span>
                  ),
                },
                {
                  key: 'target_sale_price',
                  label: 'Желаемая продажа с НДС, ₽',
                  sortable: false,
                  render: (item) => (
                    <input
                      aria-label={`Желаемая цена ${String(item.nomenclature_name)}`}
                      inputMode="decimal"
                      required
                      value={targets[String(item.quote_item_id)] || ''}
                      onChange={(event) => {
                        setTargets((current) => ({
                          ...current,
                          [String(item.quote_item_id)]: event.target.value,
                        }));
                        setHintsLoaded(false);
                      }}
                    />
                  ),
                },
                {
                  key: 'required_purchase_rub',
                  label: 'Подсказка закупа, ₽',
                  sortable: false,
                  render: (item) =>
                    !hintsLoaded
                      ? 'Нажмите «Подсказать закуп»'
                      : item.required_purchase_rub == null
                        ? 'Нет данных в последнем расчёте'
                        : decimal(item.required_purchase_rub),
                },
              ]}
            />
            <Button
              type="button"
              variant="secondary"
              busy={hintsCommand.busy}
              onClick={() => void showHints()}
            >
              Подсказать закуп по последнему расчёту
            </Button>
            <label className="field">
              Комментарий и условия клиента
              <textarea
                required
                minLength={3}
                maxLength={4000}
                placeholder="Например: готовы на более дешёвые аналоги, нужно снижение 10 %"
                value={comment}
                onChange={(event) => setComment(event.target.value)}
              />
            </label>
            <div className="modal-footer">
              <Button type="button" variant="secondary" onClick={onClose}>
                Отмена
              </Button>
              <Button type="submit" busy={command.busy}>
                Отправить на согласование
              </Button>
            </div>
          </>
        )}
      </form>
    </Modal>
  );
}

function ReductionItems({ items }: { items: Entity[] }) {
  return (
    <DataTable<Entity>
      rows={items.map((item) => ({ ...item, id: String(item.quote_item_id) }))}
      columns={[
        { key: 'nomenclature_name', label: 'Номенклатура' },
        { key: 'article', label: 'Артикул', render: (item) => String(item.article || '—') },
        {
          key: 'unit_price',
          label: 'Цена поставщика',
          render: (item) => (
            <span className={item.price_reduction_id ? 'quote-price-reduced' : undefined}>
              {decimal(item.unit_price)} {String(item.currency_code)}
              {Boolean(item.previous_unit_price) && (
                <small className="previous-price">
                  {' '}
                  · была {decimal(item.previous_unit_price)}
                </small>
              )}
            </span>
          ),
        },
        {
          key: 'delivery_days',
          label: 'Срок, дней',
          render: (item) => String(item.delivery_days ?? '—'),
        },
        {
          key: 'target_sale_price',
          label: 'Желаемая продажа, ₽',
          render: (item) => decimal(item.target_sale_price),
        },
        {
          key: 'required_purchase_rub',
          label: 'Нужный закуп, ₽',
          render: (item) => decimal(item.required_purchase_rub),
        },
      ]}
    />
  );
}

function ReductionCard({
  row,
  requestId,
  onChanged,
}: {
  row: Entity;
  requestId: string;
  onChanged: () => void;
}) {
  const auth = useAuth();
  const command = useCommand();
  const [subject, setSubject] = useState(String(row.mail_subject || ''));
  const [body, setBody] = useState(String(row.mail_body || ''));
  const [importing, setImporting] = useState(false);
  const items = useMemo(() => (row.items || []) as Entity[], [row.items]);
  const suppliers = useMemo(() => {
    const grouped = new Map<string, Entity[]>();
    for (const item of items) {
      const key = String(item.supplier_name);
      grouped.set(key, [...(grouped.get(key) || []), item]);
    }
    return [...grouped];
  }, [items]);
  const mails = (row.mails || []) as Entity[];
  const canMail = ['approved', 'awaiting_price_reduction'].includes(String(row.status));
  const canImport = row.status === 'awaiting_price_reduction';
  async function saveMail(send: boolean) {
    try {
      const result = await command.run<Entity>(
        `/price-reductions/${row.id}/mail`,
        { version: row.version, subject, body, send },
        'POST',
        true,
      );
      if (result) onChanged();
    } catch {
      /* ErrorBox displays the failure. */
    }
  }
  return (
    <Section
      title={`Запрос от ${date(row.created_at, true)} · ${String(row.leader_name || '')}`}
      action={<Badge value={row.status} />}
    >
      <div className="reduction-meta">
        <span>Второй согласующий: {String(row.manager_name || '—')}</span>
        <span>Клиент ждёт до: {date(row.client_due_date)}</span>
      </div>
      <p>{String(row.comment || '')}</p>
      <ReductionItems items={items} />
      {row.status === 'rejected' && <p className="field-error">Запрос отклонён на согласовании.</p>}
      {canMail && auth.can('quotes.write') && (
        <div className="reduction-mail-editor">
          <h4>Письмо поставщику</h4>
          <ErrorBox error={command.error} />
          <label className="field">
            Тема
            <input value={subject} onChange={(event) => setSubject(event.target.value)} />
          </label>
          <label className="field">
            Текст письма
            <textarea rows={6} value={body} onChange={(event) => setBody(event.target.value)} />
          </label>
          {suppliers.map(([supplier, supplierItems]) => (
            <div key={supplier} className="reduction-letter-table">
              <strong>{supplier}</strong>
              <ReductionItems items={supplierItems} />
            </div>
          ))}
          <div className="inline-actions">
            <Button variant="secondary" busy={command.busy} onClick={() => void saveMail(false)}>
              Сохранить текст
            </Button>
            {row.status === 'approved' && (
              <Button busy={command.busy} onClick={() => void saveMail(true)}>
                <Send size={16} /> Сформировать и отправить
              </Button>
            )}
          </div>
        </div>
      )}
      {mails.length > 0 && (
        <div className="reduction-mails">
          <h4>Сформированные письма</h4>
          {mails.map((mail) => (
            <p key={mail.id}>
              {String(mail.recipient)} · <Badge value={mail.status} /> · {String(mail.subject)}
            </p>
          ))}
        </div>
      )}
      {canImport && auth.can('quotes.write') && (
        <div className="inline-actions">
          <Button
            variant="secondary"
            onClick={() =>
              void download(
                `/requests/${requestId}/quote-import/template.xlsx?price_reduction_id=${row.id}`,
                'Шаблон_снижения_цен.xlsx',
              ).catch(command.setError)
            }
          >
            <Download size={16} /> Скачать шаблон со старыми ценами
          </Button>
          <Button onClick={() => setImporting(true)}>
            <Upload size={16} /> Загрузить цены со скидкой
          </Button>
        </div>
      )}
      {row.status === 'completed' && (
        <p className="info-note">
          Новые цены применены. Старые расчёты и выпущенные КП не изменены.
        </p>
      )}
      {importing && (
        <TableImportDialog
          requestId={requestId}
          kind="quotes"
          priceReductionId={String(row.id)}
          onClose={() => setImporting(false)}
          onSuccess={() => {
            setImporting(false);
            onChanged();
          }}
        />
      )}
    </Section>
  );
}

export function RequestPriceReductions({
  request,
  launchQuoteItemIds = [],
  onLaunchConsumed,
  onChanged,
}: {
  request: Entity;
  launchQuoteItemIds?: string[];
  onLaunchConsumed?: () => void;
  onChanged: () => void;
}) {
  const list = useApi<Page>(`/requests/${request.id}/price-reductions`);
  const [creating, setCreating] = useState(launchQuoteItemIds.length > 0);
  const [quoteItemIds, setQuoteItemIds] = useState(launchQuoteItemIds);
  const rows = (list.data?.items || []) as Entity[];
  return (
    <>
      <ErrorBox error={list.error} />
      <div className="tab-actions">
        <Button
          variant="secondary"
          disabled={!quoteItemIds.length}
          onClick={() => {
            setQuoteItemIds([]);
            setCreating(true);
          }}
          title="Позиции выбираются в таблице квот"
        >
          <Plus size={16} /> Запрос на снижение
        </Button>
      </div>
      {!list.loading && !rows.length && (
        <p className="empty-note">
          Выберите позиции во вкладке «Квоты» и нажмите «Создать запрос на снижение». После двух
          согласований здесь появятся письмо и загрузка скидки.
        </p>
      )}
      {rows.map((row) => (
        <ReductionCard
          key={row.id}
          row={row}
          requestId={String(request.id)}
          onChanged={() => {
            list.refresh();
            onChanged();
          }}
        />
      ))}
      {creating && (
        <ReductionCreateDialog
          request={request}
          quoteItemIds={quoteItemIds}
          onClose={() => {
            setCreating(false);
            onLaunchConsumed?.();
          }}
          onCreated={() => {
            setCreating(false);
            onLaunchConsumed?.();
            list.refresh();
            onChanged();
          }}
        />
      )}
    </>
  );
}
