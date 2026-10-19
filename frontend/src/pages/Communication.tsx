import {
  Bell,
  Check,
  Download,
  MessageSquare,
  Paperclip,
  Plus,
  Send,
  Users,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../app/Auth';
import { RecordForm } from '../components/Form';
import {
  Badge,
  Button,
  DataTable,
  Empty,
  ErrorBox,
  Loading,
  Modal,
  PageHeading,
  Section,
} from '../components/ui';
import { api, download } from '../lib/api';
import { date } from '../lib/format';
import { useApi, useCommand, usePolling } from '../lib/hooks';
import { useLiveEvents } from '../lib/liveEvents';
import { createRequestKey } from '../lib/requestKey';
import type { Entity, Field, Page } from '../lib/types';
export function FilesPanel({
  entityType,
  entityId,
  onAttach,
  initialFile,
}: {
  entityType: string;
  entityId: string;
  onAttach?: (file: Entity) => void;
  initialFile?: File;
}) {
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ current: number; total: number }>();
  const [autoAttachId, setAutoAttachId] = useState<string>();
  const files = useApi<Page>(
    `/files?entity_type=${entityType}&entity_id=${entityId}&page_size=100`,
  );
  const refreshFiles = files.refresh;
  const [classification, setClassification] = useState('general');
  const pendingUpload = useRef<{ signature: string; key: string } | undefined>(undefined);
  const receivedFile = useRef<File | undefined>(undefined);
  const fileInput = useRef<HTMLInputElement>(null);
  const scanning = Boolean(
    files.data?.items.some((f) => f.status === 'quarantined' || f.status === 'pending'),
  );
  usePolling(refreshFiles, scanning ? 5000 : null);
  useLiveEvents((event) => {
    if (event.kind === 'file' || event.kind === 'resync') refreshFiles();
  });
  const send = useCallback(
    async (file: File) => {
      const signature = [
        file.name,
        file.size,
        file.lastModified,
        classification,
        entityType,
        entityId,
      ].join(':');
      if (pendingUpload.current?.signature !== signature)
        pendingUpload.current = { signature, key: createRequestKey() };
      const form = new FormData();
      form.set('file', file);
      form.set('entity_type', entityType);
      form.set('entity_id', entityId);
      form.set('classification', classification);
      const result = await api<Entity>('/files', {
        method: 'POST',
        body: form,
        key: pendingUpload.current.key,
      });
      pendingUpload.current = undefined;
      if (onAttach && file === initialFile) setAutoAttachId(result.id);
    },
    [classification, entityType, entityId, initialFile, onAttach],
  );
  // Several chosen files go one after another; a failed file does not stop the rest.
  const upload = useCallback(
    async (...chosen: File[]) => {
      setBusy(true);
      setError(undefined);
      const failed: string[] = [];
      let lastError: unknown;
      try {
        for (const [index, file] of chosen.entries()) {
          if (chosen.length > 1) setProgress({ current: index + 1, total: chosen.length });
          try {
            await send(file);
          } catch (e) {
            failed.push(file.name);
            lastError = e;
          }
        }
      } finally {
        setBusy(false);
        setProgress(undefined);
        refreshFiles();
      }
      if (failed.length && chosen.length > 1)
        setError(
          new Error(
            `Не загружены файлы: ${failed.join(', ')}. ${lastError instanceof Error ? lastError.message : ''}`.trim(),
          ),
        );
      else if (failed.length) setError(lastError);
    },
    [send, refreshFiles],
  );
  useEffect(() => {
    const ready = files.data?.items.find(
      (row) => row.id === autoAttachId && row.status === 'clean',
    );
    if (!ready || !onAttach) return;
    setAutoAttachId(undefined);
    onAttach(ready);
  }, [autoAttachId, files.data, onAttach]);
  useEffect(() => {
    if (!initialFile || receivedFile.current === initialFile) return;
    receivedFile.current = initialFile;
    void upload(initialFile);
  }, [initialFile, upload]);
  useEffect(() => {
    const paste = (event: ClipboardEvent) => {
      const item = Array.from(event.clipboardData?.items || []).find((entry) =>
        entry.type.startsWith('image/'),
      );
      const source = item?.getAsFile();
      if (!source || busy) return;
      event.preventDefault();
      const extension =
        source.type === 'image/jpeg' ? 'jpg' : source.type === 'image/gif' ? 'gif' : 'png';
      void upload(new File([source], `Снимок-${Date.now()}.${extension}`, { type: source.type }));
    };
    document.addEventListener('paste', paste);
    return () => document.removeEventListener('paste', paste);
  }, [upload, busy]);
  return (
    <Section
      title={entityType === 'calendar_entry' ? 'Счёт и документы платежа' : 'Вложения'}
      description={
        entityType === 'calendar_entry'
          ? 'Загрузите счёт в PDF, Excel или изображение. Файл станет доступен после проверки.'
          : 'Прикрепите один или несколько файлов или вставьте снимок через Ctrl+V / ⌘V. Файлы станут доступны после проверки.'
      }
    >
      <ErrorBox error={error || files.error} />
      {autoAttachId && <p>Снимок проверяется и будет приложен к сообщению автоматически.</p>}
      <div className="inline-actions">
        <Button
          type="button"
          variant="secondary"
          disabled={busy}
          onClick={() => fileInput.current?.click()}
        >
          <Paperclip size={16} />
          {busy
            ? progress
              ? `Загружается ${progress.current} из ${progress.total}…`
              : 'Загружается…'
            : 'Прикрепить файлы'}
        </Button>
        <input
          ref={fileInput}
          hidden
          type="file"
          multiple
          aria-label="Файлы для загрузки"
          accept=".pdf,.png,.jpg,.jpeg,.gif,.txt,.csv,.xlsx,.docx"
          disabled={busy}
          onChange={(e) => {
            const chosen = Array.from(e.target.files || []);
            e.currentTarget.value = '';
            if (chosen.length) void upload(...chosen);
          }}
        />
        <select
          aria-label="Класс доступа к файлу"
          value={classification}
          onChange={(e) => setClassification(e.target.value)}
        >
          <option value="general">Общий для объекта</option>
          <option value="purchase">Закупочные данные</option>
          <option value="calculation">Расчёт</option>
          <option value="reward">Вознаграждение</option>
          <option value="profit">Прибыль</option>
        </select>
      </div>
      {files.loading ? (
        <Loading />
      ) : (
        <DataTable
          rows={files.data?.items || []}
          columns={[
            { key: 'name', label: 'Файл', render: (r) => String(r.original_name || r.name) },
            {
              key: 'size',
              label: 'Размер',
              render: (r) => `${Math.ceil(Number(r.size) / 1024)} КБ`,
            },
            {
              key: 'status',
              label: 'Проверка',
              render: (r) =>
                r.status === 'quarantined' || r.status === 'pending' ? (
                  'Проверяется…'
                ) : r.status === 'scan_failed' ? (
                  'Не удалось проверить файл'
                ) : (
                  <Badge value={r.status} />
                ),
            },
            {
              key: 'actions',
              label: 'Действия',
              render: (r) => (
                <div className="inline-actions compact">
                  <Button
                    variant="ghost"
                    disabled={r.status !== 'clean'}
                    onClick={() =>
                      void download(
                        `/files/${r.id}/download`,
                        String(r.original_name || r.name),
                      ).catch(setError)
                    }
                  >
                    <Download size={15} />
                    Скачать
                  </Button>
                  {onAttach && (
                    <Button
                      variant="ghost"
                      disabled={r.status !== 'clean'}
                      onClick={() => onAttach(r)}
                    >
                      В сообщение
                    </Button>
                  )}
                  <button
                    className="icon-button"
                    title="Скопировать идентификатор файла"
                    aria-label="Скопировать идентификатор файла"
                    onClick={() => void navigator.clipboard.writeText(r.id).catch(setError)}
                  >
                    ID
                  </button>
                </div>
              ),
            },
          ]}
        />
      )}
    </Section>
  );
}
type MessagePage = Page & { next_cursor: number; has_more: boolean };
export function ChatRoom({ chat, onBack }: { chat: Entity; onBack?: () => void }) {
  const auth = useAuth();
  const [messages, setMessages] = useState<Entity[]>([]);
  const [text, setText] = useState('');
  const [error, setError] = useState<unknown>();
  const [filesOpen, setFilesOpen] = useState(false);
  const [pastedFile, setPastedFile] = useState<File>();
  const [membersOpen, setMembersOpen] = useState(false);
  const [attached, setAttached] = useState<Entity[]>([]);
  const [mentionIds, setMentionIds] = useState<string[]>([]);
  const cursor = useRef(0);
  const markedRead = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);
  const operation = useCommand();
  const reload = useRef<() => void>(() => {});
  useEffect(() => {
    let stopped = false;
    let loading = false;
    cursor.current = 0;
    markedRead.current = 0;
    setMessages([]);
    async function load() {
      if (loading || stopped) return;
      loading = true;
      try {
        let more = true;
        while (more && !stopped) {
          const page = await api<MessagePage>(
            `/chats/${chat.id}/messages?after=${cursor.current}&limit=100`,
          );
          if (stopped) break;
          cursor.current = page.next_cursor;
          setMessages((current) => {
            const byId = new Map(current.map((m) => [m.id, m]));
            for (const m of page.items) byId.set(m.id, m);
            return [...byId.values()].sort((a, b) => Number(a.sequence) - Number(b.sequence));
          });
          more = page.has_more;
        }
        if (!stopped && cursor.current > markedRead.current) {
          setError(undefined);
          await api(`/chats/${chat.id}/read`, {
            method: 'POST',
            body: { through: cursor.current },
          });
          markedRead.current = cursor.current;
          window.dispatchEvent(new Event('crm:chats-read'));
        }
      } catch (e) {
        if (!stopped) setError(e);
      } finally {
        loading = false;
      }
    }
    void load();
    reload.current = () => void load();
    const online = () => void load();
    window.addEventListener('online', online);
    return () => {
      stopped = true;
      reload.current = () => {};
      window.removeEventListener('online', online);
    };
  }, [chat.id]);
  usePolling(() => reload.current(), 4000);
  useLiveEvents((event) => {
    if (event.kind === 'resync' || (event.kind === 'chat' && event.chat === chat.id))
      reload.current();
  });
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages.length]);
  async function send(e: React.FormEvent) {
    e.preventDefault();
    try {
      const result = await operation.run<Entity>(`/chats/${chat.id}/messages`, {
        content: text,
        file_ids: attached.map((f) => f.id),
        mention_ids: mentionIds,
      });
      if (result) {
        setMessages((current) =>
          current.some((m) => m.id === result.id) ? current : [...current, result],
        );
        setText('');
        setAttached([]);
        setMentionIds([]);
      }
    } catch {
      /* preserve draft and retry key */
    }
  }
  return (
    <div className="chat-room">
      <header>
        <div>
          <strong>{String(chat.title)}</strong>
          {Boolean(chat.description) && <small>{String(chat.description)}</small>}
          <small>{((chat.members || []) as Entity[]).map((m) => m.name).join(', ')}</small>
        </div>
        <div className="inline-actions compact">
          <button
            className="icon-button"
            aria-label="Участники"
            onClick={() => setMembersOpen(true)}
          >
            <Users size={18} />
          </button>
          {onBack && (
            <button className="icon-button" aria-label="Назад к обсуждениям" onClick={onBack}>
              <X size={19} />
            </button>
          )}
        </div>
      </header>
      <ErrorBox error={error || operation.error} />
      <div className="message-list" ref={listRef}>
        {messages.length ? (
          messages.map((message) => (
            <article
              key={message.id}
              className={`message ${message.author_id === auth.session?.user.id ? 'own' : ''}`}
            >
              <div className="message-meta">
                <strong>{String(message.author_name)}</strong>
                <time>{date(message.created_at, true)}</time>
              </div>
              <p>{String(message.content)}</p>
              {((message.files || []) as Entity[]).map((file) => (
                <button
                  key={file.id}
                  className="attachment-link"
                  onClick={() =>
                    void download(
                      `/files/${file.id}/download`,
                      String(file.original_name || file.name),
                    ).catch(setError)
                  }
                >
                  <Paperclip size={15} />
                  {String(file.original_name || file.name)}
                </button>
              ))}
            </article>
          ))
        ) : (
          <Empty
            title="Начните обсуждение"
            description="Сообщения и решения останутся в истории команды."
          />
        )}
      </div>
      <form onSubmit={send} className="message-compose">
        {attached.length > 0 && (
          <div className="attached-files">
            {attached.map((f) => (
              <button
                key={f.id}
                type="button"
                onClick={() => setAttached((v) => v.filter((row) => row.id !== f.id))}
              >
                {String(f.original_name || f.name)} <X size={12} />
              </button>
            ))}
          </div>
        )}
        <textarea
          aria-label="Сообщение"
          placeholder="Напишите сообщение…"
          rows={2}
          value={text}
          maxLength={20000}
          onChange={(e) => setText(e.target.value)}
          onPaste={(event) => {
            const item = Array.from(event.clipboardData.items).find((entry) =>
              entry.type.startsWith('image/'),
            );
            const source = item?.getAsFile();
            if (!source) return;
            event.preventDefault();
            const extension =
              source.type === 'image/jpeg' ? 'jpg' : source.type === 'image/gif' ? 'gif' : 'png';
            setPastedFile(
              new File([source], `Снимок-${Date.now()}.${extension}`, { type: source.type }),
            );
            setFilesOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              if (!operation.busy && (text.trim() || attached.length))
                e.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <div className="message-compose-actions">
          <div className="message-compose-tools">
            <button
              type="button"
              className="icon-button"
              aria-label="Прикрепить файл"
              onClick={() => setFilesOpen(true)}
            >
              <Paperclip size={19} />
            </button>
            <select
              aria-label="Упомянуть участника"
              value=""
              onChange={(e) => {
                setMentionIds((v) => [...new Set([...v, e.target.value])]);
                setText(
                  (v) =>
                    `${v}@${((chat.members || []) as Entity[]).find((m) => m.id === e.target.value)?.name} `,
                );
              }}
            >
              <option value="">@ Упомянуть</option>
              {((chat.members || []) as Entity[]).map((m) => (
                <option key={m.id} value={m.id}>
                  {String(m.name)}
                </option>
              ))}
            </select>
          </div>
          <div className="message-compose-submit">
            <span className="message-compose-hint">
              Enter — отправить · Shift + Enter — новая строка
            </span>
            <Button type="submit" disabled={!text.trim() && !attached.length} busy={operation.busy}>
              <Send size={17} />
              Отправить
            </Button>
          </div>
        </div>
      </form>
      {filesOpen && (
        <Modal
          title="Вложения обсуждения"
          wide
          onClose={() => {
            setFilesOpen(false);
            setPastedFile(undefined);
          }}
        >
          <div className="form-body">
            <FilesPanel
              entityType="chat"
              entityId={chat.id}
              initialFile={pastedFile}
              onAttach={(file) => {
                setAttached((v) => (v.some((f) => f.id === file.id) ? v : [...v, file]));
                setFilesOpen(false);
                setPastedFile(undefined);
              }}
            />
          </div>
        </Modal>
      )}
      {membersOpen && (
        <RecordForm
          title="Добавить участника"
          endpoint={`/chats/${chat.id}/members`}
          extra={{ version: chat.version }}
          fields={[
            {
              name: 'user_id',
              label: 'Сотрудник',
              required: true,
              type: 'select',
              source: '/users',
            },
            {
              name: 'history_acknowledged',
              label: 'Новый участник получит историю группы',
              type: 'checkbox',
              required: true,
            },
          ]}
          note="Участник получает доступ к истории группы. Доступ к связанным документам проверяется отдельно."
          onClose={() => setMembersOpen(false)}
          onSuccess={() => setMembersOpen(false)}
        />
      )}
    </div>
  );
}
export function Chats({
  entityId,
  entityType = 'request',
}: {
  entityId?: string;
  entityType?: string;
}) {
  const [params] = useSearchParams();
  const chats = useApi<Page>(`/chats${entityId ? '?entity_id=' + entityId : ''}`);
  const [selected, setSelected] = useState<Entity>();
  const [creating, setCreating] = useState(false);
  const fields: Field[] = [
    { name: 'title', label: 'Название обсуждения', required: true },
    { name: 'description', label: 'Описание', type: 'textarea', wide: true },
    {
      name: 'kind',
      label: 'Тип',
      required: true,
      type: 'select',
      value: entityId ? entityType : 'group',
      options: entityId
        ? [{ value: entityType, label: 'Обсуждение объекта' }]
        : [
            { value: 'group', label: 'Групповой' },
            { value: 'direct', label: 'Личный' },
          ],
    },
    { name: 'member_ids', label: 'Участники', type: 'multiselect', source: '/users' },
  ];
  useEffect(() => {
    if (params.get('chat') && chats.data)
      setSelected(chats.data.items.find((c) => c.id === params.get('chat')));
  }, [params, chats.data]);
  useEffect(() => {
    window.addEventListener('crm:chats-read', chats.refresh);
    return () => window.removeEventListener('crm:chats-read', chats.refresh);
  }, [chats.refresh]);
  return (
    <>
      {!entityId && (
        <PageHeading
          title="Обсуждения"
          description="Решения, контекст и файлы команды рядом с работой."
        />
      )}
      <div className={`chats-layout ${selected ? 'has-selection' : ''}`}>
        <aside className="chat-sidebar">
          <div className="section-heading">
            <h2>Обсуждения</h2>
            <button
              className="icon-button"
              aria-label="Новое обсуждение"
              onClick={() => setCreating(true)}
            >
              <Plus size={19} />
            </button>
          </div>
          <ErrorBox error={chats.error} />
          {chats.loading ? (
            <Loading />
          ) : chats.data?.items.length ? (
            chats.data.items.map((chat) => (
              <button
                key={chat.id}
                className={`chat-list-item ${selected?.id === chat.id ? 'active' : ''}`}
                onClick={() => setSelected(chat)}
              >
                <span className="chat-icon">
                  <MessageSquare size={20} />
                </span>
                <span className="chat-card-content">
                  <strong>{String(chat.title)}</strong>
                  <small className="chat-card-description">
                    {String(
                      chat.description ||
                        (chat.kind === 'direct' ? 'Личный диалог' : 'Обсуждение команды'),
                    )}
                  </small>
                  <small className="chat-card-members">
                    {((chat.members || []) as Entity[])
                      .map((member) => String(member.name))
                      .join(', ')}
                  </small>
                </span>
                {Boolean(chat.unread) && <b className="chat-unread">{String(chat.unread)}</b>}
              </button>
            ))
          ) : (
            <Empty compact title="Нет обсуждений" description="Создайте диалог с коллегами." />
          )}
        </aside>
        {selected ? (
          <ChatRoom chat={selected} onBack={() => setSelected(undefined)} />
        ) : (
          <div className="chat-placeholder">
            <MessageSquare size={40} />
            <h2>Держите команду в контексте</h2>
            <p>Выберите обсуждение или создайте новое.</p>
            <Button onClick={() => setCreating(true)}>
              <Plus size={16} />
              Новое обсуждение
            </Button>
          </div>
        )}
      </div>
      {creating && (
        <RecordForm
          title="Новое обсуждение"
          endpoint="/chats"
          fields={fields}
          extra={entityId ? { entity_id: entityId } : undefined}
          note="Участники группового обсуждения получают доступ к его истории. Файлы сохраняют права исходного объекта."
          onClose={() => setCreating(false)}
          onSuccess={(row) => {
            setSelected(row);
            setCreating(false);
            chats.refresh();
          }}
        />
      )}
    </>
  );
}
const NOTICE_CATEGORIES = [
  {
    key: 'work',
    title: 'Рабочие',
    description: 'Задачи, квоты, решения и оплаты — то, что ждёт вашего действия.',
  },
  {
    key: 'system',
    title: 'Системные',
    description:
      'Отправленные письма, проверенные файлы, сообщения чатов и события волн — для сведения. Колокольчик их не считает.',
  },
] as const;
export function Notifications() {
  const navigate = useNavigate();
  const [unread, setUnread] = useState(true);
  const [category, setCategory] = useState<'work' | 'system'>('work');
  const query = new URLSearchParams({ category });
  if (unread) query.set('read', 'false');
  const list = useApi<Page & { unread_work?: number; unread_system?: number }>(
    `/notifications?${query}`,
  );
  const command = useCommand();
  const readAll = useCommand();
  function changed() {
    list.refresh();
    window.dispatchEvent(new Event('crm:notifications-read'));
  }
  function target(row: Entity) {
    if (row.entity_type === 'workflow_review' || row.entity_type === 'payment') return '/approvals';
    if (row.entity_type === 'calendar_entry') return '/payment-calendar';
    if (row.entity_type === 'employee_absence') return '/tasks';
    return row.entity_type === 'request'
      ? `/requests/${row.entity_id}`
      : row.entity_type === 'counterparty'
        ? `/calls?client_id=${row.entity_id}`
        : row.entity_type === 'chat'
          ? `/chats?chat=${row.entity_id}`
          : row.entity_type === 'wave'
            ? '/waves'
            : '/tasks';
  }
  return (
    <>
      <PageHeading
        title="Уведомления"
        description="Рабочие — то, что требует действия: задачи, квоты, решения, оплаты. Системные — для сведения."
      />
      <div className="tabs">
        {NOTICE_CATEGORIES.map((row) => {
          const count = row.key === 'work' ? list.data?.unread_work : list.data?.unread_system;
          return (
            <button
              key={row.key}
              className={category === row.key ? 'active' : ''}
              onClick={() => setCategory(row.key)}
            >
              {row.title}
              {Boolean(count) && <span>{count}</span>}
            </button>
          );
        })}
      </div>
      <div className="tab-actions">
        <label className="calculation-internal-toggle">
          <input
            type="checkbox"
            checked={unread}
            onChange={(event) => setUnread(event.target.checked)}
          />{' '}
          Только непрочитанные
        </label>
        <Button
          variant="secondary"
          busy={readAll.busy}
          disabled={!(category === 'work' ? list.data?.unread_work : list.data?.unread_system)}
          onClick={() =>
            void readAll
              .run('/notifications/read-all', { category })
              .then(changed)
              .catch(() => {})
          }
        >
          <Check size={16} />
          Прочитать все
        </Button>
      </div>
      <ErrorBox error={command.error || readAll.error || list.error} />
      <Section
        title={
          (unread ? 'Непрочитанные · ' : '') +
          NOTICE_CATEGORIES.find((row) => row.key === category)!.title.toLowerCase()
        }
        description={NOTICE_CATEGORIES.find((row) => row.key === category)!.description}
      >
        {list.loading ? (
          <Loading />
        ) : list.data?.items.length ? (
          <div className="notification-list">
            {list.data.items.map((row) => (
              <article key={row.id} className={`notification-card ${row.read ? 'read' : 'unread'}`}>
                <span className="notification-icon">
                  <Bell size={20} />
                </span>
                <div>
                  <div className="notification-meta">
                    <span>
                      {row.entity_type === 'request'
                        ? 'Заявка'
                        : row.entity_type === 'chat'
                          ? 'Сообщение'
                          : row.entity_type === 'payment'
                            ? 'Оплата'
                            : row.category === 'system'
                              ? 'Системное событие'
                              : 'Рабочее событие'}
                    </span>
                    {!row.read && <Badge value="Новое" tone="purple" />}
                  </div>
                  <Link to={target(row)}>{String(row.title)}</Link>
                  <small>{date(row.created_at, true)}</small>
                </div>
                {!row.read && (
                  <Button
                    variant="ghost"
                    busy={command.busy}
                    onClick={() =>
                      void command
                        .run(`/notifications/${row.id}/read`, {})
                        .then(changed)
                        .catch(() => {})
                    }
                  >
                    <Check size={16} />
                    Прочитано
                  </Button>
                )}
                <Button variant="secondary" onClick={() => navigate(target(row))}>
                  Открыть
                </Button>
              </article>
            ))}
          </div>
        ) : (
          <Empty title="Вы в курсе всех событий" description="Новые уведомления появятся здесь." />
        )}
      </Section>
    </>
  );
}
