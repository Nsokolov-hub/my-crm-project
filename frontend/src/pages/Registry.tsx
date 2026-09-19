import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Collection } from '../components/Collection';
import { Badge, Button, DetailPairs, ErrorBox, Modal, PageHeading } from '../components/ui';
import { download } from '../lib/api';
import { date, decimal } from '../lib/format';
import type { Entity } from '../lib/types';
import { ApprovalDecision } from './Fulfillment';
const names = { documents: 'Документы', payments: 'Оплаты', approvals: 'Согласования' };
export function Registry({ kind }: { kind: keyof typeof names }) {
  const [selected, setSelected] = useState<Entity>();
  const [decision, setDecision] = useState(false);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState<unknown>();
  return (
    <>
      <PageHeading title={names[kind]} description="Общий реестр доступных вам заявок." />
      <ErrorBox error={error} />
      <Collection
        title={names[kind]}
        endpoint={`/${kind}`}
        refreshKey={revision}
        onSelect={setSelected}
        columns={[
          { key: 'number', label: 'Номер', render: (r) => String(r.number || r.id.slice(0, 8)) },
          { key: 'status', label: 'Состояние', render: (r) => <Badge value={r.status} /> },
          ...(kind === 'approvals'
            ? []
            : [
                {
                  key: 'amount',
                  label: 'Сумма',
                  render: (r: Entity) => `${decimal(r.total ?? r.amount)} ${r.currency ?? ''}`,
                },
              ]),
          { key: 'created_at', label: 'Создано', render: (r) => date(r.created_at, true) },
        ]}
      />
      {selected && !decision && (
        <Modal
          title={String(selected.number || names[kind])}
          onClose={() => setSelected(undefined)}
        >
          <div className="form-body">
            <DetailPairs
              values={{
                Состояние: selected.status,
                Создано: date(selected.created_at, true),
                Комментарий: selected.comment || selected.reason,
              }}
            />
            <div className="inline-actions">
              <Link
                className="button primary"
                to={`/requests/${selected.request_id}?tab=${kind === 'approvals' ? 'fulfillment' : kind}`}
              >
                Открыть заявку
              </Link>
              {kind === 'documents' &&
                ['pdf', 'xlsx'].map((format) => (
                  <Button
                    key={format}
                    variant="secondary"
                    onClick={() =>
                      void download(
                        `/documents/${selected.id}/file?format=${format}`,
                        `${selected.number}.${format}`,
                      ).catch(setError)
                    }
                  >
                    {format.toUpperCase()}
                  </Button>
                ))}
              {kind === 'approvals' && selected.status === 'pending' && (
                <Button onClick={() => setDecision(true)}>Принять решение</Button>
              )}
            </div>
          </div>
        </Modal>
      )}
      {selected && decision && (
        <ApprovalDecision
          row={selected}
          onClose={() => setDecision(false)}
          onSuccess={() => {
            setDecision(false);
            setSelected(undefined);
            setRevision((v) => v + 1);
          }}
        />
      )}
    </>
  );
}
