import { useState } from 'react';
import { useAuth } from '../app/Auth';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import { Badge, Button, DataTable, DetailPairs, Modal, PageHeading } from '../components/ui';
import { date, decimal, nowLocal } from '../lib/format';
import { useApi } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';
export function RequestFulfillment({ requestId }: { requestId: string }) {
  const auth = useAuth();
  const [approval, setApproval] = useState<Entity>();
  const [execution, setExecution] = useState<Entity>();
  const [revision, setRevision] = useState(0);
  const [executionAction, setExecutionAction] = useState<'allocate' | 'revise' | 'cancel'>();
  function executionDone() {
    setExecution(undefined);
    setExecutionAction(undefined);
    setRevision((v) => v + 1);
  }
  return (
    <>
      <Collection
        title="Позиции исполнения"
        description="Принятое, распределённое, отправленное и доставленное количество учитываются раздельно."
        endpoint={`/requests/${requestId}/executions`}
        refreshKey={revision}
        onSelect={setExecution}
        columns={[
          {
            key: 'item_id',
            label: 'Позиция',
            render: (r) => String((r.snapshot as Entity)?.description || r.item_id),
          },
          { key: 'quantity', label: 'Принято', render: (r) => `${decimal(r.quantity)} ${r.unit}` },
          { key: 'allocated', label: 'В волнах', render: (r) => decimal(r.allocated) },
          { key: 'shipped', label: 'Отправлено', render: (r) => decimal(r.shipped) },
          { key: 'delivered', label: 'Доставлено', render: (r) => decimal(r.delivered) },
          {
            key: 'financing_deficit',
            label: 'Финансирование',
            render: (r) => (
              <Badge
                value={r.financing_deficit ? 'Дефицит' : 'Без дефицита'}
                tone={r.financing_deficit ? 'red' : 'green'}
              />
            ),
          },
        ]}
      />
      <Collection
        title="Согласование запуска"
        endpoint={`/requests/${requestId}/approvals`}
        fields={[
          {
            name: 'execution_ids',
            label: 'Позиции исполнения',
            required: true,
            type: 'multiselect',
            source: `/requests/${requestId}/executions`,
            wide: true,
          },
          {
            name: 'reviewer_id',
            label: 'Руководитель',
            required: true,
            type: 'select',
            source: '/users',
          },
        ]}
        command
        createLabel="Передать на согласование"
        refreshKey={revision}
        onSelect={setApproval}
        columns={[
          { key: 'id', label: 'Согласование', render: (r) => `Решение · ${r.id.slice(0, 8)}` },
          { key: 'status', label: 'Состояние', render: (r) => <Badge value={r.status} /> },
          { key: 'created_at', label: 'Передано', render: (r) => date(r.created_at, true) },
          { key: 'reviewer_id', label: 'Руководитель' },
          { key: 'reason', label: 'Комментарий' },
        ]}
      />
      {approval && (
        <ApprovalDecision
          row={approval}
          onClose={() => setApproval(undefined)}
          onSuccess={() => {
            setApproval(undefined);
            setRevision((v) => v + 1);
          }}
        />
      )}
      {execution && !executionAction && (
        <Modal title="Позиция исполнения" onClose={() => setExecution(undefined)}>
          <div className="form-body">
            <DetailPairs
              values={{
                Принято: `${decimal(execution.quantity)} ${execution.unit}`,
                'В волнах': decimal(execution.allocated),
                Отправлено: decimal(execution.shipped),
                Доставлено: decimal(execution.delivered),
                'Версия состава': execution.revision,
                'Дефицит финансирования': execution.financing_deficit,
              }}
            />
            <div className="inline-actions">
              <Button onClick={() => setExecutionAction('allocate')}>Распределить в волну</Button>
              {auth.can('documents.write') && (
                <>
                  <Button variant="secondary" onClick={() => setExecutionAction('revise')}>
                    Изменить количество
                  </Button>
                  <Button variant="secondary" onClick={() => setExecutionAction('cancel')}>
                    Отменить принятую позицию
                  </Button>
                </>
              )}
            </div>
          </div>
        </Modal>
      )}
      {execution && executionAction === 'allocate' && (
        <WaveAssignment
          requestId={requestId}
          execution={execution}
          onClose={() => setExecutionAction(undefined)}
          onSuccess={executionDone}
        />
      )}
      {execution && executionAction === 'revise' && (
        <RecordForm
          title="Исправить принятое количество"
          endpoint={`/executions/${execution.id}/revise`}
          command
          extra={{ version: execution.version }}
          fields={[
            {
              name: 'quantity',
              label: `Новое количество (${execution.unit})`,
              type: 'decimal',
              required: true,
              value: execution.quantity,
            },
            { name: 'reason', label: 'Причина', type: 'textarea', required: true, minLength: 3 },
          ]}
          note="Ранее принятая версия сохранится. Для оплаченного счёта сначала снимите распределение оплаты, затем аннулируйте счёт."
          onClose={() => setExecutionAction(undefined)}
          onSuccess={executionDone}
        />
      )}
      {execution && executionAction === 'cancel' && (
        <RecordForm
          title="Отменить принятую позицию"
          endpoint={`/executions/${execution.id}/cancel`}
          command
          extra={{ version: execution.version }}
          fields={[
            { name: 'reason', label: 'Причина', type: 'textarea', required: true, minLength: 3 },
          ]}
          note="Операция оставит запись в истории. Активные счета и распределения по волнам нужно исправить до отмены позиции."
          onClose={() => setExecutionAction(undefined)}
          onSuccess={executionDone}
        />
      )}
    </>
  );
}
function WaveAssignment({
  requestId,
  execution,
  onClose,
  onSuccess,
}: {
  requestId: string;
  execution: Entity;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [wave, setWave] = useState('');
  const [next, setNext] = useState(false);
  const waves = useApi<Page>('/waves');
  return !next ? (
    <Modal title="Выбрать волну" onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setNext(true);
        }}
      >
        <div className="form-body">
          <label className="field">
            Открытая волна
            <select required value={wave} onChange={(e) => setWave(e.target.value)}>
              <option value="">Выберите волну</option>
              {waves.data?.items
                .filter((w) => ['planned', 'assembling'].includes(String(w.status)))
                .map((w) => (
                  <option key={w.id} value={w.id}>
                    {String(w.number)} · {String(w.route)}
                  </option>
                ))}
            </select>
          </label>
        </div>
        <div className="modal-footer">
          <Button variant="secondary" type="button" onClick={onClose}>
            Отмена
          </Button>
          <Button type="submit">Далее</Button>
        </div>
      </form>
    </Modal>
  ) : (
    <RecordForm
      title="Распределить количество в волну"
      endpoint={`/waves/${wave}/allocations`}
      fields={[
        {
          name: 'approval_id',
          label: 'Действующее согласование',
          required: true,
          type: 'select',
          source: `/requests/${requestId}/approvals`,
        },
        { name: 'quantity', label: 'Количество', type: 'decimal', required: true },
      ]}
      extra={{ execution_id: execution.id }}
      command
      onClose={() => setNext(false)}
      onSuccess={onSuccess}
    />
  );
}
export function ApprovalDecision({
  row,
  onClose,
  onSuccess,
}: {
  row: Entity;
  onClose: () => void;
  onSuccess: () => void;
}) {
  return (
    <RecordForm
      title="Решение по составу исполнения"
      endpoint={`/approvals/${row.id}/decision`}
      extra={{ version: row.version }}
      command
      fields={[
        {
          name: 'decision',
          label: 'Решение',
          type: 'select',
          required: true,
          options: [
            { value: 'approved', label: 'Согласовать' },
            { value: 'returned', label: 'Вернуть на доработку' },
            { value: 'rejected', label: 'Отклонить' },
          ],
        },
        {
          name: 'reason',
          label: 'Комментарий к решению',
          type: 'textarea',
          help: 'Обязательно при возврате и отклонении.',
        },
      ]}
      note="Решение относится к конкретной версии состава и не распространяется на последующие изменения."
      onClose={onClose}
      onSuccess={onSuccess}
    />
  );
}
const waveFields: Field[] = [
  { name: 'number', label: 'Номер волны', required: true },
  { name: 'route', label: 'Маршрут', required: true },
  { name: 'origin_country', label: 'Страна отправления', required: true },
  { name: 'owner_id', label: 'Ответственный', type: 'select', source: '/users', required: true },
  { name: 'close_date', label: 'Закрытие для добавления', type: 'date', required: true },
  { name: 'departure_date', label: 'Плановая отправка', type: 'date', required: true },
  { name: 'arrival_date', label: 'Плановое прибытие', type: 'date', required: true },
  { name: 'details', label: 'Дополнительные параметры', type: 'json', value: {} },
];
export function Waves() {
  const [selected, setSelected] = useState<Entity>();
  const [editing, setEditing] = useState(false);
  const [allocation, setAllocation] = useState<Entity>();
  const [allocationAction, setAllocationAction] = useState<'event' | 'transfer'>();
  const [revision, setRevision] = useState(0);
  function done() {
    setSelected(undefined);
    setEditing(false);
    setAllocation(undefined);
    setAllocationAction(undefined);
    setRevision((v) => v + 1);
  }
  return (
    <>
      <PageHeading
        title="Волны поставок"
        description="Объединяйте согласованные позиции и отслеживайте движение каждой партии."
      />
      <Collection
        title="Расписание волн"
        endpoint="/waves"
        fields={waveFields}
        command
        createLabel="Новая волна"
        refreshKey={revision}
        onSelect={setSelected}
        columns={[
          { key: 'number', label: 'Волна' },
          { key: 'route', label: 'Маршрут' },
          { key: 'status', label: 'Состояние', render: (r) => <Badge value={r.status} /> },
          { key: 'close_date', label: 'Приём до', render: (r) => date(r.close_date) },
          { key: 'departure_date', label: 'Отправка', render: (r) => date(r.departure_date) },
          { key: 'arrival_date', label: 'Прибытие', render: (r) => date(r.arrival_date) },
        ]}
      />
      {selected && !editing && !allocationAction && (
        <Modal title={`Волна ${selected.number}`} wide onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <DetailPairs
              values={{
                Маршрут: selected.route,
                Статус: selected.status,
                'Приём до': date(selected.close_date),
                Отправка: date(selected.departure_date),
                Прибытие: date(selected.arrival_date),
              }}
            />
            <Button variant="secondary" onClick={() => setEditing(true)}>
              Изменить сроки и состояние
            </Button>
            <h3>Распределённые позиции</h3>
            <DataTable
              rows={(selected.allocations || []) as Entity[]}
              onRow={(row) => {
                setAllocation(row);
                setAllocationAction('event');
              }}
              columns={[
                { key: 'execution_id', label: 'Позиция' },
                { key: 'quantity', label: 'Количество', render: (r) => decimal(r.quantity) },
                { key: 'shipped', label: 'Отправлено', render: (r) => decimal(r.shipped) },
                { key: 'delivered', label: 'Доставлено', render: (r) => decimal(r.delivered) },
                {
                  key: 'actions',
                  label: 'Действие',
                  render: (r) => (
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setAllocation(r);
                        setAllocationAction('transfer');
                      }}
                    >
                      Перенести
                    </Button>
                  ),
                },
              ]}
            />
          </div>
        </Modal>
      )}
      {selected && editing && (
        <RecordForm
          title="Изменить волну"
          endpoint={`/waves/${selected.id}`}
          method="PATCH"
          command
          initial={selected}
          extra={{ version: selected.version }}
          fields={[
            {
              name: 'status',
              label: 'Состояние',
              type: 'select',
              options: [
                { value: 'planned', label: 'Планируется' },
                { value: 'assembling', label: 'Комплектуется' },
                { value: 'closed', label: 'Закрыта для добавления' },
                { value: 'shipped', label: 'Отправлена' },
                { value: 'arrived', label: 'Прибыла' },
                { value: 'completed', label: 'Завершена' },
                { value: 'cancelled', label: 'Отменена' },
              ],
            },
            ...waveFields.filter((f) =>
              ['close_date', 'departure_date', 'arrival_date'].includes(f.name),
            ),
            {
              name: 'reason',
              label: 'Причина изменения',
              type: 'textarea',
              required: true,
              minLength: 3,
            },
          ]}
          onClose={() => setEditing(false)}
          onSuccess={done}
        />
      )}{' '}
      {allocation && allocationAction === 'event' && (
        <RecordForm
          title="Зафиксировать событие исполнения"
          endpoint={`/allocations/${allocation.id}/events`}
          command
          fields={[
            {
              name: 'kind',
              label: 'Событие',
              required: true,
              type: 'select',
              options: [
                { value: 'ordered', label: 'Заказ подтверждён' },
                { value: 'ready', label: 'Готовность' },
                { value: 'shipped', label: 'Отправлено' },
                { value: 'arrived', label: 'Прибыло' },
                { value: 'delivered', label: 'Передано клиенту' },
                { value: 'claim_opened', label: 'Открыта претензия' },
                { value: 'claim_resolved', label: 'Претензия закрыта' },
                { value: 'correction', label: 'Корректировка' },
              ],
            },
            { name: 'quantity', label: 'Фактическое количество', type: 'decimal', required: true },
            {
              name: 'occurred_at',
              label: 'Дата события',
              type: 'datetime-local',
              required: true,
              value: nowLocal(),
            },
            {
              name: 'reason',
              label: 'Основание / комментарий',
              required: true,
              type: 'textarea',
              minLength: 3,
            },
            { name: 'evidence_file_id', label: 'Подтверждающий файл (ID)' },
            { name: 'correction_of', label: 'ID корректируемого события' },
          ]}
          onClose={() => setAllocationAction(undefined)}
          onSuccess={done}
        />
      )}{' '}
      {allocation && allocationAction === 'transfer' && (
        <RecordForm
          title="Перенести плановое количество"
          endpoint={`/allocations/${allocation.id}/transfer`}
          extra={{ version: allocation.version }}
          command
          fields={[
            {
              name: 'target_wave_id',
              label: 'Целевая волна',
              required: true,
              type: 'select',
              source: '/waves',
            },
            { name: 'quantity', label: 'Количество', type: 'decimal', required: true },
            {
              name: 'reason',
              label: 'Причина переноса',
              type: 'textarea',
              required: true,
              minLength: 3,
            },
          ]}
          note="Отправленное количество перенести нельзя. Перенос сохраняет историю распределения."
          onClose={() => setAllocationAction(undefined)}
          onSuccess={done}
        />
      )}
    </>
  );
}
