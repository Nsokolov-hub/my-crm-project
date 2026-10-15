import {
  ArrowRight,
  ArrowUpRight,
  CalendarDays,
  CheckCheck,
  ClipboardList,
  Clock3,
  Plus,
  Target,
  Truck,
} from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../app/Auth';
import { Badge, DataTable, Empty, ErrorBox, Loading, Section, TextLink } from '../components/ui';
import { date, decimal, stageLabels } from '../lib/format';
import { useApi } from '../lib/hooks';
import type { Entity } from '../lib/types';
export type DashboardData = {
  period: { from: string; to: string; basis: string; timezone: string; generated_at: string };
  new_requests: number;
  active_requests: number;
  closed_requests: number;
  sales: number;
  conversion: string | null;
  overdue_tasks: number;
  without_next_action: number;
  stages: { stage: string; count: number }[];
  receivables: { currency: string; total: string; paid: string; balance: string }[];
  recent_requests: Entity[];
  tasks: Entity[];
  manager_activity: Entity[];
  drilldown: Record<string, string>;
};
export function Dashboard() {
  const auth = useAuth();
  const navigate = useNavigate();
  const canReadAnalytics = auth.can('analytics.read');
  const dashboard = useApi<DashboardData>(canReadAnalytics ? '/analytics/dashboard' : null);
  const data = dashboard.data;
  const availableLinks = [
    { label: 'Мои заявки', to: '/requests', allowed: auth.can('requests.read') },
    { label: 'Клиенты', to: '/clients', allowed: auth.can('clients.read') },
    { label: 'Задачи', to: '/tasks', allowed: auth.can('tasks.read') },
    { label: 'Каталог товаров', to: '/catalog', allowed: auth.can('catalog.read') },
    {
      label: 'Настройки и мой аккаунт',
      to: '/settings',
      allowed: true,
    },
  ].filter((item) => item.allowed);
  const greeting = new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    weekday: 'long',
  }).format(new Date());
  return (
    <>
      <div className="dashboard-heading">
        <div>
          <div className="eyebrow">
            <span className="online-dot" /> Команда в одном пространстве
          </div>
          <h1>
            Хорошего рабочего дня, {auth.session?.user.name.split(' ')[0]}
            <span className="greeting-dot">.</span>
          </h1>
          <p>Все важные процессы под контролем. Начните с того, что требует внимания.</p>
        </div>
        <div className="date-chip">
          <CalendarDays size={16} />
          {greeting}
        </div>
      </div>
      <div className="welcome-strip">
        <div>
          <span className="eyebrow">От потребности до результата</span>
          <h2>Каждая заявка — связная история.</h2>
          <p>Запрос, квоты, расчёт и поставка. Следующий шаг всегда рядом.</p>
          {auth.can('requests.write') && (
            <Link className="button lime" to="/requests?new=1">
              <Plus size={17} />
              Создать заявку
            </Link>
          )}
        </div>
        <div className="process-art" aria-hidden="true">
          <span>
            <ClipboardList size={24} />
            <small>Запрос</small>
          </span>
          <i />
          <span>
            <Target size={24} />
            <small>Решение</small>
          </span>
          <i />
          <span>
            <Truck size={24} />
            <small>Поставка</small>
          </span>
        </div>
      </div>
      {!canReadAnalytics ? (
        <Section title="Доступные разделы" description="Откройте нужную часть CRM для своей роли.">
          <div className="quick-grid">
            {availableLinks.map((item) => (
              <Link to={item.to} key={item.to}>
                {item.label}
                <ArrowRight />
              </Link>
            ))}
          </div>
        </Section>
      ) : dashboard.loading ? (
        <Loading />
      ) : dashboard.error ? (
        <>
          <ErrorBox error={dashboard.error} retry={dashboard.refresh} />
          <div className="quick-grid">
            <Link to="/requests">
              Открыть заявки
              <ArrowRight />
            </Link>
            <Link to="/tasks">
              Мои задачи
              <ArrowRight />
            </Link>
            <Link to="/clients">
              База клиентов
              <ArrowRight />
            </Link>
          </div>
        </>
      ) : (
        data && (
          <>
            <div className="metric-grid">
              {[
                {
                  title: 'Активные заявки',
                  value: data.active_requests,
                  icon: ClipboardList,
                  to: '/requests',
                  hint: 'В текущей работе',
                },
                {
                  title: 'Новые заявки',
                  value: data.new_requests,
                  icon: Plus,
                  to: '/requests',
                  hint: 'За выбранный период',
                },
                {
                  title: 'Подтверждённые продажи',
                  value: data.sales,
                  icon: Target,
                  to: '/requests?stage=sale_confirmed',
                  hint: 'Первое согласование исполнения',
                },
                {
                  title: 'Просроченные задачи',
                  value: data.overdue_tasks,
                  icon: Clock3,
                  to: '/tasks?overdue=true',
                  hint: 'Требуют вашего внимания',
                },
              ].map(({ title, value, icon: Icon, to, hint }) => (
                <Link key={title} className="metric-card" to={to}>
                  <div>
                    <span>{title}</span>
                    <Icon size={19} />
                  </div>
                  <strong>{value ?? '—'}</strong>
                  <small>
                    {hint}
                    <ArrowUpRight size={15} />
                  </small>
                </Link>
              ))}
            </div>
            <div className="dashboard-columns">
              <Section
                title="Заявки в работе"
                description="Последние изменения и текущие этапы"
                action={<TextLink to="/requests">Все заявки</TextLink>}
              >
                <DataTable
                  rows={data.recent_requests || []}
                  onRow={(row) => navigate(`/requests/${row.id}`)}
                  columns={[
                    {
                      key: 'title',
                      label: 'Заявка',
                      render: (r) => (
                        <span className="stacked">
                          <strong>{String(r.title)}</strong>
                          <small>{String(r.number || r.id.slice(0, 8))}</small>
                        </span>
                      ),
                    },
                    {
                      key: 'commercial_stage',
                      label: 'Этап',
                      render: (r) => <Badge value={r.commercial_stage} />,
                    },
                    { key: 'due_at', label: 'Срок', render: (r) => date(r.due_at) },
                  ]}
                  empty={
                    <Empty
                      compact
                      title="Первые заявки уже ждут"
                      description="Создайте заявку и соберите весь процесс вокруг потребности клиента."
                      action={
                        <Link className="text-link" to="/requests?new=1">
                          Создать заявку
                          <ArrowRight size={15} />
                        </Link>
                      }
                    />
                  }
                />
              </Section>
              <Section
                title="В фокусе сегодня"
                description="Ближайшие действия команды"
                action={<TextLink to="/tasks">Все</TextLink>}
              >
                {data.tasks?.length ? (
                  <div className="task-preview">
                    {data.tasks.slice(0, 5).map((task) => (
                      <Link to="/tasks" key={task.id}>
                        <span className="task-check">
                          <CheckCheck size={17} />
                        </span>
                        <span>
                          <strong>{String(task.title)}</strong>
                          <small>{date(task.due_at, true)}</small>
                        </span>
                        <ArrowUpRight size={15} />
                      </Link>
                    ))}
                  </div>
                ) : (
                  <Empty
                    compact
                    title="Можно планировать дальше"
                    description="Нет задач в выбранной выборке."
                  />
                )}
              </Section>
            </div>
            <div className="dashboard-columns lower">
              <Section
                title="Воронка заявок"
                description="Текущее состояние заявок, созданных в периоде"
                action={<TextLink to="/analytics">Аналитика</TextLink>}
              >
                <div className="funnel">
                  {data.stages?.map((stage, index) => (
                    <Link key={stage.stage} to={`/requests?stage=${stage.stage}`}>
                      <span>{stageLabels[stage.stage] || stage.stage}</span>
                      <div>
                        <i
                          style={{
                            width: `${data.new_requests ? Math.max(2, (stage.count / data.new_requests) * 100) : 0}%`,
                            background: ['#8b78ed', '#a695ed', '#bdb0f2', '#d1c8f7'][index % 4],
                          }}
                        />
                      </div>
                      <strong>{stage.count}</strong>
                    </Link>
                  ))}
                </div>
              </Section>
              <Section
                title="Расчёты с клиентами"
                description="Остатки по подтверждённым распределениям"
              >
                {data.receivables?.length ? (
                  <div className="balances">
                    {data.receivables.map((row) => (
                      <Link to="/payments" key={row.currency}>
                        <span>
                          {row.currency}
                          <small>К оплате</small>
                        </span>
                        <strong>{decimal(row.balance)}</strong>
                        <ArrowUpRight size={17} />
                      </Link>
                    ))}
                  </div>
                ) : (
                  <Empty
                    compact
                    title="Открытых сумм пока нет"
                    description="Здесь появятся остатки по выставленным счетам — отдельно по каждой валюте."
                  />
                )}
              </Section>
            </div>
            <p className="report-footnote">
              Период: {date(data.period?.from)} — {date(data.period?.to)} ·{' '}
              {data.period?.timezone || 'Europe/Moscow'} · Тестовые заявки исключены · Сформировано{' '}
              {date(data.period?.generated_at, true)}
            </p>
          </>
        )
      )}
    </>
  );
}
