import { Download, Pencil } from 'lucide-react';
import { useState } from 'react';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import { Badge, Button, DetailPairs, ErrorBox, Modal } from '../components/ui';
import { download } from '../lib/api';
import { date, decimal, nowLocal, units } from '../lib/format';
import { useApi } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';
export function RequestRfqs({ requestId }: { requestId: string }) {
  const [selected, setSelected] = useState<Entity>();
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<unknown>();
  const [revision, setRevision] = useState(0);
  const fields: Field[] = [
    {
      name: 'supplier_id',
      label: 'Поставщик',
      required: true,
      type: 'select',
      source: '/counterparties?kind=supplier',
    },
    {
      name: 'item_ids',
      label: 'Позиции запроса',
      required: true,
      type: 'multiselect',
      source: `/requests/${requestId}/items`,
      labelKey: 'description',
      wide: true,
    },
    { name: 'response_due', label: 'Ответ до', required: true, type: 'date' },
    { name: 'comment', label: 'Комментарий поставщику', type: 'textarea' },
  ];
  return (
    <>
      <ErrorBox error={error} />
      <Collection
        title="Запросы поставщикам"
        description="Отдельный файл для каждого поставщика. Отправка фиксируется после передачи файла."
        endpoint={`/requests/${requestId}/rfqs`}
        fields={fields}
        createLabel="Создать запрос"
        command
        refreshKey={revision}
        onSelect={setSelected}
        columns={[
          { key: 'id', label: 'Запрос', render: (r) => `Запрос · ${r.id.slice(0, 8)}` },
          {
            key: 'supplier_id',
            label: 'Поставщик',
            render: (r) => String(r.supplier_name || r.supplier_id),
          },
          { key: 'revision', label: 'Редакция' },
          { key: 'created_at', label: 'Создан', render: (r) => date(r.created_at) },
          {
            key: 'sent_at',
            label: 'Отправка',
            render: (r) => (r.sent_at ? date(r.sent_at, true) : <Badge value="draft" />),
          },
          {
            key: 'file',
            label: 'Файл',
            sortable: false,
            render: (r) => (
              <Button
                variant="ghost"
                onClick={() =>
                  void download(`/rfqs/${r.id}/file`, `Запрос_${r.id.slice(0, 8)}.xlsx`).catch(
                    setError,
                  )
                }
              >
                <Download size={16} />
                XLSX
              </Button>
            ),
          },
        ]}
      />
      {selected && !sending && (
        <Modal title="Запрос поставщику" onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <DetailPairs
              values={{
                Редакция: selected.revision,
                Создан: date(selected.created_at),
                Отправлен: date(selected.sent_at, true),
                Канал: selected.sent_channel,
              }}
            />
            <Button onClick={() => setSending(true)}>Отметить отправку</Button>
          </div>
        </Modal>
      )}
      {selected && sending && (
        <RecordForm
          title="Отметить отправку запроса"
          endpoint={`/rfqs/${selected.id}/sent`}
          fields={[
            { name: 'channel', label: 'Канал и адрес получателя', required: true },
            {
              name: 'sent_at',
              label: 'Дата и время отправки',
              type: 'datetime-local',
              required: true,
              value: nowLocal(),
            },
          ]}
          command
          extra={{ version: selected.version }}
          onClose={() => setSending(false)}
          onSuccess={() => {
            setSelected(undefined);
            setSending(false);
            setRevision((v) => v + 1);
          }}
        />
      )}
    </>
  );
}
export function RequestQuotes({ requestId }: { requestId: string }) {
  const [selected, setSelected] = useState<Entity>();
  const [editing, setEditing] = useState(false);
  const [revision, setRevision] = useState(0);
  const items = useApi<Page>(`/requests/${requestId}/items`);
  const quoteFields: Field[] = [
    {
      name: 'item_id',
      label: 'Позиция потребности',
      type: 'select',
      source: `/requests/${requestId}/items`,
      labelKey: 'description',
      required: true,
    },
    {
      name: 'supplier_id',
      label: 'Поставщик',
      type: 'select',
      source: '/counterparties?kind=supplier',
      required: true,
    },
    {
      name: 'product_id',
      label: 'Товарный вариант',
      type: 'select',
      source: '/catalog/products',
      required: true,
    },
    {
      name: 'supplier_request_id',
      label: 'Запрос поставщику',
      type: 'select',
      source: `/requests/${requestId}/rfqs`,
    },
    { name: 'price', label: 'Закупочная цена', type: 'decimal', required: true },
    { name: 'currency', label: 'Валюта квоты', required: true, placeholder: 'USD' },
    { name: 'price_unit', label: 'Единица цены', required: true, type: 'select', options: units },
    { name: 'available_quantity', label: 'Доступное количество', required: true, type: 'decimal' },
    { name: 'minimum_quantity', label: 'Минимальный заказ', type: 'decimal', value: '0' },
    { name: 'multiple', label: 'Кратность заказа', type: 'decimal', value: '0.000001' },
    { name: 'valid_until', label: 'Цена действует до', type: 'date' },
    { name: 'requires_confirmation', label: 'Цена требует подтверждения', type: 'checkbox' },
    { name: 'is_analogue', label: 'Предлагается аналог', type: 'checkbox' },
    { name: 'sample', label: 'Бесплатный образец', type: 'checkbox' },
    {
      name: 'terms',
      label: 'Условия и первоисточник',
      type: 'json',
      value: {},
      help: 'Срок готовности, базис и место поставки, порядок оплаты, ссылка на исходный документ.',
    },
  ];
  function transform(body: Record<string, unknown>) {
    return {
      ...body,
      item_revision: items.data?.items.find((item) => item.id === body.item_id)?.revision || 1,
    };
  }
  return (
    <>
      <Collection
        title="Квоты поставщиков"
        description="Сравните характеристики, сроки и доступные цены. Для КП выбирается один поставщик и одна исходная валюта."
        endpoint={`/requests/${requestId}/quotes`}
        fields={quoteFields}
        createLabel="Добавить квоту"
        command
        transform={transform}
        refreshKey={revision}
        onSelect={setSelected}
        columns={[
          {
            key: 'product',
            label: 'Товар',
            render: (r) => (
              <span className="stacked">
                <strong>{String((r.product as Entity)?.name || r.product_id)}</strong>
                <small>
                  {r.is_analogue ? 'Аналог · требуется решение клиента' : 'Заявленная позиция'}
                </small>
              </span>
            ),
          },
          {
            key: 'supplier_id',
            label: 'Поставщик',
            render: (r) => String(r.supplier_name || r.supplier_id),
          },
          {
            key: 'price',
            label: 'Цена закупки',
            render: (r) =>
              r.price !== undefined
                ? `${decimal(r.price)} ${r.currency} / ${r.price_unit}`
                : 'Нет доступа',
          },
          {
            key: 'available_quantity',
            label: 'Доступно',
            render: (r) => `${decimal(r.available_quantity)} ${r.price_unit}`,
          },
          {
            key: 'valid_until',
            label: 'Действует до',
            render: (r) => (
              <span
                className={
                  r.valid_until && new Date(String(r.valid_until)) < new Date() ? 'overdue' : ''
                }
              >
                {date(r.valid_until)}
              </span>
            ),
          },
          { key: 'revision', label: 'Версия' },
        ]}
      />
      {selected && !editing && (
        <Modal title="Предложение поставщика" wide onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <DetailPairs
              values={{
                Товар: (selected.product as Entity)?.name,
                Поставщик: selected.supplier_name || selected.supplier_id,
                Количество: `${decimal(selected.available_quantity)} ${selected.price_unit}`,
                Цена:
                  selected.price === undefined
                    ? 'Нет доступа'
                    : `${decimal(selected.price)} ${selected.currency}`,
                'Действует до': date(selected.valid_until),
                Редакция: selected.revision,
                Аналог: selected.is_analogue,
                Условия: selected.terms,
              }}
            />
            <Button variant="secondary" onClick={() => setEditing(true)}>
              <Pencil size={15} />
              Новая редакция квоты
            </Button>
          </div>
        </Modal>
      )}
      {selected && editing && (
        <RecordForm
          title="Новая редакция квоты"
          endpoint={`/quotes/${selected.id}/revise`}
          fields={[
            ...quoteFields,
            {
              name: 'revision_reason',
              label: 'Причина новой редакции',
              type: 'textarea',
              required: true,
              minLength: 3,
            },
          ]}
          command
          initial={selected}
          transform={transform}
          onClose={() => setEditing(false)}
          onSuccess={() => {
            setSelected(undefined);
            setEditing(false);
            setRevision((v) => v + 1);
          }}
        />
      )}
    </>
  );
}
