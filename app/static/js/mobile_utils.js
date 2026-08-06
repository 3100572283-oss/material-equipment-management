/* =============================================================
 * mobile_utils.js - 移动端通用工具集
 * 包含：
 *   1. MobileDraft        - 表单草稿自动保存/恢复（localStorage，7天清理）
 *   2. MobileLocation    - 现场定位留痕（开关由 mobile_location_enabled 控制）
 *   3. MobileRecentUse   - 最近使用优先排序（物资/供应商/单位选择弹窗）
 *   4. MobileQuickEdit   - 首页快捷功能自定义（编辑模式、拖拽、localStorage）
 * 依赖：mobile/base.html 中的 M.toast / M.loading / M.confirm
 * ============================================================= */
(function () {
    'use strict';

    /* ========== 工具函数 ========== */
    function lsKey(prefix, biz) {
        var user = (window.CURRENT_USER && window.CURRENT_USER.id) || 'anon';
        return prefix + '_' + user + '_' + (biz || 'default');
    }
    function nowTs() { return Date.now(); }
    function todayStr() {
        var d = new Date();
        var p = function (n) { return n < 10 ? '0' + n : '' + n; };
        return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
    }
    function parseJSON(str, def) {
        try { return JSON.parse(str); } catch (e) { return def; }
    }

    /* =========================================================
     * 1. MobileDraft - 表单草稿自动保存
     * 用法：
     *   MobileDraft.bind(formEl, 'stock_in', { onRestore: fn, exclude: ['code'] });
     *   MobileDraft.clear('stock_in');
     * ========================================================= */
    var DRAFT_PREFIX = 'm_draft';
    var DRAFT_TTL = 7 * 24 * 3600 * 1000; // 7天

    var MobileDraft = {
        /**
         * 绑定表单：自动保存（1秒防抖）+ 进入页面检测草稿弹窗恢复
         * @param {HTMLFormElement} formEl
         * @param {string} bizType  业务类型（如 stock_in / stock_out / concrete / scrap / pr / equipment_in / turnover_borrow）
         * @param {object} opts
         *   - onRestore: function(data) 恢复后回调
         *   - exclude: [field names] 不保存的字段
         *   - autoRestore: bool 是否自动检测恢复（默认 true）
         *   - imageField: string 图片字段名（保存临时引用）
         */
        bind: function (formEl, bizType, opts) {
            opts = opts || {};
            if (!formEl) return;
            var key = lsKey(DRAFT_PREFIX, bizType);
            var timer = null;
            var exclude = opts.exclude || [];

            function collectData() {
                var data = { _ts: nowTs(), _fields: {} };
                for (var i = 0; i < formEl.elements.length; i++) {
                    var el = formEl.elements[i];
                    if (!el.name || exclude.indexOf(el.name) >= 0) continue;
                    if (el.type === 'checkbox') {
                        data._fields[el.name] = el.checked;
                    } else if (el.type === 'radio') {
                        if (el.checked) data._fields[el.name] = el.value;
                    } else if (el.type === 'file') {
                        // 文件不保存内容，只记录数量
                        data._fields['__file_' + el.name] = (el.files && el.files.length) || 0;
                    } else {
                        data._fields[el.name] = el.value;
                    }
                }
                // 保存图片临时引用（如有）
                if (opts.imageField && window[opts.imageField]) {
                    data._images = window[opts.imageField].map(function (img) {
                        return { name: img.name, size: img.size, ts: img.ts };
                    });
                }
                return data;
            }

            function save() {
                try {
                    var data = collectData();
                    localStorage.setItem(key, JSON.stringify(data));
                } catch (e) {
                    console.warn('草稿保存失败', e);
                }
            }

            function debouncedSave() {
                if (timer) clearTimeout(timer);
                timer = setTimeout(save, 1000);
            }

            function restore() {
                var raw = localStorage.getItem(key);
                if (!raw) return false;
                var data = parseJSON(raw, null);
                if (!data || !data._fields) return false;
                if (nowTs() - data._ts > DRAFT_TTL) {
                    localStorage.removeItem(key);
                    return false;
                }
                // 仅当有实质内容时才提示
                var hasValue = false;
                for (var k in data._fields) {
                    if (data._fields[k] && data._fields[k] !== '' && data._fields[k] !== false) {
                        hasValue = true;
                        break;
                    }
                }
                if (!hasValue) return false;

                if (window.M && M.confirm) {
                    M.confirm('检测到未完成的草稿，是否继续编辑？', function () {
                        applyData(data);
                        if (opts.onRestore) opts.onRestore(data);
                        if (window.M && M.toast) M.toast('已恢复草稿', 'info');
                    }, function () {
                        localStorage.removeItem(key);
                    });
                } else {
                    applyData(data);
                    if (opts.onRestore) opts.onRestore(data);
                }
                return true;
            }

            function applyData(data) {
                for (var fname in data._fields) {
                    if (fname.indexOf('__file_') === 0) continue;
                    var els = formEl.elements.namedItem(fname);
                    if (!els) continue;
                    if (els.length && els[0] && els[0].type === 'radio') {
                        for (var j = 0; j < els.length; j++) {
                            if (els[j].value === data._fields[fname]) { els[j].checked = true; break; }
                        }
                    } else if (els.type === 'checkbox') {
                        els.checked = !!data._fields[fname];
                    } else {
                        els.value = data._fields[fname];
                    }
                    // 触发 change 事件，便于业务联动
                    var evt = document.createEvent('Event');
                    evt.initEvent('change', true, true);
                    if (els.length) { for (var k = 0; k < els.length; k++) { /* skip */ } }
                    else els.dispatchEvent(evt);
                }
            }

            // 监听输入
            formEl.addEventListener('input', debouncedSave);
            formEl.addEventListener('change', debouncedSave);
            formEl.addEventListener('blur', debouncedSave, true);

            // 提交成功后清除草稿
            formEl.addEventListener('submit', function () {
                localStorage.removeItem(key);
            });

            // 进入页面检测草稿
            if (opts.autoRestore !== false) {
                setTimeout(restore, 300);
            }

            return {
                save: save,
                clear: function () { localStorage.removeItem(key); }
            };
        },

        /** 手动清除指定业务草稿 */
        clear: function (bizType) {
            var key = lsKey(DRAFT_PREFIX, bizType);
            localStorage.removeItem(key);
        },

        /**
         * 手动保存草稿数据（兼容旧API）
         * @param {string} bizType
         * @param {object} data 任意可序列化数据
         */
        save: function (bizType, data) {
            try {
                var key = lsKey(DRAFT_PREFIX, bizType);
                var payload = { _ts: nowTs(), _fields: {}, _raw: data };
                // 如果 data 是扁平对象，写入 _fields 便于 bind() 读取
                if (data && typeof data === 'object' && !Array.isArray(data)) {
                    for (var k in data) {
                        if (typeof data[k] !== 'function' && data[k] !== undefined) {
                            payload._fields[k] = data[k];
                        }
                    }
                }
                localStorage.setItem(key, JSON.stringify(payload));
            } catch (e) {
                console.warn('草稿保存失败', e);
            }
        },

        /**
         * 手动加载草稿数据（兼容旧API）
         * @param {string} bizType
         * @returns {object|null} 原始数据
         */
        load: function (bizType) {
            try {
                var key = lsKey(DRAFT_PREFIX, bizType);
                var raw = localStorage.getItem(key);
                if (!raw) return null;
                var data = JSON.parse(raw);
                if (data && data._raw) return data._raw;
                return data;
            } catch (e) { return null; }
        },

        /** 检测是否存在未提交草稿 */
        has: function (bizType) {
            var key = lsKey(DRAFT_PREFIX, bizType);
            var raw = localStorage.getItem(key);
            if (!raw) return false;
            var data = parseJSON(raw, null);
            if (!data) return false;
            if (nowTs() - (data._ts || 0) > DRAFT_TTL) {
                localStorage.removeItem(key);
                return false;
            }
            return true;
        },

        /** 清理所有过期草稿 */
        cleanup: function () {
            for (var i = 0; i < localStorage.length; i++) {
                var k = localStorage.key(i);
                if (k && k.indexOf(DRAFT_PREFIX + '_') === 0) {
                    var raw = localStorage.getItem(k);
                    var data = parseJSON(raw, null);
                    if (data && data._ts && nowTs() - data._ts > DRAFT_TTL) {
                        localStorage.removeItem(k);
                    }
                }
            }
        }
    };

    /* =========================================================
     * 2. MobileLocation - 现场定位留痕
     * 用法：
     *   MobileLocation.capture().then(function(loc){ ... });
     *   loc = { lat, lng, accuracy, time } 或 null（用户拒绝/未开启）
     * ========================================================= */
    var LOC_PROMPT_KEY = 'm_location_prompted';
    var MobileLocation = {
        /**
         * 检查后端是否启用了定位留痕
         * 由模板通过 window.MOBILE_LOCATION_ENABLED 注入
         */
        isEnabled: function () {
            return window.MOBILE_LOCATION_ENABLED === true;
        },

        /**
         * 一次性获取当前位置
         * @returns {Promise<object|null>} 位置对象或 null
         */
        capture: function () {
            return new Promise(function (resolve) {
                if (!MobileLocation.isEnabled()) {
                    resolve(null);
                    return;
                }
                if (!navigator.geolocation) {
                    console.warn('设备不支持地理定位');
                    resolve(null);
                    return;
                }
                // 首次使用前弹窗说明
                var prompted = localStorage.getItem(LOC_PROMPT_KEY);
                if (!prompted) {
                    if (window.M && M.confirm) {
                        M.confirm('为确认单据录入地点，系统将获取您的当前位置（仅本次，不追踪个人轨迹）。是否允许？',
                            function () {
                                localStorage.setItem(LOC_PROMPT_KEY, '1');
                                doCapture(resolve);
                            },
                            function () {
                                resolve(null);
                            });
                        return;
                    }
                }
                doCapture(resolve);
            });
        }
    };

    function doCapture(resolve) {
        navigator.geolocation.getCurrentPosition(
            function (pos) {
                resolve({
                    lat: pos.coords.latitude,
                    lng: pos.coords.longitude,
                    accuracy: pos.coords.accuracy,
                    time: new Date().toISOString()
                });
            },
            function (err) {
                console.warn('定位失败：', err.message);
                if (window.M && M.toast) {
                    M.toast('无法获取位置，请检查定位权限', 'warning');
                }
                resolve(null);
            },
            { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
        );
    }

    /* =========================================================
     * 3. MobileRecentUse - 最近使用优先排序
     * 用法：
     *   MobileRecentUse.record('material', { id: 1, name: '钢筋' });
     *   MobileRecentUse.merge('material', list);  // 在原列表前插入最近5条
     *   MobileRecentUse.getList('material', 5);
     * ========================================================= */
    var RECENT_PREFIX = 'm_recent';
    var RECENT_MAX = 20;
    var RECENT_TOP_N = 5;

    var MobileRecentUse = {
        /**
         * 记录一次使用
         * @param {string} bizType material / supplier / usage_unit / unit_team
         * @param {object} item { id, name, ... }
         */
        record: function (bizType, item) {
            if (!item || !item.id) return;
            var key = lsKey(RECENT_PREFIX, bizType);
            var list = parseJSON(localStorage.getItem(key) || '[]', []);
            // 移除已存在的同ID项
            list = list.filter(function (x) { return x.id !== item.id; });
            // 添加到最前
            list.unshift({ id: item.id, name: item.name, ts: nowTs() });
            // 限制数量
            if (list.length > RECENT_MAX) list = list.slice(0, RECENT_MAX);
            localStorage.setItem(key, JSON.stringify(list));
        },

        /**
         * 获取最近N条
         */
        getList: function (bizType, n) {
            var key = lsKey(RECENT_PREFIX, bizType);
            var list = parseJSON(localStorage.getItem(key) || '[]', []);
            return list.slice(0, n || RECENT_TOP_N);
        },

        /**
         * 将最近使用项合并到原列表前（去重）
         * @param {string} bizType
         * @param {array} list 原始列表（按原有规则排序）
         * @returns {array} 合并后列表
         */
        merge: function (bizType, list) {
            var recent = MobileRecentUse.getList(bizType, RECENT_TOP_N);
            if (!recent.length) return list || [];
            var recentIds = recent.map(function (x) { return x.id; });
            var rest = (list || []).filter(function (x) { return recentIds.indexOf(x.id) < 0; });
            return recent.concat(rest);
        },

        /**
         * 清空指定类型记录
         */
        clear: function (bizType) {
            var key = lsKey(RECENT_PREFIX, bizType);
            localStorage.removeItem(key);
        }
    };

    /* =========================================================
     * 4. MobileQuickEdit - 首页快捷功能自定义
     * 用法：
     *   MobileQuickEdit.init({
     *     container: '.m-quick-grid',  // 快捷按钮容器选择器
     *     available: [{key, label, icon, url, color}],  // 功能池
     *     maxCount: 8, minCount: 4
     *   });
     * ========================================================= */
    var QUICK_KEY = 'm_quick_config';
    var MobileQuickEdit = {
        _config: null,
        _available: null,
        _opts: null,

        /**
         * 初始化
         */
        init: function (opts) {
            this._opts = opts || {};
            this._available = opts.available || [];
            this._config = this._loadConfig();
        },

        _loadConfig: function () {
            var user = (window.CURRENT_USER && window.CURRENT_USER.id) || 'anon';
            var raw = localStorage.getItem(QUICK_KEY + '_' + user);
            var cfg = parseJSON(raw, null);
            if (!cfg || !Array.isArray(cfg.keys)) {
                // 默认4个
                cfg = { keys: ['stock_in', 'stock_out', 'inventory', 'concrete'] };
            }
            return cfg;
        },

        _saveConfig: function () {
            var user = (window.CURRENT_USER && window.CURRENT_USER.id) || 'anon';
            localStorage.setItem(QUICK_KEY + '_' + user, JSON.stringify(this._config));
        },

        /**
         * 渲染首页快捷按钮容器
         * @param {HTMLElement} container
         */
        render: function (container) {
            if (!container) return;
            var self = this;
            container.innerHTML = '';
            var map = {};
            this._available.forEach(function (it) { map[it.key] = it; });

            this._config.keys.forEach(function (key) {
                var item = map[key];
                if (!item) return;
                var a = document.createElement('a');
                a.href = item.url;
                a.className = 'm-quick-btn ' + (item.color || 'blue');
                a.dataset.key = key;
                a.innerHTML = '<i class="bi ' + item.icon + '"></i><span>' + item.label + '</span>';
                container.appendChild(a);
            });
        },

        /**
         * 打开编辑模式弹窗
         */
        openEditor: function () {
            var self = this;
            var overlay = document.createElement('div');
            overlay.className = 'm-quick-editor-mask';
            overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:9999;display:flex;flex-direction:column;justify-content:flex-end;';

            var panel = document.createElement('div');
            panel.style.cssText = 'background:#fff;border-radius:12px 12px 0 0;padding:16px;max-height:80vh;overflow-y:auto;';
            panel.innerHTML =
                '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">' +
                '<h6 style="margin:0;font-size:16px;font-weight:600;">编辑快捷功能</h6>' +
                '<button class="m-qe-close" style="background:none;border:none;font-size:22px;color:#666;">×</button>' +
                '</div>' +
                '<div style="font-size:12px;color:#6c757d;margin-bottom:8px;">已选 <span class="m-qe-count">0</span> / ' + (this._opts.maxCount || 8) + '（最少 ' + (this._opts.minCount || 4) + '）</div>' +
                '<div class="m-qe-selected" style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:16px;"></div>' +
                '<div style="font-size:13px;color:#6c757d;margin-bottom:8px;">可选功能池</div>' +
                '<div class="m-qe-pool" style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;"></div>' +
                '<div style="display:flex;gap:8px;margin-top:16px;">' +
                '<button class="m-qe-cancel" style="flex:1;padding:10px;border:1px solid #ddd;background:#fff;border-radius:6px;">取消</button>' +
                '<button class="m-qe-save" style="flex:2;padding:10px;background:#409eff;color:#fff;border:none;border-radius:6px;">保存</button>' +
                '</div>';

            overlay.appendChild(panel);
            document.body.appendChild(overlay);

            var selectedContainer = panel.querySelector('.m-qe-selected');
            var poolContainer = panel.querySelector('.m-qe-pool');
            var countEl = panel.querySelector('.m-qe-count');
            var selected = this._config.keys.slice();

            function renderSelected() {
                selectedContainer.innerHTML = '';
                countEl.textContent = selected.length;
                selected.forEach(function (key) {
                    var item = self._available.find(function (x) { return x.key === key; });
                    if (!item) return;
                    var div = document.createElement('div');
                    div.className = 'm-quick-btn ' + (item.color || 'blue');
                    div.style.cssText = 'cursor:pointer;position:relative;';
                    div.innerHTML = '<i class="bi ' + item.icon + '"></i><span>' + item.label + '</span>' +
                        '<i class="bi bi-x-circle" style="position:absolute;top:-4px;right:-4px;background:#fff;border-radius:50%;color:#dc3545;font-size:14px;"></i>';
                    div.addEventListener('click', function () {
                        selected = selected.filter(function (k) { return k !== key; });
                        renderSelected();
                        renderPool();
                    });
                    selectedContainer.appendChild(div);
                });
            }

            function renderPool() {
                poolContainer.innerHTML = '';
                self._available.forEach(function (item) {
                    var inSelected = selected.indexOf(item.key) >= 0;
                    var div = document.createElement('div');
                    div.className = 'm-quick-btn ' + (item.color || 'blue');
                    div.style.cssText = 'cursor:pointer;opacity:' + (inSelected ? '0.4' : '1') + ';';
                    div.innerHTML = '<i class="bi ' + item.icon + '"></i><span>' + item.label + '</span>';
                    if (!inSelected) {
                        div.addEventListener('click', function () {
                            if (selected.length >= (self._opts.maxCount || 8)) {
                                if (window.M && M.toast) M.toast('最多 ' + (self._opts.maxCount || 8) + ' 个', 'warning');
                                return;
                            }
                            selected.push(item.key);
                            renderSelected();
                            renderPool();
                        });
                    }
                    poolContainer.appendChild(div);
                });
            }

            renderSelected();
            renderPool();

            panel.querySelector('.m-qe-close').addEventListener('click', function () { overlay.remove(); });
            panel.querySelector('.m-qe-cancel').addEventListener('click', function () { overlay.remove(); });
            panel.querySelector('.m-qe-save').addEventListener('click', function () {
                if (selected.length < (self._opts.minCount || 4)) {
                    if (window.M && M.toast) M.toast('至少 ' + (self._opts.minCount || 4) + ' 个', 'warning');
                    return;
                }
                self._config.keys = selected;
                self._saveConfig();
                overlay.remove();
                window.location.reload();
            });
        }
    };

    /* =========================================================
     * 5. MobileConcreteOffline - 商砼小票离线存储与同步
     * 用法：
     *   MobileConcreteOffline.save(ticket) -> Promise<ticket>
     *   MobileConcreteOffline.getAll() -> Promise<ticket[]>
     *   MobileConcreteOffline.remove(client_id) -> Promise<bool>
     *   MobileConcreteOffline.autoSync() -> Promise<{synced, failed, skipped}>
     * ticket 字段：
     *   { client_id, ticket_no, supplier_id, supplier_name, strength_grade,
     *     pour_part, work_number_id, vehicle_no, vehicle_count, driver_name,
     *     volume, slump, arrival_time, remark, photos:[base64...],
     *     location:{lat,lng,accuracy}, created_at, status, sync_error }
     * ========================================================= */
    var MobileConcreteOffline = {
        DB_NAME: 'm_concrete_offline',
        DB_VERSION: 1,
        STORE: 'tickets',
        _db: null,

        /** 初始化 IndexedDB */
        init: function () {
            var self = this;
            return new Promise(function (resolve, reject) {
                if (self._db) return resolve(self._db);
                if (!window.indexedDB) {
                    reject(new Error('浏览器不支持 IndexedDB'));
                    return;
                }
                var req = indexedDB.open(self.DB_NAME, self.DB_VERSION);
                req.onupgradeneeded = function (e) {
                    var d = e.target.result;
                    if (!d.objectStoreNames.contains(self.STORE)) {
                        var s = d.createObjectStore(self.STORE, { keyPath: 'client_id' });
                        s.createIndex('status', 'status', { unique: false });
                        s.createIndex('created_at', 'created_at', { unique: false });
                    }
                };
                req.onsuccess = function (e) { self._db = e.target.result; resolve(self._db); };
                req.onerror = function (e) { reject(e.target.error); };
            });
        },

        _genClientId: function () {
            return 'ct-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
        },

        /** 保存一条离线小票 */
        save: function (ticket) {
            var self = this;
            if (!ticket.client_id) ticket.client_id = self._genClientId();
            if (!ticket.created_at) ticket.created_at = Date.now();
            if (!ticket.status) ticket.status = 'pending'; // pending / syncing / synced / failed
            return self.init().then(function (d) {
                return new Promise(function (resolve, reject) {
                    var tx = d.transaction([self.STORE], 'readwrite');
                    tx.objectStore(self.STORE).put(ticket);
                    tx.oncomplete = function () { resolve(ticket); };
                    tx.onerror = function (e) { reject(e.target.error); };
                });
            });
        },

        /** 获取所有未同步的离线小票 */
        getAll: function () {
            var self = this;
            return self.init().then(function (d) {
                return new Promise(function (resolve, reject) {
                    var tx = d.transaction([self.STORE], 'readonly');
                    var req = tx.objectStore(self.STORE).getAll();
                    req.onsuccess = function () { resolve(req.result || []); };
                    req.onerror = function (e) { reject(e.target.error); };
                });
            });
        },

        /** 删除已同步的离线小票 */
        remove: function (client_id) {
            var self = this;
            return self.init().then(function (d) {
                return new Promise(function (resolve, reject) {
                    var tx = d.transaction([self.STORE], 'readwrite');
                    tx.objectStore(self.STORE).delete(client_id);
                    tx.oncomplete = function () { resolve(true); };
                    tx.onerror = function (e) { reject(e.target.error); };
                });
            });
        },

        /** 更新同步状态（含错误信息） */
        updateStatus: function (client_id, status, errorMsg) {
            var self = this;
            return self.init().then(function (d) {
                return new Promise(function (resolve, reject) {
                    var tx = d.transaction([self.STORE], 'readwrite');
                    var store = tx.objectStore(self.STORE);
                    var getReq = store.get(client_id);
                    getReq.onsuccess = function () {
                        var data = getReq.result;
                        if (!data) return resolve(false);
                        data.status = status;
                        data.sync_error = errorMsg || null;
                        data.updated_at = Date.now();
                        store.put(data);
                    };
                    tx.oncomplete = function () { resolve(true); };
                    tx.onerror = function (e) { reject(e.target.error); };
                });
            });
        },

        /** 检查网络状态 */
        isOnline: function () { return navigator.onLine; },

        /** 自动同步：联网时调用 */
        autoSync: function () {
            var self = this;
            if (!self.isOnline()) return Promise.resolve({ synced: 0, failed: 0, skipped: 0 });
            return self.getAll().then(function (tickets) {
                if (!tickets || tickets.length === 0) {
                    return { synced: 0, failed: 0, skipped: 0 };
                }
                var pending = tickets.filter(function (t) {
                    return t.status === 'pending' || t.status === 'failed';
                });
                if (pending.length === 0) {
                    return { synced: 0, failed: 0, skipped: tickets.length };
                }
                var synced = 0, failed = 0;
                var chain = Promise.resolve();
                pending.forEach(function (ticket) {
                    chain = chain.then(function () {
                        return self._syncOne(ticket).then(function (ok) {
                            if (ok) synced++;
                            else failed++;
                        }).catch(function () { failed++; });
                    });
                });
                return chain.then(function () {
                    return {
                        synced: synced,
                        failed: failed,
                        skipped: tickets.length - pending.length
                    };
                });
            });
        },

        /** 同步单条小票到 /mobile/api/concrete/sync */
        _syncOne: function (ticket) {
            var self = this;
            return self.updateStatus(ticket.client_id, 'syncing').then(function () {
                return fetch('/mobile/api/concrete/sync', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ticket: ticket })
                }).then(function (r) {
                    return r.json().then(function (data) {
                        if (r.ok && data && data.success) {
                            return self.remove(ticket.client_id).then(function () { return true; });
                        }
                        var errMsg = (data && data.message) || ('HTTP ' + r.status);
                        return self.updateStatus(ticket.client_id, 'failed', errMsg)
                            .then(function () { return false; });
                    });
                }).catch(function (err) {
                    return self.updateStatus(ticket.client_id, 'failed', String(err))
                        .then(function () { return false; });
                });
            });
        }
    };

    /* =========================================================
     * 6. 启动时清理过期草稿
     * ========================================================= */
    if (document.readyState !== 'loading') {
        MobileDraft.cleanup();
    } else {
        document.addEventListener('DOMContentLoaded', MobileDraft.cleanup);
    }

    /* ========== 暴露到全局 ========== */
    window.MobileDraft = MobileDraft;
    window.MobileLocation = MobileLocation;
    window.MobileRecentUse = MobileRecentUse;
    window.MobileQuickEdit = MobileQuickEdit;
    window.MobileConcreteOffline = MobileConcreteOffline;

    /* ========== 提供将定位数据注入表单的辅助函数 ========== */
    window.injectLocationToForm = function (form, loc, prefix) {
        if (!form || !loc) return;
        prefix = prefix || 'location_';
        var fields = [
            { name: prefix + 'lat', value: loc.lat },
            { name: prefix + 'lng', value: loc.lng },
            { name: prefix + 'accuracy', value: loc.accuracy },
            { name: prefix + 'time', value: loc.time }
        ];
        fields.forEach(function (f) {
            var existing = form.querySelector('input[name="' + f.name + '"]');
            if (existing) {
                existing.value = f.value;
            } else {
                var input = document.createElement('input');
                input.type = 'hidden';
                input.name = f.name;
                input.value = f.value;
                form.appendChild(input);
            }
        });
    };
})();
