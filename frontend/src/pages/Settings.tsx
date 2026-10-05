import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus, ShieldCheck } from 'lucide-react';
import { useAuth } from '../app/Auth';
import { Collection } from '../components/Collection';
import { ExpenseTypeEditor } from '../components/ExpenseTypeEditor';
import { FinancialProfileEditor } from '../components/FinancialProfileEditor';
import { ItemizedProfileEditor } from '../components/ItemizedProfileEditor';
import { RolePermissionsEditor, UserAccessEditor } from '../components/RolePermissionsEditor';
import { WorkingRulesEditor } from '../components/WorkingRulesEditor';
import { Badge, Button, DetailPairs, ErrorBox, PageHeading, Section } from '../components/ui';
import { api } from '../lib/api';
import { useCommand } from '../lib/hooks';
import { date } from '../lib/format';
import type { Entity, Field } from '../lib/types';

const sellerFields: Field[] = [
  { name: 'name', label: 'Название организации', required: true },
  {
    name: 'currency',
    label: 'Валюта учёта',
    required: true,
    value: 'RUB',
    help: 'Трёхбуквенный код валюты, например RUB.',
  },
  { name: 'tax_id', label: 'ИНН' },
  { name: 'registration_code', label: 'КПП' },
  { name: 'legal_address', label: 'Юридический адрес', wide: true },
  { name: 'bank', label: 'Банк' },
  { name: 'bank_account', label: 'Расчётный счёт' },
  { name: 'bank_code', label: 'БИК' },
];
const sellerDetailFields = {
  tax_id: 'ИНН',
  registration_code: 'КПП',
  legal_address: 'Юридический адрес',
  bank: 'Банк',
  bank_account: 'Расчётный счёт',
  bank_code: 'БИК',
};
function sellerBody(values: Record<string, unknown>): Record<string, unknown> {
  const details = Object.fromEntries(
    Object.entries(sellerDetailFields)
      .map(([key, label]) => [label, String(values[key] || '').trim()])
      .filter(([, value]) => value),
  );
  return {
    name: String(values.name || '').trim(),
    currency: String(values.currency || '')
      .trim()
      .toUpperCase(),
    details,
  };
}
function sellerDetails(row: Entity): string {
  const details = row.details;
  if (!details || typeof details !== 'object' || Array.isArray(details)) return 'Не заполнены';
  const values = Object.entries(details as Record<string, unknown>)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => `${key}: ${String(value)}`);
  return values.length ? values.join(' · ') : 'Не заполнены';
}
const userFields: Field[] = [
  { name: 'own_requests_only', label: 'Показывать только собственные заявки', type: 'checkbox' },
  { name: 'name', label: 'Имя сотрудника', required: true },
  { name: 'email', label: 'Рабочая почта', type: 'email', required: true },
  { name: 'password', label: 'Начальный пароль', type: 'password', minLength: 12, required: true },
  { name: 'role_ids', label: 'Роли', type: 'multiselect', source: '/admin/roles' },
];
const calculationLabels: Record<string, string> = {
  FIXED: 'Фиксированная сумма',
  PERCENTAGE: 'Процент',
  BRACKET: 'По диапазону',
  MANUAL: 'Ручной ввод',
};
const distributionLabels: Record<string, string> = {
  BY_QUANTITY: 'По количеству',
  BY_PURCHASE_VALUE: 'По закупочной стоимости',
  EQUALLY_BY_POSITION: 'Поровну по строкам',
  BY_WEIGHT: 'По весу',
  MANUAL: 'Вручную',
};
const baseLabels: Record<string, string> = {
  PURCHASE: 'Закупка',
  CUSTOMS_BASE: 'Таможенная стоимость',
  DUTY: 'Пошлина',
  COST: 'Себестоимость',
};
export function Settings() {
  const auth = useAuth();
  const [tab, setTab] = useState('organization');
  const [selected, setSelected] = useState<Entity>();
  const [editing, setEditing] = useState(false);
  const [profileMode, setProfileMode] = useState<'itemized_v2' | 'legacy_formula'>('itemized_v2');
  const [expenseRevision, setExpenseRevision] = useState(0);
  const [creatingExpense, setCreatingExpense] = useState(false);
  const [expenseType, setExpenseType] = useState<Entity>();
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState<unknown>();
  const [mfa, setMfa] = useState<{ secret: string; uri: string }>();
  const [otp, setOtp] = useState('');
  const command = useCommand();
  const tabs = [
    ...(auth.can('admin.settings') ? [['organization', 'Организация']] : []),
    ...(auth.can('admin.users')
      ? [
          ['users', 'Сотрудники'],
          ['roles', 'Роли и права'],
        ]
      : []),
    ...(auth.can('finance.calculations.read') ? [['profiles', 'Финансовые профили']] : []),
    ...(auth.can('admin.settings') ? [['settings', 'Правила работы']] : []),
    ...(auth.can('catalog.read') ? [['tariffs', 'Доставка СДЭК']] : []),
    ...(auth.can('audit.read') ? [['audit', 'Аудит']] : []),
    ...(auth.can('admin.settings') ? [['jobs', 'Фоновые операции']] : []),
    ['account', 'Мой аккаунт'],
  ];
  const currentTab = tabs.some(([key]) => key === tab) ? tab : tabs[0][0];
  return (
    <>
      <PageHeading
        title="Настройки"
        description="Организация, сотрудники и правила, по которым работает ваша CRM."
      />
      <nav className="tabs" aria-label="Настройки">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            className={currentTab === key ? 'active' : ''}
            onClick={() => {
              setTab(key);
              setSelected(undefined);
              setExpenseType(undefined);
              setEditing(false);
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <ErrorBox error={error || command.error} />
      {currentTab === 'organization' && (
        <Collection
          title="Организации продавца"
          endpoint="/sellers"
          fields={sellerFields}
          transform={sellerBody}
          createLabel="Добавить организацию"
          canCreate={auth.can('admin.settings')}
          columns={[
            { key: 'name', label: 'Название' },
            { key: 'currency', label: 'Валюта' },
            { key: 'details', label: 'Реквизиты', render: sellerDetails },
          ]}
        />
      )}
      {currentTab === 'users' && (
        <Collection
          title="Приглашённые сотрудники"
          endpoint="/admin/users"
          fields={userFields}
          createLabel="Создать доступ"
          refreshKey={revision}
          onSelect={(r) => {
            setSelected(r);
            setEditing(true);
          }}
          columns={[
            { key: 'name', label: 'Сотрудник' },
            { key: 'email', label: 'Почта' },
            {
              key: 'active',
              label: 'Доступ',
              render: (r) => <Badge value={r.active ? 'Активен' : 'Заблокирован'} />,
            },
            { key: 'roles', label: 'Назначенные роли' },
          ]}
        />
      )}
      {currentTab === 'roles' && <RolePermissionsEditor />}
      {currentTab === 'profiles' && (
        <>
          <Collection
            title="Виды расходов"
            description="Создавайте статьи для профилей и расчётов. Суммы и способы распределения можно изменить для конкретной сделки."
            endpoint="/expense-types"
            action={
              auth.can('profiles.write') ? (
                <Button onClick={() => setCreatingExpense(true)}>
                  <Plus size={16} /> Добавить вид расхода
                </Button>
              ) : undefined
            }
            refreshKey={expenseRevision}
            onSelect={setExpenseType}
            columns={[
              { key: 'name', label: 'Название' },
              {
                key: 'calculation_type',
                label: 'Тип',
                render: (row) =>
                  calculationLabels[String(row.calculation_type)] || String(row.calculation_type),
              },
              { key: 'default_value', label: 'По умолчанию' },
              { key: 'currency_code', label: 'Валюта' },
              {
                key: 'distribution_method',
                label: 'Распределение',
                render: (row) =>
                  distributionLabels[String(row.distribution_method)] ||
                  String(row.distribution_method),
              },
            ]}
          />
          <Collection
            title="Отключённые виды расходов"
            endpoint="/expense-types?active=false"
            canCreate={false}
            refreshKey={expenseRevision}
            onSelect={setExpenseType}
            columns={[
              { key: 'name', label: 'Название' },
              {
                key: 'calculation_type',
                label: 'Тип',
                render: (row) =>
                  calculationLabels[String(row.calculation_type)] || String(row.calculation_type),
              },
            ]}
          />
          <Section
            title="Настраиваемая финансовая модель"
            description="Профиль задаёт ставки, валюты, порядок расчёта, условия оплаты и вид документов."
          >
            <div className="form-body">
              <p>
                Укажите ставки вашей компании и проверьте результат на контрольном примере.
                Сохранение создаст черновик; после проверки опубликуйте его.
              </p>
              <div className="inline-actions">
                <Button
                  onClick={() => {
                    setSelected(undefined);
                    setProfileMode('itemized_v2');
                    setEditing(true);
                  }}
                  disabled={!auth.can('profiles.write') || !auth.can('templates.write')}
                >
                  Создать профиль расчёта
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setSelected(undefined);
                    setProfileMode('legacy_formula');
                    setEditing(true);
                  }}
                  disabled={!auth.can('profiles.write') || !auth.can('templates.write')}
                >
                  Создать формульный профиль
                </Button>
              </div>
            </div>
          </Section>
          <Collection
            title="Версии профилей"
            endpoint="/profiles"
            refreshKey={revision}
            onSelect={
              auth.can('profiles.write') && auth.can('templates.write')
                ? (r) => {
                    setSelected(r);
                    setProfileMode(
                      (r.definition as Record<string, unknown> | undefined)?.methodology ===
                        'itemized_v2'
                        ? 'itemized_v2'
                        : 'legacy_formula',
                    );
                    setEditing(true);
                  }
                : undefined
            }
            columns={[
              { key: 'name', label: 'Профиль' },
              {
                key: 'effective_from',
                label: 'Действует с',
                render: (r) => (r.status === 'draft' ? 'После публикации' : date(r.effective_from)),
              },
              { key: 'reason', label: 'Основание' },
              {
                key: 'status',
                label: 'Статус',
                render: (r) =>
                  r.status === 'published' ? (
                    '✅ Действует'
                  ) : r.status === 'archived' ? (
                    'Архив'
                  ) : auth.can('profiles.write') && auth.can('templates.write') ? (
                    <Button
                      variant="secondary"
                      onClick={(e) => {
                        e.stopPropagation();
                        void command
                          .run(`/profiles/${r.id}/publish`, {}, 'POST', true)
                          .then(() => setRevision((v) => v + 1))
                          .catch(setError);
                      }}
                    >
                      Опубликовать
                    </Button>
                  ) : (
                    'Черновик'
                  ),
              },
            ]}
          />
        </>
      )}
      {currentTab === 'profiles' && expenseType && (
        <Section title="Вид расхода">
          <div className="form-body">
            <DetailPairs
              values={{
                Название: expenseType.name,
                Состояние: expenseType.active ? 'Действует' : 'Отключён',
                'Тип расчёта':
                  calculationLabels[String(expenseType.calculation_type)] ||
                  expenseType.calculation_type,
                'Значение по умолчанию': expenseType.default_value,
                Валюта: expenseType.currency_code,
                Распределение:
                  distributionLabels[String(expenseType.distribution_method)] ||
                  expenseType.distribution_method,
                Этап:
                  expenseType.stage === 'INTERNATIONAL_LOGISTICS'
                    ? 'Международная логистика'
                    : expenseType.stage === 'DOMESTIC_LOGISTICS'
                      ? 'Вывоз из аэропорта — вся волна'
                      : expenseType.stage === 'CLIENT_DELIVERY'
                        ? 'Доставка клиенту — этот расчёт'
                        : 'Общий расход',
                ...(expenseType.percent_base
                  ? {
                      'База начисления':
                        baseLabels[String(expenseType.percent_base)] || expenseType.percent_base,
                    }
                  : {}),
                'В себестоимости': expenseType.include_in_cost ? 'Да' : 'Нет',
                'В денежной потребности': expenseType.include_in_cash ? 'Да' : 'Нет',
              }}
            />
            {Array.isArray(expenseType.brackets) && expenseType.brackets.length > 0 && (
              <div className="expense-bracket-summary">
                <strong>Диапазоны расходов</strong>
                <ul>
                  {(expenseType.brackets as Record<string, unknown>[]).map((range, index) => (
                    <li key={index}>
                      От {String(range.from_amount)} до{' '}
                      {range.to_amount == null ? 'без верхней границы' : String(range.to_amount)} —{' '}
                      {String(range.fee)} {String(expenseType.currency_code || '')}
                      {range.valid_from ? ` · с ${String(range.valid_from)}` : null}
                      {range.valid_to ? ` по ${String(range.valid_to)}` : null}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="inline-actions">
              <Button variant="secondary" onClick={() => setExpenseType(undefined)}>
                Закрыть
              </Button>
              {auth.can('profiles.write') && (
                <Button
                  variant="secondary"
                  onClick={() => {
                    void command
                      .run(
                        `/expense-types/${expenseType.id}`,
                        { version: expenseType.version, active: !expenseType.active },
                        'PATCH',
                      )
                      .then(() => {
                        setExpenseType(undefined);
                        setExpenseRevision((value) => value + 1);
                      })
                      .catch(setError);
                  }}
                >
                  {expenseType.active ? 'Отключить' : 'Включить'}
                </Button>
              )}
            </div>
          </div>
        </Section>
      )}
      {currentTab === 'profiles' && creatingExpense && (
        <ExpenseTypeEditor
          onClose={() => setCreatingExpense(false)}
          onSuccess={() => {
            setCreatingExpense(false);
            setExpenseRevision((value) => value + 1);
          }}
        />
      )}
      {currentTab === 'settings' && <WorkingRulesEditor />}
      {currentTab === 'tariffs' && (
        <Collection
          title="Доставка из Москвы — ТМ-35, до 15 кг"
          endpoint="/delivery-tariffs"
          pageSize={100}
          columns={[
            { key: 'city', label: 'Город' },
            { key: 'amount', label: 'Стоимость без НДС, ₽' },
          ]}
        />
      )}
      {currentTab === 'audit' && (
        <Collection
          title="Журнал аудита"
          endpoint="/admin/audit"
          columns={[
            { key: 'created_at', label: 'Дата', render: (r) => date(r.created_at, true) },
            { key: 'entity_type', label: 'Объект' },
            { key: 'action', label: 'Действие' },
            { key: 'reason', label: 'Основание' },
            { key: 'request_id', label: 'Код операции' },
          ]}
        />
      )}
      {currentTab === 'jobs' && (
        <Collection
          title="Фоновые операции"
          endpoint="/admin/jobs"
          columns={[
            { key: 'kind', label: 'Операция' },
            { key: 'status', label: 'Состояние', render: (r) => <Badge value={r.status} /> },
            { key: 'progress', label: 'Прогресс' },
            { key: 'attempts', label: 'Попытки' },
            { key: 'error', label: 'Причина ошибки' },
          ]}
        />
      )}
      {currentTab === 'account' && (
        <Section title="Защита аккаунта">
          <div className="form-body">
            <DetailPairs
              values={{
                Имя: auth.session?.user.name,
                Почта: auth.session?.user.email,
                'Второй фактор': auth.session?.user.mfa_enabled ? 'Включён' : 'Не включён',
              }}
            />
            {!auth.session?.user.mfa_enabled && (
              <>
                <Button
                  variant="secondary"
                  onClick={() =>
                    void api<{ secret: string; uri: string }>('/auth/mfa/setup', { method: 'POST' })
                      .then(setMfa)
                      .catch(setError)
                  }
                >
                  <ShieldCheck size={17} />
                  Настроить второй фактор
                </Button>
                {mfa && (
                  <form
                    className="form-grid"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void command
                        .run('/auth/mfa/enable', { otp })
                        .then(() => window.location.reload())
                        .catch(setError);
                    }}
                  >
                    <label className="field wide">
                      Ключ для приложения аутентификации
                      <input readOnly value={mfa.secret} />
                    </label>
                    <label className="field">
                      Проверочный код
                      <input
                        value={otp}
                        onChange={(e) => setOtp(e.target.value)}
                        inputMode="numeric"
                        required
                        maxLength={6}
                      />
                    </label>
                    <Button type="submit" busy={command.busy}>
                      Подтвердить
                    </Button>
                  </form>
                )}
              </>
            )}
          </div>
        </Section>
      )}
      {editing && currentTab === 'users' && selected && (
        <UserAccessEditor
          user={selected}
          onClose={() => setEditing(false)}
          onSuccess={() => {
            setEditing(false);
            setRevision((v) => v + 1);
          }}
        />
      )}
      {editing &&
        currentTab === 'profiles' &&
        (profileMode === 'itemized_v2' ? (
          <ItemizedProfileEditor
            initial={selected}
            onClose={() => {
              setEditing(false);
              setSelected(undefined);
            }}
            onSuccess={() => {
              setEditing(false);
              setSelected(undefined);
              setRevision((v) => v + 1);
            }}
          />
        ) : (
          <FinancialProfileEditor
            initial={selected}
            onClose={() => {
              setEditing(false);
              setSelected(undefined);
            }}
            onSuccess={() => {
              setEditing(false);
              setSelected(undefined);
              setRevision((v) => v + 1);
            }}
          />
        ))}
    </>
  );
}
export function Guide() {
  return (
    <>
      <PageHeading title="Начало работы" description="Последовательность действий в CRM." />
      <Section title="От заявки до поставки">
        <ol className="guide-list">
          <li>
            <Link to="/settings">Заполните организацию</Link>, назначьте роли и создайте финансовый
            профиль.
          </li>
          <li>
            <Link to="/clients">Добавьте клиента</Link> вручную или через предварительный просмотр
            XLSX.
          </li>
          <li>
            <Link to="/requests">Создайте заявку</Link> и внесите исходную потребность клиента.
          </li>
          <li>
            Сформируйте запрос поставщику, зарегистрируйте квоты и проверьте товарные варианты.
          </li>
          <li>Выберите квоты, рассчитайте и сохраните версию расчёта. Выпустите КП.</li>
          <li>
            Зафиксируйте принятый клиентом состав, выставьте счёт, подтвердите и распределите
            оплату.
          </li>
          <li>
            Передайте состав руководителю, распределите согласованное количество по волнам и
            зафиксируйте исполнение.
          </li>
        </ol>
      </Section>
      <div className="info-note">
        Доступ к операциям определяется серверными правами. Отправка документов отмечается отдельно
        от скачивания. Заявление об оплате учитывается только после подтверждения.
      </div>
    </>
  );
}
