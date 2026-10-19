import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../app/Auth';
import { Collection } from '../components/Collection';
import {
  Badge,
  Button,
  DataTable,
  DetailPairs,
  ErrorBox,
  Modal,
  PageHeading,
  Section,
} from '../components/ui';
import { download } from '../lib/api';
import { date, decimal } from '../lib/format';
import { useApi, useCommand } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
import { WorkflowApprovals } from './Business';
import { ApprovalDecision } from './Fulfillment';
const names = { documents: 'Документы', payments: 'Оплаты', approvals: 'Согласования' };

/** Client payments declared in requests and waiting for someone to confirm the receipt. */
export function PaymentConfirmations() {
  const list = useApi<Page>('/payments?status=declared&page_size=100');
  const operation = useCommand();
  const [selected, setSelected] = useState<Entity>();
  const rows = list.data?.items || [];
  async function confirm() {
    if (!selected) return;
    const result = await operation
      .run(`/payments/${selected.id}/confirm`, { version: selected.version }, 'POST', true)
      .catch(() => undefined);
    if (!result) return;
    setSelected(undefined);
    list.refresh();
    window.dispatchEvent(new Event('crm:notifications-read'));
  }
  return (
    <Section
      title={`Оплаты на подтверждение${rows.length ? ` (${rows.length})` : ''}`}
      description="Платежи, которые менеджеры заявили в заявках. Подтвердите поступление здесь или в заявке; распределение по счетам — во вкладке «Оплаты» заявки."
    >
      <ErrorBox error={list.error} retry={list.refresh} />
      <DataTable
        rows={rows}
        onRow={setSelected}
        empty={<p className="muted form-body">Неподтверждённых оплат нет.</p>}
        columns={[
          { key: 'request_number', label: 'Заявка', render: (r) => `№${String(r.request_number)}` },
          { key: 'client_name', label: 'Клиент' },
          { key: 'number', label: 'Платёжный документ' },
          { key: 'payment_date', label: 'Дата', render: (r) => date(r.payment_date) },
          { key: 'amount', label: 'Сумма', render: (r) => `${decimal(r.amount)} ${r.currency}` },
          { key: 'declared_by_name', label: 'Заявил' },
        ]}
      />
      {selected && (
        <Modal title={`Платёж ${String(selected.number)}`} onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <ErrorBox error={operation.error} />
            <DetailPairs
              values={{
                Заявка: `№${String(selected.request_number)}`,
                Клиент: selected.client_name,
                Сумма: `${decimal(selected.amount)} ${selected.currency}`,
                Дата: date(selected.payment_date),
                Комментарий: selected.comment,
                Заявил: selected.declared_by_name,
              }}
            />
            <div className="inline-actions">
              <Button busy={operation.busy} onClick={() => void confirm()}>
                Подтвердить поступление
              </Button>
              <Link
                className="button secondary"
                to={`/requests/${String(selected.request_id)}?tab=payments`}
              >
                Открыть заявку
              </Link>
            </div>
          </div>
        </Modal>
      )}
    </Section>
  );
}
export function Registry({ kind }: { kind: keyof typeof names }) {
  const auth = useAuth();
  const [selected, setSelected] = useState<Entity>();
  const [decision, setDecision] = useState(false);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState<unknown>();
  return (
    <>
      <PageHeading title={names[kind]} description="Общий реестр доступных вам заявок." />
      <ErrorBox error={error} />
      {kind === 'approvals' && auth.can('payments.confirm') && <PaymentConfirmations />}
      {kind === 'approvals' && <WorkflowApprovals />}
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
                        `${String(selected.file_name || selected.number)}.${format}`,
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
