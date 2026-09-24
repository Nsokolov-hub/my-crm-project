import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import { useAuth } from '../app/Auth';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import { Badge, Button, DetailPairs, ErrorBox, PageHeading, Section } from '../components/ui';
import { api } from '../lib/api';
import { useCommand } from '../lib/hooks';
import { date, today } from '../lib/format';
import type { Entity, Field } from '../lib/types';

const sellerFields: Field[] = [
  { name: 'name', label: 'Название организации', required: true },
  { name: 'currency', label: 'Управленческая валюта (ISO)', required: true },
  {
    name: 'details',
    label: 'Юридические и банковские реквизиты',
    type: 'json',
    value: {},
    help: 'Реквизиты копируются в документ при его выпуске.',
  },
];
const userFields: Field[] = [
  { name: 'name', label: 'Имя сотрудника', required: true },
  { name: 'email', label: 'Рабочая почта', type: 'email', required: true },
  { name: 'password', label: 'Начальный пароль', type: 'password', minLength: 12, required: true },
  { name: 'role_ids', label: 'Роли', type: 'multiselect', source: '/admin/roles' },
];
export function Settings() {
  const auth = useAuth();
  const [tab, setTab] = useState('organization');
  const [selected, setSelected] = useState<Entity>();
  const [editing, setEditing] = useState(false);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState<unknown>();
  const [profile, setProfile] = useState<Record<string, unknown>>();
  const [mfa, setMfa] = useState<{ secret: string; uri: string }>();
  const [otp, setOtp] = useState('');
  const command = useCommand();
  const tabs = [
    ['organization', 'Организация'],
    ['users', 'Сотрудники'],
    ['roles', 'Роли и права'],
    ['profiles', 'Финансовые профили'],
    ['settings', 'Правила работы'],
    ['audit', 'Аудит'],
    ['jobs', 'Фоновые операции'],
    ['account', 'Мой аккаунт'],
  ];
  async function example() {
    try {
      const result = await api<{ definition: Record<string, unknown> }>('/profiles/example');
      setProfile(result.definition);
      setEditing(true);
    } catch (e) {
      setError(e);
    }
  }
  const profileFields: Field[] = [
    { name: 'name', label: 'Название профиля', required: true },
    { name: 'effective_from', label: 'Действует с', required: true, type: 'date', value: today() },
    { name: 'effective_until', label: 'Действует по', type: 'date' },
    {
      name: 'definition',
      label: 'Формулы, ставки, округление и шаблон',
      required: true,
      type: 'json',
      value: profile || {},
      wide: true,
      help: 'Именованные формулы вычисляются по порядку. Допускается только арифметика; налоги и ставки задаёте вы. Изменения создают отдельную версию.',
    },
    { name: 'reason', label: 'Основание утверждения', required: true, type: 'textarea' },
  ];
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
            className={tab === key ? 'active' : ''}
            onClick={() => {
              setTab(key);
              setSelected(undefined);
              setEditing(false);
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <ErrorBox error={error || command.error} />
      {tab === 'organization' && (
        <Collection
          title="Организации продавца"
          endpoint="/sellers"
          fields={sellerFields}
          createLabel="Добавить организацию"
          canCreate={auth.can('admin.settings')}
          columns={[
            { key: 'name', label: 'Название' },
            { key: 'currency', label: 'Валюта' },
            { key: 'details', label: 'Реквизиты' },
          ]}
        />
      )}
      {tab === 'users' && (
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
      {tab === 'roles' && (
        <>
          <div className="info-note">
            Итоговые права складываются из ролей. Персональный запрет сотрудника имеет приоритет.
            Область действия: свои, совместные или все записи.
          </div>
          <Collection
            title="Роли"
            endpoint="/admin/roles"
            fields={[
              { name: 'name', label: 'Название роли', required: true },
              {
                name: 'grants',
                label: 'Разрешения',
                type: 'json',
                value: [],
                required: true,
                help: 'Массив {code,scope,allow}; коды доступны в справочнике ниже.',
              },
            ]}
            refreshKey={revision}
            onSelect={(r) => {
              setSelected(r);
              setEditing(true);
            }}
            columns={[
              { key: 'name', label: 'Роль' },
              { key: 'grants', label: 'Разрешения' },
            ]}
          />
          <Collection
            title="Справочник разрешений"
            endpoint="/admin/permissions"
            columns={[
              { key: 'code', label: 'Код' },
              { key: 'name', label: 'Действие' },
            ]}
          />
        </>
      )}
      {tab === 'profiles' && (
        <>
          <Section
            title="Настраиваемая финансовая модель"
            description="Профиль фиксирует формулы, точность, расходы, финансирование и шаблон документов."
          >
            <div className="form-body">
              <p>
                Реальных налоговых ставок по умолчанию нет. Сначала заполните собственные правила и
                проверьте итог на контрольных примерах. Утверждённый профиль сохраняется отдельной
                версией.
              </p>
              <div className="inline-actions">
                <Button
                  onClick={() => {
                    setProfile({});
                    setEditing(true);
                  }}
                >
                  Создать профиль
                </Button>
                <Button variant="secondary" onClick={() => void example()}>
                  Открыть условный пример ТЗ
                </Button>
              </div>
            </div>
          </Section>
          <Collection
            title="Версии профилей"
            endpoint="/profiles"
            refreshKey={revision}
            onSelect={(r) => {
              setSelected(r);
              setProfile(r.definition as Record<string, unknown>);
              setEditing(true);
            }}
            columns={[
              { key: 'name', label: 'Профиль' },
              {
                key: 'effective_from',
                label: 'Действует с',
                render: (r) => date(r.effective_from),
              },
              { key: 'reason', label: 'Основание' },
              { key: 'status', label: 'Статус', render: (r) => r.status === 'published' ? '✅ Действует' : (
                  <Button variant="secondary" onClick={(e) => { e.stopPropagation(); void command.run(`/profiles/${r.id}/publish`, {}, 'POST', true).then(() => setRevision(v => v + 1)); }}>
                    Опубликовать
                  </Button>
                ) },
            ]}
          />
        </>
      )}
      {tab === 'settings' && (
        <Collection
          title="Правила работы"
          endpoint="/settings"
          fields={[
            { name: 'key', label: 'Ключ настройки', required: true },
            { name: 'value', label: 'Значения', type: 'json', required: true, value: {} },
          ]}
          refreshKey={revision}
          onSelect={(r) => {
            setSelected(r);
            setEditing(true);
          }}
          columns={[
            { key: 'key', label: 'Настройка' },
            { key: 'value', label: 'Значения' },
            { key: 'version', label: 'Версия' },
          ]}
        />
      )}
      {tab === 'audit' && (
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
      {tab === 'jobs' && (
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
      {tab === 'account' && (
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
      {editing && tab === 'users' && selected && (
        <RecordForm
          title="Доступ сотрудника"
          endpoint={`/admin/users/${selected.id}`}
          method="PATCH"
          initial={{ ...selected, role_ids: ((selected.roles || []) as Entity[]).map((r) => r.id) }}
          extra={{ version: selected.version }}
          fields={[
            { name: 'name', label: 'Имя', required: true },
            { name: 'active', label: 'Доступ активен', type: 'checkbox' },
            { name: 'role_ids', label: 'Роли', type: 'multiselect', source: '/admin/roles' },
            { name: 'grants', label: 'Персональные разрешения и запреты', type: 'json', value: [] },
            {
              name: 'reassign_to',
              label: 'Передать работу сотруднику',
              type: 'select',
              source: '/users',
            },
            { name: 'reason', label: 'Причина изменения', required: true, type: 'textarea' },
          ]}
          onClose={() => setEditing(false)}
          onSuccess={() => {
            setEditing(false);
            setRevision((v) => v + 1);
          }}
        />
      )}
      {editing && tab === 'roles' && selected && (
        <RecordForm
          title="Изменить роль"
          endpoint={`/admin/roles/${selected.id}`}
          method="PUT"
          initial={selected}
          extra={{ version: selected.version }}
          fields={[
            { name: 'name', label: 'Название', required: true },
            { name: 'grants', label: 'Разрешения', type: 'json', required: true },
          ]}
          onClose={() => setEditing(false)}
          onSuccess={() => {
            setEditing(false);
            setRevision((v) => v + 1);
          }}
        />
      )}
      {editing && tab === 'settings' && selected && (
        <RecordForm
          title="Изменить настройку"
          endpoint="/settings"
          initial={selected}
          extra={{ version: selected.version }}
          fields={[
            { name: 'key', label: 'Ключ', required: true },
            { name: 'value', label: 'Значения', type: 'json', required: true },
          ]}
          onClose={() => setEditing(false)}
          onSuccess={() => {
            setEditing(false);
            setRevision((v) => v + 1);
          }}
        />
      )}
      {editing && tab === 'profiles' && (
        <RecordForm
          title={selected ? 'Новая версия профиля' : 'Утвердить профиль расчёта'}
          endpoint="/profiles"
          command
          initial={selected ? { name: selected.name, definition: profile } : undefined}
          extra={selected ? { previous_id: selected.id } : undefined}
          fields={profileFields}
          note="Сохранение создаёт новую версию (черновик). Для применения правил к новым заявкам необходимо опубликовать её."
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
      )}
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
