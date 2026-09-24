import { useState } from 'react';
import { Plus } from 'lucide-react';
import { useApi, useCommand, useDirtyProtection } from '../lib/hooks';
import type { Entity, Grant, Page } from '../lib/types';
import { Button, Empty, ErrorBox, Loading, Modal, Section } from './ui';
import '../styles/permissions.scss';

type Permission = { code: string; name: string };
type Role = Entity & { name: string; grants: Grant[] };
type Employee = Entity & {
  name: string;
  email: string;
  active: boolean;
  roles?: { id: string; name: string }[];
  grants?: Grant[];
};
type Scope = 'own' | 'shared' | 'all';

const groups: { name: string; codes: string[] }[] = [
  {
    name: 'Клиенты и продажи',
    codes: [
      'clients.read',
      'clients.write',
      'calls.write',
      'requests.read',
      'requests.write',
      'requests.assign',
      'tasks.read',
      'tasks.write',
      'approvals.submit',
      'approvals.decide',
    ],
  },
  {
    name: 'Каталог, закупка и поставка',
    codes: ['catalog.read', 'catalog.write', 'quotes.write', 'waves.write'],
  },
  {
    name: 'Расчёты и оплата',
    codes: [
      'finance.purchase.read',
      'finance.calculations.read',
      'finance.reward.read',
      'finance.profit.read',
      'calculations.write',
      'profiles.write',
      'payments.write',
      'payments.confirm',
    ],
  },
  {
    name: 'Документы и обмен данными',
    codes: [
      'documents.write',
      'templates.write',
      'exports.download',
      'imports.write',
      'files.upload',
      'chats.use',
    ],
  },
  {
    name: 'Аналитика и управление системой',
    codes: [
      'analytics.read',
      'admin.users',
      'admin.settings',
      'audit.read',
      'settings.dictionaries.write',
      'settings.system.write',
      'settings.commerce.write',
    ],
  },
];

const descriptions: Record<string, string> = {
  'requests.read': 'Видеть заявки и связанные с ними данные.',
  'requests.write': 'Создавать и изменять заявки.',
  'requests.assign': 'Назначать ответственного за заявку.',
  'clients.read': 'Видеть карточки компаний и контактов.',
  'clients.write': 'Добавлять и редактировать компании и контакты.',
  'calls.write': 'Фиксировать звонки клиентам.',
  'tasks.read': 'Видеть задачи.',
  'tasks.write': 'Создавать и выполнять задачи.',
  'quotes.write': 'Запрашивать и сохранять предложения поставщиков.',
  'finance.purchase.read': 'Видеть закупочные цены поставщиков.',
  'finance.calculations.read': 'Видеть подробности финансового расчёта.',
  'finance.reward.read': 'Видеть вознаграждение сотрудников.',
  'finance.profit.read': 'Видеть плановую доходность сделки.',
  'calculations.write': 'Создавать и изменять расчёты по заявкам.',
  'profiles.write': 'Создавать и публиковать правила финансового расчёта.',
  'payments.write': 'Регистрировать заявленные оплаты.',
  'payments.confirm': 'Подтверждать и распределять поступившие оплаты.',
  'approvals.submit': 'Отправлять сделки на согласование.',
  'approvals.decide': 'Принимать решения по согласованиям.',
  'waves.write': 'Планировать партии и отгрузки.',
  'documents.write': 'Создавать коммерческие и отгрузочные документы.',
  'templates.write': 'Менять шаблоны документов.',
  'exports.download': 'Скачивать документы и выгружать данные.',
  'imports.write': 'Загружать клиентскую базу.',
  'admin.users': 'Создавать сотрудников, назначать роли и менять права.',
  'admin.settings': 'Менять настройки организации.',
  'settings.dictionaries.write': 'Менять рабочие справочники.',
  'settings.system.write': 'Менять системные правила.',
  'settings.commerce.write': 'Менять коммерческие правила.',
};

const titles: Record<string, string> = {
  'requests.read': 'Просматривать заявки',
  'requests.write': 'Создавать и изменять заявки',
  'requests.assign': 'Назначать ответственных',
  'clients.read': 'Просматривать клиентов',
  'clients.write': 'Создавать и изменять клиентов',
  'calls.write': 'Вести историю звонков',
  'tasks.read': 'Просматривать задачи',
  'tasks.write': 'Создавать и выполнять задачи',
  'catalog.read': 'Просматривать каталог товаров',
  'catalog.write': 'Редактировать каталог товаров',
  'quotes.write': 'Работать с предложениями поставщиков',
  'finance.purchase.read': 'Смотреть закупочные цены',
  'finance.calculations.read': 'Смотреть подробные расчёты',
  'finance.reward.read': 'Смотреть вознаграждения',
  'finance.profit.read': 'Смотреть плановую доходность',
  'calculations.write': 'Создавать и изменять расчёты',
  'profiles.write': 'Настраивать финансовые профили',
  'documents.write': 'Выпускать документы',
  'templates.write': 'Настраивать шаблоны документов',
  'payments.write': 'Регистрировать оплаты',
  'payments.confirm': 'Подтверждать оплаты',
  'approvals.submit': 'Отправлять на согласование',
  'approvals.decide': 'Принимать решения по согласованиям',
  'waves.write': 'Планировать партии и отгрузки',
  'exports.download': 'Скачивать документы и данные',
  'imports.write': 'Загружать клиентов из файла',
  'analytics.read': 'Просматривать отчёты',
  'chats.use': 'Общаться внутри CRM',
  'files.upload': 'Прикладывать файлы',
  'admin.users': 'Управлять сотрудниками и ролями',
  'admin.settings': 'Настраивать CRM',
  'audit.read': 'Просматривать историю изменений',
  'settings.dictionaries.write': 'Менять рабочие справочники',
  'settings.system.write': 'Менять системные правила',
  'settings.commerce.write': 'Менять коммерческие правила',
};

function friendlyName(permission: Permission) {
  return titles[permission.code] || permission.name;
}

// Only these actions use the grant's scope in server-side record access checks.
const scopedPermissions = new Set([
  'requests.read',
  'requests.write',
  'requests.assign',
  'clients.read',
  'clients.write',
  'tasks.read',
  'tasks.write',
  'quotes.write',
  'finance.purchase.read',
  'finance.calculations.read',
  'finance.reward.read',
  'finance.profit.read',
  'calculations.write',
  'documents.write',
  'payments.write',
  'payments.confirm',
  'approvals.submit',
  'approvals.decide',
  'waves.write',
  'exports.download',
]);

function arrangedPermissions(permissions: Permission[]) {
  const known = new Set(groups.flatMap((group) => group.codes));
  const lookup = new Map(permissions.map((permission) => [permission.code, permission]));
  return [
    ...groups.map((group) => ({
      name: group.name,
      items: group.codes.flatMap((code) => lookup.get(code) || []),
    })),
    {
      name: 'Другие действия',
      items: permissions.filter((permission) => !known.has(permission.code)),
    },
  ].filter((group) => group.items.length > 0);
}

function grantState(grants: Grant[], code: string, personal: boolean) {
  const grant = grants.find((item) => item.code === code);
  return grant ? (grant.allow ? 'allow' : personal ? 'deny' : 'none') : 'none';
}

function changeGrant(grants: Grant[], code: string, state: string, scoped: boolean): Grant[] {
  const prior = grants.find((item) => item.code === code);
  const other = grants.filter((item) => item.code !== code);
  if (state === 'none') return other;
  return [
    ...other,
    {
      code,
      allow: state === 'allow',
      scope: state === 'allow' && scoped ? prior?.scope || 'own' : 'all',
    },
  ];
}

function changeScope(grants: Grant[], code: string, scope: Scope): Grant[] {
  return grants.map((grant) => (grant.code === code ? { ...grant, scope } : grant));
}

export function PermissionGrantsEditor({
  permissions,
  grants,
  onChange,
  personal = false,
}: {
  permissions: Permission[];
  grants: Grant[];
  onChange: (grants: Grant[]) => void;
  personal?: boolean;
}) {
  const arranged = arrangedPermissions(permissions);
  return (
    <div className="permission-groups">
      {arranged.map((group) => (
        <section className="permission-group" key={group.name}>
          <h3>{group.name}</h3>
          {group.items.map((permission) => {
            const grant = grants.find((item) => item.code === permission.code);
            const state = grantState(grants, permission.code, personal);
            const scoped = scopedPermissions.has(permission.code);
            const name = friendlyName(permission);
            return (
              <div className="permission-row" key={permission.code}>
                <div className="permission-explanation">
                  <strong>{name}</strong>
                  {descriptions[permission.code] && <small>{descriptions[permission.code]}</small>}
                </div>
                <div className="permission-controls">
                  <select
                    aria-label={`${name}: доступ`}
                    value={state}
                    onChange={(event) =>
                      onChange(changeGrant(grants, permission.code, event.target.value, scoped))
                    }
                  >
                    <option value="none">
                      {personal ? 'По назначенным ролям' : 'Нет доступа'}
                    </option>
                    <option value="allow">Разрешено</option>
                    {personal && <option value="deny">Запрещено лично</option>}
                  </select>
                  {state === 'allow' && scoped && (
                    <select
                      aria-label={`${name}: область доступа`}
                      value={grant?.scope === 'all' ? 'all' : 'own'}
                      onChange={(event) =>
                        onChange(changeScope(grants, permission.code, event.target.value as Scope))
                      }
                    >
                      <option value="own">Свои и совместные записи</option>
                      <option value="all">Все записи</option>
                    </select>
                  )}
                </div>
              </div>
            );
          })}
        </section>
      ))}
    </div>
  );
}

function RoleForm({
  role,
  permissions,
  onClose,
  onSuccess,
}: {
  role?: Role;
  permissions: Permission[];
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [name, setName] = useState(role?.name || '');
  const [grants, setGrants] = useState<Grant[]>(role?.grants || []);
  const [dirty, setDirty] = useState(false);
  const command = useCommand();
  useDirtyProtection(dirty);
  function close() {
    if (command.busy) return;
    if (!dirty || window.confirm('Есть несохранённые изменения. Закрыть форму без сохранения?'))
      onClose();
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    try {
      await command.run(
        role ? `/admin/roles/${role.id}` : '/admin/roles',
        { name: name.trim(), grants, ...(role ? { version: role.version } : {}) },
        role ? 'PUT' : 'POST',
      );
      onSuccess();
    } catch {
      // useCommand exposes the server error under the form.
    }
  }
  return (
    <Modal title={role ? `Права роли «${role.name}»` : 'Новая роль'} onClose={close} wide>
      <form onSubmit={(event) => void submit(event)}>
        <div className="form-body">
          <p>
            Выберите, какие действия разрешены сотрудникам с этой ролью. При нескольких ролях
            разрешения складываются. Для действий с заявками можно ограничить доступ своими и
            совместными записями.
          </p>
          <label className="field wide">
            <span>Название роли *</span>
            <input
              aria-label="Название роли"
              value={name}
              onChange={(event) => {
                setName(event.target.value);
                setDirty(true);
              }}
              maxLength={200}
              required
            />
          </label>
          <PermissionGrantsEditor
            permissions={permissions}
            grants={grants}
            onChange={(next) => {
              setGrants(next);
              setDirty(true);
            }}
          />
          <ErrorBox error={command.error} />
        </div>
        <div className="modal-footer">
          <Button type="button" variant="secondary" onClick={close}>
            Отмена
          </Button>
          <Button type="submit" busy={command.busy}>
            Сохранить роль
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export function RolePermissionsEditor() {
  const [revision, setRevision] = useState(0);
  const [editing, setEditing] = useState<Role | null>();
  const roles = useApi<Page<Role>>(`/admin/roles?ui_revision=${revision}`);
  const permissions = useApi<{ items: Permission[] }>('/admin/permissions');
  const permissionNames = new Map(
    permissions.data?.items.map((item) => [item.code, friendlyName(item)]),
  );
  return (
    <Section
      title="Роли и права"
      description="Настройте доступ по рабочим действиям. Нажмите на роль, чтобы изменить её."
      action={
        <Button onClick={() => setEditing(null)} disabled={!permissions.data}>
          <Plus size={16} />
          Добавить роль
        </Button>
      }
    >
      <div className="info-note">
        Права сотрудника складываются из назначенных ролей. Личный запрет сотрудника имеет
        приоритет. Ограничение «Свои и совместные записи» действует для заявок, клиентов, задач и
        связанных с ними действий.
      </div>
      <ErrorBox
        error={roles.error || permissions.error}
        retry={() => {
          roles.refresh();
          permissions.refresh();
        }}
      />
      {roles.loading || permissions.loading ? (
        <Loading />
      ) : roles.data?.items.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Роль</th>
                <th>Разрешено</th>
                <th>Нет доступа</th>
              </tr>
            </thead>
            <tbody>
              {roles.data.items.map((role) => {
                const allowed = (role.grants || []).filter((grant) => grant.allow);
                const denied = (permissions.data?.items || []).length - allowed.length;
                return (
                  <tr key={role.id}>
                    <td>
                      <button className="row-link" onClick={() => setEditing(role)}>
                        {role.name}
                      </button>
                    </td>
                    <td>
                      <strong>{allowed.length} действий</strong>
                      <small className="permission-summary">
                        {allowed
                          .slice(0, 3)
                          .map((grant) => permissionNames.get(grant.code))
                          .filter(Boolean)
                          .join(', ')}
                        {allowed.length > 3 ? ' и другие' : ''}
                      </small>
                    </td>
                    <td>{denied > 0 ? `${denied} действий` : 'Все действия разрешены'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          title="Ролей пока нет"
          description="Добавьте роль и выберите разрешённые действия."
        />
      )}
      {editing !== undefined && permissions.data && (
        <RoleForm
          role={editing || undefined}
          permissions={permissions.data.items}
          onClose={() => setEditing(undefined)}
          onSuccess={() => {
            setEditing(undefined);
            setRevision((value) => value + 1);
          }}
        />
      )}
    </Section>
  );
}

export function UserAccessEditor({
  user,
  onClose,
  onSuccess,
}: {
  user: Entity;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const employee = user as Employee;
  const [name, setName] = useState(employee.name);
  const [active, setActive] = useState(employee.active);
  const [roleIds, setRoleIds] = useState<string[]>((employee.roles || []).map((role) => role.id));
  const [grants, setGrants] = useState<Grant[]>(employee.grants || []);
  const [reassignTo, setReassignTo] = useState('');
  const [reason, setReason] = useState('');
  const [dirty, setDirty] = useState(false);
  const command = useCommand();
  const roles = useApi<Page<Role>>('/admin/roles');
  const permissions = useApi<{ items: Permission[] }>('/admin/permissions');
  const employees = useApi<Page<{ id: string; name: string }>>('/users');
  useDirtyProtection(dirty);
  function close() {
    if (command.busy) return;
    if (!dirty || window.confirm('Есть несохранённые изменения. Закрыть форму без сохранения?'))
      onClose();
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!roles.data || !permissions.data) return;
    try {
      await command.run(
        `/admin/users/${user.id}`,
        {
          version: user.version,
          name: name.trim(),
          active,
          role_ids: roleIds,
          grants,
          reason: reason.trim(),
          ...(reassignTo ? { reassign_to: reassignTo } : {}),
        },
        'PATCH',
      );
      onSuccess();
    } catch {
      // useCommand exposes the server error under the form.
    }
  }
  return (
    <Modal title={`Доступ сотрудника: ${employee.name}`} onClose={close} wide>
      <form onSubmit={(event) => void submit(event)}>
        <div className="form-body">
          <p>
            Назначьте рабочие роли. Персональные разрешения дополняют роли, а персональный запрет
            отменяет доступ по всем ролям. Если специального правила нет, действует доступ по ролям.
          </p>
          <div className="form-grid">
            <label className="field">
              <span>Имя сотрудника *</span>
              <input
                aria-label="Имя сотрудника"
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                  setDirty(true);
                }}
                maxLength={200}
                required
              />
            </label>
            <label className="field checkbox-field">
              <input
                type="checkbox"
                checked={active}
                onChange={(event) => {
                  setActive(event.target.checked);
                  setDirty(true);
                }}
              />
              Доступ активен
            </label>
            <div className="field wide">
              <strong>Назначенные роли</strong>
              {roles.loading ? (
                <Loading />
              ) : roles.error ? (
                <ErrorBox error={roles.error} />
              ) : (
                <div className="role-checklist">
                  {roles.data?.items.map((role) => (
                    <label key={role.id}>
                      <input
                        type="checkbox"
                        checked={roleIds.includes(role.id)}
                        onChange={(event) => {
                          setRoleIds(
                            event.target.checked
                              ? [...roleIds, role.id]
                              : roleIds.filter((id) => id !== role.id),
                          );
                          setDirty(true);
                        }}
                      />
                      {role.name}
                    </label>
                  ))}
                </div>
              )}
            </div>
          </div>
          <h3 className="permission-subheading">Персональные исключения</h3>
          <p>
            Оставьте «По назначенным ролям» для обычного доступа. Используйте личный запрет для
            исключений.
          </p>
          {permissions.loading ? (
            <Loading />
          ) : permissions.error ? (
            <ErrorBox error={permissions.error} />
          ) : (
            <PermissionGrantsEditor
              personal
              permissions={permissions.data?.items || []}
              grants={grants}
              onChange={(next) => {
                setGrants(next);
                setDirty(true);
              }}
            />
          )}
          {!active && (
            <label className="field wide permission-reassign">
              <span>Передать открытые задачи и записи сотруднику</span>
              <select
                value={reassignTo}
                onChange={(event) => {
                  setReassignTo(event.target.value);
                  setDirty(true);
                }}
              >
                <option value="">Выберите, если есть незавершённая работа</option>
                {employees.data?.items
                  .filter((employee) => employee.id !== user.id)
                  .map((employee) => (
                    <option key={employee.id} value={employee.id}>
                      {employee.name}
                    </option>
                  ))}
              </select>
            </label>
          )}
          <label className="field wide permission-reason">
            <span>Причина изменения *</span>
            <textarea
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
                setDirty(true);
              }}
              maxLength={2000}
              required
              rows={2}
            />
          </label>
          <ErrorBox error={command.error} />
        </div>
        <div className="modal-footer">
          <Button type="button" variant="secondary" onClick={close}>
            Отмена
          </Button>
          <Button type="submit" busy={command.busy} disabled={!roles.data || !permissions.data}>
            Сохранить доступ
          </Button>
        </div>
      </form>
    </Modal>
  );
}
