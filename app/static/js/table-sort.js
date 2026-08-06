/**
 * Table Column Sort Plugin
 * Adds click-to-sort on table headers
 * Uses front-end sorting on current page data
 */
(function () {
    'use strict';

    /**
     * Initialize sorting on tables with class 'table-sortable'
     * or tables whose th elements have data-sort attribute
     */
    function initTableSort() {
        var tables = document.querySelectorAll('table.table-sortable, table[data-sortable="true"]');
        
        tables.forEach(function (table) {
            if (table.dataset.sortInitialized) return;
            table.dataset.sortInitialized = '1';
            
            var tbody = table.querySelector('tbody');
            if (!tbody) return;
            
            var headers = table.querySelectorAll('thead th');
            headers.forEach(function (th, idx) {
                // Skip columns with checkbox or action columns
                if (th.querySelector('input[type="checkbox"]')) return;
                if (th.classList.contains('no-sort')) return;
                if (th.textContent.trim() === '操作') return;
                
                th.style.cursor = 'pointer';
                th.style.userSelect = 'none';
                th.style.whiteSpace = 'nowrap';
                
                // Add sort indicator
                var indicator = document.createElement('span');
                indicator.className = 'sort-indicator';
                indicator.style.cssText = 'margin-left:4px;font-size:0.7rem;color:#ccc;';
                indicator.innerHTML = '⇅';
                th.appendChild(indicator);
                
                th.dataset.sortCol = idx;
                th.dataset.sortDir = '';
                
                th.addEventListener('click', function () {
                    var col = parseInt(th.dataset.sortCol);
                    var currentDir = th.dataset.sortDir;
                    var newDir = currentDir === 'asc' ? 'desc' : 'asc';
                    
                    // Reset all indicators
                    headers.forEach(function (h) {
                        if (h.dataset.sortDir !== undefined) {
                            h.dataset.sortDir = '';
                            var ind = h.querySelector('.sort-indicator');
                            if (ind) ind.innerHTML = '⇅';
                            h.classList.remove('sort-active');
                        }
                    });
                    
                    th.dataset.sortDir = newDir;
                    indicator.innerHTML = newDir === 'asc' ? '▲' : '▼';
                    indicator.style.color = '#409eff';
                    th.classList.add('sort-active');
                    
                    sortTable(table, tbody, col, newDir);
                });
            });
        });
    }

    function sortTable(table, tbody, colIdx, direction) {
        var rows = Array.from(tbody.querySelectorAll('tr'));
        // Skip rows that are "no data" placeholders
        var dataRows = rows.filter(function (r) {
            return !r.querySelector('.text-muted.text-center') || r.querySelectorAll('td').length > 1;
        });
        var placeholderRows = rows.filter(function (r) {
            return dataRows.indexOf(r) === -1;
        });

        dataRows.sort(function (a, b) {
            var aCell = a.querySelectorAll('td')[colIdx];
            var bCell = b.querySelectorAll('td')[colIdx];
            if (!aCell || !bCell) return 0;

            var aVal = extractValue(aCell);
            var bVal = extractValue(bCell);

            if (aVal === null || aVal === '' || aVal === '-') aVal = direction === 'asc' ? Infinity : -Infinity;
            if (bVal === null || bVal === '' || bVal === '-') bVal = direction === 'asc' ? Infinity : -Infinity;

            if (typeof aVal === 'number' && typeof bVal === 'number') {
                return direction === 'asc' ? aVal - bVal : bVal - aVal;
            }

            aVal = String(aVal).toLowerCase();
            bVal = String(bVal).toLowerCase();

            if (aVal < bVal) return direction === 'asc' ? -1 : 1;
            if (aVal > bVal) return direction === 'asc' ? 1 : -1;
            return 0;
        });

        // Re-append sorted rows
        dataRows.forEach(function (row) {
            tbody.appendChild(row);
        });
        placeholderRows.forEach(function (row) {
            tbody.appendChild(row);
        });
    }

    function extractValue(cell) {
        // Try to get numeric value first
        var text = cell.textContent.trim().replace(/[¥,]/g, '');
        
        // Check for date pattern
        if (/^\d{4}-\d{2}-\d{2}/.test(text)) {
            return new Date(text.split(' ')[0]).getTime();
        }
        
        // Check for pure number
        var num = parseFloat(text);
        if (!isNaN(num) && text !== '' && text !== '-') {
            return num;
        }
        
        return text;
    }

    // Add CSS for sort indicators
    var style = document.createElement('style');
    style.textContent = '' +
        '.sort-indicator { transition: color 0.2s; }' +
        'th.sort-active { background-color: #e7f1ff !important; }' +
        'th[style*="cursor: pointer"]:hover { background-color: #f0f4ff !important; }';
    document.head.appendChild(style);

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initTableSort);
    } else {
        initTableSort();
    }

    // Re-init when modals are shown (for tables inside modals)
    document.addEventListener('shown.bs.modal', function () {
        setTimeout(initTableSort, 100);
    });

    // Export for manual call
    window.initTableSort = initTableSort;
})();
