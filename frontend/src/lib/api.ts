import type { Session } from './types';
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public field?: string,
    public requestId?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
let csrfToken = '';
export function setCsrf(token: string) {
  csrfToken = token;
}
export function clearSession() {
  csrfToken = '';
}
export type ApiOptions = Omit<RequestInit, 'body'> & { body?: unknown; key?: string };
export async function api<T = unknown>(path: string, options: ApiOptions = {}): Promise<T> {
  const { body, key, ...rest } = options;
  const headers = new Headers(options.headers);
  if (body !== undefined && !(body instanceof FormData))
    headers.set('Content-Type', 'application/json');
  if (options.method && !['GET', 'HEAD'].includes(options.method)) {
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken);
    if (key) headers.set('Idempotency-Key', key);
  }
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...rest,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(
      0,
      'NETWORK_ERROR',
      'Нет связи с сервером. Проверьте подключение и повторите. Введённые данные сохранены в форме.',
    );
  }
  if (!response.ok) {
    const data = (await response.json().catch(() => ({}))) as Record<string, string>;
    if (response.status === 401 && path !== '/auth/login' && path !== '/auth/me')
      window.dispatchEvent(new Event('session-expired'));
    throw new ApiError(
      response.status,
      data.code || 'HTTP_ERROR',
      data.message ||
        (response.status === 409
          ? 'Запись изменена другим сотрудником. Обновите данные и повторите действие.'
          : `Операция не выполнена (${response.status}). Повторите или обратитесь к администратору.`),
      data.field,
      data.requestId,
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export async function authenticate(email: string, password: string, otp?: string) {
  const session = await api<Session>('/auth/login', {
    method: 'POST',
    body: { email, password, ...(otp ? { otp } : {}) },
  });
  setCsrf(session.csrf_token);
  return session;
}
export async function download(path: string, filename: string) {
  const response = await fetch(`/api/v1${path}`, { credentials: 'same-origin' });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      error.code || 'DOWNLOAD_FAILED',
      error.message || 'Файл пока недоступен. Проверьте права доступа и статус проверки файла.',
    );
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function errorMessage(error: unknown) {
  return error instanceof Error
    ? error.message
    : 'Не удалось выполнить операцию. Повторите попытку.';
}
