import {useState} from 'react';

// Native shadcn/ui registry primitives. Theme and size variants live in
// ui-primitives.css and form-controls.css alongside their DOM adapters.
function Button({variant = 'outline', type = 'button', ...props}) {
  return <button type={type} data-slot="button" data-variant={variant} {...props} />;
}

function Badge({tone, children}) {
  return <span data-slot="badge" data-variant="outline" data-tone={tone}>{children}</span>;
}

export function ColumnChooser({columns, available, onSave}) {
  const [rows, setRows] = useState(() => [
    ...columns.map(([key, label]) => ({key, label, checked: true})),
    ...available.filter(([key]) => !columns.some(([active]) => active === key))
      .map(([key, label]) => ({key, label, checked: false})),
  ]);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState('');
  const names = Object.fromEntries(available);
  function change(index, patch) {
    setStatus('');
    setRows(previous => previous.map((row, i) => i === index ? {...row, ...patch} : row));
  }
  function move(index, direction) {
    setStatus('');
    setRows(previous => {
      const next = previous.slice();
      next.splice(index + direction, 0, next.splice(index, 1)[0]);
      return next;
    });
  }
  async function save(event) {
    event.preventDefault();
    const active = rows.filter(row => row.checked);
    if (!active.length || active.some(row => !row.label.trim())) {
      setStatus('Выберите хотя бы один столбец и укажите подпись для каждого выбранного.');
      return;
    }
    setSaving(true);
    try {
      await onSave(active.map(row => [row.key, row.label.trim()]));
      setStatus('Вид таблицы сохранён.');
    } catch (error) {
      setStatus(error.message || 'Не удалось сохранить вид таблицы.');
    } finally {
      setSaving(false);
    }
  }
  return <form id="view-form" onSubmit={save}>
    <fieldset className="column-choices" disabled={saving}>
      <legend>Столбцы и порядок</legend>
      {rows.map((row, index) => <div className="column-choice" key={row.key}>
        <label className="check" data-slot="label">
          <input type="checkbox" checked={row.checked} onChange={e => change(index, {checked: e.target.checked})}/>
          {names[row.key] || row.key}
        </label>
        <input type="text" data-slot="input" aria-label={'Подпись столбца «' + (names[row.key] || row.key) + '»'}
          disabled={!row.checked} value={row.label} onChange={e => change(index, {label: e.target.value})}/>
        <div className="column-move">
          <Button aria-label={'Переместить выше: ' + row.label} disabled={index === 0} onClick={() => move(index, -1)}>↑</Button>
          <Button aria-label={'Переместить ниже: ' + row.label} disabled={index === rows.length - 1} onClick={() => move(index, 1)}>↓</Button>
        </div>
      </div>)}
    </fieldset>
    <Button type="submit" disabled={saving}>Сохранить вид</Button>
    <p role="status">{status}</p>
  </form>;
}

const statuses = {
  usable: ['Пригодны', 'Пригодно', 'success'],
  flagged: ['С флагами качества', 'Флаг качества', 'danger'],
  empty: ['Пустые значения', 'Пустое значение', 'warning'],
  no_column: ['Без параметра', 'Нет параметра', 'warning'],
};

export function QualityRows({rows, formatValue}) {
  const [filter, setFilter] = useState('all');
  const counts = rows.reduce((totals, row) => {
    totals[row.status] = (totals[row.status] || 0) + 1;
    return totals;
  }, {});
  const visible = rows.filter(row => filter === 'all' || row.status === filter);
  return <>
    <label className="qc-row-filter" data-slot="label">Статус наблюдения
      <select value={filter} onChange={event => setFilter(event.target.value)}>
        <option value="all">Все наблюдения · {rows.length}</option>
        {Object.entries(counts).map(([key, count]) =>
          <option key={key} value={key}>{statuses[key]?.[0] || key} · {count}</option>)}
      </select>
    </label>
    <div className="table-wrap" data-slot="table-container">
      <table aria-label="Исходные наблюдения и флаги качества" data-slot="table">
        <thead data-slot="table-header"><tr data-slot="table-row">
          <th scope="col" data-slot="table-head">Время / источник</th>
          <th scope="col" data-slot="table-head">Значение / качество</th>
        </tr></thead>
        <tbody data-slot="table-body">{visible.map((row, index) => <tr data-slot="table-row" key={row.id ?? index}>
          <td data-slot="table-cell">{row.original_stamp}<br/><small>{row.source_file}:{row.source_row} · {row.zone === 'unknown' ? 'пояс неизвестен' : row.zone}</small></td>
          <td data-slot="table-cell">{formatValue(row.value)}
            {row.received_at && <small> · Принято {row.received_at}</small>}
            <div className="qc-flags">{row.reasons.length
              ? row.reasons.map(reason => <Badge key={reason.code} tone={statuses[row.status]?.[2] || 'neutral'}>{reason.label}</Badge>)
              : <Badge tone={statuses[row.status]?.[2] || 'neutral'}>{statuses[row.status]?.[1] || row.status}</Badge>}
            </div>
          </td>
        </tr>)}</tbody>
      </table>
      {!visible.length && <p role="status">За эти сутки исходные строки отсутствуют.</p>}
    </div>
  </>;
}
