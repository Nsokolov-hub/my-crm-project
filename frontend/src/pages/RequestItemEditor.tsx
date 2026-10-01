import { Plus } from 'lucide-react';
import { useState } from 'react';
import { DirectorySelect } from '../components/Form';
import { Button, DetailPairs, ErrorBox, Modal } from '../components/ui';
import { useApi, useCommand, useDirtyProtection } from '../lib/hooks';
import type { Entity, Field, Page } from '../lib/types';

const nomenclatureField: Field = {
  name: 'nomenclature_id',
  label: 'Номенклатура',
  type: 'select',
  source: '/nomenclatures',
  labelKey: 'name',
};

export function RequestItemEditor({
  requestId,
  initial,
  onClose,
  onSaved,
}: {
  requestId: string;
  initial?: Entity;
  onClose: () => void;
  onSaved: () => void;
}) {
  const groups = useApi<Page>('/product-groups?page_size=100');
  const command = useCommand();
  const [nomenclatureId, setNomenclatureId] = useState(String(initial?.nomenclature_id || ''));
  const selectedNomenclature = useApi<Entity>(
    nomenclatureId ? `/nomenclatures/${nomenclatureId}` : null,
  );
  const [packingId, setPackingId] = useState(String(initial?.packing_id || ''));
  const [quantity, setQuantity] = useState(String(initial ? (initial.quantity ?? '') : '1'));
  const [rawUnit, setRawUnit] = useState(String(initial?.unit || 'pcs'));
  const [rawArticle, setRawArticle] = useState(String(initial?.article || ''));
  const [description, setDescription] = useState(String(initial?.description || ''));
  const [reason, setReason] = useState('');
  const [allowAnalogue, setAllowAnalogue] = useState(Boolean(initial?.allow_analogue));
  const [archived, setArchived] = useState(Boolean(initial?.archived));
  const [quickMode, setQuickMode] = useState<'nomenclature' | 'packing' | ''>('');
  const [newName, setNewName] = useState('');
  const [groupId, setGroupId] = useState('');
  const [packingValue, setPackingValue] = useState('');
  const [packingUnit, setPackingUnit] = useState('mg');
  const [createdName, setCreatedName] = useState<Entity>();
  const [createdPacking, setCreatedPacking] = useState<Entity>();
  const [localError, setLocalError] = useState<unknown>();
  const [dirty, setDirty] = useState(false);
  useDirtyProtection(dirty);
  const selectedName =
    createdName?.id === nomenclatureId
      ? createdName
      : selectedNomenclature.data?.id === nomenclatureId
        ? selectedNomenclature.data
        : undefined;
  const packings = [...((selectedName?.packings || []) as Entity[])];
  if (createdPacking && !packings.some((row) => row.id === createdPacking.id))
    packings.push(createdPacking);
  function close() {
    if (!dirty || window.confirm('Есть несохранённые изменения. Закрыть форму?')) onClose();
  }
  async function createReference() {
    if (
      !packingValue.trim() ||
      !Number.isFinite(Number(packingValue)) ||
      Number(packingValue) <= 0
    ) {
      setLocalError(new Error('Укажите положительное значение фасовки.'));
      return;
    }
    if (quickMode === 'nomenclature' && (!newName.trim() || !groupId)) {
      setLocalError(new Error('Укажите название и товарную группу номенклатуры.'));
      return;
    }
    try {
      if (quickMode === 'nomenclature') {
        const result = await command.run<Entity>(
          '/nomenclatures',
          {
            name: newName.trim(),
            product_group_id: groupId,
            packings: [{ value: packingValue.replace(',', '.'), unit: packingUnit }],
          },
          'POST',
        );
        if (!result) return;
        setCreatedName(result);
        setNomenclatureId(result.id);
        setPackingId(String(((result.packings || []) as Entity[])[0]?.id || ''));
        setDescription((current) => current || String(result.name));
      } else {
        const result = await command.run<Entity>(
          '/nomenclatures/' + nomenclatureId + '/packings',
          {
            value: packingValue.replace(',', '.'),
            unit: packingUnit,
          },
          'POST',
        );
        if (!result) return;
        setCreatedPacking(result);
        setPackingId(result.id);
      }
      setDirty(true);
      setQuickMode('');
      setLocalError(undefined);
      selectedNomenclature.refresh();
    } catch {
      /* Ошибка остаётся видимой в форме. */
    }
  }
  async function save() {
    if (
      nomenclatureId &&
      (!packingId || !Number.isInteger(Number(quantity)) || Number(quantity) <= 0)
    ) {
      setLocalError(new Error('Выберите номенклатуру, фасовку и целое количество больше нуля.'));
      return;
    }
    if (
      !nomenclatureId &&
      (!description.trim() ||
        (quantity &&
          (!Number.isFinite(Number(quantity.replace(',', '.'))) ||
            Number(quantity.replace(',', '.')) <= 0)))
    ) {
      setLocalError(new Error('Заполните исходное наименование и проверьте количество.'));
      return;
    }
    if (initial && !reason.trim()) {
      setLocalError(new Error('Укажите причину изменения позиции.'));
      return;
    }
    try {
      const result = await command.run<Entity>(
        initial ? '/request-items/' + initial.id : '/requests/' + requestId + '/items',
        {
          nomenclature_id: nomenclatureId || null,
          packing_id: packingId || null,
          quantity: quantity ? quantity.replace(',', '.') : null,
          unit: nomenclatureId ? 'pcs' : rawUnit,
          ...(!nomenclatureId ? { article: rawArticle || null } : {}),
          description: description.trim() || String(selectedName?.name || ''),
          allow_analogue: allowAnalogue,
          ...(initial ? { version: initial.version, reason: reason.trim(), archived } : {}),
        },
        initial ? 'PATCH' : 'POST',
      );
      if (result) onSaved();
    } catch {
      /* Ошибка остаётся видимой в форме. */
    }
  }
  return (
    <Modal
      title={initial ? 'Изменить позицию заявки' : 'Новая позиция заявки'}
      wide
      onClose={close}
    >
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="form-body request-item-editor">
          {Array.isArray(initial?.source_columns) && initial.source_columns.length > 0 && (
            <details>
              <summary>Исходная строка клиента</summary>
              <DetailPairs
                values={Object.fromEntries(
                  (initial.source_columns as string[]).map((column, index) => [
                    column,
                    (initial.source_values as string[])[index],
                  ]),
                )}
              />
            </details>
          )}
          <p className="muted">
            {nomenclatureId
              ? 'Количество указывается в единицах выбранной фасовки. Например, 3 × 100 mg.'
              : 'Исходную потребность можно сохранить без номенклатуры. Уточните товар после ответа поставщика.'}
          </p>
          <ErrorBox
            error={localError || command.error || selectedNomenclature.error || groups.error}
          />
          <div className="form-grid">
            {!nomenclatureId && (
              <>
                <label className="field">
                  Артикул клиента
                  <input
                    value={rawArticle}
                    maxLength={150}
                    onChange={(event) => {
                      setRawArticle(event.target.value);
                      setDirty(true);
                    }}
                  />
                </label>
                <label className="field">
                  Единица количества
                  <select
                    value={rawUnit}
                    onChange={(event) => {
                      setRawUnit(event.target.value);
                      setDirty(true);
                    }}
                  >
                    {['pcs', 'g', 'kg', 'mg', 'ml', 'l'].map((unit) => (
                      <option key={unit} value={unit}>
                        {unit === 'pcs' ? 'шт.' : unit}
                      </option>
                    ))}
                  </select>
                </label>
              </>
            )}
            <div className="field">
              <label htmlFor="field-nomenclature_id">Номенклатура</label>
              <DirectorySelect
                field={nomenclatureField}
                value={nomenclatureId}
                onChange={(value) => {
                  setNomenclatureId(String(value));
                  setPackingId('');
                  setCreatedPacking(undefined);
                  setDirty(true);
                }}
              />
            </div>
            <label className="field">
              Фасовка
              <select
                value={packingId}
                disabled={!nomenclatureId}
                onChange={(event) => {
                  setPackingId(event.target.value);
                  setDirty(true);
                }}
              >
                <option value="">Выберите фасовку</option>
                {packings.map((row) => (
                  <option key={row.id} value={row.id}>
                    {String(row.display_name || `${row.value} ${row.unit}`)}
                  </option>
                ))}
              </select>
            </label>
            <div className="quick-create-actions">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setQuickMode(quickMode === 'nomenclature' ? '' : 'nomenclature')}
              >
                <Plus size={14} /> Создать номенклатуру
              </Button>
              <Button
                type="button"
                variant="ghost"
                disabled={!nomenclatureId}
                onClick={() => setQuickMode(quickMode === 'packing' ? '' : 'packing')}
              >
                <Plus size={14} /> Добавить фасовку
              </Button>
            </div>
            {quickMode && (
              <div className="quick-create-panel">
                {quickMode === 'nomenclature' && (
                  <>
                    <label>
                      Название{' '}
                      <input value={newName} onChange={(event) => setNewName(event.target.value)} />
                    </label>
                    <label>
                      Группа{' '}
                      <select value={groupId} onChange={(event) => setGroupId(event.target.value)}>
                        <option value="">Выберите группу</option>
                        {groups.data?.items.map((row) => (
                          <option key={row.id} value={row.id}>
                            {String(row.name)}
                          </option>
                        ))}
                      </select>
                    </label>
                  </>
                )}
                <label>
                  Значение фасовки{' '}
                  <input
                    inputMode="decimal"
                    value={packingValue}
                    onChange={(event) => setPackingValue(event.target.value)}
                  />
                </label>
                <label>
                  Единица{' '}
                  <select
                    value={packingUnit}
                    onChange={(event) => setPackingUnit(event.target.value)}
                  >
                    {['mg', 'g', 'kg', 'ml', 'l', 'pcs'].map((unit) => (
                      <option key={unit} value={unit}>
                        {unit}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  type="button"
                  variant="secondary"
                  busy={command.busy}
                  onClick={() => void createReference()}
                >
                  Создать и выбрать
                </Button>
              </div>
            )}
            <label className="field">
              Количество фасовок
              <input
                type="number"
                min="1"
                step="1"
                value={quantity}
                onChange={(event) => {
                  setQuantity(event.target.value);
                  setDirty(true);
                }}
              />
            </label>
            <label className="field">
              Единица <input value="шт. выбранной фасовки" disabled />
            </label>
            <label className="field wide">
              Исходное наименование клиента
              <input
                value={description}
                onChange={(event) => {
                  setDescription(event.target.value);
                  setDirty(true);
                }}
              />
            </label>
            <label className="field checkbox-field">
              <input
                type="checkbox"
                checked={allowAnalogue}
                onChange={(event) => {
                  setAllowAnalogue(event.target.checked);
                  setDirty(true);
                }}
              />{' '}
              Допустим аналог
            </label>
            {initial && (
              <>
                <label className="field checkbox-field">
                  <input
                    type="checkbox"
                    checked={archived}
                    onChange={(event) => {
                      setArchived(event.target.checked);
                      setDirty(true);
                    }}
                  />{' '}
                  Архивировать позицию
                </label>
                <label className="field wide">
                  Причина изменения
                  <textarea
                    rows={2}
                    value={reason}
                    onChange={(event) => {
                      setReason(event.target.value);
                      setDirty(true);
                    }}
                  />
                </label>
              </>
            )}
          </div>
        </div>
        <div className="modal-footer">
          <Button type="button" variant="secondary" onClick={close}>
            Отмена
          </Button>
          <Button type="submit" busy={command.busy}>
            Сохранить позицию
          </Button>
        </div>
      </form>
    </Modal>
  );
}
