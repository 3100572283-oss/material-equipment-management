// 物资管理系统移动端 Service Worker
// Scope: /mobile/
// 策略：
//   - 静态资源（CSS/JS/图标）→ 缓存优先
//   - 移动端页面 GET 请求 → 网络优先，失败回退缓存
//   - API/POST 请求 → 直走网络
//   - 业务图片上传/同步 → 直走网络

var CACHE_VERSION = 'mobile-v2.0.1-20260721';
var STATIC_CACHE = CACHE_VERSION + '-static';
var PAGE_CACHE = CACHE_VERSION + '-pages';

// 预缓存的核心静态资源
var PRECACHE_URLS = [
    '/static/css/mobile.css',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/icons/favicon.png',
    'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css',
    'https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/dist/html5-qrcode.min.js'
];

// 移动端核心页面（用于离线访问的 fallback）
var PAGE_FALLBACKS = [
    '/mobile/',
    '/mobile/login'
];

// ========== 安装：预缓存 ==========
self.addEventListener('install', function(event){
    event.waitUntil(
        caches.open(STATIC_CACHE).then(function(cache){
            // 逐个添加，避免某个失败导致整体失败
            return Promise.all(
                PRECACHE_URLS.map(function(url){
                    return cache.add(url).catch(function(e){
                        console.warn('SW: 预缓存失败 ' + url, e);
                    });
                })
            );
        }).then(function(){
            // 立即激活（跳过等待）
            return self.skipWaiting();
        })
    );
});

// ========== 激活：清理旧缓存 ==========
self.addEventListener('activate', function(event){
    event.waitUntil(
        caches.keys().then(function(names){
            return Promise.all(
                names.filter(function(name){
                    return name.indexOf(CACHE_VERSION) !== 0;
                }).map(function(name){
                    return caches.delete(name);
                })
            );
        }).then(function(){
            // 立即接管所有客户端
            return self.clients.claim();
        })
    );
});

// ========== 请求拦截 ==========
self.addEventListener('fetch', function(event){
    var req = event.request;
    var url = new URL(req.url);

    // 只拦截同源请求
    if (url.origin !== self.location.origin) {
        // 外部 CDN 资源走缓存优先
        if (req.method === 'GET' && (url.hostname === 'cdn.jsdelivr.net')) {
            event.respondWith(cacheFirst(req, STATIC_CACHE));
        }
        return;
    }

    // 非 GET 请求（POST/PUT/DELETE）直接走网络
    if (req.method !== 'GET') {
        event.respondWith(fetch(req));
        return;
    }

    // 静态资源：JS/CSS 走网络优先（确保更新及时），其他走缓存优先
    if (url.pathname.startsWith('/static/')) {
        if (url.pathname.match(/\.(js|css)$/)) {
            // JS/CSS 文件：网络优先，避免缓存旧版本
            event.respondWith(networkFirst(req, STATIC_CACHE));
            return;
        }
        event.respondWith(cacheFirst(req, STATIC_CACHE));
        return;
    }

    // 移动端页面走网络优先
    if (url.pathname.startsWith('/mobile/')) {
        // 不缓存 API 和数据接口
        if (url.pathname.startsWith('/mobile/api/') ||
            url.pathname.indexOf('/sync') >= 0 ||
            url.pathname.indexOf('/save') >= 0 ||
            url.pathname.indexOf('/confirm') >= 0 ||
            url.pathname.indexOf('/approve') >= 0 ||
            url.pathname.indexOf('/reject') >= 0 ||
            url.pathname.indexOf('/read') >= 0) {
            event.respondWith(fetch(req));
            return;
        }
        event.respondWith(networkFirst(req, PAGE_CACHE));
        return;
    }

    // 其他请求直接走网络
    // event.respondWith(fetch(req));
});

// 缓存优先策略
function cacheFirst(req, cacheName){
    return caches.open(cacheName).then(function(cache){
        return cache.match(req).then(function(cached){
            if (cached) {
                // 后台异步更新
                fetch(req).then(function(resp){
                    if (resp && resp.ok) cache.put(req, resp.clone());
                }).catch(function(){});
                return cached;
            }
            return fetch(req).then(function(resp){
                if (resp && resp.ok) {
                    cache.put(req, resp.clone());
                }
                return resp;
            }).catch(function(){
                return new Response('网络不可用', {status: 503, statusText: 'Network Unavailable'});
            });
        });
    });
}

// 网络优先策略
function networkFirst(req, cacheName){
    return fetch(req).then(function(resp){
        // 只缓存成功的页面响应
        if (resp && resp.ok && resp.type === 'basic') {
            var respClone = resp.clone();
            caches.open(cacheName).then(function(cache){
                cache.put(req, respClone).catch(function(){});
            });
        }
        return resp;
    }).catch(function(){
        // 网络失败，回退到缓存
        return caches.open(cacheName).then(function(cache){
            return cache.match(req).then(function(cached){
                if (cached) return cached;
                // 都没有，回退到登录页
                return cache.match('/mobile/login');
            });
        });
    });
}

// ========== 消息通信 ==========
self.addEventListener('message', function(event){
    if (event.data === 'SKIP_WAITING') {
        self.skipWaiting();
    }
});

// ========== 推送通知（三期，预留）==========
self.addEventListener('push', function(event){
    if (!event.data) return;
    var data = {};
    try {
        data = event.data.json();
    } catch(e) {
        data = { title: '物资管理', body: event.data.text() };
    }
    var title = data.title || '物资管理系统';
    var options = {
        body: data.body || data.content || '',
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/favicon.png',
        tag: data.tag || 'default',
        data: data
    };
    event.waitUntil(
        self.registration.showNotification(title, options)
    );
});

// 推送点击：跳转到对应 URL
self.addEventListener('notificationclick', function(event){
    event.notification.close();
    var url = (event.notification.data && event.notification.data.url) || '/mobile/';
    event.waitUntil(
        clients.matchAll({type: 'window'}).then(function(clientList){
            for (var i = 0; i < clientList.length; i++) {
                var c = clientList[i];
                if (c.url.indexOf(url) >= 0 && 'focus' in c) {
                    return c.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(url);
            }
        })
    );
});
