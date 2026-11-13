import { History } from 'lucide-react';
import { ErrorBox, Loading, Modal } from '../components/ui';
import { date } from '../lib/format';
import { useApi } from '../lib/hooks';

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
export function WaveHistoryTab({ onOpen }: { onOpen: () => void }) {
  return (
    <button
      type="button"
      className="wave-history-tab"
      onClick={onOpen}
      aria-label="История изменений волны"
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
  const history = useApi<{ items: WaveEvent[] }>(`/waves/${waveId}/history`);
  const items = history.data?.items || [];
  // Панель выезжает справа поверх карточки волны: своё окно, Esc закрывает только её
  return (
    <Modal title={`История изменений · ${number}`} className="side-drawer" onClose={onClose}>
      <div className="wave-history">
        {history.loading && <Loading />}
        <ErrorBox error={history.error} />
        {!history.loading && !items.length && !history.error && (
          <p className="muted">Изменений пока нет.</p>
        )}
        <ol className="wave-history-list">
          {items.map((event) => (
            <li key={event.id}>
              <p className="wave-history-meta">
                {date(event.created_at, true)}
                {event.actor_name ? ` · ${event.actor_name}` : ''}
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
            </li>
          ))}
        </ol>
      </div>
    </Modal>
  );
}
