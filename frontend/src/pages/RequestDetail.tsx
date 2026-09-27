import { ArrowLeft, Pencil, Plus } from 'lucide-react';
import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import type { Entity, Page } from '../lib/types';
import { requestEditFields, taskFields } from '../lib/fields';
import { date } from '../lib/format';
import {
  Badge,
  Button,
  DetailPairs,
  ErrorBox,
  Loading,
  PageHeading,
  Section,
} from '../components/ui';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import { RequestRfqs, RequestQuotes } from './RequestProcurement';
import { RequestCalculations } from './RequestCalculations';
import { RequestItemEditor } from './RequestItemEditor';
import { RequestDocuments } from './RequestDocuments';
import { RequestPayments } from './RequestPayments';
import { RequestFulfillment } from './Fulfillment';
import { Chats, FilesPanel } from './Communication';

const tabs = [
  ['overview', 'Обзор'],
  ['items', 'Позиции запроса'],
  ['rfqs', 'Запросы поставщикам'],
  ['quotes', 'Квоты'],
  ['calculations', 'Расчёты'],
  ['documents', 'КП и счета'],
  ['payments', 'Оплаты'],
  ['fulfillment', 'Исполнение'],
  ['history', 'История и обсуждение'],
];
export function RequestDetail() {
  const { id = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'overview';
  const request = useApi<Entity>(`/requests/${id}`);
  const [edit, setEdit] = useState(false);
  const [item, setItem] = useState<Entity>();
  const [addingItem, setAddingItem] = useState(false);
  const [task, setTask] = useState(false);
  const [revision, setRevision] = useState(0);
  const [selectedItemIds, setSelectedItemIds] = useState<Set<string>>(new Set());
  const [rfqItemIds, setRfqItemIds] = useState<string[]>([]);
  const [quoteItemIds, setQuoteItemIds] = useState<string[]>([]);
  const counts = useApi<Page>(`/requests/${id}/items?ui_revision=${revision}`);
  const refresh = () => {
    request.refresh();
    counts.refresh();
    setRevision((v) => v + 1);
  };
  if (request.loading) return <Loading />;
  if (request.error) return <ErrorBox error={request.error} retry={request.refresh} />;
  if (!request.data) return null;
  const row = request.data;
  return (
    <>
      <Link className="back-link" to="/requests">
        <ArrowLeft size={16} />
        Все заявки
      </Link>
      <PageHeading
        eyebrow={String(row.number)}
        title={String(row.title)}
        description={String(row.client_name || '')}
        actions={
          <>
            <Badge value={row.commercial_stage} />
            <Button variant="secondary" onClick={() => setEdit(true)}>
              <Pencil size={16} />
              Изменить
            </Button>
          </>
        }
      />
      <nav className="tabs" aria-label="Разделы заявки">
        {tabs.map(([key, title]) => (
          <button
            key={key}
            className={tab === key ? 'active' : ''}
            onClick={() => {
              setParams({ tab: key });
              request.refresh();
            }}
          >
            {title}
            {key === 'items' && <span>{counts.data?.total ?? 0}</span>}
          </button>
        ))}
      </nav>
      {tab === 'overview' && (
        <>
          <div className="dashboard-columns">
            <Section title="О заявке">
              <DetailPairs
                values={{
                  Клиент: row.client_name,
                  Ответственный: row.owner_name,
                  Создана: date(row.created_at, true),
                  'Желаемый срок': date(row.due_at),
                  'Коммерческий этап': row.commercial_stage,
                  'Номер версии': row.version,
                }}
              />
            </Section>
            <Section
              title="Следующее действие"
              action={
                <Button variant="secondary" onClick={() => setTask(true)}>
                  <Plus size={15} />
                  Задача
                </Button>
              }
            >
              {row.next_task ? (
                <DetailPairs
                  values={{
                    Задача: (row.next_task as Entity).title,
                    Срок: date((row.next_task as Entity).due_at, true),
                  }}
                />
              ) : (
                <p className="form-body muted">Следующее действие ещё не назначено.</p>
              )}
            </Section>
          </div>
          <Section
            title="Документы и исполнение"
            description="Откройте этап, чтобы перейти к связанным записям."
          >
            <div className="workflow-links">
              {tabs.slice(2, 8).map(([key, title], i) => (
                <button key={key} onClick={() => setParams({ tab: key })}>
                  <span>{String(i + 1).padStart(2, '0')}</span>
                  <strong>{title}</strong>
                </button>
              ))}
            </div>
          </Section>
          <FilesPanel entityType="request" entityId={id} />
        </>
      )}
      {tab === 'items' && (
        <>
          <div className="tab-actions">
            <span className="selection-count">Выбрано позиций: {selectedItemIds.size}</span>
            <Button variant="secondary" onClick={() => setAddingItem(true)}>
              <Plus size={16} /> Добавить позицию
            </Button>
            <Button
              disabled={selectedItemIds.size === 0}
              title={selectedItemIds.size ? undefined : 'Сначала выберите позиции заявки'}
              onClick={() => {
                setRfqItemIds([...selectedItemIds]);
                setParams({ tab: 'rfqs' });
              }}
            >
              Создать запрос поставщику
            </Button>
          </div>
          <Collection
            title="Потребность клиента"
            onChanged={refresh}
            endpoint={`/requests/${id}/items`}
            refreshKey={revision}
            pageSize={100}
            selection={{
              selectedIds: selectedItemIds,
              onToggle: (itemId) =>
                setSelectedItemIds((current) => {
                  const next = new Set(current);
                  if (next.has(itemId)) next.delete(itemId);
                  else next.add(itemId);
                  return next;
                }),
              onSelectPage: (ids, checked) =>
                setSelectedItemIds((current) => {
                  const next = new Set(current);
                  ids.forEach((itemId) => (checked ? next.add(itemId) : next.delete(itemId)));
                  return next;
                }),
            }}
            onSelect={setItem}
            columns={[
              {
                key: 'description',
                label: 'Номенклатура',
                render: (r) => String(r.nomenclature_name || r.description),
              },
              {
                key: 'packing_name',
                label: 'Фасовка',
                render: (r) => String(r.packing_name || r.packaging || '—'),
              },
              { key: 'quantity', label: 'Количество' },
              { key: 'unit', label: 'Единица' },
              { key: 'cas', label: 'CAS' },
              { key: 'revision', label: 'Редакция' },
              { key: 'archived', label: 'В архиве' },
            ]}
          />
        </>
      )}
      {tab === 'rfqs' && (
        <RequestRfqs
          requestId={id}
          requestNumber={String(row.number || '')}
          launchItemIds={rfqItemIds}
          onLaunchConsumed={() => setRfqItemIds([])}
        />
      )}
      {tab === 'quotes' && (
        <RequestQuotes
          requestId={id}
          onCalculate={(ids) => {
            setQuoteItemIds(ids);
            setParams({ tab: 'calculations' });
          }}
        />
      )}
      {tab === 'calculations' && (
        <RequestCalculations
          request={row}
          launchQuoteItemIds={quoteItemIds}
          onLaunchConsumed={() => setQuoteItemIds([])}
        />
      )}
      {tab === 'documents' && <RequestDocuments requestId={id} />}
      {tab === 'payments' && <RequestPayments requestId={id} />}
      {tab === 'fulfillment' && <RequestFulfillment requestId={id} />}
      {tab === 'history' && (
        <>
          <Collection
            title="История изменений"
            endpoint={`/requests/${id}/history`}
            columns={[
              { key: 'created_at', label: 'Дата', render: (r) => date(r.created_at, true) },
              { key: 'entity_type', label: 'Объект' },
              { key: 'action', label: 'Действие' },
              { key: 'reason', label: 'Основание' },
            ]}
          />
          <Chats entityType="request" entityId={id} />
        </>
      )}
      {edit && (
        <RecordForm
          title="Изменить заявку"
          endpoint={`/requests/${id}`}
          method="PATCH"
          initial={row}
          extra={{ version: row.version }}
          fields={requestEditFields}
          onClose={() => setEdit(false)}
          onSuccess={() => {
            setEdit(false);
            refresh();
          }}
        />
      )}
      {addingItem && (
        <RequestItemEditor
          requestId={id}
          onClose={() => setAddingItem(false)}
          onSaved={() => {
            setAddingItem(false);
            refresh();
          }}
        />
      )}
      {item && (
        <RequestItemEditor
          requestId={id}
          initial={item}
          onClose={() => setItem(undefined)}
          onSaved={() => {
            setItem(undefined);
            refresh();
          }}
        />
      )}
      {task && (
        <RecordForm
          title="Следующее действие"
          endpoint="/tasks"
          fields={taskFields.filter((f) => !['entity_type', 'entity_id'].includes(f.name))}
          extra={{ entity_type: 'request', entity_id: id }}
          onClose={() => setTask(false)}
          onSuccess={() => {
            setTask(false);
            refresh();
          }}
        />
      )}
    </>
  );
}
