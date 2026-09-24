import {
  Bell,
  BookOpen,
  Building2,
  ChartNoAxesCombined,
  ChevronDown,
  CircleHelp,
  ClipboardCheck,
  FileText,
  FlaskConical,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquare,
  Phone,
  Search,
  Settings2,
  Truck,
  Wallet,
  X,
  ClipboardList,
  CheckCheck,
} from 'lucide-react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useState } from 'react';
import { useAuth } from './Auth';
import { useApi } from '../lib/hooks';
import type { Page } from '../lib/types';
const links = [
  { label: 'Рабочий стол', path: '/', icon: LayoutDashboard, permission: undefined },
  { label: 'Заявки', path: '/requests', icon: ClipboardList, permission: 'requests.read' },
  { label: 'Клиенты', path: '/clients', icon: Building2, permission: 'clients.read' },
  { label: 'База обзвона', path: '/calls', icon: Phone, permission: 'clients.read' },
  { label: 'Задачи', path: '/tasks', icon: CheckCheck, permission: 'tasks.read' },
];
const operations = [
  { label: 'Номенклатура', path: '/catalog', icon: FlaskConical, permission: 'catalog.read' },
  { label: 'Документы', path: '/documents', icon: FileText, permission: 'requests.read' },
  { label: 'Оплаты', path: '/payments', icon: Wallet, permission: 'requests.read' },
  { label: 'Согласования', path: '/approvals', icon: ClipboardCheck, permission: 'requests.read' },
  { label: 'Волны поставок', path: '/waves', icon: Truck, permission: 'requests.read' },
];
export function Layout() {
  const auth = useAuth();
  const navigate = useNavigate();
  const [mobile, setMobile] = useState(false);
  const [search, setSearch] = useState('');
  const notifications = useApi<Page>('/notifications?read=false&page_size=1');
  const name = auth.session?.user.name || 'Сотрудник';
  const availableLinks = links.filter((link) => !link.permission || auth.can(link.permission));
  const availableOperations = operations.filter((link) => auth.can(link.permission));
  const canSeeAnalytics = auth.can('analytics.read');
  const canUseChats = auth.can('chats.use');
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Перейти к содержимому
      </a>
      {mobile && (
        <button
          className="sidebar-scrim"
          aria-label="Закрыть меню"
          onClick={() => setMobile(false)}
        />
      )}
      <aside className={`sidebar ${mobile ? 'open' : ''}`}>
        <NavLink to="/" className="brand" onClick={() => setMobile(false)}>
          <span className="brand-symbol">
            <FlaskConical size={23} />
          </span>
          <span>
            реактив<span className="brand-crm"> CRM</span>
          </span>
        </NavLink>
        <button className="workspace-switch" onClick={() => navigate('/settings')}>
          <span className="workspace-avatar">Р</span>
          <span>
            Рабочее пространство<small>Продажи и закупки</small>
          </span>
          <ChevronDown size={16} />
        </button>
        <nav aria-label="Основная навигация">
          <span className="nav-label">Работа</span>
          {availableLinks.map(({ label, path, icon: Icon }) => (
            <NavLink key={path} to={path} end={path === '/'} onClick={() => setMobile(false)}>
              <Icon size={19} />
              {label}
            </NavLink>
          ))}
          {availableOperations.length > 0 && (
            <>
              <span className="nav-label">Операции</span>
              {availableOperations.map(({ label, path, icon: Icon }) => (
                <NavLink key={path} to={path} onClick={() => setMobile(false)}>
                  <Icon size={19} />
                  {label}
                </NavLink>
              ))}
            </>
          )}
          {(canSeeAnalytics || canUseChats) && <span className="nav-label">Команда</span>}
          {canSeeAnalytics && (
            <NavLink to="/analytics" onClick={() => setMobile(false)}>
              <ChartNoAxesCombined size={19} />
              Аналитика
            </NavLink>
          )}
          {canUseChats && (
            <NavLink to="/chats" onClick={() => setMobile(false)}>
              <MessageSquare size={19} />
              Обсуждения
            </NavLink>
          )}
        </nav>
        <div className="sidebar-bottom">
          <NavLink to="/settings" onClick={() => setMobile(false)}>
            <Settings2 size={19} />
            Настройки
          </NavLink>
          <NavLink to="/guide" onClick={() => setMobile(false)}>
            <CircleHelp size={19} />
            Помощь и процессы
          </NavLink>
          <div className="sidebar-note">
            <span className="online-dot" /> Единое пространство команды
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            aria-label={mobile ? 'Закрыть меню' : 'Открыть меню'}
            onClick={() => setMobile(!mobile)}
          >
            {mobile ? <X size={22} /> : <Menu size={22} />}
          </button>
          <span className="topbar-label">
            CRM / <strong>Рабочее пространство</strong>
          </span>
          {auth.can('requests.read') && (
            <form
              className="global-search"
              onSubmit={(e) => {
                e.preventDefault();
                navigate(`/requests?q=${encodeURIComponent(search)}`);
              }}
            >
              <Search size={17} />
              <input
                aria-label="Поиск заявок"
                placeholder="Поиск заявок…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <kbd>↵</kbd>
            </form>
          )}
          <div className="topbar-actions">
            <NavLink className="icon-button" to="/guide" aria-label="Руководство">
              <BookOpen size={19} />
            </NavLink>
            <NavLink
              className="icon-button notification-button"
              to="/notifications"
              aria-label="Уведомления"
            >
              <Bell size={20} />
              {Boolean(notifications.data?.unread) && <i />}
            </NavLink>
            <span className="topbar-divider" />
            <div className="user-info">
              <span className="avatar">
                {name
                  .split(' ')
                  .map((v) => v[0])
                  .slice(0, 2)
                  .join('')}
              </span>
              <span>
                <strong>{name}</strong>
                <small>Мой аккаунт</small>
              </span>
            </div>
            <button
              className="icon-button"
              title="Завершить сеанс"
              aria-label="Выйти"
              onClick={() => void auth.logout()}
            >
              <LogOut size={17} />
            </button>
          </div>
        </header>
        <main id="main-content" className="main-content">
          <Outlet />
        </main>
        <footer className="app-footer">
          <span>Реактив CRM</span>
          <span>Часовой пояс: Москва, UTC+3</span>
        </footer>
      </div>
    </div>
  );
}
