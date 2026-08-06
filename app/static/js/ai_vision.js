/**
 * AI视觉识别通用工具
 * 提供：图片压缩、base64转换、识别调用、结果填充
 */
var AIVision = (function() {
    var MAX_SIZE = 5 * 1024 * 1024;  // 5MB
    var MAX_LONG_EDGE = 1280;        // 长边1280px
    var QUALITY = 0.7;               // 质量70%

    /**
     * 压缩图片
     */
    function compressImage(file, callback) {
        if (!file.type.match(/image\/(jpeg|jpg|png|gif|bmp|webp)/i)) {
            callback(null, '仅支持JPG、PNG格式图片');
            return;
        }
        if (file.size > MAX_SIZE) {
            callback(null, '图片大小不能超过5MB');
            return;
        }

        var reader = new FileReader();
        reader.onload = function(e) {
            var img = new Image();
            img.onload = function() {
                var canvas = document.createElement('canvas');
                var ctx = canvas.getContext('2d');

                var width = img.width;
                var height = img.height;
                var longEdge = Math.max(width, height);

                if (longEdge > MAX_LONG_EDGE) {
                    var scale = MAX_LONG_EDGE / longEdge;
                    width = Math.round(width * scale);
                    height = Math.round(height * scale);
                }

                canvas.width = width;
                canvas.height = height;
                ctx.fillStyle = '#ffffff';
                ctx.fillRect(0, 0, width, height);
                ctx.drawImage(img, 0, 0, width, height);

                var dataUrl = canvas.toDataURL('image/jpeg', QUALITY);
                var base64 = dataUrl.split(',')[1];
                callback(base64, null);
            };
            img.onerror = function() {
                callback(null, '图片加载失败');
            };
            img.src = e.target.result;
        };
        reader.onerror = function() {
            callback(null, '文件读取失败');
        };
        reader.readAsDataURL(file);
    }

    /**
     * 调用视觉识别API
     * @param {String} type - license/invoice/receipt/ocr
     * @param {String} base64 - 图片base64编码
     * @param {Function} callback - callback(success, data, message)
     */
    function recognize(type, base64, callback) {
        fetch('/ai/vision/recognize', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCsrfToken()
            },
            body: JSON.stringify({
                type: type,
                image: base64
            })
        })
        .then(function(resp) { return resp.json(); })
        .then(function(data) {
            if (data.success) {
                callback(true, data.data, null);
            } else {
                callback(false, null, data.message || '识别失败');
            }
        })
        .catch(function(err) {
            callback(false, null, '网络错误：' + err.message);
        });
    }

    function getCsrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }

    /**
     * 完整的识别流程：选择图片 → 压缩 → 调用API → 返回结果
     * @param {String} type - license/invoice/receipt/ocr
     * @param {File} file - 文件对象
     * @param {Function} callback - callback(success, data, message)
     */
    function recognizeImage(type, file, callback) {
        // PDF文件：直接用FormData上传，不压缩
        var fileName = (file.name || '').toLowerCase();
        if (fileName.endsWith('.pdf') || file.type === 'application/pdf') {
            if (file.size > 20 * 1024 * 1024) {
                callback(false, null, 'PDF文件不能超过20MB');
                return;
            }
            var formData = new FormData();
            formData.append('image', file);
            formData.append('type', type);
            var csrfMeta = document.querySelector('meta[name="csrf-token"]');
            if (csrfMeta) formData.append('csrf_token', csrfMeta.getAttribute('content'));

            fetch('/ai/vision/recognize', {
                method: 'POST',
                body: formData
            })
            .then(function(resp) { return resp.json(); })
            .then(function(data) {
                if (data.success) {
                    callback(true, data.data, null);
                } else {
                    callback(false, null, data.message || '识别失败');
                }
            })
            .catch(function(err) {
                callback(false, null, '网络错误：' + err.message);
            });
            return;
        }
        // 图片文件：压缩后用JSON发送
        compressImage(file, function(base64, err) {
            if (err) {
                callback(false, null, err);
                return;
            }
            recognize(type, base64, callback);
        });
    }

    /**
     * 创建识别按钮的UI组件
     * @param {String} type - license/invoice/receipt/ocr
     * @param {String} label - 按钮文字
     * @param {Function} onResult - 识别成功回调
     */
    function createButton(type, label, onResult) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-outline-info btn-sm ms-2';
        btn.innerHTML = '<i class="bi bi-camera me-1"></i>' + label;

        var input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/jpeg,image/png,application/pdf';
        input.style.display = 'none';

        btn.appendChild(input);
        btn.onclick = function(e) {
            e.preventDefault();
            input.click();
        };

        input.onchange = function() {
            if (!input.files || !input.files[0]) return;
            var file = input.files[0];

            // 显示加载状态
            var originalHtml = btn.innerHTML;
            btn.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>识别中...';
            btn.disabled = true;

            recognizeImage(type, file, function(success, data, message) {
                btn.innerHTML = originalHtml;
                btn.disabled = false;
                input.value = '';

                if (success) {
                    if (onResult) onResult(data);
                    showToast('识别成功', 'success');
                } else {
                    showToast(message || '识别失败，请手动录入', 'danger');
                }
            });
        };

        return btn;
    }

    function showToast(message, type) {
        type = type || 'info';
        var toast = document.createElement('div');
        toast.className = 'toast align-items-center text-white bg-' + type + ' border-0 position-fixed top-0 end-0 m-3';
        toast.style.zIndex = '9999';
        toast.innerHTML = '<div class="d-flex"><div class="toast-body">' + message + '</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>';
        document.body.appendChild(toast);
        var bsToast = new bootstrap.Toast(toast, { delay: 3000 });
        bsToast.show();
        toast.addEventListener('hidden.bs.toast', function() {
            document.body.removeChild(toast);
        });
    }

    return {
        compressImage: compressImage,
        recognize: recognize,
        recognizeImage: recognizeImage,
        createButton: createButton,
        showToast: showToast
    };
})();
