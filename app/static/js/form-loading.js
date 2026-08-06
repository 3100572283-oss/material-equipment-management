/**
 * Form Submit Loading + Anti-Duplicate Submission
 * Adds loading state to all form submit buttons
 * Prevents duplicate submissions
 */
(function () {
    'use strict';

    var submitting = false;

    function initFormLoading() {
        // Target all forms that are POST forms
        document.querySelectorAll('form[method="POST"], form[method="post"]').forEach(function (form) {
            if (form.dataset.formLoadingInit) return;
            form.dataset.formLoadingInit = '1';

            form.addEventListener('submit', function (e) {
                // Skip if already submitting
                if (form.dataset.submitting === '1') {
                    e.preventDefault();
                    e.stopPropagation();
                    return false;
                }

                // Find the submit button that was clicked
                var submitBtn = form.querySelector('button[type="submit"].btn-loading-active');
                if (!submitBtn) {
                    // Try to find the clicked button
                    submitBtn = form.querySelector('button[type="submit"]');
                }

                if (submitBtn && !submitBtn.classList.contains('no-loading')) {
                    form.dataset.submitting = '1';
                    setLoadingState(submitBtn);
                } else if (!submitBtn) {
                    // If no specific submit button, disable all submit buttons
                    var btns = form.querySelectorAll('button[type="submit"]');
                    btns.forEach(function (b) {
                        if (!b.classList.contains('no-loading')) {
                            form.dataset.submitting = '1';
                            setLoadingState(b);
                        }
                    });
                }

                // Safety timeout: re-enable after 30 seconds in case of error
                setTimeout(function () {
                    form.dataset.submitting = '0';
                    form.querySelectorAll('button[type="submit"]').forEach(function (b) {
                        if (b.dataset.originalHTML) {
                            restoreButton(b);
                        }
                    });
                }, 30000);
            });

            // Track which submit button was clicked for multi-button forms
            form.querySelectorAll('button[type="submit"]').forEach(function (btn) {
                btn.addEventListener('click', function () {
                    form.querySelectorAll('button[type="submit"]').forEach(function (b) {
                        b.classList.remove('btn-loading-active');
                    });
                    btn.classList.add('btn-loading-active');
                });
            });
        });
    }

    function setLoadingState(btn) {
        if (btn.dataset.loading === '1') return;
        btn.dataset.loading = '1';
        btn.dataset.originalHTML = btn.innerHTML;
        btn.dataset.originalDisabled = btn.disabled ? '1' : '0';
        
        btn.disabled = true;
        var spinnerSize = btn.classList.contains('btn-sm') ? 'spinner-border-sm' : 'spinner-border-sm';
        var btnText = btn.textContent.trim() || '提交';
        btn.innerHTML = '<span class="spinner-border ' + spinnerSize + ' me-1" role="status"></span>' + btnText + '中...';
    }

    function restoreButton(btn) {
        if (btn.dataset.originalHTML) {
            btn.innerHTML = btn.dataset.originalHTML;
            btn.disabled = btn.dataset.originalDisabled === '1';
            delete btn.dataset.loading;
            delete btn.dataset.originalHTML;
            delete btn.dataset.originalDisabled;
        }
    }

    // Also handle AJAX form buttons (buttons that trigger fetch requests)
    function initAjaxButtonLoading() {
        document.querySelectorAll('button[data-ajax-loading="true"]').forEach(function (btn) {
            if (btn.dataset.ajaxLoadingInit) return;
            btn.dataset.ajaxLoadingInit = '1';

            btn.addEventListener('click', function () {
                if (btn.dataset.ajaxLoading === '1') return;
                btn.dataset.ajaxLoading = '1';
                btn.dataset.originalHTML = btn.innerHTML;
                btn.disabled = true;
                var btnText = btn.textContent.trim() || '处理';
                btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>' + btnText + '中...';
                
                // Auto-restore after 10 seconds
                setTimeout(function () {
                    if (btn.dataset.ajaxLoading === '1') {
                        restoreButton(btn);
                        delete btn.dataset.ajaxLoading;
                    }
                }, 10000);
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            initFormLoading();
            initAjaxButtonLoading();
        });
    } else {
        initFormLoading();
        initAjaxButtonLoading();
    }

    // Re-init when modals are shown
    document.addEventListener('shown.bs.modal', function () {
        setTimeout(function () {
            initFormLoading();
            initAjaxButtonLoading();
        }, 100);
    });

    // Export for manual call
    window.initFormLoading = initFormLoading;
    window.restoreFormButton = restoreButton;
})();
