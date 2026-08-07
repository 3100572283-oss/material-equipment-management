/**
 * 外部对接中心 - AJAX 交互增强
 * 复用项目既有基建：window.showToast / window.customConfirm / CSRF meta。
 * 用法：
 *   <button type="button" data-ajax-post="/url" data-confirm="确认？">动作</button>
 *   <form data-ajax-form data-ajax-confirm="确认？" method="post" action="/url"> ... <button type="submit">保存</button></form>
 * XHR 时路由返回 {toast, toast_type, reload|redirect}，本脚本负责 loading/toast/跳转。
 */
(function () {
    'use strict';

    function csrf() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute('content') : '';
    }

    function setLoading(btn) {
        if (!btn) return;
        btn._oh = btn.innerHTML;
        btn._od = btn.disabled;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status"></span>处理中...';
    }

    function restore(btn) {
        if (!btn) return;
        btn.disabled = btn._od;
        if (btn._oh) btn.innerHTML = btn._oh;
    }

    function toastResult(d, ok) {
        var type = (d && d.toast_type) || (ok ? 'success' : 'error');
        var msg = (d && (d.toast || d.message)) || (ok ? '操作成功' : '操作失败');
        if (window.showToast) window.showToast(msg, type);
        else alert(msg);
    }

    function after(d, ok, btn) {
        toastResult(d, ok);
        if (d && d.redirect) {
            setTimeout(function () { location.href = d.redirect; }, 600);
        } else if (d && d.reload) {
            setTimeout(function () { location.reload(); }, 600);
        } else {
            restore(btn);
        }
    }

    function send(url, formData, btn) {
        setLoading(btn);
        formData.append('csrf_token', csrf());
        fetch(url, {
            method: 'POST',
            body: formData,
            headers: {
                'X-CSRFToken': csrf(),
                'X-Requested-With': 'XMLHttpRequest'
            }
        }).then(function (r) {
            return r.json().then(function (d) {
                return { ok: r.ok, d: d };
            }).catch(function () {
                return { ok: false, d: {} };
            });
        }).then(function (res) {
            after(res.d, res.ok, btn);
        }).catch(function () {
            toastResult({}, false);
            restore(btn);
        });
    }

    function init() {
        // 表单型（含字段，如接口保存）
        document.querySelectorAll('form[data-ajax-form]').forEach(function (form) {
            if (form.dataset.aiInit) return;
            form.dataset.aiInit = '1';
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                var btn = form.querySelector('button[type="submit"]');
                var go = function () {
                    send(form.getAttribute('action') || form.action, new FormData(form), btn);
                };
                var cf = form.getAttribute('data-ajax-confirm');
                if (cf && window.customConfirm) window.customConfirm(cf, go);
                else go();
            });
        });

        // 按钮型（无字段，如测试/清缓存/删除）
        document.querySelectorAll('button[data-ajax-post]').forEach(function (btn) {
            if (btn.dataset.aiInit) return;
            btn.dataset.aiInit = '1';
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                var go = function () {
                    var fd = new FormData();
                    send(btn.getAttribute('data-ajax-post'), fd, btn);
                };
                var cf = btn.getAttribute('data-confirm');
                if (cf && window.customConfirm) window.customConfirm(cf, go);
                else go();
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    // 动态内容（模态框内）出现后重新绑定
    document.addEventListener('shown.bs.modal', function () {
        setTimeout(init, 100);
    });

    window.integrationUI = { init: init };
})();
