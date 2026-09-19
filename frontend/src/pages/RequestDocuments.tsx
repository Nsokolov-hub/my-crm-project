import { Download, Plus } from 'lucide-react';
import { useState } from 'react';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import { LineCommand } from '../components/LineCommand';
import { Badge, Button, DataTable, DetailPairs, ErrorBox, Modal } from '../components/ui';
import { download } from '../lib/api';
import { date, decimal, nowLocal } from '../lib/format';
import { useApi } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';
export function RequestDocuments({ requestId }: { requestId: string }) {
  const [selected, setSelected] = useState<Entity>();
  const [action, setAction] = useState<'proposal' | 'invoice' | 'accept' | 'sent'>();
  const [error, setError] = useState<unknown>();
  const [revision, setRevision] = useState(0);
  const executions = useApi<Page>(`/requests/${requestId}/executions`);
  function done() {
    setAction(undefined);
    setSelected(undefined);
    setRevision((v) => v + 1);
    executions.refresh();
  }
  const common = [
    { key: 'number', label: 'Номер документа' },
    { key: 'status', label: 'Статус', render: (r: Entity) => <Badge value={r.status} /> },
    { key: 'total', label: 'Сумма', render: (r: Entity) => `${decimal(r.total)} ${r.currency}` },
    { key: 'created_at', label: 'Выпущен', render: (r: Entity) => date(r.created_at) },
    {
      key: 'file',
      label: 'Файл',
      sortable: false,
      render: (r: Entity) => (
        <div className="inline-actions compact">
          {['pdf', 'xlsx'].map((format) => (
            <Button
              key={format}
              variant="ghost"
              onClick={() =>
                void download(
                  `/documents/${r.id}/file?format=${format}`,
                  `${r.number}.${format}`,
                ).catch(setError)
              }
            >
              <Download size={14} />
              {format.toUpperCase()}
            </Button>
          ))}
        </div>
      ),
    },
  ];
  const proposalFields: Field[] = [
    {
      name: 'calculation_id',
      label: 'Записанная версия расчёта',
      required: true,
      type: 'select',
      source: `/requests/${requestId}/calculations`,
      labelKey: 'reason',
    },
    { name: 'valid_until', label: 'Действует до', type: 'date', required: true },
    { name: 'terms', label: 'Условия поставки и оплаты', type: 'textarea', required: true },
  ];
  const snapshot = selected?.snapshot as Record<string, unknown> | undefined;
  const lines = ((snapshot?.lines || []) as Entity[]).map((r, i) => ({
    ...r,
    id: String(r.line_id || r.id || i),
    name: String(r.description || r.name || r.product_name || r.id),
  }));
  return (
    <>
      <div className="tab-actions">
        <Button onClick={() => setAction('proposal')}>
          <Plus size={16} />
          Выпустить КП
        </Button>
      </div>
      <ErrorBox error={error} />
      <Collection
        title="Коммерческие предложения"
        endpoint={`/requests/${requestId}/proposals`}
        refreshKey={revision}
        columns={common}
        onSelect={setSelected}
      />
      <Collection
        title="Счета"
        endpoint={`/requests/${requestId}/invoices`}
        refreshKey={revision}
        columns={[
          ...common,
          {
            key: 'balance',
            label: 'К оплате',
            render: (r) => `${decimal(r.balance)} ${r.currency}`,
          },
        ]}
        onSelect={setSelected}
      />
      {selected && !action && (
        <Modal title={String(selected.number)} wide onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <DetailPairs
              values={{
                Статус: selected.status,
                Валюта: selected.currency,
                Сумма: decimal(selected.total),
                'Действует до': date(selected.valid_until),
                'Дата выпуска': date(selected.created_at, true),
                Расчёт: selected.calculation_id,
                Предложение: selected.proposal_id,
              }}
            />
            <DataTable<Entity>
              rows={lines}
              columns={[
                { key: 'name', label: 'Позиция' },
                { key: 'quantity', label: 'Количество', render: (r) => decimal(r.quantity) },
                { key: 'unit', label: 'Ед.' },
                { key: 'total', label: 'Сумма', render: (r) => decimal(r.total) },
              ]}
            />
            {selected.kind === 'proposal' && (
              <div className="inline-actions">
                <Button onClick={() => setAction('accept')}>Принять состав</Button>
                <Button variant="secondary" onClick={() => setAction('invoice')}>
                  Выставить счёт
                </Button>
                <Button variant="secondary" onClick={() => setAction('sent')}>
                  Отметить отправку
                </Button>
              </div>
            )}
          </div>
        </Modal>
      )}
      {action === 'proposal' && (
        <RecordForm
          title="Выпустить коммерческое предложение"
          endpoint={`/requests/${requestId}/proposals`}
          fields={proposalFields}
          command
          note="Клиентский документ фиксирует реквизиты, строки, цены, налоги и шаблон. После выпуска его файл неизменяем."
          onClose={() => setAction(undefined)}
          onSuccess={done}
        />
      )}{' '}
      {selected && action === 'accept' && (
        <LineCommand
          title="Зафиксировать принятие состава клиентом"
          endpoint={`/proposals/${selected.id}/accept`}
          rows={lines}
          rowKey="line_id"
          extra={{ version: selected.version }}
          reasonRequired
          includeAnalogue
          note="Укажите фактически принятое количество. Для аналога зафиксируйте основание решения клиента."
          onClose={() => setAction(undefined)}
          onSuccess={done}
        />
      )}{' '}
      {selected && action === 'invoice' && (
        <InvoiceEditor
          requestId={requestId}
          proposal={selected}
          executions={executions.data?.items || []}
          onClose={() => setAction(undefined)}
          onSuccess={done}
        />
      )}{' '}
      {selected && action === 'sent' && (
        <RecordForm
          title="Отправка КП"
          endpoint={`/proposals/${selected.id}/sent`}
          command
          extra={{ version: selected.version }}
          fields={[
            { name: 'channel', label: 'Канал и получатель', required: true },
            {
              name: 'sent_at',
              label: 'Дата отправки',
              required: true,
              type: 'datetime-local',
              value: nowLocal(),
            },
          ]}
          onClose={() => setAction(undefined)}
          onSuccess={done}
        />
      )}
    </>
  );
}
function InvoiceEditor({
  requestId,
  proposal,
  executions,
  onClose,
  onSuccess,
}: {
  requestId: string;
  proposal: Entity;
  executions: Entity[];
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [dueDate, setDueDate] = useState('');
  const [terms, setTerms] = useState('');
  return (
    <LineCommand
      title="Выставить счёт на принятые позиции"
      endpoint={`/requests/${requestId}/invoices`}
      rows={executions
        .filter((e) => e.proposal_id === proposal.id)
        .map((e) => ({ ...e, name: String((e.snapshot as Entity)?.description || e.item_id) }))}
      rowKey="execution_id"
      extra={{ proposal_id: proposal.id, due_date: dueDate, terms }}
      note="Счёт выпускается в пределах принятого и ещё не выставленного количества."
      additional={
        <div className="form-grid">
          <label className="field">
            Оплатить до
            <input
              type="date"
              required
              value={dueDate}
              onChange={(e) => setDueDate(e.target.value)}
            />
          </label>
          <label className="field">
            Условия оплаты
            <input required value={terms} onChange={(e) => setTerms(e.target.value)} />
          </label>
        </div>
      }
      onClose={onClose}
      onSuccess={onSuccess}
    />
  );
}
