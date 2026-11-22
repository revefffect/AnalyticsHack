// DOM adapter for shadcn/ui's MIT-licensed Table; shares its semantic element
// and data-slot contract with the official Tasks data-table example.
// https://ui.shadcn.com/r/styles/new-york-v4/table.json
export function stationDataTable({label, columns, className}) {
  const container = document.createElement('div');
  container.dataset.slot = 'table-container';
  container.className = 'atlas-table-scroll';

  const table = document.createElement('table');
  table.dataset.slot = 'table';
  if (className) table.className = className;
  table.setAttribute('aria-label', label);
  container.append(table);

  const header = table.createTHead();
  header.dataset.slot = 'table-header';
  const head = header.insertRow();
  head.dataset.slot = 'table-row';
  for (const column of columns) {
    const cell = document.createElement('th');
    cell.dataset.slot = 'table-head';
    cell.scope = 'col';
    cell.textContent = typeof column === 'string' ? column : column.label;
    if (column.className) cell.className = column.className;
    head.append(cell);
  }

  const body = table.createTBody();
  body.dataset.slot = 'table-body';
  return {container, table, head, body};
}
