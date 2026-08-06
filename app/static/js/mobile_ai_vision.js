/**
 * 移动端 AI 视觉识别通用组件
 * --------------------------------------------------------
 * 依赖：移动端 base.html 中的 M 全局对象（toast / loading / confirm）
 * 功能：
 *   1. 拍照/相册选图（input[accept=image/*, capture=environment]）
 *   2. 图片压缩（长边 1280，质量 0.7）
 *   3. 旋转/裁剪确认浮层（可选，通过 needConfirm=true 开启）
 *   4. 调用 /ai/vision/recognize 接口（与 PC 端共用同一接口）
 *   5. 加载状态、错误提示、重试
 *
 * 用法：
 *   MobileAIVision.recognize('license', {
 *       onResult: function(data){ ... },      // 识别成功回调
 *       onError:   function(msg){ ... },       // 识别失败回调（可选）
 *       title:     '识别营业执照',              // 浮层标题（可选）
 *       needConfirm: true                       // 是否弹出裁剪/旋转确认浮层
 *   });
 *
 *   // 通用文字提取：MobileAIVision.extractText({ onResult, onError });
 */
window.MobileAIVision = (function () {
    'use strict';

    var MAX_SIZE = 5 * 1024 * 1024;       // 5MB
    var MAX_LONG_EDGE = 1280;              // 压缩长边
    var QUALITY = 0.7;                     // 压缩质量
    var API_URL = '/ai/vision/recognize';  // 复用 PC 端接口
    var csrfTokenCache = null;

    function getCsrfToken() {
        if (csrfTokenCache !== null) return csrfTokenCache;
        var meta = document.querySelector('meta[name="csrf-token"]');
        csrfTokenCache = meta ? (meta.getAttribute('content') || '') : '';
        return csrfTokenCache;
    }

    function toast(msg, type) {
        if (window.M && M.toast) M.toast(msg, type || 'info');
        else if (window.AIVision && AIVision.showToast) AIVision.showToast(msg, type);
    }

    function showLoading(show) {
        if (window.M && M.loading) M.loading(show);
    }

    /**
     * 读取文件 → 压缩为 base64（jpeg）
     */
    function compressImage(file, callback) {
        if (!file) { callback(null, '未选择文件'); return; }
        if (!file.type || !file.type.match(/image\/(jpeg|jpg|png)/i)) {
            callback(null, '仅支持 JPG、PNG 格式图片');
            return;
        }
        if (file.size > MAX_SIZE) {
            callback(null, '图片大小不能超过 5MB');
            return;
        }

        var reader = new FileReader();
        reader.onload = function (e) {
            var img = new Image();
            img.onload = function () {
                var canvas = document.createElement('canvas');
                var ctx = canvas.getContext('2d');
                var w = img.width, h = img.height;
                var longEdge = Math.max(w, h);
                if (longEdge > MAX_LONG_EDGE) {
                    var scale = MAX_LONG_EDGE / longEdge;
                    w = Math.round(w * scale);
                    h = Math.round(h * scale);
                }
                canvas.width = w;
                canvas.height = h;
                ctx.fillStyle = '#ffffff';
                ctx.fillRect(0, 0, w, h);
                ctx.drawImage(img, 0, 0, w, h);
                var dataUrl = canvas.toDataURL('image/jpeg', QUALITY);
                var base64 = dataUrl.split(',')[1];
                callback(base64, null, dataUrl);
            };
            img.onerror = function () { callback(null, '图片加载失败'); };
            img.src = e.target.result;
        };
        reader.onerror = function () { callback(null, '文件读取失败'); };
        reader.readAsDataURL(file);
    }

    /**
     * 调用后端识别接口
     */
    function callRecognize(type, base64, callback) {
        showLoading(true);
        fetch(API_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken()
            },
            body: JSON.stringify({ type: type, image: base64 })
        })
        .then(function (resp) {
            // 403 / 401 权限问题
            if (resp.status === 401 || resp.status === 403) {
                return { success: false, message: '权限不足或登录已过期，请重新登录' };
            }
            return resp.json();
        })
        .then(function (data) {
            showLoading(false);
            if (data && data.success) {
                callback(true, data.data, null);
            } else {
                callback(false, null, (data && data.message) || '识别失败，请重试');
            }
        })
        .catch(function (err) {
            showLoading(false);
            callback(false, null, '网络错误：' + (err && err.message ? err.message : err));
        });
    }

    // ============== 旋转/裁剪确认浮层 ==============
    // 通过 canvas 实现旋转，确认后输出 base64
    var confirmOverlay = null;
    var currentRotation = 0;
    var currentDataUrl = '';
    var confirmCallback = null;

    function ensureOverlay() {
        if (confirmOverlay) return;
        var css =
            '.maiv-mask{position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.92);display:none;flex-direction:column;}' +
            '.maiv-mask.show{display:flex;}' +
            '.maiv-head{height:48px;padding:0 12px;display:flex;align-items:center;justify-content:space-between;color:#fff;font-size:15px;background:rgba(0,0,0,0.4)}' +
            '.maiv-head .t{flex:1;text-align:center;}' +
            '.maiv-head .btn{background:none;border:none;color:#fff;font-size:14px;padding:6px 10px;}' +
            '.maiv-head .btn.primary{color:#4dabf7;font-weight:600;}' +
            '.maiv-body{flex:1;position:relative;overflow:hidden;display:flex;align-items:center;justify-content:center;}' +
            '.maiv-body img{max-width:95%;max-height:80vh;transition:transform 0.2s;}' +
            '.maiv-foot{padding:14px 16px 24px;display:flex;gap:10px;background:rgba(0,0,0,0.6);}' +
            '.maiv-foot button{flex:1;height:44px;border:none;border-radius:8px;font-size:15px;}' +
            '.maiv-rotate{background:rgba(255,255,255,0.15);color:#fff;flex:0 0 56px;}' +
            '.maiv-cancel{background:#6c757d;color:#fff;flex:0 0 90px;}' +
            '.maiv-ok{background:#409eff;color:#fff;flex:1;}';
        var style = document.createElement('style');
        style.textContent = css;
        document.head.appendChild(style);

        confirmOverlay = document.createElement('div');
        confirmOverlay.className = 'maiv-mask';
        confirmOverlay.innerHTML =
            '<div class="maiv-head">' +
                '<button type="button" class="btn" id="maivCancelTop"><i class="bi bi-x-lg"></i></button>' +
                '<span class="t" id="maivTitle">确认图片</span>' +
                '<span style="width:40px"></span>' +
            '</div>' +
            '<div class="maiv-body">' +
                '<img id="maivImg" alt="preview">' +
            '</div>' +
            '<div class="maiv-foot">' +
                '<button type="button" class="maiv-rotate" id="maivRotate" title="旋转90°"><i class="bi bi-arrow-clockwise"></i></button>' +
                '<button type="button" class="maiv-cancel" id="maivCancel">重选</button>' +
                '<button type="button" class="maiv-ok" id="maivOk">确认识别</button>' +
            '</div>';
        document.body.appendChild(confirmOverlay);

        document.getElementById('maivRotate').onclick = function () {
            currentRotation = (currentRotation + 90) % 360;
            var img = document.getElementById('maivImg');
            img.style.transform = 'rotate(' + currentRotation + 'deg)';
        };
        document.getElementById('maivCancel').onclick =
        document.getElementById('maivCancelTop').onclick = function () {
            closeConfirm();
            if (confirmCallback) confirmCallback(null, '用户取消');
        };
        document.getElementById('maivOk').onclick = function () {
            var finalDataUrl = currentDataUrl;
            if (currentRotation !== 0) {
                finalDataUrl = rotateDataUrl(currentDataUrl, currentRotation);
            }
            closeConfirm();
            if (confirmCallback) confirmCallback(finalDataUrl, null);
        };
    }

    function closeConfirm() {
        if (confirmOverlay) confirmOverlay.classList.remove('show');
        currentRotation = 0;
    }

    function rotateDataUrl(dataUrl, deg) {
        // 将原图按 deg 角度旋转，输出新的 dataUrl
        try {
            var img = new Image();
            img.src = dataUrl;
            // 同步绘制
            var canvas = document.createElement('canvas');
            var ctx = canvas.getContext('2d');
            if (deg === 90 || deg === 270) {
                canvas.width = img.height;
                canvas.height = img.width;
            } else {
                canvas.width = img.width;
                canvas.height = img.height;
            }
            ctx.translate(canvas.width / 2, canvas.height / 2);
            ctx.rotate(deg * Math.PI / 180);
            ctx.drawImage(img, -img.width / 2, -img.height / 2);
            return canvas.toDataURL('image/jpeg', QUALITY);
        } catch (e) {
            return dataUrl;
        }
    }

    function showConfirm(dataUrl, title, callback) {
        ensureOverlay();
        currentDataUrl = dataUrl;
        currentRotation = 0;
        confirmCallback = callback;
        var img = document.getElementById('maivImg');
        img.src = dataUrl;
        img.style.transform = 'rotate(0deg)';
        document.getElementById('maivTitle').textContent = title || '确认图片';
        confirmOverlay.classList.add('show');
    }

    // ============== 对外入口 ==============

    /**
     * 打开图片选择（拍照/相册），自动调用识别接口
     * @param {String} type - license/invoice/receipt/ocr
     * @param {Object} opts - { onResult, onError, title, needConfirm, source }
     *   source: 'camera' | 'album' | 'both'（默认 both，弹出选择）
     */
    function recognize(type, opts) {
        opts = opts || {};
        if (!window.MOBILE_AI_VISION_ENABLED) {
            toast('AI 视觉识别未开启，请联系管理员', 'warning');
            return;
        }

        pickFile(opts.source || 'both', function (file) {
            if (!file) return;
            compressImage(file, function (base64, err, dataUrl) {
                if (err) {
                    toast(err, 'danger');
                    if (opts.onError) opts.onError(err);
                    return;
                }
                // 弹出确认浮层（旋转/裁剪）
                if (opts.needConfirm && dataUrl) {
                    showConfirm(dataUrl, opts.title || '确认识别图片', function (confirmedDataUrl, cancelErr) {
                        if (cancelErr) {
                            if (opts.onError) opts.onError(cancelErr);
                            return;
                        }
                        var b64 = confirmedDataUrl.split(',')[1];
                        doRecognize(type, b64, opts);
                    });
                } else {
                    doRecognize(type, base64, opts);
                }
            });
        });
    }

    function doRecognize(type, base64, opts) {
        toast('识别中，请稍候...', 'info');
        callRecognize(type, base64, function (success, data, message) {
            if (success) {
                toast('识别成功，请核对结果', 'success');
                if (opts.onResult) opts.onResult(data);
            } else {
                toast(message || '识别失败，请重试', 'danger');
                if (opts.onError) opts.onError(message);
            }
        });
    }

    /**
     * 选择文件：拍照 / 相册 / 让用户选择
     */
    function pickFile(source, callback) {
        var input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/jpeg,image/png';
        if (source === 'camera') {
            input.setAttribute('capture', 'environment');
        }
        input.style.display = 'none';
        document.body.appendChild(input);

        input.onchange = function () {
            if (input.files && input.files[0]) {
                callback(input.files[0]);
            }
            setTimeout(function () {
                if (input.parentNode) input.parentNode.removeChild(input);
            }, 200);
        };

        if (source === 'both') {
            // 弹出选择：拍照 / 相册
            showSourcePicker(function (choice) {
                if (choice === 'camera') {
                    input.setAttribute('capture', 'environment');
                }
                input.click();
            });
        } else {
            input.click();
        }
    }

    function showSourcePicker(callback) {
        var mask = document.createElement('div');
        mask.style.cssText = 'position:fixed;inset:0;z-index:9998;background:rgba(0,0,0,0.5);display:flex;align-items:flex-end;justify-content:center;';
        var sheet = document.createElement('div');
        sheet.style.cssText = 'background:#fff;width:100%;max-width:520px;border-radius:14px 14px 0 0;padding:8px 0 20px;';
        sheet.innerHTML =
            '<button type="button" data-act="camera" style="width:100%;height:52px;border:none;background:#fff;font-size:15px;color:#409eff;border-bottom:1px solid #f1f3f5;">' +
                '<i class="bi bi-camera me-2"></i>拍照</button>' +
            '<button type="button" data-act="album" style="width:100%;height:52px;border:none;background:#fff;font-size:15px;color:#409eff;border-bottom:8px solid #f1f3f5;">' +
                '<i class="bi bi-image me-2"></i>从相册选择</button>' +
            '<button type="button" data-act="cancel" style="width:100%;height:52px;border:none;background:#fff;font-size:15px;color:#6c757d;">取消</button>';
        mask.appendChild(sheet);
        document.body.appendChild(mask);

        sheet.addEventListener('click', function (e) {
            var act = e.target.getAttribute('data-act') ||
                      (e.target.closest && e.target.closest('[data-act]') && e.target.closest('[data-act]').getAttribute('data-act'));
            document.body.removeChild(mask);
            if (act === 'camera' || act === 'album') callback(act);
        });
    }

    /**
     * 通用文字提取（type='ocr'）
     */
    function extractText(opts) {
        recognize('ocr', opts);
    }

    /**
     * 创建一个移动端风格的识别按钮（返回 HTML 字符串）
     * @param {String} type - license/invoice/receipt/ocr
     * @param {String} label - 按钮文字
     * @param {Object} opts - 同 recognize 的 opts
     */
    function createButtonHtml(type, label, opts) {
        var id = 'maiv_btn_' + Date.now() + '_' + Math.floor(Math.random() * 10000);
        setTimeout(function () {
            var btn = document.getElementById(id);
            if (btn) {
                btn.addEventListener('click', function (e) {
                    e.preventDefault();
                    recognize(type, opts || {});
                });
            }
        }, 0);
        return '<button type="button" id="' + id + '" class="m-btn m-btn-outline-info" ' +
               'style="font-size:13px;padding:6px 12px;border-radius:8px;">' +
               '<i class="bi bi-camera me-1"></i>' + label + '</button>';
    }

    return {
        recognize: recognize,
        extractText: extractText,
        compressImage: compressImage,
        callRecognize: callRecognize,
        createButtonHtml: createButtonHtml
    };
})();
