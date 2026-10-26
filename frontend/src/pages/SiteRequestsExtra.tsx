import { Download, Link2 } from 'lucide-react';
import { useState } from 'react';
import { Button, DataTable, Empty, ErrorBox, Loading, Modal, Pagination } from '../components/ui';
import { download } from '../lib/api';
import { date } from '../lib/format';
import { useApi, useCommand, useDebounced } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';

/**
 * Отчёт по каналам, подписчики сайта и «Добавить в существующую заявку» для раздела «Заявки с сайта»
 * (ТЗ сайта 9.4.1, 11.2, 15.3).
 */

type ChannelLine = {
  channel: string | null;
  name: string;
  leads: number;
  rejected: number;
  converted: number;
  qualified: number;
  proposals: number;
  sales: number;
  revenue: string;
};
type Report = {
  date_from: string;
  date_to: string;
  channels: ChannelLine[];
  totals: Omit<ChannelLine, 'channel' | 'name'>;
};

const money = (value: string | number) =>
  Number(value).toLocaleString('ru-RU', { maximumFractionDigits: 0 }) + ' ₽';
const share = (part: number, whole: number) =>
  whole ? `${Math.round((part / whole) * 100)} %` : '—';

/** Сквозная воронка: заявки с сайта → квалифицированные → КП → продажи → выручка, по каналу первого визита */
export function ChannelReport() {
  const today = new Date().toISOString().slice(0, 10);
  const monthAgo = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const [from, setFrom] = useState(monthAgo);
  const [to, setTo] = useState(today);
  const report = useApi<Report>(`/site-requests/report?date_from=${from}&date_to=${to}`);
  const data = report.data;
  const lines = data ? [...data.channels, { channel: 'total', name: 'Итого', ...data.totals }] : [];
  return (
    <div className="panel">
      <div className="request-toolbar">
        <label>
          С <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label>
          по <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        <small className="muted">
          Канал — по первому визиту (ТЗ сайта 15.3). Выручка — по счёту подтверждённой продажи, в
          рублях.
        </small>
      </div>
      <ErrorBox error={report.error} retry={report.refresh} />
      {report.loading ? (
        <Loading />
      ) : (
        <DataTable<Entity & ChannelLine>
          rows={lines.map((line) => ({ ...line, id: String(line.channel) }))}
          empty={<Empty compact title="Заявок за период нет" />}
          columns={[
            { key: 'name', label: 'Канал' },
            { key: 'leads', label: 'Заявки с сайта' },
            { key: 'rejected', label: 'Отклонено' },
            {
              key: 'qualified',
              label: 'Квалифицированные',
              render: (r) => `${r.qualified} · ${share(r.qualified, r.leads)}`,
            },
            {
              key: 'proposals',
              label: 'КП отправлено',
              render: (r) => `${r.proposals} · ${share(r.proposals, r.leads)}`,
            },
            {
              key: 'sales',
              label: 'Продажи',
              render: (r) => `${r.sales} · ${share(r.sales, r.leads)}`,
            },
            { key: 'revenue', label: 'Выручка', render: (r) => money(r.revenue) },
          ]}
        />
      )}
    </div>
  );
}

type Subscriber = Entity & {
  email: string;
  lang: string;
  subscribed: boolean;
  consent_at?: string | null;
  unsubscribed_at?: string | null;
  source?: string | null;
};

/** Подписчики рассылки сайта: контакты для рассылок с согласием, полученным на сайте */
export function SiteSubscribers() {
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [error, setError] = useState<unknown>();
  const debounced = useDebounced(q);
  const list = useApi<Page<Subscriber> & { counts: { subscribed: number; unsubscribed: number } }>(
    `/site-subscribers?page=${page}&q=${encodeURIComponent(debounced)}`,
  );
  return (
    <div className="panel">
      <div className="request-toolbar">
        <input
          aria-label="Поиск по почте"
          placeholder="Почта…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
        />
        <small className="muted">
          Подписаны: {list.data?.counts.subscribed ?? 0} · отписались:{' '}
          {list.data?.counts.unsubscribed ?? 0}
        </small>
        <Button
          variant="secondary"
          onClick={() =>
            download('/site-subscribers/export', 'site-subscribers.csv').catch(setError)
          }
        >
          <Download size={15} /> CSV для рассылки
        </Button>
      </div>
      <ErrorBox error={error || list.error} retry={list.refresh} />
      {list.loading ? (
        <Loading />
      ) : (
        <>
          <DataTable<Subscriber>
            rows={list.data?.items || []}
            empty={
              <Empty
                compact
                title="Подписчиков пока нет"
                description="Они появятся, когда посетители сайта подпишутся на рассылку."
              />
            }
            columns={[
              { key: 'email', label: 'Почта' },
              { key: 'lang', label: 'Язык' },
              {
                key: 'subscribed',
                label: 'Состояние',
                render: (r) =>
                  r.subscribed ? 'Подписан' : `Отписался ${date(r.unsubscribed_at || '')}`,
              },
              {
                key: 'consent_at',
                label: 'Согласие',
                render: (r) => (r.consent_at ? date(r.consent_at, true) : '—'),
              },
              { key: 'source', label: 'Источник', render: (r) => r.source || '—' },
            ]}
          />
          <Pagination page={page} total={Number(list.data?.total || 0)} onChange={setPage} />
        </>
      )}
    </div>
  );
}

type RequestRow = Entity & {
  number: string;
  title: string;
  client_name?: string;
  commercial_stage?: string;
};
type Conversion = { proposal: { items: Record<string, unknown>[] } };

/** Несколько заявок с сайта от одного клиента — в одну заявку CRM (ТЗ сайта 9.4.1, P1) */
export function AttachToRequest({
  siteRequestId,
  version,
  company,
  onClose,
  onDone,
}: {
  siteRequestId: string;
  version: number;
  company: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const [q, setQ] = useState('');
  const debounced = useDebounced(q);
  const requests = useApi<Page<RequestRow>>(
    `/requests?page_size=15&q=${encodeURIComponent(debounced)}`,
  );
  const conversion = useApi<Conversion>(`/site-requests/${siteRequestId}/conversion`);
  const command = useCommand();
  async function attach(requestId: string) {
    const items = conversion.data?.proposal.items ?? [];
    const result = await command
      .run(
        `/site-requests/${siteRequestId}/attach`,
        { version, request_id: requestId, items },
        'POST',
      )
      .catch(() => undefined);
    if (result) onDone();
  }
  return (
    <Modal title="Добавить в существующую заявку" wide onClose={onClose}>
      <div className="form-body">
        <p className="muted">
          Позиции и вложения заявки с сайта ({company}) перейдут в выбранную заявку CRM; заявка с
          сайта станет «Преобразованной» со ссылкой на неё.
        </p>
        <input
          aria-label="Поиск заявки"
          placeholder="Номер или название заявки CRM…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <ErrorBox error={command.error || requests.error || conversion.error} />
        {requests.loading ? (
          <Loading />
        ) : (
          <DataTable<RequestRow>
            rows={requests.data?.items || []}
            empty={<Empty compact title="Заявки не найдены" />}
            columns={[
              { key: 'number', label: 'Номер' },
              { key: 'title', label: 'Название' },
              { key: 'client_name', label: 'Клиент', render: (r) => r.client_name || '—' },
              {
                key: 'id',
                label: '',
                render: (r) => (
                  <Button
                    busy={command.busy}
                    disabled={!conversion.data}
                    onClick={() => void attach(r.id)}
                  >
                    <Link2 size={15} /> Добавить сюда
                  </Button>
                ),
              },
            ]}
          />
        )}
      </div>
    </Modal>
  );
}
