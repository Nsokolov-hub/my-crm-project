import { Search } from 'lucide-react';
import { useState } from 'react';
import { useCommand, useDirtyProtection } from '../lib/hooks';
import type { Entity } from '../lib/types';
import { Button, ErrorBox, Modal } from './ui';
import { label } from '../lib/format';
export function LineCommand({
  title,
  endpoint,
  rows,
  rowKey = 'id',
  arrayKey = 'lines',
  extra = {},
  onClose,
  onSuccess,
  note,
  amountKey = 'quantity',
  labelText = 'Количество',
  reasonRequired = false,
  includeAnalogue = false,
  additional,
}: {
  title: string;
  endpoint: string;
  rows: Entity[];
  rowKey?: string;
  arrayKey?: string;
  extra?: Record<string, unknown>;
  onClose: () => void;
  onSuccess: () => void;
  note?: string;
  amountKey?: string;
  labelText?: string;
  reasonRequired?: boolean;
  includeAnalogue?: boolean;
  additional?: React.ReactNode;
}) {
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [reason, setReason] = useState('');
  const [analogues, setAnalogues] = useState<Record<string, string>>({});
  const [filter, setFilter] = useState('');
  const operation = useCommand();
  // Business remark of 11 November 2026: positions are chosen all at once, cleared and found by name.
  const text = filter.trim().toLocaleLowerCase('ru');
  const shown = text
    ? rows.filter((row) =>
        [label(row), row.article, row.description]
          .map((value) => String(value || '').toLocaleLowerCase('ru'))
          .some((value) => value.includes(text)),
      )
    : rows;
  const chosen = Object.keys(selected).length;
  function choose(list: Entity[], checked: boolean) {
    setSelected((current) => {
      const next = { ...current };
      for (const row of list) {
        if (checked) next[row.id] = next[row.id] ?? String(row.quantity || '');
        else delete next[row.id];
      }
      return next;
    });
  }
  const dirty = Object.keys(selected).length > 0 || Boolean(reason);
  useDirtyProtection(dirty);
  function close() {
    if (!dirty || window.confirm('Закрыть форму без сохранения?')) onClose();
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const lines = Object.entries(selected)
      .filter(([, quantity]) => quantity)
      .map(([id, quantity]) => ({
        [rowKey]: id,
        [amountKey]: quantity,
        ...(includeAnalogue ? { analogue_reason: analogues[id] || '' } : {}),
      }));
    try {
      await operation.run(
        endpoint,
        { ...extra, [arrayKey]: lines, ...(reasonRequired ? { reason } : {}) },
        'POST',
        true,
      );
      onSuccess();
    } catch {
      /* form retains values and operation key */
    }
  }
  return (
    <Modal title={title} wide onClose={close}>
      <form onSubmit={submit}>
        <div className="form-body">
          {note && <div className="info-note">{note}</div>}
          <ErrorBox error={operation.error} />
          {additional}
          {rows.length > 1 && (
            <div className="selection-toolbar">
              <Button
                type="button"
                variant="secondary"
                disabled={shown.every((row) => row.id in selected)}
                onClick={() => choose(shown, true)}
              >
                {text ? `Выбрать найденные (${shown.length})` : `Выбрать все (${rows.length})`}
              </Button>
              <Button
                type="button"
                variant="ghost"
                disabled={!chosen}
                onClick={() => (text ? choose(shown, false) : setSelected({}))}
              >
                {text ? 'Снять найденные' : 'Снять все'}
              </Button>
              {rows.length > 5 && (
                <div className="search-input">
                  <Search size={16} />
                  <input
                    aria-label="Найти позицию"
                    placeholder="Найти позицию…"
                    value={filter}
                    onChange={(event) => setFilter(event.target.value)}
                  />
                </div>
              )}
              <span className="selection-count" aria-live="polite">
                Выбрано: {chosen} из {rows.length}
              </span>
            </div>
          )}
          <div className="selection-lines">
            {shown.map((row) => (
              <div className={`selection-line${row.id in selected ? ' chosen' : ''}`} key={row.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={row.id in selected}
                    onChange={(e) =>
                      setSelected((v) => {
                        const next = { ...v };
                        if (e.target.checked) next[row.id] = String(row.quantity || '');
                        else delete next[row.id];
                        return next;
                      })
                    }
                  />
                  <span>
                    <strong>{label(row)}</strong>
                    <small>
                      {[
                        row.quantity != null && row.quantity !== ''
                          ? `${String(row.quantity)} ${String(row.unit || '')}`.trim()
                          : String(row.unit || row.currency || ''),
                        row.total != null && row.total !== '' ? `сумма ${String(row.total)}` : '',
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </small>
                  </span>
                </label>
                {row.id in selected && (
                  <div className="selection-line-fields">
                    <label>
                      {labelText}
                      <input
                        required
                        inputMode="decimal"
                        pattern="[0-9]+([.,][0-9]{1,8})?"
                        value={selected[row.id]}
                        onChange={(e) =>
                          setSelected((v) => ({ ...v, [row.id]: e.target.value.replace(',', '.') }))
                        }
                      />
                    </label>
                    {includeAnalogue && (
                      <label>
                        Основание принятия аналога
                        <input
                          value={analogues[row.id] || ''}
                          onChange={(e) =>
                            setAnalogues((v) => ({ ...v, [row.id]: e.target.value }))
                          }
                        />
                      </label>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
          {reasonRequired && (
            <label className="field">
              Основание решения
              <textarea
                required
                minLength={3}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
          )}
        </div>
        <div className="modal-footer">
          <Button variant="secondary" type="button" onClick={close}>
            Отмена
          </Button>
          <Button type="submit" busy={operation.busy} disabled={!Object.keys(selected).length}>
            Подтвердить
          </Button>
        </div>
      </form>
    </Modal>
  );
}
