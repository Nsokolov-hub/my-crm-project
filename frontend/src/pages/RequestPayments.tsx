import { useState } from 'react';
import { useAuth } from '../app/Auth';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import { LineCommand } from '../components/LineCommand';
import { Badge, Button, DetailPairs, ErrorBox, Modal } from '../components/ui';
import { date, decimal } from '../lib/format';
import { paymentFields } from '../lib/fields';
import { useApi, useCommand } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
export function RequestPayments({ requestId }: { requestId: string }) {
  const auth = useAuth();
  const [selected, setSelected] = useState<Entity>();
  const [action, setAction] = useState<'allocate' | 'reverse'>();
  const [revision, setRevision] = useState(0);
  const invoices = useApi<Page>(`/requests/${requestId}/invoices`);
  const currencies = useApi<Page>(
    auth.can('payments.write') ? `/requests/${requestId}/payment-currencies` : null,
  );
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
        счетам. Позиции переводятся в работу отдельно, во вкладке «Исполнение».
      </div>
      <ErrorBox error={currencies.error} retry={currencies.refresh} />
      <Collection
        title="Платежи"
        endpoint={`/requests/${requestId}/payments`}
        fields={[
          {
            name: 'invoice_id',
            label: 'Счёт',
            type: 'select',
            options: (invoices.data?.items || [])
              .filter((invoice) => invoice.status !== 'cancelled')
              .map((invoice) => ({
                value: invoice.id,
                label: `${invoice.number} · ${invoice.currency}`,
              })),
          },
          ...paymentFields.map((field) =>
            field.name === 'currency'
              ? {
                  ...field,
                  options: (currencies.data?.items || []).map((currency) => ({
                    value: String(currency.code || currency.id).toUpperCase(),
                    label: String(currency.name || currency.code || currency.id),
                  })),
                  help: 'При выборе счёта валюта подставляется автоматически.',
                }
              : field,
          ),
          {
            name: 'evidence_file_id',
            label: 'Подтверждающий файл',
            help: 'Идентификатор загруженного файла из раздела вложений.',
          },
        ]}
        createLabel="Заявить оплату"
        canCreate={auth.can('payments.write')}
        command
        deriveValues={(values, changedField) => {
          if (changedField !== 'invoice_id') return values;
          const invoice = invoices.data?.items.find((row) => row.id === values.invoice_id);
          return invoice ? { ...values, currency: invoice.currency } : values;
        }}
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
            {auth.can('payments.confirm') && (
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
            )}
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
