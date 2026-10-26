(() => {
  function initialize() {
    for (const [tableIndex, table] of [...document.querySelectorAll('main .table-scroll > table')].entries()) {
      const headers = [...table.querySelectorAll('thead th')];
      if (!headers.length) continue;
      table.id ||= `experiment-document-table-${tableIndex + 1}`;
      table.classList.add('docs-data-table');
      table.parentElement.classList.add('docs-table-container');
      table.setAttribute('role', 'table');
      const labels = headers.map((header, index) => {
        header.id ||= `${table.id}-column-${index + 1}`;
        header.scope = 'col';
        header.setAttribute('role', 'columnheader');
        const clone = header.cloneNode(true);
        for (const line of clone.querySelectorAll('br')) line.replaceWith(' · ');
        return clone.textContent.replace(/\s+/g, ' ').trim();
      });
      for (const group of table.querySelectorAll('thead, tbody')) group.setAttribute('role', 'rowgroup');
      for (const row of table.querySelectorAll('tr')) row.setAttribute('role', 'row');
      for (const row of table.querySelectorAll('tbody tr')) {
        let column = 0;
        for (const cell of row.children) {
          const span = cell.colSpan || 1;
          cell.dataset.label = labels.slice(column, column + span).join(' / ') || '数据';
          cell.setAttribute('role', 'cell');
          cell.setAttribute('headers', headers.slice(column, column + span).map(header => header.id).join(' '));
          column += span;
        }
      }
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, {once: true});
  else initialize();
})();
