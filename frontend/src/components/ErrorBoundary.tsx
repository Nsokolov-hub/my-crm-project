import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { api } from '../lib/api';

type Props = { children: ReactNode; resetKey?: string };
type State = { error?: Error };

/**
 * Business remark of 11 November 2026: a screen that fails shows what to do instead of a blank page or a
 * restart, and the failure reaches the API log, so it can be found and fixed.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = {};

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    void api('/client-errors', {
      method: 'POST',
      body: {
        message: String(error.message || error).slice(0, 2000),
        stack: String(error.stack || '').slice(0, 8000),
        component: String(info.componentStack || '').slice(0, 4000),
        path: window.location.pathname.slice(0, 500),
      },
    }).catch(() => undefined);
  }

  componentDidUpdate(previous: Props) {
    // Another section of the menu starts clean.
    if (this.state.error && previous.resetKey !== this.props.resetKey)
      this.setState({ error: undefined });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="panel error-screen" role="alert">
        <h2>Не удалось показать этот экран</h2>
        <p>
          Ошибка уже записана в журнал CRM. Введённые в форме данные могли не сохраниться —
          проверьте их после повтора.
        </p>
        <div className="inline-actions">
          <button
            type="button"
            className="button primary"
            onClick={() => this.setState({ error: undefined })}
          >
            Повторить
          </button>
          <button
            type="button"
            className="button secondary"
            onClick={() => window.location.reload()}
          >
            Обновить страницу
          </button>
        </div>
      </div>
    );
  }
}
