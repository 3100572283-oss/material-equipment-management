/* ============================================
   P2 体验增强 - 全局组件 JS
   ============================================ */

(function() {
    'use strict';

    /* === 1. 全局Toast组件 === */
    window.showToast = function(message, type, duration) {
        type = type || 'info';
        duration = duration || 3000;
        var container = document.querySelector('.toast-container-global');
        if (!container) {
            container = document.createElement('div');
            container.className = 'toast-container-global';
            document.body.appendChild(container);
        }
        var toast = document.createElement('div');
        toast.className = 'toast-item toast-' + type;
        var iconMap = {success: 'bi-check-circle', error: 'bi-x-circle', warning: 'bi-exclamation-triangle', info: 'bi-info-circle'};
        toast.innerHTML = '<i class="bi ' + (iconMap[type] || 'bi-info-circle') + '"></i><span>' + message + '</span>';
        container.appendChild(toast);
        setTimeout(function() {
            toast.classList.add('toast-fade-out');
            setTimeout(function() { toast.remove(); }, 300);
        }, duration);
    };

    /* AJAX全局Toast拦截 */
    var origXHRSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function() {
        var xhr = this;
        var origOnLoad = xhr.onload;
        xhr.addEventListener('load', function() {
            if (xhr.status >= 200 && xhr.status < 300) {
                try {
                    var resp = JSON.parse(xhr.responseText);
                    if (resp.toast) {
                        showToast(resp.toast, resp.toast_type || 'success');
                    }
                } catch(e) {}
            }
            if (origOnLoad) origOnLoad.apply(xhr, arguments);
        });
        origXHRSend.apply(xhr, arguments);
    };

    /* === 2. 面包屑自动生成 === */
    window.initBreadcrumb = function() {
        var bc = document.querySelector('[data-breadcrumb]');
        if (!bc) return;
        var items = [];
        // 首页
        items.push({text: '首页', href: '/'});
        // 从data属性解析
        var crumbs = bc.getAttribute('data-breadcrumb');
        if (crumbs) {
            try {
                var parsed = JSON.parse(crumbs);
                parsed.forEach(function(item, idx) {
                    items.push({text: item.text, href: item.href || null, active: idx === parsed.length - 1});
                });
            } catch(e) {}
        }
        if (items.length <= 1) return;
        var html = '<ol class="breadcrumb global-breadcrumb">';
        items.forEach(function(item, idx) {
            html += '<li class="breadcrumb-item';
            if (item.active || idx === items.length - 1) {
                html += ' active" aria-current="page">' + item.text + '</li>';
            } else {
                html += '"><a href="' + (item.href || '#') + '">' + item.text + '</a></li>';
            }
        });
        html += '</ol>';
        bc.innerHTML = html;
    };

    /* === 3. 空状态自动增强 === */
    window.enhanceEmptyStates = function() {
        document.querySelectorAll('td.text-center.text-muted').forEach(function(td) {
            if (td.textContent.trim() === '暂无数据' && !td.querySelector('.empty-state')) {
                var colspan = td.getAttribute('colspan') || '1';
                var wrapper = document.createElement('div');
                wrapper.className = 'empty-state';
                wrapper.innerHTML = '<i class="bi bi-inbox empty-icon"></i><p class="empty-text">暂无数据</p>';
                td.innerHTML = '';
                td.appendChild(wrapper);
            }
        });
        // 也处理 div 空状态
        document.querySelectorAll('.text-center.text-muted').forEach(function(el) {
            if (el.tagName === 'DIV' && el.textContent.trim() === '暂无数据' && !el.querySelector('.empty-icon')) {
                el.classList.add('empty-state');
                el.innerHTML = '<i class="bi bi-inbox empty-icon"></i><p class="empty-text">暂无数据</p>';
            }
        });
    };

    /* === 4. 每页条数选择器 === */
    window.initPerPageSelector = function() {
        var pagDivs = document.querySelectorAll('.pagination-container');
        pagDivs.forEach(function(div) {
            if (div.querySelector('.per-page-selector')) return;
            var url = new URL(window.location.href);
            var currentPerPage = url.searchParams.get('per_page') || '10';
            var selector = document.createElement('div');
            selector.className = 'per-page-selector';
            selector.innerHTML = '<span>每页</span><select onchange="changePerPage(this.value)">' +
                [10, 20, 50, 100].map(function(n) {
                    return '<option value="' + n + '"' + (String(n) === currentPerPage ? ' selected' : '') + '>' + n + '</option>';
                }).join('') +
                '</select><span>条</span>';
            div.insertBefore(selector, div.firstChild);
        });
    };
    window.changePerPage = function(val) {
        var url = new URL(window.location.href);
        url.searchParams.set('per_page', val);
        url.searchParams.set('page', '1');
        window.location.href = url.toString();
    };

    /* === 5. 骨架屏 === */
    window.showSkeleton = function(container, type) {
        type = type || 'table';
        var overlay = document.createElement('div');
        overlay.className = 'skeleton-overlay';
        if (type === 'table') {
            var html = '<div class="skeleton-card">';
            for (var i = 0; i < 2; i++) {
                html += '<div class="skeleton-line short"></div>';
            }
            html += '</div>';
            for (var j = 0; j < 5; j++) {
                html += '<div class="skeleton-table-row">';
                for (var k = 0; k < 4; k++) {
                    html += '<div class="skeleton-line medium" style="flex:1"></div>';
                }
                html += '</div>';
            }
            overlay.innerHTML = html;
        } else {
            overlay.innerHTML = '<div class="skeleton-card"><div class="skeleton-line long"></div><div class="skeleton-line long"></div><div class="skeleton-line medium"></div></div>';
        }
        (container || document.body).appendChild(overlay);
        return overlay;
    };
    window.hideSkeleton = function(overlay) {
        if (overlay) {
            overlay.classList.add('hidden');
            setTimeout(function() { overlay.remove(); }, 300);
        }
    };

    /* === 6. 表格列固定 === */
    window.initStickyCols = function() {
        document.querySelectorAll('table.table-sticky-cols').forEach(function(table) {
            var thead = table.querySelector('thead');
            var tbody = table.querySelector('tbody');
            if (!thead || !tbody) return;
            // 确保首列和末列（操作列）有sticky类
            var firstTh = thead.querySelector('th:first-child');
            var lastTh = thead.querySelector('th:last-child');
            if (firstTh) firstTh.classList.add('col-sticky-left');
            if (lastTh && lastTh.textContent.indexOf('操作') !== -1) lastTh.classList.add('col-sticky-right');
            tbody.querySelectorAll('tr').forEach(function(tr) {
                var firstTd = tr.querySelector('td:first-child');
                var lastTd = tr.querySelector('td:last-child');
                if (firstTd) firstTd.classList.add('col-sticky-left');
                if (lastTd && lastTh && lastTh.classList.contains('col-sticky-right')) lastTd.classList.add('col-sticky-right');
            });
        });
    };

    /* === 初始化 === */
    document.addEventListener('DOMContentLoaded', function() {
        initBreadcrumb();
        enhanceEmptyStates();
        initPerPageSelector();
        initStickyCols();
    });

})();
