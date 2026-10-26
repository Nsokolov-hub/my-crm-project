import {
  ClipboardPlus,
  Download,
  FileSpreadsheet,
  Link2,
  Search,
  UserRoundCheck,
  XCircle,
} from 'lucide-react';
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { RecordForm } from '../components/Form';
import {
  Badge,
  Button,
  DataTable,
  DetailPairs,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  PageHeading,
  Pagination,
  Section,
  TextLink,
} from '../components/ui';
import { download } from '../lib/api';
import { date } from '../lib/format';
import { useApi, useDebounced } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
import { SiteRequestConvert } from './SiteRequestConvert';
import { AttachToRequest, ChannelReport, SiteSubscribers } from './SiteRequestsExtra';
import '../styles/site-requests.scss';

/**
 * «Заявки с сайта» (ТЗ сайта 9.4.1): входящие заявки ogk-chem.ru в том виде, как их отправил клиент.
 * Главный администратор назначает ответственного, тот связывается с клиентом за 2 рабочих часа
 * и создаёт заявку CRM или отклоняет запрос с причиной. Контракт — docs/site-integration.md.
 */

type SiteRequestRow = Entity & {
  number: string;
  site_number: string;
  kind_label: string;
  status: string;
  status_label: string;
  received_at: string;
  company: string;
  inn?: string | null;
  customer_name: string;
  phone: string;
  email: string;
  items_count: number;
  channel?: string | null;
  assignee_name?: string | null;
  request_id?: string | null;
  request_number?: string | null;
  version: number;
};

type SiteRequestPage = Page<SiteRequestRow> & {
  counts: Record<string, number>;
  statuses: Record<string, string>;
};

type SiteRequestDetail = SiteRequestRow & {
  lang: string;
  form_type?: string | null;
  page_url?: string | null;
  city?: string | null;
  comment?: string | null;
  desired_date?: string | null;
  marketing_consent: boolean;
  service?: { slug: string; name: string; fields: Record<string, string> } | null;
  assigned_at?: string | null;
  reject_reason?: string | null;
  items: Entity[];
  attribution?: Record<string, string | null> | null;
  files: Entity[];
  history: { action: string; at: string; reason?: string | null; actor_name?: string | null }[];
  can_assign: boolean;
  can_process: boolean;
};

const STATUS_TONES: Record<string, string> = {
  new: 'amber',
  in_work: 'purple',
  converted: 'green',
  rejected: 'red',
};
const TABS = [
  { key: '', title: 'Все' },
  { key: 'new', title: 'Новые' },
  { key: 'in_work', title: 'В работе' },
  { key: 'converted', title: 'Преобразованы' },
  { key: 'rejected', title: 'Отклонены' },
];
const CHANNELS: Record<string, string> = {
  cpc: 'Реклама',
  organic: 'Поиск',
  referral: 'Переход с сайта',
  direct: 'Прямой заход',
  email: 'Рассылка',
  social: 'Соцсети',
};
const ANALOGUES: Record<string, string> = {
  none: 'Только этот товар',
  allowed: 'Готовы рассмотреть аналоги',
  requested: 'Подберите аналог',
};
const ACTIONS: Record<string, string> = {
  received: 'Получена с сайта',
  assigned: 'Назначен ответственный',
  reassigned: 'Ответственный изменён',
  rejected: 'Отклонена',
  converted: 'Создана заявка CRM',
};

function quantity(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? String(Number(number.toFixed(3))) : String(value ?? '');
}

export function SiteRequestCard({
  id,
  onClose,
  onChanged,
}: {
  id: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const detail = useApi<SiteRequestDetail>(`/site-requests/${id}`);
  const [action, setAction] = useState<'assign' | 'reject' | 'convert' | 'attach' | null>(null);
  const [fileError, setFileError] = useState<unknown>();
  const row = detail.data;
  const open = row && !['converted', 'rejected'].includes(row.status);
  function done() {
    setAction(null);
    detail.refresh();
    onChanged();
  }
  if (row && action === 'assign')
    return (
      <RecordForm
        title={row.assignee_name ? 'Переназначить ответственного' : 'Назначить ответственного'}
        endpoint={`/site-requests/${id}/assign`}
        submitLabel="Назначить"
        note="Ответственный получит уведомление и задачу «Связаться с клиентом» со сроком 2 рабочих часа."
        fields={[
          {
            name: 'assignee_id',
            label: 'Ответственный',
            type: 'select',
            source: '/users',
            required: true,
          },
          ...(row.assignee_name
            ? [
                {
                  name: 'reason',
                  label: 'Причина переназначения',
                  type: 'textarea' as const,
                  required: true,
                },
              ]
            : []),
        ]}
        extra={{ version: row.version }}
        onClose={() => setAction(null)}
        onSuccess={done}
      />
    );
  if (row && action === 'convert')
    return (
      <SiteRequestConvert
        siteRequestId={id}
        version={row.version}
        onClose={() => setAction(null)}
        onDone={done}
      />
    );
  if (row && action === 'attach')
    return (
      <AttachToRequest
        siteRequestId={id}
        version={row.version}
        company={row.company}
        onClose={() => setAction(null)}
        onDone={done}
      />
    );
  if (row && action === 'reject')
    return (
      <RecordForm
        title="Отклонить заявку с сайта"
        endpoint={`/site-requests/${id}/reject`}
        submitLabel="Отклонить"
        note="Спам, дубль или не наш профиль. Причина попадёт в историю и в отчёт по каналам."
        fields={[{ name: 'reason', label: 'Причина', type: 'textarea', required: true }]}
        extra={{ version: row.version }}
        onClose={() => setAction(null)}
        onSuccess={done}
      />
    );
  const attribution = row?.attribution;
  return (
    <Modal title={row ? `Заявка с сайта ${row.number}` : 'Заявка с сайта'} wide onClose={onClose}>
      <div className="form-body">
        {detail.loading ? (
          <Loading />
        ) : (
          <>
            <ErrorBox error={detail.error} retry={detail.refresh} />
            {row && (
              <>
                <div className="heading-actions">
                  <Badge value={row.status_label} tone={STATUS_TONES[row.status]} />
                  {open && row.can_process && (
                    <Button onClick={() => setAction('convert')}>
                      <ClipboardPlus size={15} /> Создать заявку
                    </Button>
                  )}
                  {open && row.can_process && (
                    <Button variant="secondary" onClick={() => setAction('attach')}>
                      <Link2 size={15} /> В существующую заявку
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    onClick={() =>
                      download(`/site-requests/${id}/export.xlsx`, `${row.number}.xlsx`).catch(
                        setFileError,
                      )
                    }
                  >
                    <FileSpreadsheet size={15} /> Excel
                  </Button>
                  {open && row.can_assign && (
                    <Button variant="secondary" onClick={() => setAction('assign')}>
                      <UserRoundCheck size={15} />
                      {row.assignee_name ? 'Переназначить' : 'Назначить ответственного'}
                    </Button>
                  )}
                  {open && row.can_process && (
                    <Button variant="danger" onClick={() => setAction('reject')}>
                      <XCircle size={15} /> Отклонить
                    </Button>
                  )}
                </div>
                <DetailPairs
                  values={{
                    'Номер на сайте': row.site_number,
                    Получена: date(row.received_at, true),
                    Тип: row.kind_label,
                    Язык: row.lang === 'en' ? 'Английский' : 'Русский',
                    Ответственный: row.assignee_name || 'Не назначен',
                    ...(row.request_number ? { 'Заявка CRM': row.request_number } : {}),
                    ...(row.reject_reason ? { 'Причина отклонения': row.reject_reason } : {}),
                  }}
                />
                {row.request_id && (
                  <TextLink to={`/requests/${row.request_id}`}>Открыть заявку CRM</TextLink>
                )}
                <Section title="Клиент" description="Как ввёл клиент на сайте">
                  <DetailPairs
                    values={{
                      Компания: row.company,
                      ИНН: row.inn,
                      Контакт: row.customer_name,
                      Телефон: row.phone,
                      Почта: row.email,
                      Город: row.city,
                      'Желаемый срок': row.desired_date ? date(row.desired_date) : null,
                      'Согласие на рассылку': row.marketing_consent ? 'Да' : 'Нет',
                      Комментарий: row.comment,
                    }}
                  />
                </Section>
                {row.service && (
                  <Section title={`Услуга: ${row.service.name}`}>
                    <DetailPairs values={row.service.fields} />
                  </Section>
                )}
                {row.items.length > 0 && (
                  <Section title={`Позиции (${row.items.length})`}>
                    <DataTable
                      rows={row.items}
                      columns={[
                        { key: 'position', label: '№' },
                        { key: 'name', label: 'Наименование' },
                        { key: 'brand', label: 'Бренд' },
                        { key: 'catalog_number', label: 'Кат. №' },
                        { key: 'cas', label: 'CAS' },
                        {
                          key: 'packaging',
                          label: 'Фасовка',
                          render: (item) =>
                            `${String(item.packaging ?? '—')}${item.custom_packaging ? ' (своя)' : ''}`,
                        },
                        {
                          key: 'quantity',
                          label: 'Кол-во',
                          render: (item) => quantity(item.quantity),
                        },
                        {
                          key: 'analogue',
                          label: 'Аналоги',
                          render: (item) => ANALOGUES[String(item.analogue)],
                        },
                        { key: 'comment', label: 'Комментарий' },
                      ]}
                    />
                  </Section>
                )}
                {row.files.length > 0 && (
                  <Section title="Вложения">
                    <ErrorBox error={fileError} />
                    {row.files.map((file) => (
                      <Button
                        key={file.id}
                        variant="ghost"
                        disabled={file.status !== 'clean'}
                        title={
                          file.status === 'clean' ? undefined : 'Файл ещё проверяется антивирусом'
                        }
                        onClick={() =>
                          download(`/files/${file.id}/download`, String(file.name)).catch(
                            setFileError,
                          )
                        }
                      >
                        <Download size={15} /> {String(file.name)}
                        {file.status !== 'clean' && ' — проверяется'}
                      </Button>
                    ))}
                  </Section>
                )}
                <Section title="Источник">
                  <DetailPairs
                    values={{
                      Канал: attribution?.channel ? CHANNELS[attribution.channel] : 'Не определён',
                      'Последнее касание': [
                        attribution?.last_utm_source,
                        attribution?.last_utm_medium,
                        attribution?.last_utm_campaign,
                      ]
                        .filter(Boolean)
                        .join(' / '),
                      'Первое касание': [
                        attribution?.first_utm_source,
                        attribution?.first_utm_medium,
                        attribution?.first_referrer,
                      ]
                        .filter(Boolean)
                        .join(' / '),
                      'Страница входа': attribution?.first_landing_url,
                      'Страница заявки': attribution?.conversion_page_url || row.page_url,
                      yclid: attribution?.yclid,
                      'ClientID Метрики': attribution?.ym_uid,
                    }}
                  />
                </Section>
                <Section title="История">
                  <ol className="history-list">
                    {row.history.map((event) => (
                      <li key={event.at + event.action}>
                        <strong>{ACTIONS[event.action] || event.action}</strong> ·{' '}
                        {date(event.at, true)}
                        {event.actor_name ? ` · ${event.actor_name}` : ''}
                        {event.reason ? ` — ${event.reason}` : ''}
                      </li>
                    ))}
                  </ol>
                </Section>
              </>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

export function SiteRequests() {
  const [params, setParams] = useSearchParams();
  const [view, setView] = useState<'requests' | 'report' | 'subscribers'>('requests');
  const [exportError, setExportError] = useState<unknown>();
  const [status, setStatus] = useState(params.get('status') || '');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const debounced = useDebounced(q);
  const list = useApi<SiteRequestPage>(
    `/site-requests?page=${page}&page_size=25&q=${encodeURIComponent(debounced)}${status ? `&status=${status}` : ''}`,
  );
  const openId = params.get('id');
  const total = list.data
    ? Object.values(list.data.counts).reduce((sum, value) => sum + value, 0)
    : 0;
  return (
    <>
      <PageHeading
        title="Заявки с сайта"
        description="Запросы с ogk-chem.ru: корзина, «Заказать в 1 клик», формы услуг. Новые назначает главный администратор, ответственный связывается с клиентом за 2 рабочих часа."
      />
      <div className="tabs">
        {(
          [
            ['requests', 'Заявки'],
            ['report', 'Отчёт по каналам'],
            ['subscribers', 'Подписчики сайта'],
          ] as const
        ).map(([key, title]) => (
          <button key={key} className={view === key ? 'active' : ''} onClick={() => setView(key)}>
            {title}
          </button>
        ))}
      </div>
      {view === 'report' && <ChannelReport />}
      {view === 'subscribers' && <SiteSubscribers />}
      {view === 'requests' && (
        <div className="panel">
          <div className="tabs">
            {TABS.map((tab) => {
              const count = tab.key ? list.data?.counts[tab.key] : total;
              return (
                <button
                  key={tab.key}
                  className={status === tab.key ? 'active' : ''}
                  onClick={() => {
                    setStatus(tab.key);
                    setPage(1);
                  }}
                >
                  {tab.title}
                  {Boolean(count) && <span>{count}</span>}
                </button>
              );
            })}
          </div>
          <div className="request-toolbar">
            <div className="search-input">
              <Search size={17} />
              <input
                aria-label="Поиск по заявкам с сайта"
                placeholder="Компания, ИНН, телефон, почта или номер…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setPage(1);
                }}
              />
            </div>
            <Button
              variant="secondary"
              onClick={() =>
                download(
                  `/site-requests/export.xlsx?q=${encodeURIComponent(debounced)}${status ? `&status=${status}` : ''}`,
                  'site-requests.xlsx',
                ).catch(setExportError)
              }
            >
              <FileSpreadsheet size={15} /> Выгрузить в Excel
            </Button>
          </div>
          <ErrorBox error={exportError || list.error} retry={list.refresh} />
          {list.loading ? (
            <Loading />
          ) : (
            <>
              <DataTable
                rows={list.data?.items || []}
                onRow={(row) => setParams({ ...(status ? { status } : {}), id: row.id })}
                empty={
                  <Empty
                    compact
                    title="Заявок нет"
                    description={
                      q
                        ? 'По запросу ничего не найдено.'
                        : 'Новые заявки с сайта появятся здесь сами.'
                    }
                  />
                }
                columns={[
                  { key: 'number', label: 'Номер' },
                  {
                    key: 'received_at',
                    label: 'Получена',
                    render: (row) => date(row.received_at, true),
                  },
                  {
                    key: 'status',
                    label: 'Статус',
                    render: (row) => (
                      <Badge value={row.status_label} tone={STATUS_TONES[row.status]} />
                    ),
                  },
                  { key: 'kind_label', label: 'Тип' },
                  {
                    key: 'company',
                    label: 'Компания',
                    render: (row) => (
                      <span>
                        {row.company}
                        {row.inn && <small className="muted"> · ИНН {row.inn}</small>}
                      </span>
                    ),
                  },
                  { key: 'customer_name', label: 'Контакт' },
                  { key: 'items_count', label: 'Позиций' },
                  {
                    key: 'channel',
                    label: 'Канал',
                    render: (row) => (row.channel ? CHANNELS[row.channel] : '—'),
                  },
                  {
                    key: 'assignee_name',
                    label: 'Ответственный',
                    render: (row) => row.assignee_name || '—',
                  },
                ]}
              />
              <Pagination page={page} total={Number(list.data?.total || 0)} onChange={setPage} />
            </>
          )}
        </div>
      )}
      {openId && (
        <SiteRequestCard
          id={openId}
          onClose={() => setParams(status ? { status } : {})}
          onChanged={list.refresh}
        />
      )}
    </>
  );
}
