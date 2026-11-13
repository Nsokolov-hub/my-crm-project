import { useId, useMemo, useState } from 'react';
import { X } from 'lucide-react';

/**
 * Выбор из длинного списка по первым буквам (замечание бизнеса 13.11.2026: поставщиков много, выпадающий список
 * неудобен). Сначала варианты, где с набранного начинается название или любое его слово («хим» → ООО «ТД»
 * ХИММЕД), затем те, где оно встречается внутри. Enter выбирает первый вариант, крестик сбрасывает выбор.
 */
export function SearchSelect({
  label,
  value,
  options,
  onChange,
  allLabel,
}: {
  /** Подпись для экранного диктора, как aria-label у прежнего списка */
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  /** Пустое значение: «Все поставщики» */
  allLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [active, setActive] = useState(0);
  const listId = useId();
  const matches = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    if (!query) return options;
    const first: string[] = [];
    const inside: string[] = [];
    for (const option of options) {
      const text = option.toLocaleLowerCase();
      if (
        text.startsWith(query) ||
        text.split(/[^\p{L}\p{N}]+/u).some((word) => word.startsWith(query))
      )
        first.push(option);
      else if (text.includes(query)) inside.push(option);
    }
    return [...first, ...inside];
  }, [options, search]);
  const choices = search.trim() ? matches : ['', ...matches];

  function choose(option: string) {
    onChange(option);
    setSearch('');
    setOpen(false);
  }

  return (
    <div
      className="directory-select search-select"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <input
        role="combobox"
        aria-label={label}
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listId}
        placeholder={allLabel}
        value={open ? search : value}
        onFocus={() => {
          setSearch('');
          setActive(0);
          setOpen(true);
        }}
        onChange={(event) => {
          setSearch(event.target.value);
          setActive(0);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            setOpen(false);
            return;
          }
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setOpen(true);
            const step = event.key === 'ArrowDown' ? 1 : -1;
            setActive((index) =>
              Math.min(Math.max(index + step, 0), Math.max(choices.length - 1, 0)),
            );
          }
          if (event.key === 'Enter' && open && choices.length) {
            event.preventDefault();
            choose(choices[Math.min(active, choices.length - 1)]);
          }
        }}
      />
      {value && !open && (
        <button
          type="button"
          className="search-select-clear"
          aria-label={`Сбросить: ${label}`}
          onClick={() => onChange('')}
        >
          <X size={14} />
        </button>
      )}
      {open && (
        <div className="directory-options" id={listId} role="listbox">
          {choices.map((option, index) => (
            <button
              type="button"
              key={option || '__all__'}
              role="option"
              aria-selected={index === active || (!search && option === value)}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(option)}
            >
              {option || allLabel}
            </button>
          ))}
          {!choices.length && <p>Совпадений нет</p>}
        </div>
      )}
    </div>
  );
}
