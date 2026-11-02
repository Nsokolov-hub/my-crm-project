import { RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { useAuth } from '../app/Auth';
import { BarChart } from '../components/BarChart';
import { Badge, Button, DataTable, ErrorBox, Loading, Section } from '../components/ui';
import { date } from '../lib/format';
import { useApi, useCommand, usePolling } from '../lib/hooks';
import { queryClient } from '../lib/queryClient';
import '../styles/site-analytics.scss';

/*
 * Яндекс Метрика и Вебмастер сайта. Токен Яндекса и отчёты хранит сайт (раз в час обновляет их сам);
 * CRM получает их по подписанному каналу событий. Токен вставляет сотрудник с «Настройками системы».
 */

type Access = { ok: boolean; error?: string; name?: string; host?: string };
type Status = {
  connected: boolean;
  source: 'admin' | 'server' | null;
  tail: string | null;
  login: string | null;
  savedAt: string | null;
  savedBy: string | null;
  access: { metrika?: Access; webmaster?: Access };
  tokenInvalid: boolean;
};
type Goal = {
  id: number;
  name: string;
  identifier: string | null;
  reaches: number;
  conversion: number;
};
type Metrika = {
  counter: string | null;
  errors: string[];
  missingGoals: string[];
  totals?: {
    visits: number;
    users: number;
    pageviews: number;
    bounceRate: number;
    duration: number;
  };
  days?: { date: string; visits: number; users: number }[];
  sources?: { key: string | null; name: string; visits: number; users: number }[];
  engines?: { name: string; visits: number }[];
  pages?: { path: string; visits: number; bounceRate: number }[];
  goals?: Goal[];
};
type Webmaster = {
  host: string | null;
  // false — Вебмастер ещё не загрузил данные о сайте: отчётов и добавления Sitemap пока нет.
  loaded?: boolean;
  errors: string[];
  summary?: { inSearch: number; excluded: number; sqi: number };
  problems?: { code: string; title: string; severity: string; severityTitle: string }[];
  sitemaps?: {
    url: string;
    urls: number;
    errors: number;
    children: number;
    lastAccess: string | null;
    added: boolean;
  }[];
  removed?: { title: string; count: number; examples: string[] }[];
  queries?: { text: string; shows: number; clicks: number; position: number }[];
  queryTotals?: {
    shows: number;
    clicks: number;
    days: { date: string; shows: number; clicks: number }[];
  };
  inSearch?: { date: string; value: number | null }[];
  crawled?: { ok: number; redirect: number; notFound: number; error: number };
};
type Report = {
  status: Status;
  period: number;
  fetchedAt: string | null;
  metrika: Metrika | null;
  webmaster: Webmaster | null;
};
type GoalResult = {
  identifier: string;
  name: string;
  state: 'exists' | 'created' | 'error';
  error?: string;
};
type Connected = {
  status: Status;
  access: { metrika: Access; webmaster: Access };
  goals: GoalResult[] | string;
};

const SITE = 'https://www.ogk-chem.ru';
const PERIODS = [7, 30, 90];
const n = (value: number | null | undefined) => (value ?? 0).toLocaleString('ru-RU');
const pct = (part: number, whole: number) =>
  whole ? `${((part / whole) * 100).toLocaleString('ru-RU', { maximumFractionDigits: 1 })}%` : '—';
const day = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
const minutes = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;

function goalsText(goals: GoalResult[] | string) {
  if (typeof goals === 'string') return `Цели не созданы: ${goals}`;
  const created = goals.filter((g) => g.state === 'created').length;
  const failed = goals.filter((g) => g.state === 'error');
  return (
    `Цели Метрики: создано ${created}, уже были ${goals.length - created - failed.length}` +
    (failed.length ? `, не удалось ${failed.length} (${failed[0].error})` : '')
  );
}

/** Линия по дням; пропуски — разрыв. Число дня — в подсказке при наведении. */
function LineChart({
  title,
  points,
}: {
  title: string;
  points: { date: string; value: number | null }[];
}) {
  const values = points.map((p) => p.value).filter((v): v is number => v !== null);
  if (!values.length || values.every((v) => v === 0))
    return <p className="site-analytics-text muted">{title}: за период данных пока нет</p>;
  const W = 640;
  const H = 150;
  const max = Math.max(...values);
  const x = (i: number) => (points.length === 1 ? W / 2 : (i * W) / (points.length - 1));
  const y = (v: number) => H - 2 - (v / max) * (H - 12);
  const segments: [number, number][][] = [];
  let current: [number, number][] = [];
  points.forEach((p, i) => {
    if (p.value === null) {
      if (current.length) segments.push(current);
      current = [];
    } else current.push([x(i), y(p.value)]);
  });
  if (current.length) segments.push(current);
  const step = W / points.length;
  return (
    <figure className="line-chart" aria-label={title}>
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img">
        <line x1={0} x2={W} y1={y(max)} y2={y(max)} />
        <line x1={0} x2={W} y1={H - 2} y2={H - 2} />
        {segments.map((s) => {
          const line = s.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(' ');
          return (
            <g key={line}>
              <polygon points={`${s[0][0]},${H - 2} ${line} ${s[s.length - 1][0]},${H - 2}`} />
              <polyline points={line} />
            </g>
          );
        })}
        {points.map((p, i) => (
          <rect key={p.date} x={x(i) - step / 2} y={0} width={step} height={H} fill="transparent">
            <title>{`${day(p.date)}: ${p.value === null ? 'нет данных' : n(p.value)}`}</title>
          </rect>
        ))}
      </svg>
      <div className="line-chart-axis">
        <span>{day(points[0].date)}</span>
        <span>максимум {n(max)}</span>
        <span>{day(points[points.length - 1].date)}</span>
      </div>
    </figure>
  );
}

function Warnings({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="site-analytics-warning" role="status">
      {items.map((item) => (
        <div key={item}>{item}</div>
      ))}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Connection({
  status,
  admin,
  onChange,
}: {
  status: Status;
  admin: boolean;
  onChange: () => void;
}) {
  const command = useCommand();
  const [token, setToken] = useState('');
  const [result, setResult] = useState<Connected | null>(null);
  const access = result?.access ?? status.access;
  async function connect() {
    const answer = await command
      .run<Connected>('/site-analytics/token', { token }, 'PUT')
      .catch(() => undefined);
    if (!answer) return;
    setToken('');
    setResult(answer);
    onChange();
  }
  async function disconnect() {
    if (
      !window.confirm(
        'Отключить Яндекс? Отчёты и передача результатов заявок в Метрику остановятся.',
      )
    )
      return;
    const answer = await command.run('/site-analytics/token', {}, 'DELETE').catch(() => undefined);
    if (answer) {
      setResult(null);
      onChange();
    }
  }
  return (
    <Section
      title="Подключение к Яндексу"
      description="Один токен аккаунта, которому принадлежат счётчик Метрики и сайт в Вебмастере"
    >
      <div className="site-analytics-text">
        <p>
          {status.tokenInvalid ? (
            <Badge value="Токен не работает — вставьте новый" tone="red" />
          ) : status.connected ? (
            <Badge value="Подключено" tone="green" />
          ) : (
            <Badge value="Не подключено" tone="amber" />
          )}{' '}
          {status.source === 'admin' && (
            <span className="muted">
              {status.login ? `аккаунт ${status.login} · ` : ''}токен …{status.tail} ·{' '}
              {status.savedBy}, {date(status.savedAt, true)}
            </span>
          )}
        </p>
        {(access.metrika || access.webmaster) && (
          <ul>
            {(
              [
                ['Метрика', access.metrika],
                ['Вебмастер', access.webmaster],
              ] as const
            ).map(([label, item]) =>
              item ? (
                <li key={label}>
                  {label}: {item.ok ? 'есть доступ' : 'нет доступа'}
                  <span className="muted"> — {item.ok ? item.name || item.host : item.error}</span>
                </li>
              ) : null,
            )}
          </ul>
        )}
        {result && <p>{goalsText(result.goals)}</p>}
        <ErrorBox error={command.error} />
        {admin ? (
          <div className="site-analytics-token">
            <label>
              {status.connected ? 'Новый токен (заменит текущий)' : 'Токен Яндекса'}
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                placeholder="y0_…"
                value={token}
                onChange={(event) => setToken(event.target.value)}
              />
            </label>
            <div className="site-analytics-actions">
              <Button busy={command.busy} disabled={!token.trim()} onClick={() => void connect()}>
                {status.connected ? 'Заменить токен' : 'Подключить'}
              </Button>
              {status.source === 'admin' && (
                <Button variant="ghost" disabled={command.busy} onClick={() => void disconnect()}>
                  Отключить
                </Button>
              )}
            </div>
            <p className="muted">
              Токен уходит на сайт и хранится там зашифрованным; здесь видны только последние 4
              символа. Получить: oauth.yandex.ru → приложение с правами metrika:read, metrika:write,
              webmaster:hostinfo, webmaster:verify → ссылка
              https://oauth.yandex.ru/authorize?response_type=token&amp;client_id=…
            </p>
          </div>
        ) : (
          !status.connected && (
            <p className="muted">Подключить может сотрудник с правом «Настройки системы».</p>
          )
        )}
      </div>
    </Section>
  );
}

function MetrikaView({
  m,
  charts,
  admin,
  onChange,
}: {
  m: Metrika;
  charts: boolean;
  admin: boolean;
  onChange: () => void;
}) {
  const command = useCommand();
  const [done, setDone] = useState('');
  const sources = m.sources ?? [];
  const total = sources.reduce((sum, s) => sum + s.visits, 0);
  const goals = [...(m.goals ?? [])].sort((a, b) => b.reaches - a.reaches);
  async function createGoals() {
    const answer = await command
      .run<{ goals: GoalResult[] }>('/site-analytics/goals')
      .catch(() => undefined);
    if (answer) {
      setDone(goalsText(answer.goals));
      onChange();
    }
  }
  return (
    <>
      <Warnings items={m.errors} />
      {m.totals && (
        <div className="metric-grid">
          <Metric label="Визиты" value={n(m.totals.visits)} />
          <Metric label="Посетители" value={n(m.totals.users)} />
          <Metric label="Отказы" value={`${m.totals.bounceRate.toLocaleString('ru-RU')}%`} />
          <Metric label="Время на сайте" value={minutes(m.totals.duration)} />
        </div>
      )}
      <Section
        title="Посещаемость — Яндекс Метрика"
        description={`Просмотров страниц: ${n(m.totals?.pageviews)}`}
        action={
          m.counter ? (
            <a
              className="text-link"
              href={`https://metrika.yandex.ru/dashboard?id=${m.counter}`}
              target="_blank"
              rel="noreferrer"
            >
              Открыть Метрику
            </a>
          ) : undefined
        }
      >
        {charts && m.days && (
          <LineChart
            title="Визиты по дням"
            points={m.days.map((d) => ({ date: d.date, value: d.visits }))}
          />
        )}
        {charts && sources.length > 0 && (
          <BarChart
            title="Откуда приходят, визиты"
            rows={sources.map((s) => ({
              id: s.name,
              label: s.name,
              value: s.visits,
              display: `${n(s.visits)} · ${pct(s.visits, total)}`,
            }))}
          />
        )}
        {charts && Boolean(m.engines?.length) && (
          <BarChart
            title="Из поисковых систем, визиты"
            rows={(m.engines ?? []).map((e) => ({
              id: e.name,
              label: e.name,
              value: e.visits,
              display: n(e.visits),
            }))}
          />
        )}
        {!charts && (
          <DataTable
            rows={sources.map((s) => ({ ...s, id: s.name }))}
            empty="Визитов пока нет"
            columns={[
              { key: 'name', label: 'Источник' },
              { key: 'visits', label: 'Визиты', render: (r) => n(r.visits) },
              { key: 'share', label: 'Доля', render: (r) => pct(r.visits, total) },
            ]}
          />
        )}
      </Section>
      <Section title="Страницы входа" description="С каких страниц начинают визит">
        <DataTable
          rows={(m.pages ?? []).map((p) => ({ ...p, id: p.path }))}
          empty="Визитов пока нет"
          columns={[
            {
              key: 'path',
              label: 'Страница',
              render: (r) => (
                <a className="text-link" href={`${SITE}${r.path}`} target="_blank" rel="noreferrer">
                  {r.path}
                </a>
              ),
            },
            { key: 'visits', label: 'Визиты', render: (r) => n(r.visits) },
            {
              key: 'bounceRate',
              label: 'Отказы',
              render: (r) => `${r.bounceRate.toLocaleString('ru-RU')}%`,
            },
          ]}
        />
      </Section>
      <Section
        title="Цели"
        description="Заявки, клики и стадии заявок из CRM: «CRM: …» Метрика получает, когда заявка с сайта взята в работу, отправлено КП или подтверждена продажа"
      >
        {m.missingGoals.length > 0 && (
          <div className="site-analytics-text">
            <Warnings
              items={[
                `В Метрике нет целей для событий сайта: ${m.missingGoals.join(', ')}. Без них они не видны в отчётах.`,
              ]}
            />
            {admin && (
              <Button variant="secondary" busy={command.busy} onClick={() => void createGoals()}>
                Создать цели
              </Button>
            )}
          </div>
        )}
        {done && <p className="site-analytics-text">{done}</p>}
        <ErrorBox error={command.error} />
        <DataTable
          rows={goals.map((g) => ({ ...g, id: String(g.id) }))}
          empty="Целей нет"
          columns={[
            {
              key: 'name',
              label: 'Цель',
              render: (r) => (
                <>
                  {r.name} {r.identifier && <code>{r.identifier}</code>}
                </>
              ),
            },
            { key: 'reaches', label: 'Достижений', render: (r) => n(r.reaches) },
            {
              key: 'conversion',
              label: 'Конверсия',
              render: (r) => `${r.conversion.toLocaleString('ru-RU')}%`,
            },
          ]}
        />
      </Section>
    </>
  );
}

function WebmasterView({
  w,
  charts,
  admin,
  onChange,
}: {
  w: Webmaster;
  charts: boolean;
  admin: boolean;
  onChange: () => void;
}) {
  const command = useCommand();
  const [done, setDone] = useState('');
  const q = w.queryTotals;
  const ownSitemap = (w.sitemaps ?? []).some((s) => s.added && s.url.endsWith('/sitemap.xml'));
  async function addSitemap() {
    const answer = await command
      .run<{ url: string; state: string }>('/site-analytics/sitemap')
      .catch(() => undefined);
    if (answer) {
      setDone(
        answer.state === 'added'
          ? `Sitemap добавлен в Вебмастер: ${answer.url}`
          : 'Sitemap уже был добавлен',
      );
      onChange();
    }
  }
  return (
    <>
      <Warnings items={w.errors} />
      <div className="metric-grid">
        <Metric label="Страниц в поиске" value={n(w.summary?.inSearch)} />
        <Metric label="Исключено из поиска" value={n(w.summary?.excluded)} />
        <Metric label="Показы в поиске" value={n(q?.shows)} />
        <Metric label={`Клики, CTR ${q ? pct(q.clicks, q.shows) : '—'}`} value={n(q?.clicks)} />
      </div>
      <Section
        title="Поиск — Яндекс Вебмастер"
        description={`ИКС сайта: ${n(w.summary?.sqi)} · показы и клики Вебмастер считает с задержкой в несколько дней`}
        action={
          <a
            className="text-link"
            href="https://webmaster.yandex.ru/sites/"
            target="_blank"
            rel="noreferrer"
          >
            Открыть Вебмастер
          </a>
        }
      >
        {charts && w.inSearch && <LineChart title="Страниц в поиске" points={w.inSearch} />}
        {charts && q && (
          <LineChart
            title="Показы по дням"
            points={q.days.map((d) => ({ date: d.date, value: d.shows }))}
          />
        )}
        <DataTable
          rows={(w.queries ?? []).map((r) => ({ ...r, id: r.text }))}
          empty="Запросов за период пока нет"
          columns={[
            { key: 'text', label: 'Поисковый запрос' },
            { key: 'shows', label: 'Показы', render: (r) => n(r.shows) },
            { key: 'clicks', label: 'Клики', render: (r) => n(r.clicks) },
            { key: 'ctr', label: 'CTR', render: (r) => pct(r.clicks, r.shows) },
            {
              key: 'position',
              label: 'Позиция',
              render: (r) => r.position.toLocaleString('ru-RU'),
            },
          ]}
        />
      </Section>
      <div className="site-analytics-grid">
        <Section
          title="Почему страницы выпадают из поиска"
          description="По последним 100 событиям Вебмастера"
        >
          <DataTable
            rows={(w.removed ?? []).map((r) => ({ ...r, id: r.title }))}
            empty="Недавно из поиска ничего не выпадало"
            columns={[
              {
                key: 'title',
                label: 'Причина',
                render: (r) => (
                  <>
                    {r.title}
                    <div className="muted site-analytics-examples">
                      {r.examples.slice(0, 2).join(', ')}
                    </div>
                  </>
                ),
              },
              { key: 'count', label: 'Страниц', render: (r) => n(r.count) },
            ]}
          />
        </Section>
        <Section
          title="Проблемы сайта"
          description={
            w.crawled
              ? `Робот обошёл: ${n(w.crawled.ok)} с ответом 200, ${n(w.crawled.redirect)} перенаправлений, ${n(w.crawled.notFound)} не найдено, ${n(w.crawled.error)} ошибок`
              : undefined
          }
        >
          <DataTable
            rows={(w.problems ?? []).map((p) => ({ ...p, id: p.code }))}
            empty="Вебмастер проблем не нашёл"
            columns={[
              { key: 'title', label: 'Проблема' },
              {
                key: 'severity',
                label: 'Важность',
                render: (r) => (
                  <Badge
                    value={r.severityTitle}
                    tone={
                      ['FATAL', 'CRITICAL'].includes(r.severity)
                        ? 'red'
                        : r.severity === 'POSSIBLE_PROBLEM'
                          ? 'amber'
                          : 'gray'
                    }
                  />
                ),
              },
            ]}
          />
        </Section>
      </div>
      <Section
        title="Файлы Sitemap"
        action={
          admin && !ownSitemap && w.host && w.loaded !== false ? (
            <Button variant="secondary" busy={command.busy} onClick={() => void addSitemap()}>
              Добавить sitemap.xml в Вебмастер
            </Button>
          ) : undefined
        }
      >
        {w.loaded === false && (
          <p className="site-analytics-text muted">
            Вебмастер ещё не загрузил данные о сайте и пока не принимает Sitemap. Яндекс находит
            карту сайта сам через robots.txt, а когда данные загрузятся, сайт добавит её в Вебмастер
            автоматически.
          </p>
        )}
        {done && <p className="site-analytics-text">{done}</p>}
        <ErrorBox error={command.error} />
        <DataTable
          rows={(w.sitemaps ?? []).map((s) => ({ ...s, id: s.url }))}
          empty="Вебмастер ещё не нашёл Sitemap"
          columns={[
            {
              key: 'url',
              label: 'Файл',
              render: (r) => (r.added ? `${r.url} (добавлен вручную)` : r.url),
            },
            { key: 'urls', label: 'Адресов', render: (r) => n(r.urls) },
            { key: 'children', label: 'Вложенных', render: (r) => n(r.children) },
            { key: 'errors', label: 'Ошибок', render: (r) => n(r.errors) },
            { key: 'lastAccess', label: 'Робот загружал', render: (r) => date(r.lastAccess, true) },
          ]}
        />
      </Section>
    </>
  );
}

export function SiteAnalyticsReport({ charts }: { charts: boolean }) {
  const auth = useAuth();
  const admin = auth.can('admin.settings');
  const [period, setPeriod] = useState(() => {
    try {
      return Number(localStorage.getItem('report:site:period')) || 30;
    } catch {
      return 30;
    }
  });
  const path = `/site-analytics?period=${period}`;
  const report = useApi<Report>(path);
  const refresh = useCommand();
  const d = report.data;
  // Сразу после подключения отчётов ещё нет: сайт загружает их из Яндекса в течение минуты.
  usePolling(report.refresh, d?.status.connected && !d.fetchedAt ? 20_000 : null);
  function choose(value: number) {
    setPeriod(value);
    try {
      localStorage.setItem('report:site:period', String(value));
    } catch {
      /* Период просто не запомнится. */
    }
  }
  function changed() {
    void queryClient.invalidateQueries({
      queryKey: ['api'],
      predicate: (q) => String(q.queryKey[1]).startsWith('/site-analytics'),
    });
  }
  async function reload() {
    const fresh = await refresh
      .run<Report>(`/site-analytics/refresh?period=${period}`)
      .catch(() => undefined);
    if (fresh) queryClient.setQueryData(['api', path], fresh);
  }
  return (
    <>
      <div className="site-analytics-toolbar">
        <div className="segmented" role="group" aria-label="Период">
          {PERIODS.map((value) => (
            <button
              key={value}
              type="button"
              className={value === period ? 'active' : ''}
              onClick={() => choose(value)}
            >
              {value} дней
            </button>
          ))}
        </div>
        {d?.fetchedAt && (
          <span className="muted">Данные на {date(d.fetchedAt, true)} · обновляются раз в час</span>
        )}
        {d?.status.connected && (
          <Button variant="secondary" busy={refresh.busy} onClick={() => void reload()}>
            <RefreshCw size={16} />
            Обновить сейчас
          </Button>
        )}
      </div>
      <ErrorBox error={refresh.error} />
      {report.loading ? (
        <Loading />
      ) : report.error ? (
        <ErrorBox error={report.error} retry={report.refresh} />
      ) : d ? (
        <>
          {d.status.tokenInvalid && (
            <Warnings
              items={[
                'Яндекс не принимает токен: он отозван или истёк. Вставьте новый в блоке «Подключение к Яндексу» внизу.',
              ]}
            />
          )}
          {d.status.connected && !d.fetchedAt && <Loading />}
          {d.metrika && (
            <MetrikaView m={d.metrika} charts={charts} admin={admin} onChange={changed} />
          )}
          {d.webmaster && (
            <WebmasterView w={d.webmaster} charts={charts} admin={admin} onChange={changed} />
          )}
          <Connection status={d.status} admin={admin} onChange={changed} />
        </>
      ) : null}
    </>
  );
}
