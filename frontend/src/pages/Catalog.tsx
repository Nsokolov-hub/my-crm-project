import { useState } from 'react';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import { Badge, PageHeading } from '../components/ui';
import { productFields } from '../lib/fields';
import type { Entity } from '../lib/types';
export function Catalog() {
  const [selected, setSelected] = useState<Entity>();
  const [revision, setRevision] = useState(0);
  return (
    <>
      <PageHeading
        title="Номенклатура"
        description="Вещества и товарные варианты: производитель, чистота, фасовка и спецификация."
      />
      <Collection
        title="Товарные варианты"
        endpoint="/catalog/products"
        fields={productFields}
        createLabel="Добавить товар"
        refreshKey={revision}
        onSelect={setSelected}
        columns={[
          {
            key: 'name',
            label: 'Наименование',
            render: (r) => (
              <span className="stacked">
                <strong>{String(r.name)}</strong>
                <small>{String(r.article || 'Без артикула')}</small>
              </span>
            ),
          },
          { key: 'cas', label: 'CAS' },
          { key: 'manufacturer', label: 'Производитель' },
          { key: 'purity', label: 'Чистота / марка' },
          { key: 'packaging', label: 'Фасовка' },
          { key: 'unit', label: 'Единица' },
          {
            key: 'verified',
            label: 'Проверка',
            render: (r) => <Badge value={r.verified ? 'verified' : 'requires_review'} />,
          },
        ]}
      />
      {selected && (
        <RecordForm
          title={`Проверить товар: ${selected.name}`}
          endpoint={`/catalog/products/${selected.id}/verify`}
          fields={[
            {
              name: 'reason',
              label: 'Основание проверки соответствия и характеристик',
              type: 'textarea',
              required: true,
              minLength: 3,
            },
          ]}
          extra={{ version: selected.version }}
          command
          note="Контрольная цифра CAS проверяет формат. Подтвердите соответствие номера веществу и полноту характеристик по первоисточнику."
          onClose={() => setSelected(undefined)}
          onSuccess={() => {
            setSelected(undefined);
            setRevision((v) => v + 1);
          }}
        />
      )}
    </>
  );
}
