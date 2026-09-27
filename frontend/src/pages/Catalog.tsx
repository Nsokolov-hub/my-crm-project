import { Plus } from 'lucide-react';
import { useState } from 'react';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import { Badge, Button, DataTable, DetailPairs, ErrorBox, Loading, Modal, PageHeading, Pagination, Section } from '../components/ui';
import { api } from '../lib/api';
import { productFields } from '../lib/fields';
import { useApi, useDebounced } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';

type Packing = Entity & { value: string; unit: string; display_name: string };
type Nomenclature = Entity & {
  name: string;
  article?: string;
  product_group_name?: string;
  cas?: string;
  linear_formula?: string;
  description?: string;
  packings: Packing[];
};

const nomenclatureFields: Field[] = [
  { name: 'name', label: 'Название', required: true, wide: true },
  { name: 'article', label: 'Артикул' },
  { name: 'product_group_id', label: 'Товарная группа', type: 'select', source: '/product-groups' },
  { name: 'cas', label: 'CAS' },
  { name: 'linear_formula', label: 'Линейная формула' },
  { name: 'description', label: 'Описание', type: 'textarea' },
  { name: 'packing_value', label: 'Первая фасовка — значение', type: 'decimal', required: true },
  { name: 'packing_unit', label: 'Первая фасовка — единица', required: true, placeholder: 'mg, g, ml…' },
];
const packingFields: Field[] = [
  { name: 'value', label: 'Значение', type: 'decimal', required: true },
  { name: 'unit', label: 'Единица', required: true, placeholder: 'mg, g, ml…' },
  { name: 'display_name', label: 'Название для показа' },
];

export function Catalog() {
  const [tab, setTab] = useState<'nomenclature' | 'legacy'>('nomenclature');
  const [search, setSearch] = useState('');
  const debounced = useDebounced(search, 300);
  const [page, setPage] = useState(1);
  const list = useApi<Page<Nomenclature>>(`/nomenclatures?page=${page}&page_size=25&q=${encodeURIComponent(debounced)}`);
  const [selected, setSelected] = useState<Nomenclature>();
  const [creating, setCreating] = useState(false);
  const [addingPacking, setAddingPacking] = useState(false);
  const [legacySelected, setLegacySelected] = useState<Entity>();
  const [legacyRevision, setLegacyRevision] = useState(0);
  const [error, setError] = useState<unknown>();

  async function refreshSelected(id: string) {
    try {
      setSelected(await api<Nomenclature>(`/nomenclatures/${id}`));
      list.refresh();
    } catch (cause) {
      setError(cause);
    }
  }

  return (
    <>
      <PageHeading
        title="Номенклатура"
        description="Товары, группы и фасовки хранятся отдельно, чтобы квоты и расчёты ссылались на точную позицию."
        actions={tab === 'nomenclature' && <Button onClick={() => setCreating(true)}><Plus size={16} /> Добавить номенклатуру</Button>}
      />
      <div className="tabs">
        <button className={tab === 'nomenclature' ? 'active' : ''} onClick={() => setTab('nomenclature')}>Номенклатура и фасовки</button>
        <button className={tab === 'legacy' ? 'active' : ''} onClick={() => setTab('legacy')}>Ранее созданные товарные варианты</button>
      </div>
      {tab === 'nomenclature' ? (
        <Section title="Справочник номенклатуры">
          <ErrorBox error={error || list.error} retry={list.refresh} />
          <input
            aria-label="Поиск номенклатуры"
            placeholder="Поиск по названию или артикулу"
            value={search}
            onChange={(event) => { setSearch(event.target.value); setPage(1); }}
          />
          {list.loading ? <Loading /> : <>
            <DataTable<Nomenclature>
              rows={list.data?.items || []}
              onRow={setSelected}
              columns={[
                { key: 'name', label: 'Название', render: (row) => <strong>{row.name}</strong> },
                { key: 'article', label: 'Артикул', render: (row) => row.article || '—' },
                { key: 'group', label: 'Группа', render: (row) => row.product_group_name || 'Другое' },
                { key: 'cas', label: 'CAS', render: (row) => row.cas || '—' },
                { key: 'formula', label: 'Линейная формула', render: (row) => row.linear_formula || '—' },
                { key: 'packings', label: 'Фасовки', render: (row) => row.packings.map((packing) => packing.display_name).join(', ') || '—' },
              ]}
            />
            <Pagination page={page} total={list.data?.total || 0} onChange={setPage} />
          </>}
        </Section>
      ) : (
        <Collection
          title="Товарные варианты предыдущей модели"
          endpoint="/catalog/products"
          fields={productFields}
          createLabel="Добавить товарный вариант"
          refreshKey={legacyRevision}
          onSelect={setLegacySelected}
          columns={[
            { key: 'name', label: 'Наименование', render: (row) => String(row.name) },
            { key: 'cas', label: 'CAS' },
            { key: 'manufacturer', label: 'Производитель' },
            { key: 'packaging', label: 'Фасовка' },
            { key: 'verified', label: 'Проверка', render: (row) => <Badge value={row.verified ? 'verified' : 'requires_review'} /> },
          ]}
        />
      )}
      {creating && <RecordForm
        title="Новая номенклатура"
        endpoint="/nomenclatures"
        fields={nomenclatureFields}
        transform={(values) => {
          const { packing_value, packing_unit, ...item } = values;
          return { ...item, packings: [{ value: packing_value, unit: packing_unit }] };
        }}
        onClose={() => setCreating(false)}
        onSuccess={(row) => { setCreating(false); setSelected(row as Nomenclature); list.refresh(); }}
      />}
      {selected && <Modal title={selected.name} wide onClose={() => { setAddingPacking(false); setSelected(undefined); }}>
        <div className="form-body">
          <DetailPairs values={{
            Артикул: selected.article || '—',
            'Товарная группа': selected.product_group_name || 'Другое',
            CAS: selected.cas || '—',
            'Линейная формула': selected.linear_formula || '—',
            Описание: selected.description || '—',
          }} />
          <Section title="Фасовки" action={<Button onClick={() => setAddingPacking(true)}><Plus size={16} /> Добавить фасовку</Button>}>
            <DataTable<Packing>
              rows={selected.packings || []}
              columns={[
                { key: 'display_name', label: 'Фасовка' },
                { key: 'value', label: 'Значение' },
                { key: 'unit', label: 'Единица' },
              ]}
            />
          </Section>
        </div>
      </Modal>}
      {addingPacking && selected && <RecordForm
        title={`Добавить фасовку для ${selected.name}`}
        endpoint={`/nomenclatures/${selected.id}/packings`}
        fields={packingFields}
        onClose={() => setAddingPacking(false)}
        onSuccess={() => { setAddingPacking(false); void refreshSelected(selected.id); }}
      />}
      {legacySelected && <RecordForm
        title={`Проверить товар: ${legacySelected.name}`}
        endpoint={`/catalog/products/${legacySelected.id}/verify`}
        fields={[{ name: 'reason', label: 'Основание проверки соответствия и характеристик', type: 'textarea', required: true, minLength: 3 }]}
        extra={{ version: legacySelected.version }}
        command
        onClose={() => setLegacySelected(undefined)}
        onSuccess={() => { setLegacySelected(undefined); setLegacyRevision((value) => value + 1); }}
      />}
    </>
  );
}
