import { useState } from 'react';
import { useAuth } from '../app/Auth';
import { Collection } from '../components/Collection';
import { RecordForm } from '../components/Form';
import {
  Button,
  DataTable,
  DetailPairs,
  ErrorBox,
  Loading,
  Modal,
  Section,
} from '../components/ui';
import { decimal } from '../lib/format';
import { useApi, useCommand } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';
import '../styles/manufacturers.scss';

export type Manufacturer = Entity & {
  internal_code: number;
  name: string;
  label: string;
  aliases: string[];
  umbrella_id: string | null;
  umbrella_name: string | null;
  nomenclatures: number;
  brand_members: number;
  merged_into_id: string | null;
  merged_into_name: string | null;
};
type DuplicateGroup = Entity & { manufacturers: Manufacturer[] };

/** One spelling per line, as people paste them from a supplier's list. */
function spellings(value: unknown): string[] {
  return String(value || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}
const umbrellaField: Field = {
  name: 'umbrella_id',
  label: 'Зонтичный бренд',
  type: 'select',
  source: '/manufacturers?umbrellas=true',
  labelKey: 'label',
  help: 'Например, Sigma-Aldrich и Supelco входят в бренд Merck. Аналитика складывает их по бренду.',
};
const manufacturerFields: Field[] = [
  { name: 'name', label: 'Название', required: true },
  {
    name: 'aliases',
    label: 'Другие написания',
    type: 'textarea',
    help: 'По одному в строке. Квоты и номенклатура с этими написаниями попадут к этому производителю.',
  },
  umbrellaField,
];
function asInput(values: Record<string, unknown>) {
  return { ...values, aliases: spellings(values.aliases), umbrella_id: values.umbrella_id || null };
}

/** Merge every other entry of a duplicate group into the one chosen as main. */
function DuplicateMerge({
  group,
  onClose,
  onDone,
}: {
  group: Manufacturer[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [mainId, setMainId] = useState(
    [...group].sort((a, b) => b.nomenclatures - a.nomenclatures)[0].id,
  );
  const [reason, setReason] = useState('Одно и то же написание производителя');
  const merge = useCommand();
  async function run() {
    try {
      for (const row of group.filter((entry) => entry.id !== mainId)) {
        const result = await merge.run(
          `/manufacturers/${row.id}/merge`,
          { version: row.version, target_id: mainId, reason },
          'POST',
          true,
        );
        if (!result) return;
      }
      onDone();
    } catch {
      /* the error is shown in the dialog */
    }
  }
  return (
    <Modal title="Объединить написания производителя" onClose={onClose}>
      <div className="form-body">
        <p className="info-note">
          Номенклатура и написания остальных записей перейдут к основной. Их номера останутся в
          истории, а в файлах квот будут находить основную запись.
        </p>
        <fieldset className="manufacturer-merge">
          <legend>Основная запись</legend>
          {group.map((row) => (
            <label key={row.id}>
              <input
                type="radio"
                name="main"
                checked={row.id === mainId}
                onChange={() => setMainId(row.id)}
              />
              {row.label} · номенклатур: {row.nomenclatures}
            </label>
          ))}
        </fieldset>
        <label className="field wide">
          Причина *
          <textarea value={reason} rows={2} onChange={(event) => setReason(event.target.value)} />
        </label>
        <ErrorBox error={merge.error} />
        <div className="inline-actions">
          <Button busy={merge.busy} disabled={reason.trim().length < 3} onClick={() => void run()}>
            Объединить
          </Button>
          <Button variant="secondary" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </div>
    </Modal>
  );
}

export function ManufacturerRegistry() {
  const auth = useAuth();
  const canWrite = auth.can('catalog.write');
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<Manufacturer>();
  const [action, setAction] = useState<'edit' | 'merge'>();
  const [group, setGroup] = useState<Manufacturer[]>();
  const duplicates = useApi<Page<DuplicateGroup>>(
    `/manufacturers/duplicates?ui_revision=${revision}`,
  );
  function done() {
    setSelected(undefined);
    setAction(undefined);
    setGroup(undefined);
    setRevision((value) => value + 1);
  }
  const groups = duplicates.data?.items || [];
  return (
    <>
      {groups.length > 0 && (
        <Section
          title="Похоже на одного производителя"
          description="Названия совпадают без учёта регистра, пробелов, знаков и слов вроде Pvt Ltd или одно начинается с другого. Проверьте и объедините."
        >
          <ErrorBox error={duplicates.error} retry={duplicates.refresh} />
          <div className="manufacturer-duplicates">
            {groups.map((item) => (
              <article key={item.id}>
                <ul>
                  {item.manufacturers.map((row) => (
                    <li key={row.id}>
                      <strong>{row.label}</strong>
                      <span>номенклатур: {row.nomenclatures}</span>
                    </li>
                  ))}
                </ul>
                {canWrite && (
                  <Button variant="secondary" onClick={() => setGroup(item.manufacturers)}>
                    Объединить
                  </Button>
                )}
              </article>
            ))}
          </div>
        </Section>
      )}
      {duplicates.loading && !duplicates.data && <Loading />}
      <Collection
        title="Реестр производителей"
        description="Номер производителя указывают в Excel-форме загрузки квоты. Новое название из квоты или номенклатуры добавляет производителя в реестр."
        endpoint="/manufacturers"
        refreshKey={revision}
        fields={manufacturerFields}
        transform={asInput}
        createLabel="Добавить производителя"
        canCreate={canWrite || auth.can('requests.write')}
        onSelect={(row) => setSelected(row as Manufacturer)}
        columns={[
          { key: 'internal_code', label: 'Номер' },
          {
            key: 'name',
            label: 'Производитель',
            render: (row) => <strong>{row.name as string}</strong>,
          },
          {
            key: 'umbrella_name',
            label: 'Зонтичный бренд',
            render: (row) =>
              row.umbrella_name
                ? String(row.umbrella_name)
                : Number(row.brand_members)
                  ? `Бренд · входят: ${row.brand_members}`
                  : '—',
          },
          {
            key: 'aliases',
            label: 'Другие написания',
            render: (row) => (row.aliases as string[]).join(', ') || '—',
          },
          { key: 'nomenclatures', label: 'Номенклатура' },
        ]}
      />
      {selected && !action && (
        <Modal title={selected.label} onClose={() => setSelected(undefined)}>
          <div className="form-body">
            <DetailPairs
              values={{
                Номер: selected.internal_code,
                Название: selected.name,
                'Зонтичный бренд': selected.umbrella_name || '—',
                'Входят в бренд': selected.brand_members || '—',
                'Другие написания': selected.aliases.join(', ') || '—',
                Номенклатура: selected.nomenclatures,
              }}
            />
            {canWrite && (
              <div className="inline-actions">
                <Button variant="secondary" onClick={() => setAction('edit')}>
                  Изменить
                </Button>
                <Button variant="secondary" onClick={() => setAction('merge')}>
                  Объединить с другим
                </Button>
              </div>
            )}
          </div>
        </Modal>
      )}
      {selected && action === 'edit' && (
        <RecordForm
          title={`Изменить: ${selected.name}`}
          endpoint={`/manufacturers/${selected.id}`}
          method="PATCH"
          initial={{ ...selected, aliases: selected.aliases.join('\n') }}
          extra={{ version: selected.version }}
          fields={manufacturerFields}
          transform={asInput}
          note="Прежнее название останется среди написаний: файлы и номенклатура с ним найдут этого производителя."
          onClose={() => setAction(undefined)}
          onSuccess={done}
        />
      )}
      {selected && action === 'merge' && (
        <RecordForm
          title={`Объединить «${selected.name}» с основной записью`}
          endpoint={`/manufacturers/${selected.id}/merge`}
          command
          extra={{ version: selected.version }}
          submitLabel="Объединить"
          fields={[
            {
              name: 'target_id',
              label: 'Основная запись',
              type: 'select',
              source: '/manufacturers',
              labelKey: 'label',
              required: true,
            },
            { name: 'reason', label: 'Причина', type: 'textarea', required: true, minLength: 3 },
          ]}
          note="Номенклатура, написания и производители бренда перейдут к основной записи. Эта запись останется в истории с отметкой об объединении."
          onClose={() => setAction(undefined)}
          onSuccess={done}
        />
      )}
      {group && <DuplicateMerge group={group} onClose={() => setGroup(undefined)} onDone={done} />}
    </>
  );
}

type ReportRow = Entity & {
  manufacturer_id: string | null;
  internal_code: number | null;
  name: string;
  umbrella_name: string | null;
  requests: number;
  positions: number;
  quoted_positions: number;
  ordered_positions: number;
  ordered_quantity: string;
  ordered_sales: string;
};

/** Requests and orders by manufacturer or umbrella brand for the analytics period. */
export function ManufacturerReport({ from, to }: { from: string; to: string }) {
  const [by, setBy] = useState<'manufacturer' | 'umbrella'>('manufacturer');
  const report = useApi<Page<ReportRow> & { basis: string }>(
    `/analytics/manufacturers?date_from=${from}&date_to=${to}&by=${by}`,
  );
  return (
    <Section
      title="Производители и бренды"
      description={report.data?.basis}
      action={
        <div className="segmented-text" role="group" aria-label="Разрез">
          <button
            type="button"
            className={by === 'manufacturer' ? 'active' : ''}
            aria-pressed={by === 'manufacturer'}
            onClick={() => setBy('manufacturer')}
          >
            По производителям
          </button>
          <button
            type="button"
            className={by === 'umbrella' ? 'active' : ''}
            aria-pressed={by === 'umbrella'}
            onClick={() => setBy('umbrella')}
          >
            По зонтичным брендам
          </button>
        </div>
      }
    >
      <ErrorBox error={report.error} retry={report.refresh} />
      {report.loading ? (
        <Loading />
      ) : (
        <DataTable<ReportRow>
          rows={report.data?.items || []}
          columns={[
            {
              key: 'name',
              label: by === 'umbrella' ? 'Бренд' : 'Производитель',
              render: (row) => (
                <span className="stacked">
                  <strong>
                    {row.internal_code ? `${row.internal_code} · ` : ''}
                    {row.name}
                  </strong>
                  {row.umbrella_name && <small>{row.umbrella_name}</small>}
                </span>
              ),
            },
            { key: 'requests', label: 'Заявок' },
            { key: 'positions', label: 'Позиций в запросах' },
            { key: 'quoted_positions', label: 'С ценой поставщика' },
            { key: 'ordered_positions', label: 'Заказано позиций' },
            {
              key: 'ordered_quantity',
              label: 'Заказано, шт.',
              render: (row) => decimal(row.ordered_quantity),
            },
            {
              key: 'ordered_sales',
              label: 'Сумма заказов, ₽',
              render: (row) => decimal(row.ordered_sales),
            },
          ]}
        />
      )}
    </Section>
  );
}
