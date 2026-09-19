import { ArrowLeft, Pencil, Plus } from 'lucide-react';
import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { useApi } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';
import { itemFields, requestEditFields, taskFields } from '../lib/fields';
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
  const [task, setTask] = useState(false);
  const [revision, setRevision] = useState(0);
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
        <Collection
          title="Потребность клиента"
          onChanged={refresh}
          endpoint={`/requests/${id}/items`}
          fields={itemFields}
          createLabel="Добавить позицию"
          refreshKey={revision}
          onSelect={setItem}
          columns={[
            { key: 'description', label: 'Исходное наименование' },
            { key: 'cas', label: 'CAS' },
            { key: 'quantity', label: 'Количество' },
            { key: 'unit', label: 'Единица' },
            { key: 'purity', label: 'Чистота' },
            { key: 'packaging', label: 'Фасовка' },
            { key: 'revision', label: 'Редакция' },
            { key: 'archived', label: 'В архиве' },
          ]}
        />
      )}
      {tab === 'rfqs' && <RequestRfqs requestId={id} />}
      {tab === 'quotes' && <RequestQuotes requestId={id} />}
      {tab === 'calculations' && <RequestCalculations request={row} />}
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
      {item && (
        <RecordForm
          title="Новая редакция позиции"
          endpoint={`/request-items/${item.id}`}
          method="PATCH"
          initial={item}
          extra={{ version: item.version }}
          fields={
            [
              ...itemFields,
              { name: 'archived', label: 'Архивировать позицию', type: 'checkbox' },
              { name: 'reason', label: 'Причина изменения', required: true, type: 'textarea' },
            ] as Field[]
          }
          onClose={() => setItem(undefined)}
          onSuccess={() => {
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
