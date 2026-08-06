/**
 * Custom Modal Component - Replaces native confirm() and alert()
 * Uses Bootstrap 5 modal system, matches system style
 */
(function () {
    'use strict';

    var modalHTML = '' +
        '<div class="modal fade" id="__customModal" tabindex="-1" data-bs-backdrop="static">' +
        '  <div class="modal-dialog modal-dialog-centered">' +
        '    <div class="modal-content">' +
        '      <div class="modal-header" id="__customModalHeader">' +
        '        <h5 class="modal-title" id="__customModalTitle"></h5>' +
        '        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>' +
        '      </div>' +
        '      <div class="modal-body" id="__customModalBody"></div>' +
        '      <div class="modal-footer" id="__customModalFooter"></div>' +
        '    </div>' +
        '  </div>' +
        '</div>';

    var modalInstance = null;

    function ensureModal() {
        var existing = document.getElementById('__customModal');
        if (existing) return existing;
        var div = document.createElement('div');
        div.innerHTML = modalHTML;
        document.body.appendChild(div.firstChild);
        return document.getElementById('__customModal');
    }

    function getModalInstance() {
        var el = ensureModal();
        if (typeof bootstrap !== 'undefined') {
            modalInstance = bootstrap.Modal.getOrCreateInstance(el);
        }
        return modalInstance;
    }

    /**
     * Custom confirm dialog
     * @param {string} message - Message to display
     * @param {function} onConfirm - Callback when confirmed
     * @param {object} opts - {title, confirmText, cancelText, confirmClass}
     */
    window.customConfirm = function (message, onConfirm, opts) {
        opts = opts || {};
        var el = ensureModal();
        var titleEl = document.getElementById('__customModalTitle');
        var bodyEl = document.getElementById('__customModalBody');
        var footerEl = document.getElementById('__customModalFooter');
        var headerEl = document.getElementById('__customModalHeader');

        titleEl.textContent = opts.title || '确认操作';
        bodyEl.innerHTML = '<div class="d-flex align-items-start">' +
            '<i class="bi bi-exclamation-triangle-fill text-warning me-2 fs-4"></i>' +
            '<div>' + escapeHtml(message) + '</div></div>';

        headerEl.className = 'modal-header';
        footerEl.innerHTML = '';

        var cancelBtn = document.createElement('button');
        cancelBtn.className = 'btn btn-secondary';
        cancelBtn.textContent = opts.cancelText || '取消';
        cancelBtn.setAttribute('data-bs-dismiss', 'modal');

        var confirmBtn = document.createElement('button');
        confirmBtn.className = 'btn ' + (opts.confirmClass || 'btn-primary');
        confirmBtn.textContent = opts.confirmText || '确定';

        footerEl.appendChild(cancelBtn);
        footerEl.appendChild(confirmBtn);

        confirmBtn.addEventListener('click', function () {
            var inst = getModalInstance();
            if (inst) inst.hide();
            if (typeof onConfirm === 'function') onConfirm();
        });

        var inst = getModalInstance();
        if (inst) inst.show();
    };

    /**
     * Custom confirm that returns a Promise (for async usage)
     */
    window.customConfirmAsync = function (message, opts) {
        return new Promise(function (resolve) {
            window.customConfirm(message, function () {
                resolve(true);
            }, opts);
            // Listen for modal hidden to resolve false if dismissed
            var el = document.getElementById('__customModal');
            if (el) {
                el.addEventListener('hidden.bs.modal', function onHidden() {
                    el.removeEventListener('hidden.bs.modal', onHidden);
                    resolve(false);
                }, { once: true });
            }
        });
    };

    /**
     * Custom alert dialog
     * @param {string} message - Message to display
     * @param {function} onOk - Callback when OK clicked
     * @param {object} opts - {title, okText, type}
     */
    window.customAlert = function (message, onOk, opts) {
        opts = opts || {};
        var el = ensureModal();
        var titleEl = document.getElementById('__customModalTitle');
        var bodyEl = document.getElementById('__customModalBody');
        var footerEl = document.getElementById('__customModalFooter');
        var headerEl = document.getElementById('__customModalHeader');

        titleEl.textContent = opts.title || '提示';
        var iconClass = opts.type === 'error' ? 'bi-x-circle-fill text-danger' :
                        opts.type === 'success' ? 'bi-check-circle-fill text-success' :
                        opts.type === 'warning' ? 'bi-exclamation-triangle-fill text-warning' :
                        'bi-info-circle-fill text-primary';
        bodyEl.innerHTML = '<div class="d-flex align-items-start">' +
            '<i class="bi ' + iconClass + ' me-2 fs-4"></i>' +
            '<div>' + escapeHtml(message) + '</div></div>';

        headerEl.className = 'modal-header';
        footerEl.innerHTML = '';

        var okBtn = document.createElement('button');
        okBtn.className = 'btn ' + (opts.type === 'error' ? 'btn-danger' : opts.type === 'success' ? 'btn-success' : 'btn-primary');
        okBtn.textContent = opts.okText || '确定';
        okBtn.setAttribute('data-bs-dismiss', 'modal');

        footerEl.appendChild(okBtn);

        okBtn.addEventListener('click', function () {
            if (typeof onOk === 'function') onOk();
        });

        var inst = getModalInstance();
        if (inst) inst.show();
    };

    /**
     * Override native confirm() and alert() globally
     * This ensures all existing code works without modification
     */
    var _originalConfirm = window.confirm;
    var _originalAlert = window.alert;

    // Store originals for potential restoration
    window._originalConfirm = _originalConfirm;
    window._originalAlert = _originalAlert;

    // Override confirm to use custom modal with callback support
    // For onsubmit="return confirm(...)" patterns, we need a synchronous fallback
    // Since modal is async, we intercept form submissions differently
    window.confirm = function (message) {
        // If called from a form onsubmit context, we handle it via event interception
        // This synchronous override will be handled by form-loading.js for form submissions
        // For non-form contexts, fall through to original
        return _originalConfirm.call(window, message);
    };

    window.alert = function (message) {
        // Use custom alert for all alert calls
        window.customAlert(message);
    };

    /**
     * Intercept all form onsubmit="return confirm(...)" patterns
     * Replace with async custom modal
     */
    function interceptFormConfirms() {
        document.querySelectorAll('form[onsubmit*="confirm"]').forEach(function (form) {
            if (form.dataset.confirmIntercepted) return;
            form.dataset.confirmIntercepted = '1';

            var onsubmitAttr = form.getAttribute('onsubmit') || '';
            // Extract the confirm message
            var match = onsubmitAttr.match(/confirm\(['"](.+?)['"]\)/);
            if (!match) {
                // Try with escaped quotes
                match = onsubmitAttr.match(/confirm\((.+?)\)\s*;?\s*\}/);
            }
            if (!match) return;

            var confirmMsg = match[1].replace(/\\n/g, '\n');

            form.removeAttribute('onsubmit');

            form.addEventListener('submit', function (e) {
                e.preventDefault();
                window.customConfirm(confirmMsg, function () {
                    // Add loading state to submit button before direct submit
                    var submitBtn = form.querySelector('button[type="submit"]');
                    if (submitBtn && !submitBtn.classList.contains('no-loading')) {
                        submitBtn.disabled = true;
                        var origHTML = submitBtn.innerHTML;
                        var btnText = submitBtn.textContent.trim() || '提交';
                        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>' + btnText + '中...';
                    }
                    form.submit();
                });
            });
        });
    }

    // Also intercept onclick="return confirm(...)" on buttons
    function interceptButtonConfirms() {
        document.querySelectorAll('button[onclick*="confirm"], a[onclick*="confirm"], input[onclick*="confirm"]').forEach(function (el) {
            if (el.dataset.confirmIntercepted) return;
            el.dataset.confirmIntercepted = '1';

            var onclickAttr = el.getAttribute('onclick') || '';
            var match = onclickAttr.match(/confirm\(['"](.+?)['"]\)/);
            if (!match) return;

            var confirmMsg = match[1].replace(/\\n/g, '\n');
            // Get the action after confirm
            var afterConfirm = onclickAttr.replace(/.*confirm\([^)]*\)\s*\)\s*\{?\s*/, '').replace(/\s*\}?\s*$/, '');
            if (afterConfirm.endsWith(';')) afterConfirm = afterConfirm.slice(0, -1);

            el.removeAttribute('onclick');

            el.addEventListener('click', function (e) {
                e.preventDefault();
                window.customConfirm(confirmMsg, function () {
                    if (afterConfirm) {
                        try { eval(afterConfirm); } catch (err) { console.error(err); }
                    }
                });
            });
        });
    }

    // Run interception on DOMContentLoaded and after any dynamic content load
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            interceptFormConfirms();
            interceptButtonConfirms();
        });
    } else {
        interceptFormConfirms();
        interceptButtonConfirms();
    }

    // Re-intercept when modals/dynamic content are shown
    document.addEventListener('shown.bs.modal', function () {
        setTimeout(function () {
            interceptFormConfirms();
            interceptButtonConfirms();
        }, 100);
    });

    function escapeHtml(text) {
        if (text == null) return '';
        var div = document.createElement('div');
        div.textContent = String(text);
        return div.innerHTML;
    }
})();
