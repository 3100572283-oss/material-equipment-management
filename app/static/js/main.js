document.addEventListener('DOMContentLoaded', function() {
    // ========== 侧边栏抽屉菜单 ==========
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebarOverlay = document.getElementById('sidebarOverlay');
    const wrapper = document.getElementById('wrapper');
    const sidebarWrapper = document.getElementById('sidebar-wrapper');
    const SIDEBAR_COLLAPSED_KEY = 'sidebar_collapsed';

    function isDesktop() {
        return window.innerWidth >= 1199.98;
    }

    function isMobile() {
        return window.innerWidth < 768;
    }

    function getSavedCollapsedState() {
        try {
            return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true';
        } catch (e) {
            return false;
        }
    }

    function saveCollapsedState(collapsed) {
        try {
            localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed));
        } catch (e) {}
    }

    function openSidebar() {
        if (wrapper) wrapper.classList.add('toggled');
        saveCollapsedState(true);
        if (sidebarOverlay) sidebarOverlay.classList.add('show');
        document.body.style.overflow = 'hidden';
    }

    function closeSidebar() {
        if (wrapper) wrapper.classList.remove('toggled');
        saveCollapsedState(false);
        if (sidebarOverlay) sidebarOverlay.classList.remove('show');
        document.body.style.overflow = '';
    }

    function toggleSidebar() {
        if (wrapper.classList.contains('toggled')) {
            closeSidebar();
        } else {
            openSidebar();
        }
    }

    // 初始化侧边栏状态
    if (isDesktop()) {
        if (getSavedCollapsedState()) {
            openSidebar();
        } else {
            closeSidebar();
        }
    } else if (isMobile()) {
        openSidebar();
    }

    if (sidebarToggle) {
        sidebarToggle.addEventListener('click', function(e) {
            e.preventDefault();
            toggleSidebar();
        });
    }

    // 点击遮罩层关闭
    if (sidebarOverlay) {
        sidebarOverlay.addEventListener('click', closeSidebar);
    }

    // 点击菜单项后，在移动端自动关闭侧边栏
    const sidebarLinks = document.querySelectorAll('#sidebar-wrapper .sidebar-item');
    sidebarLinks.forEach(function(link) {
        link.addEventListener('click', function() {
            // 保存侧边栏滚动位置(桌面端),避免整页跳转后菜单回到顶部
            if (sidebarWrapper && window.innerWidth >= 1199.98) {
                try {
                    sessionStorage.setItem('sidebar_scroll_top', sidebarWrapper.scrollTop);
                } catch (e) {}
            }
            if (window.innerWidth < 1199.98) {
                // 延迟关闭，让页面有机会跳转
                setTimeout(closeSidebar, 150);
            }
        });
    });

    // 页面卸载前保存侧边栏滚动位置(覆盖刷新、手动跳转等场景)
    window.addEventListener('beforeunload', function() {
        if (sidebarWrapper) {
            try {
                sessionStorage.setItem('sidebar_scroll_top', sidebarWrapper.scrollTop);
            } catch (e) {}
        }
    });

    // ESC 键关闭侧边栏
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && wrapper && wrapper.classList.contains('toggled')) {
            closeSidebar();
        }
    });

    // 窗口尺寸变化时处理状态
    window.addEventListener('resize', function() {
        if (isDesktop()) {
            if (getSavedCollapsedState()) {
                wrapper.classList.add('toggled');
            } else {
                wrapper.classList.remove('toggled');
            }
        }
    });

    // ========== 侧边栏分组折叠菜单 ==========
    const SIDEBAR_STATE_KEY = 'sidebar_group_state';
    const groups = document.querySelectorAll('.sidebar-group');
    const isMobile = window.innerWidth < 768;

    function saveGroupState() {
        const state = {};
        groups.forEach(function(group) {
            state[group.dataset.groupId] = group.classList.contains('expanded');
        });
        try {
            localStorage.setItem(SIDEBAR_STATE_KEY, JSON.stringify(state));
        } catch (e) {}
    }

    function loadGroupState() {
        try {
            return JSON.parse(localStorage.getItem(SIDEBAR_STATE_KEY)) || {};
        } catch (e) {
            return {};
        }
    }

    function expandGroup(group, save) {
        group.classList.add('expanded');
        const title = group.querySelector('.sidebar-group-title');
        const items = group.querySelector('.sidebar-group-items');
        if (title) title.setAttribute('aria-expanded', 'true');
        if (items) items.style.display = 'block';
        if (save !== false) saveGroupState();
    }

    function collapseGroup(group, save) {
        group.classList.remove('expanded');
        const title = group.querySelector('.sidebar-group-title');
        const items = group.querySelector('.sidebar-group-items');
        if (title) title.setAttribute('aria-expanded', 'false');
        if (items) items.style.display = 'none';
        if (save !== false) saveGroupState();
    }

    function toggleGroup(group) {
        const isExpanded = group.classList.contains('expanded');
        // 手风琴模式：同一时间只能展开一个分组
        if (!isExpanded) {
            groups.forEach(function(g) {
                if (g !== group && g.classList.contains('expanded')) {
                    collapseGroup(g, false);
                }
            });
            expandGroup(group);
        } else {
            collapseGroup(group);
        }
    }

    // 初始化分组状态
    const savedState = loadGroupState();
    groups.forEach(function(group) {
        const groupId = group.dataset.groupId;
        const isActive = group.classList.contains('active');
        let shouldExpand = false;

        if (isMobile) {
            // 手机端默认全部折叠，除非当前页面在该分组内
            shouldExpand = isActive;
        } else {
            // 桌面端优先使用保存的状态，未保存则展开当前所在分组
            if (savedState.hasOwnProperty(groupId)) {
                shouldExpand = savedState[groupId];
            } else {
                shouldExpand = isActive;
            }
        }

        if (shouldExpand) {
            expandGroup(group, false);
        } else {
            collapseGroup(group, false);
        }

        // 绑定标题点击事件
        const title = group.querySelector('.sidebar-group-title');
        if (title) {
            const sysLink = group.dataset.sysLink;
            if (sysLink) {
                // 系统管理分组：点击跳转独立页面
                title.addEventListener('click', function(e) {
                    e.preventDefault();
                    window.location.href = sysLink;
                });
            } else {
                title.addEventListener('click', function(e) {
                    e.preventDefault();
                    toggleGroup(group);
                });
            }
        }
    });

    // ========== 恢复侧边栏滚动位置 ==========
    // 必须在分组状态初始化之后执行,否则分组展开/折叠会改变内容高度,导致恢复位置不准确
    // 使用 requestAnimationFrame 确保浏览器已完成布局计算
    if (sidebarWrapper) {
        try {
            const savedScrollTop = sessionStorage.getItem('sidebar_scroll_top');
            if (savedScrollTop !== null) {
                const scrollTop = parseInt(savedScrollTop, 10) || 0;
                if (scrollTop > 0) {
                    requestAnimationFrame(function() {
                        sidebarWrapper.scrollTop = scrollTop;
                    });
                }
            }
        } catch (e) {}
    }

    // ========== 激活菜单项自动滚动到可视区域 ==========
    function scrollToActiveItem() {
        if (!sidebarWrapper) return;
        
        const activeItem = document.querySelector('.sidebar-item.active');
        if (!activeItem) return;

        const wrapperRect = sidebarWrapper.getBoundingClientRect();
        const itemRect = activeItem.getBoundingClientRect();

        const itemTop = itemRect.top - wrapperRect.top;
        const itemBottom = itemRect.bottom - wrapperRect.top;
        const viewportHeight = wrapperRect.height;

        const targetPosition = itemTop - viewportHeight * 0.3;

        if (itemTop < 0 || itemBottom > viewportHeight) {
            sidebarWrapper.scrollTo({
                top: Math.max(0, targetPosition),
                behavior: 'smooth'
            });
        }
    }

    setTimeout(function() {
        scrollToActiveItem();
    }, 100);

    // ========== 常用功能菜单 ==========
    const FAVORITES_KEY = 'sidebar_favorites';
    const MAX_FAVORITES = 10;
    const DEFAULT_FAVORITES = [
        'stock_in.index',
        'stock_out.index',
        'inventory.index',
        'contract.index'
    ];

    function getFavorites() {
        try {
            const raw = localStorage.getItem(FAVORITES_KEY);
            if (raw) return JSON.parse(raw);
        } catch (e) {}
        return DEFAULT_FAVORITES.slice();
    }

    function saveFavorites(list) {
        try {
            localStorage.setItem(FAVORITES_KEY, JSON.stringify(list));
        } catch (e) {}
    }

    function renderFavorites() {
        const list = getFavorites();
        const container = document.getElementById('favoritesList');
        if (!container) return;
        container.innerHTML = '';

        const validItems = [];
        list.forEach(function(endpoint) {
            const el = document.querySelector('.sidebar-item[data-endpoint="' + endpoint + '"]');
            if (el) validItems.push(endpoint);
        });

        if (validItems.length === 0) {
            container.innerHTML = '<div class="sidebar-item text-muted" style="cursor:default;font-size:12px;padding-left:36px;">暂无常用功能</div>';
            return;
        }

        validItems.forEach(function(endpoint) {
            const el = document.querySelector('.sidebar-item[data-endpoint="' + endpoint + '"]');
            if (!el) return;
            const a = document.createElement('a');
            a.href = el.href;
            a.className = 'sidebar-item';
            if (el.classList.contains('active')) a.classList.add('active');
            a.dataset.endpoint = endpoint;
            a.innerHTML = el.dataset.title + '<span class="fav-remove" title="移除"><i class="bi bi-x-lg"></i></span>';
            a.addEventListener('click', function() {
                if (window.innerWidth < 1199.98) {
                    setTimeout(closeSidebar, 150);
                }
            });
            container.appendChild(a);
        });

        container.querySelectorAll('.fav-remove').forEach(function(btn) {
            btn.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                const endpoint = btn.closest('.sidebar-item').dataset.endpoint;
                removeFavorite(endpoint);
            });
        });

        updateStarStates();
    }

    function updateStarStates() {
        const list = getFavorites();
        document.querySelectorAll('.sidebar-item .fav-star').forEach(function(star) {
            const item = star.closest('.sidebar-item');
            const endpoint = item.dataset.endpoint;
            if (list.indexOf(endpoint) !== -1) {
                star.classList.add('active');
                star.title = '取消常用';
                star.querySelector('i').className = 'bi bi-star-fill';
            } else {
                star.classList.remove('active');
                star.title = '加入常用';
                star.querySelector('i').className = 'bi bi-star';
            }
        });
    }

    function addFavorite(endpoint) {
        const list = getFavorites();
        if (list.indexOf(endpoint) !== -1) return;
        if (list.length >= MAX_FAVORITES) {
            alert('常用功能最多10个，请先移除部分');
            return;
        }
        list.unshift(endpoint);
        saveFavorites(list);
        renderFavorites();
    }

    function removeFavorite(endpoint) {
        const list = getFavorites();
        const idx = list.indexOf(endpoint);
        if (idx !== -1) {
            list.splice(idx, 1);
            saveFavorites(list);
            renderFavorites();
        }
    }

    document.querySelectorAll('.sidebar-item .fav-star').forEach(function(star) {
        star.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            const item = star.closest('.sidebar-item');
            const endpoint = item.dataset.endpoint;
            const list = getFavorites();
            if (list.indexOf(endpoint) !== -1) {
                removeFavorite(endpoint);
            } else {
                addFavorite(endpoint);
            }
        });
    });

    renderFavorites();

    // ========== 显示密度切换 ==========
    const DENSITY_KEY = 'ui_density';
    const densityBtn = document.getElementById('densityToggle');
    const densityMenu = document.getElementById('densityMenu');

    function setDensity(mode) {
        document.body.classList.remove('density-standard', 'density-compact');
        document.body.classList.add('density-' + mode);
        try {
            localStorage.setItem(DENSITY_KEY, mode);
        } catch (e) {}
        if (densityMenu) {
            densityMenu.querySelectorAll('.dropdown-item').forEach(function(item) {
                item.classList.remove('active');
                if (item.dataset.density === mode) item.classList.add('active');
            });
        }
    }

    function initDensity() {
        let mode = 'compact';
        try {
            const saved = localStorage.getItem(DENSITY_KEY);
            if (saved) mode = saved;
        } catch (e) {}
        setDensity(mode);
    }

    if (densityMenu) {
        densityMenu.querySelectorAll('.dropdown-item').forEach(function(item) {
            item.addEventListener('click', function(e) {
                e.preventDefault();
                setDensity(item.dataset.density);
            });
        });
    }

    initDensity();

    // ========== 当前日期显示 ==========
    const dateEl = document.getElementById('currentDate');
    if (dateEl) {
        const now = new Date();
        const options = { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' };
        dateEl.textContent = now.toLocaleDateString('zh-CN', options);
    }

    // ========== 移动端表格优化 ==========
    // 为所有 .table-responsive 自动添加首列固定样式
    function enhanceMobileTables() {
        if (window.innerWidth >= 768) return;

        const tables = document.querySelectorAll('.table-responsive table.table');
        tables.forEach(function(table) {
            // 添加首列固定（仅当表格宽度超出容器时）
            const firstTh = table.querySelector('thead th:first-child');
            const firstTds = table.querySelectorAll('tbody td:first-child');
            if (firstTh && !firstTh.classList.contains('sticky-col')) {
                firstTh.classList.add('sticky-col');
            }
            firstTds.forEach(function(td) {
                if (!td.classList.contains('sticky-col')) {
                    td.classList.add('sticky-col');
                }
            });

            // tfoot首列也固定
            const tfootFirstTd = table.querySelector('tfoot td:first-child');
            if (tfootFirstTd && !tfootFirstTd.classList.contains('sticky-col')) {
                tfootFirstTd.classList.add('sticky-col');
            }
        });
    }

    enhanceMobileTables();
    window.addEventListener('resize', enhanceMobileTables);

    // ========== 分页简化（移动端） ==========
    function simplifyPagination() {
        if (window.innerWidth >= 768) return;

        const paginations = document.querySelectorAll('.pagination');
        paginations.forEach(function(pagination) {
            if (pagination.classList.contains('pagination-mobile-compact')) return;
            pagination.classList.add('pagination-mobile-compact');

            // 给首末页和前后页添加标记类
            const items = pagination.querySelectorAll('.page-item');
            items.forEach(function(item, idx) {
                const link = item.querySelector('.page-link');
                if (!link) return;
                const text = link.textContent.trim();
                if (text.includes('«') || text.includes('‹') || idx === 0) {
                    item.classList.add('page-prev');
                }
                if (text.includes('»') || text.includes('›') || idx === items.length - 1) {
                    item.classList.add('page-next');
                }
            });
        });
    }

    simplifyPagination();
    window.addEventListener('resize', simplifyPagination);

    // ========== 表单输入框聚焦时避免键盘遮挡 ==========
    if (window.innerWidth < 768) {
        const inputs = document.querySelectorAll('input[type="text"], input[type="number"], input[type="email"], input[type="date"], textarea, select');
        inputs.forEach(function(input) {
            input.addEventListener('focus', function() {
                setTimeout(function() {
                    input.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }, 300);
            });
        });
    }

    // ========== 模态框打开时禁用 body 滚动（移动端） ==========
    document.querySelectorAll('.modal').forEach(function(modal) {
        modal.addEventListener('shown.bs.modal', function() {
            if (window.innerWidth < 768) {
                document.body.style.overflow = 'hidden';
            }
        });
        modal.addEventListener('hidden.bs.modal', function() {
            document.body.style.overflow = '';
        });
    });

    // ========== 阻止双击缩放（移动端） ==========
    let lastTouchEnd = 0;
    document.addEventListener('touchend', function(e) {
        const now = Date.now();
        if (now - lastTouchEnd <= 300) {
            e.preventDefault();
        }
        lastTouchEnd = now;
    }, { passive: false });

    // ========== 关闭 Flash 消息（3秒后自动关闭） ==========
    setTimeout(function() {
        document.querySelectorAll('.alert').forEach(function(alert) {
            const bsAlert = bootstrap.Alert.getInstance(alert);
            if (bsAlert) bsAlert.close();
        });
    }, 5000);

    // ========== AI助手功能 ==========
    (function() {
        const floatBtn = document.getElementById('aiFloatButton');
        const chatPanel = document.getElementById('aiChatPanel');
        const chatOverlay = document.getElementById('aiChatOverlay');
        const closeBtn = document.getElementById('aiCloseBtn');
        const messagesContainer = document.getElementById('aiChatMessages');
        const inputEl = document.getElementById('aiInput');
        const sendBtn = document.getElementById('aiSendBtn');
        const quickButtons = document.querySelectorAll('.ai-quick-btn');

        let chatContext = [];
        let isSending = false;

        function checkAIEnabled() {
            fetch('/ai/is_enabled')
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    if (data.enabled && floatBtn) {
                        floatBtn.style.display = 'flex';
                    }
                })
                .catch(function() {});
        }

        function openChat() {
            if (!chatPanel || !chatOverlay) return;
            chatPanel.classList.add('show');
            chatOverlay.style.display = 'block';
            document.body.style.overflow = 'hidden';
            setTimeout(function() { inputEl && inputEl.focus(); }, 300);
        }

        function closeChat() {
            if (!chatPanel || !chatOverlay) return;
            chatPanel.classList.remove('show');
            chatOverlay.style.display = 'none';
            document.body.style.overflow = '';
        }

        function addMessage(text, type) {
            if (!messagesContainer) return;
            const div = document.createElement('div');
            div.className = 'ai-message ' + type;
            div.innerHTML = '<div class="ai-message-bubble">' + escapeHtml(text) + '</div>';
            messagesContainer.appendChild(div);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }

        function addTypingIndicator() {
            if (!messagesContainer) return;
            const div = document.createElement('div');
            div.className = 'ai-message ai';
            div.innerHTML = '<div class="ai-message-bubble"><div class="ai-typing-indicator"><span></span><span></span><span></span></div></div>';
            div.id = 'ai-typing-indicator';
            messagesContainer.appendChild(div);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }

        function removeTypingIndicator() {
            const indicator = document.getElementById('ai-typing-indicator');
            if (indicator) indicator.remove();
        }

        function escapeHtml(text) {
            const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
            return text.replace(/[&<>"']/g, function(m) { return map[m]; });
        }

        function formatAIResponse(text) {
            text = escapeHtml(text);
            text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
            text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
            text = text.replace(/\n/g, '<br>');
            text = text.replace(/^\d+\.\s/gm, function(m) { return '<br>' + m; });
            return text;
        }

        function sendMessage() {
            const text = inputEl.value.trim();
            if (!text || isSending) return;

            isSending = true;
            inputEl.value = '';
            addMessage(text, 'user');
            addTypingIndicator();

            chatContext.push({ role: 'user', content: text });

            // 超时控制: 30秒后中止请求
            var controller = new AbortController();
            var timeoutId = setTimeout(function() { controller.abort(); }, 30000);

            fetch('/ai/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: text,
                    context: chatContext.slice(-5)
                }),
                signal: controller.signal
            })
            .then(function(res) { return res.json(); })
            .then(function(data) {
                clearTimeout(timeoutId);
                removeTypingIndicator();
                if (data.success) {
                    addMessage(formatAIResponse(data.message), 'ai');
                    chatContext.push({ role: 'assistant', content: data.message });
                } else {
                    addMessage('AI服务暂时不可用：' + (data.message || '未知错误'), 'ai');
                }
            })
            .catch(function(err) {
                clearTimeout(timeoutId);
                removeTypingIndicator();
                if (err.name === 'AbortError') {
                    addMessage('AI 响应超时,请稍后重试', 'ai');
                } else {
                    addMessage('AI服务暂时不可用,请稍后再试', 'ai');
                }
            })
            .finally(function() {
                isSending = false;
            });
        }

        if (floatBtn) {
            floatBtn.addEventListener('click', openChat);
        }

        if (closeBtn) {
            closeBtn.addEventListener('click', closeChat);
        }

        if (chatOverlay) {
            chatOverlay.addEventListener('click', closeChat);
        }

        if (sendBtn) {
            sendBtn.addEventListener('click', sendMessage);
        }

        if (inputEl) {
            inputEl.addEventListener('keydown', function(e) {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendMessage();
                }
            });
        }

        quickButtons.forEach(function(btn) {
            btn.addEventListener('click', function() {
                const msg = btn.dataset.msg;
                if (msg) {
                    inputEl.value = msg;
                    sendMessage();
                }
            });
        });

        checkAIEnabled();
    })();
});
