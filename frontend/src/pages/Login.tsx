import { ArrowRight, FlaskConical, LockKeyhole } from 'lucide-react';
import { useState } from 'react';
import { useAuth } from '../app/Auth';
import { Button, ErrorBox } from '../components/ui';
export function Login() {
  const auth = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [otp, setOtp] = useState('');
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await auth.login(email, password, otp);
    } catch (error) {
      setError(error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-page">
      <aside className="login-brand">
        <a className="brand" href="/">
          <span className="brand-symbol">
            <FlaskConical size={23} />
          </span>
          <span>
            реактив<span className="brand-crm"> CRM</span>
          </span>
        </a>
        <div className="login-story">
          <span className="eyebrow">Химия сильных связей</span>
          <h1>
            От первого звонка
            <br />
            до точной поставки.
          </h1>
          <p>
            Клиенты, закупки, расчёты и команда.
            <br />В одном рабочем пространстве.
          </p>
          <div className="molecule" aria-hidden="true">
            <i />
            <i />
            <i />
            <i />
            <i />
            <i />
            <i />
          </div>
        </div>
        <small>Единая история. Прозрачные решения.</small>
      </aside>
      <main className="login-form-area">
        <form onSubmit={submit} className="login-form">
          <span className="login-icon">
            <LockKeyhole size={23} />
          </span>
          <h2>С возвращением</h2>
          <p>Войдите в рабочее пространство вашей команды.</p>
          <ErrorBox error={error} />
          <label htmlFor="email">Рабочая почта</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            placeholder="you@company.ru"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <label htmlFor="password">Пароль</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            placeholder="Введите пароль"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          <details className="otp-details">
            <summary>Использовать двухфакторный код</summary>
            <label htmlFor="otp">Код из приложения</label>
            <input
              id="otp"
              autoComplete="one-time-code"
              inputMode="numeric"
              maxLength={6}
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
            />
          </details>
          <Button busy={busy} type="submit">
            Войти в CRM
            <ArrowRight size={18} />
          </Button>
          <p className="login-help">
            Доступ выдаёт администратор вашей организации.
            <br />
            Если это первый запуск, создайте пользователя по инструкции развёртывания.
          </p>
        </form>
        <span className="login-footer">Реактив CRM · Продажи и закупки химической продукции</span>
      </main>
    </div>
  );
}
