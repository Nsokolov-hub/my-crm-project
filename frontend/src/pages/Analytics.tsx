import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Download,
  Factory,
  Mailbox,
  Tags,
  Users,
  Wallet,
  Workflow,
} from 'lucide-react';
import { useState } from 'react';
import type { ComponentType } from 'react';
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../app/Auth';
import { BarChart } from '../components/BarChart';
import {
  Badge,
  Button,
  DataTable,
  ErrorBox,
  Loading,
  PageHeading,
  Section,
} from '../components/ui';
import { download } from '../lib/api';
import { date, decimal, stageLabels, today } from '../lib/format';
import { useApi } from '../lib/hooks';
import { SalesManagerReport } from './Business';
import type { Entity } from '../lib/types';
import type { DashboardData } from './Dashboard';
import { ManufacturerReport } from './Manufacturers';
import { SupplierPricesReport } from './SupplierPrices';
import { SupplierReport } from './SupplierResponses';

type Settings = { preset: string; from: string; to: string; charts: boolean };
type ReportProps = { from: string; to: string; charts: boolean };
type Report = {
  id: string;
  title: string;
  description: string;
  icon: ComponentType<{ size?: number }>;
  permission: string;
  charts: boolean;
  exportable?: boolean;
  defaultPreset: string;
  render: (props: ReportProps) => React.ReactNode;
};

function iso(value: Date) {
  return new Date(value.getTime() - value.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

const PRESETS: { id: string; label: string; range?: () => [string, string] }[] = [
  {
    id: 'month',
    label: 'Этот месяц',
    range: () => [today().slice(0, 8) + '01', today()],
  },
  {
    id: 'previous_month',
    label: 'Прошлый месяц',
    range: () => {
      const now = new Date();
      return [
        iso(new Date(now.getFullYear(), now.getMonth() - 1, 1)),
        iso(new Date(now.getFullYear(), now.getMonth(), 0)),
      ];
    },
  },
  {
    id: 'quarter',
    label: 'Этот квартал',
    range: () => {
      const now = new Date();
      return [iso(new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3, 1)), today()];
    },
  },
  { id: 'year', label: 'С начала года', range: () => [today().slice(0, 4) + '-01-01', today()] },
  { id: 'custom', label: 'Свой период' },
];

function Summary({ from, to, charts }: ReportProps) {
  const navigate = useNavigate();
  const report = useApi<DashboardData>(`/analytics/dashboard?date_from=${from}&date_to=${to}`);
  const d = report.data;
  if (report.loading) return <Loading />;
  if (report.error) return <ErrorBox error={report.error} retry={report.refresh} />;
  if (!d) return null;
  return (
    <>
      <div className="metric-grid">
        {[
          { title: 'Создано заявок', value: d.new_requests },
          { title: 'Закрыто заявок', value: d.closed_requests },
          { title: 'Подтверждено продаж', value: d.sales },
          {
            title: 'Конверсия закрытых',
            value: d.conversion === null ? '—' : `${decimal(d.conversion)}%`,
          },
        ].map((item) => (
          <div className="metric-card" key={item.title}>
            <span>{item.title}</span>
            <strong>{item.value}</strong>
          </div>
        ))}
      </div>
      <div className="info-note">
        Конверсия = продажи среди закрытых заявок / все закрытые заявки. Незавершённые заявки не
        входят в знаменатель. Альтернативные КП не образуют отдельные продажи.
      </div>
      <Section title="Распределение по этапам">
        {charts && d.stages.length > 0 && (
          <BarChart
            title="Заявок на этапе"
            rows={d.stages.map((row) => ({
              id: row.stage,
              label: stageLabels[row.stage] || row.stage,
              value: row.count,
              display: String(row.count),
            }))}
          />
        )}
        <DataTable
          rows={d.stages.map((r) => ({ ...r, id: r.stage }))}
          onRow={(r) => navigate(`/requests?stage=${r.stage}`)}
          columns={[
            { key: 'stage', label: 'Этап', render: (r) => <Badge value={r.stage} /> },
            { key: 'count', label: 'Заявок' },
          ]}
        />
      </Section>
      <p className="report-footnote">
        {d.period?.basis} · {date(d.period?.from)} — {date(d.period?.to)} · Сформировано{' '}
        {date(d.period?.generated_at, true)}
      </p>
    </>
  );
}

function Receivables({ from, to, charts }: ReportProps) {
  const report = useApi<DashboardData>(`/analytics/dashboard?date_from=${from}&date_to=${to}`);
  const rows = (report.data?.receivables || []).map((r) => ({ ...r, id: r.currency }));
  return (
    <Section title="Взаиморасчёты по валютам" description="Остатки по выставленным счетам">
      <ErrorBox error={report.error} retry={report.refresh} />
      {report.loading ? (
        <Loading />
      ) : (
        <>
          {charts && rows.length > 0 && (
            <BarChart
              title="Остаток к оплате"
              rows={rows.map((r) => ({
                id: r.currency,
                label: r.currency,
                value: Number(r.balance),
                display: decimal(r.balance),
              }))}
            />
          )}
          <DataTable
            rows={rows}
            columns={[
              { key: 'currency', label: 'Валюта' },
              { key: 'total', label: 'Выставлено', render: (r) => decimal(r.total) },
              { key: 'paid', label: 'Распределено', render: (r) => decimal(r.paid) },
              { key: 'balance', label: 'Остаток', render: (r) => decimal(r.balance) },
            ]}
          />
        </>
      )}
    </Section>
  );
}

function Activity({ from, to, charts }: ReportProps) {
  const report = useApi<DashboardData>(`/analytics/dashboard?date_from=${from}&date_to=${to}`);
  const rows: Entity[] = (report.data?.manager_activity || []).map((r, i) => ({
    ...r,
    id: String(r.id || i),
  }));
  return (
    <Section title="Активность сотрудников" description="Звонки, заявки и просроченные задачи">
      <ErrorBox error={report.error} retry={report.refresh} />
      {report.loading ? (
        <Loading />
      ) : (
        <>
          {charts && rows.length > 0 && (
            <BarChart
              title="Звонков за период"
              rows={rows.map((r) => ({
                id: r.id,
                label: String(r.name),
                value: Number(r.calls || 0),
                display: String(r.calls || 0),
              }))}
            />
          )}
          <DataTable
            rows={rows}
            columns={[
              { key: 'name', label: 'Сотрудник' },
              { key: 'calls', label: 'Звонки' },
              { key: 'requests', label: 'Заявки' },
              { key: 'overdue_tasks', label: 'Просрочено задач' },
            ]}
          />
        </>
      )}
    </Section>
  );
}

const REPORTS: Report[] = [
  {
    id: 'sales-managers',
    title: 'Продажи по менеджерам',
    description:
      'Принятые позиции подтверждённых продаж: выручка, валовая прибыль, рентабельность.',
    icon: BarChart3,
    permission: 'finance.profit.read',
    charts: true,
    defaultPreset: 'month',
    render: (props) => <SalesManagerReport {...props} />,
  },
  {
    id: 'requests',
    title: 'Заявки и конверсия',
    description: 'Создано, закрыто и продано за период, конверсия и распределение по этапам.',
    icon: Workflow,
    permission: 'analytics.reports',
    charts: true,
    exportable: true,
    defaultPreset: 'year',
    render: (props) => <Summary {...props} />,
  },
  {
    id: 'manufacturers',
    title: 'Производители и бренды',
    description: 'Заявки, позиции, цены поставщиков и заказы по производителям и брендам.',
    icon: Factory,
    permission: 'analytics.reports',
    charts: false,
    defaultPreset: 'year',
    render: ({ from, to }) => <ManufacturerReport from={from} to={to} />,
  },
  {
    id: 'suppliers',
    title: 'Ответы поставщиков',
    description: 'Доля ответов на запросы цен, время ответа и производители без цен.',
    icon: Mailbox,
    permission: 'analytics.reports',
    charts: false,
    defaultPreset: 'year',
    render: ({ from, to }) => <SupplierReport from={from} to={to} />,
  },
  {
    // Business remark of 21 October 2026.
    id: 'supplier-prices',
    title: 'База цен поставщиков',
    description:
      'Все цены из квот: товар, фасовка, цена в валюте и в рублях, срок. Выгрузка в Excel с лучшими ценами.',
    icon: Tags,
    permission: 'finance.purchase.read',
    charts: false,
    defaultPreset: 'year',
    render: ({ from, to }) => <SupplierPricesReport from={from} to={to} />,
  },
  {
    id: 'receivables',
    title: 'Взаиморасчёты по валютам',
    description: 'Выставлено, распределено и остаток к оплате в каждой валюте.',
    icon: Wallet,
    permission: 'analytics.reports',
    charts: true,
    defaultPreset: 'year',
    render: (props) => <Receivables {...props} />,
  },
  {
    id: 'activity',
    title: 'Активность сотрудников',
    description: 'Звонки, заявки и просроченные задачи каждого сотрудника.',
    icon: Users,
    permission: 'analytics.reports',
    charts: true,
    defaultPreset: 'month',
    render: (props) => <Activity {...props} />,
  },
];

function useReports() {
  const auth = useAuth();
  return REPORTS.filter((report) => auth.can('analytics.read') && auth.can(report.permission));
}

export function Analytics() {
  const reports = useReports();
  return (
    <>
      <PageHeading
        title="Аналитика"
        description="Выберите отчёт: у каждого свой период и настройки отображения."
      />
      {reports.length ? (
        <div className="report-catalog">
          {reports.map(({ id, title, description, icon: Icon }) => (
            <Link key={id} to={`/analytics/${id}`}>
              <strong>
                <Icon size={18} />
                {title}
                <ArrowRight size={15} />
              </strong>
              <span>{description}</span>
            </Link>
          ))}
        </div>
      ) : (
        <p className="info-note">Для вашей роли отчёты не настроены.</p>
      )}
    </>
  );
}

function initialSettings(report: Report): Settings {
  const preset = PRESETS.find((item) => item.id === report.defaultPreset) || PRESETS[0];
  const [from, to] = preset.range?.() || [today(), today()];
  const fallback = { preset: preset.id, from, to, charts: report.charts };
  try {
    const saved = JSON.parse(
      localStorage.getItem(`report:${report.id}`) || 'null',
    ) as Settings | null;
    if (!saved) return fallback;
    // A named period is recalculated from today; a custom one is kept as entered.
    const range = PRESETS.find((item) => item.id === saved.preset)?.range?.();
    return range ? { ...saved, from: range[0], to: range[1] } : saved;
  } catch {
    return fallback;
  }
}

function ReportView({ report }: { report: Report }) {
  const auth = useAuth();
  const [settings, setSettings] = useState<Settings>(() => initialSettings(report));
  const [error, setError] = useState<unknown>();
  function change(next: Partial<Settings>) {
    setSettings((current) => {
      const value = { ...current, ...next };
      try {
        localStorage.setItem(`report:${report.id}`, JSON.stringify(value));
      } catch {
        /* Настройки просто не запомнятся. */
      }
      return value;
    });
  }
  const query = `?date_from=${settings.from}&date_to=${settings.to}`;
  return (
    <>
      <Link className="text-link back-link" to="/analytics">
        <ArrowLeft size={15} /> Все отчёты
      </Link>
      <PageHeading
        title={report.title}
        description={report.description}
        actions={
          report.exportable && auth.can('exports.download') ? (
            <Button
              variant="secondary"
              onClick={() =>
                void download(`/analytics/export.xlsx${query}`, `${report.title}.xlsx`).catch(
                  setError,
                )
              }
            >
              <Download size={17} />
              Экспорт XLSX
            </Button>
          ) : undefined
        }
      />
      <div className="report-settings" role="group" aria-label="Настройки отчёта">
        <label>
          Период
          <select
            value={settings.preset}
            onChange={(event) => {
              const range = PRESETS.find((item) => item.id === event.target.value)?.range?.();
              change({
                preset: event.target.value,
                ...(range ? { from: range[0], to: range[1] } : {}),
              });
            }}
          >
            {PRESETS.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          С даты
          <input
            type="date"
            value={settings.from}
            onChange={(event) => change({ preset: 'custom', from: event.target.value })}
          />
        </label>
        <label>
          По дату
          <input
            type="date"
            value={settings.to}
            onChange={(event) => change({ preset: 'custom', to: event.target.value })}
          />
        </label>
        {report.charts && (
          <label className="report-toggle">
            <input
              type="checkbox"
              checked={settings.charts}
              onChange={(event) => change({ charts: event.target.checked })}
            />
            Показывать графики
          </label>
        )}
        <span className="muted">Москва, UTC+3 · Без тестовых данных</span>
      </div>
      <ErrorBox error={error} />
      {settings.from && settings.to ? (
        report.render({ from: settings.from, to: settings.to, charts: settings.charts })
      ) : (
        <p className="info-note">Укажите начало и конец периода.</p>
      )}
    </>
  );
}

export function AnalyticsReport() {
  const { report: id } = useParams();
  const reports = useReports();
  const report = reports.find((item) => item.id === id);
  if (!report) return <Navigate to="/analytics" replace />;
  // A new key resets the settings when moving between reports.
  return <ReportView key={report.id} report={report} />;
}
