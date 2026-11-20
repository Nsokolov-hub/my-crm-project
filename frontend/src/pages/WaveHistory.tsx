import { History } from 'lucide-react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { Button, ErrorBox, Loading, Modal } from '../components/ui';
import { api } from '../lib/api';
import { date } from '../lib/format';
import { queryClient } from '../lib/queryClient';

type WaveEvent = {
  id: string;
  created_at: string;
  actor_name: string | null;
  title: string;
  changes: { label: string; before: string | null; after: string | null }[];
  details: string | null;
  comment: string | null;
};

/** Плашка сбоку карточки волны (замечание бизнеса 13.11.2026): открывает историю изменений */
export function WaveHistoryTab({
  onOpen,
  expanded = false,
}: {
  onOpen: () => void;
  expanded?: boolean;
}) {
  return (
    <button
      type="button"
      className="wave-history-tab"
      onClick={onOpen}
      aria-label="История изменений волны"
      aria-haspopup="dialog"
      aria-expanded={expanded}
    >
      <History size={15} />
      <span>История</span>
    </button>
  );
}

/**
 * Business remark of 13 November 2026: every change of the wave with the comment it was made with — edits of the wave,
 * its planned volume, orders handed into it, requests that joined or left it (GET /waves/{id}/history).
 */
export function WaveHistory({
  waveId,
  number,
  onClose,
}: {
  waveId: string;
  number: string;
  onClose: () => void;
}) {
  const history = useInfiniteQuery(
    {
      queryKey: ['api', `/waves/${waveId}/history`],
      initialPageParam: undefined as string | undefined,
      queryFn: ({ pageParam, signal }) =>
        api<{ items: WaveEvent[]; next_cursor?: string | null }>(
          `/waves/${waveId}/history${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ''}`,
          { signal },
        ),
      getNextPageParam: (last) => last.next_cursor || undefined,
    },
    queryClient,
  );
  const items = history.data?.pages.flatMap((page) => page.items) || [];
  // Панель выезжает справа поверх карточки волны: своё окно, Esc закрывает только её
  return (
    <Modal title={`История изменений · ${number}`} className="side-drawer" onClose={onClose}>
      <div className="wave-history">
        {history.isPending && <Loading />}
        <ErrorBox error={history.error} />
        {!history.isPending && !items.length && !history.error && (
          <p className="muted">Изменений пока нет.</p>
        )}
        {items.length > 0 && <p className="muted">Показано изменений: {items.length}</p>}
        <ol className="wave-history-list">
          {items.map((event) => (
            <li key={event.id}>
              <p className="wave-history-meta">
                {date(event.created_at, true)}
                {` · ${event.actor_name || 'Система'}`}
              </p>
              <p className="wave-history-title">{event.title}</p>
              {event.details && <p className="wave-history-details">{event.details}</p>}
              {event.changes.length > 0 && (
                <ul className="wave-history-changes">
                  {event.changes.map((change) => (
                    <li key={change.label}>
                      {change.label}: <s>{change.before || '—'}</s> →{' '}
                      <strong>{change.after || '—'}</strong>
                    </li>
                  ))}
                </ul>
              )}
              {event.comment && (
                <blockquote className="wave-history-comment">{event.comment}</blockquote>
              )}
              {!event.comment && <p className="muted">Комментарий не указан.</p>}
            </li>
          ))}
        </ol>
        {history.hasNextPage && (
          <Button
            variant="secondary"
            busy={history.isFetchingNextPage}
            onClick={() => void history.fetchNextPage()}
          >
            Показать ещё изменения
          </Button>
        )}
      </div>
    </Modal>
  );
}
