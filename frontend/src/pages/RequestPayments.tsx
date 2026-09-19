import { useState } from 'react';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import { LineCommand } from '../components/LineCommand';
import { Badge, Button, DetailPairs, ErrorBox, Modal } from '../components/ui';
import { date, decimal } from '../lib/format';
import { paymentFields } from '../lib/fields';
import { useApi, useCommand } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
export function RequestPayments({ requestId }: { requestId: string }) {
  const [selected, setSelected] = useState<Entity>();
  const [action, setAction] = useState<'allocate' | 'reverse'>();
  const [revision, setRevision] = useState(0);
  const invoices = useApi<Page>(`/requests/${requestId}/invoices`);
  const operation = useCommand();
  function done() {
    setSelected(undefined);
    setAction(undefined);
    setRevision((v) => v + 1);
    invoices.refresh();
  }
  async function confirm() {
    if (!selected) return;
    try {
      await operation.run(
        `/payments/${selected.id}/confirm`,
        { version: selected.version },
        'POST',
        true,
      );
      done();
    } catch {
      /* visible error */
    }
  }
  return (
    <>
      <div className="info-note">
        Заявленный платёж не уменьшает долг. Остаток меняется после подтверждения и распределения по
        счетам.
      </div>
      <Collection
        title="Платежи"
        endpoint={`/requests/${requestId}/payments`}
        fields={[
          {
            name: 'invoice_id',
            label: 'Счёт',
            type: 'select',
            source: `/requests/${requestId}/invoices`,
            labelKey: 'number',
          },
          ...paymentFields,
          {
            name: 'evidence_file_id',
            label: 'Подтверждающий файл',
            help: 'Идентификатор загруженного файла из раздела вложений.',
          },
        ]}
        createLabel="Заявить оплату"
        command
        onSelect={setSelected}
        refreshKey={revision}
        columns={[
          { key: 'number', label: 'Платёжный документ' },
          { key: 'payment_date', label: 'Дата', render: (r) => date(r.payment_date) },
          { key: 'amount', label: 'Сумма', render: (r) => `${decimal(r.amount)} ${r.currency}` },
          { key: 'status', label: 'Подтверждение', render: (r) => <Badge value={r.status} /> },
          { key: 'unallocated', label: 'Не распределено', render: (r) => decimal(r.unallocated) },
          { key: 'confirmed_at', label: 'Подтверждён', render: (r) => date(r.confirmed_at, true) },
        ]}
      />
      {selected && !action && (
        <Modal title={`Платёж ${selected.number}`} onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <ErrorBox error={operation.error} />
            <DetailPairs
              values={{
                Состояние: selected.status,
                Сумма: `${decimal(selected.amount)} ${selected.currency}`,
                Дата: date(selected.payment_date),
                'Не распределено': decimal(selected.unallocated),
                Комментарий: selected.comment,
                Автор: selected.declared_by,
                Подтвердил: selected.confirmed_by,
              }}
            />
            <div className="inline-actions">
              {selected.status === 'declared' ? (
                <Button busy={operation.busy} onClick={() => void confirm()}>
                  Подтвердить поступление
                </Button>
              ) : (
                <>
                  <Button onClick={() => setAction('allocate')}>Распределить</Button>
                  <Button variant="secondary" onClick={() => setAction('reverse')}>
                    Обратная операция
                  </Button>
                </>
              )}
            </div>
          </div>
        </Modal>
      )}
      {selected && action === 'allocate' && (
        <LineCommand
          title="Распределить платёж по счетам"
          endpoint={`/payments/${selected.id}/allocate`}
          rows={(invoices.data?.items || []).map((r) => ({
            ...r,
            name: String(r.number),
            quantity: undefined,
          }))}
          rowKey="invoice_id"
          arrayKey="allocations"
          amountKey="amount"
          labelText="Сумма распределения"
          extra={{ version: selected.version }}
          onClose={() => setAction(undefined)}
          onSuccess={done}
          note="Распределение разрешено между счетами одного клиента, продавца и валюты. Излишек остаётся авансом."
        />
      )}
      {selected && action === 'reverse' && (
        <RecordForm
          title="Обратная операция по платежу"
          endpoint={`/payments/${selected.id}/reverse`}
          extra={{ version: selected.version }}
          command
          fields={[
            {
              name: 'kind',
              label: 'Операция',
              type: 'select',
              required: true,
              options: [
                { value: 'unallocate', label: 'Снять распределение' },
                { value: 'refund', label: 'Возврат средств' },
                { value: 'correction', label: 'Корректировка' },
              ],
            },
            { name: 'amount', label: 'Сумма', required: true, type: 'decimal' },
            {
              name: 'allocation_id',
              label: 'Распределение',
              type: 'select',
              options: ((selected.allocations || []) as Entity[]).map((a) => ({
                value: a.id,
                label: `${a.invoice_id} · ${decimal(a.amount)}`,
              })),
            },
            { name: 'reason', label: 'Причина', required: true, type: 'textarea', minLength: 3 },
          ]}
          onClose={() => setAction(undefined)}
          onSuccess={done}
          note="История сохраняется. При уменьшении финансирования согласованного состава руководитель получит уведомление."
        />
      )}
    </>
  );
}
