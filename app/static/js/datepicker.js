/**
 * 统一日期选择器组件
 * 支持快速选年、选月，日期选择，月份选择
 * 自动增强所有 input[type="date"] 和 input[type="month"]
 */
(function() {
    'use strict';

    var pickerId = 0;
    var pickerMap = {};  // panel.id -> DatePicker 实例，便于事件中查找

    function DatePicker(input, options) {
        this.id = ++pickerId;
        this.input = input;
        this.options = options || {};
        var inputType = input.getAttribute('data-dtp-type') || input.type || 'date';
        if (this.options.mode) {
            this.mode = this.options.mode;
        } else if (inputType === 'month') {
            this.mode = 'month';
        } else if (inputType === 'datetime-local') {
            this.mode = 'datetime';
        } else if (inputType === 'time') {
            this.mode = 'time';
        } else {
            this.mode = 'date';
        }
        this.isOpen = false;
        this.viewDate = new Date();
        this.selectedDate = null;

        this.init();
    }

    DatePicker.prototype.init = function() {
        var self = this;

        this.wrapper = document.createElement('div');
        this.wrapper.className = 'dtp-wrapper';
        this.wrapper.style.position = 'relative';
        this.wrapper.style.display = 'inline-block';
        this.wrapper.style.width = '100%';

        this.input.parentNode.insertBefore(this.wrapper, this.input);
        this.wrapper.appendChild(this.input);

        // type 已在 initDatePickers 中改为 text，这里只需确保 data-dtp-type 存在
        if (!this.input.getAttribute('data-dtp-type')) {
            this.input.setAttribute('data-dtp-type', this.input.type || 'date');
        }

        this.input.style.paddingRight = '32px';
        this.input.style.cursor = 'pointer';

        var icon = document.createElement('i');
        icon.className = 'bi bi-calendar3 dtp-icon';
        icon.style.cssText = 'position:absolute;right:8px;top:50%;transform:translateY(-50%);pointer-events:none;color:#6c757d;font-size:14px;';
        this.wrapper.appendChild(icon);

        this.panel = document.createElement('div');
        this.panel.className = 'dtp-panel';
        this.panel.id = 'dtp-panel-' + this.id;
        this.panel.style.display = 'none';
        document.body.appendChild(this.panel);
        pickerMap[this.panel.id] = this;

        this.parseInputValue();

        this.input.addEventListener('click', function(e) {
            e.stopPropagation();
            self.toggle();
        });

        this.input.addEventListener('focus', function() {
            if (!self.isOpen) self.show();
        });

        this.input.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                self.hide();
            }
        });

        this.render();
    };

    DatePicker.prototype.parseInputValue = function() {
        var val = this.input.value;
        if (val) {
            if (this.mode === 'month') {
                var parts = val.split('-');
                this.selectedDate = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, 1);
                this.viewDate = new Date(this.selectedDate);
            } else if (this.mode === 'datetime') {
                // 格式：YYYY-MM-DDTHH:MM
                var d = new Date(val.replace('T', ' '));
                if (!isNaN(d.getTime())) {
                    this.selectedDate = d;
                    this.viewDate = new Date(d);
                }
            } else if (this.mode === 'time') {
                // 格式：HH:MM
                var tp = val.split(':');
                this.selectedTime = { h: parseInt(tp[0]) || 0, m: parseInt(tp[1]) || 0 };
            } else {
                var d = new Date(val);
                if (!isNaN(d.getTime())) {
                    this.selectedDate = d;
                    this.viewDate = new Date(d);
                }
            }
        }
    };

    DatePicker.prototype.render = function() {
        var year = this.viewDate.getFullYear();
        var month = this.viewDate.getMonth();
        var today = new Date();

        var html = '';
        html += '<div class="dtp-header">';
        html += '  <button type="button" class="dtp-nav-btn" data-action="prev-year" title="上一年"><i class="bi bi-chevron-double-left"></i></button>';
        html += '  <button type="button" class="dtp-nav-btn" data-action="prev-month" title="上一月"><i class="bi bi-chevron-left"></i></button>';
        html += '  <div class="dtp-title">';
        html += '    <select class="dtp-year-select" data-action="year"></select>';
        html += '    <select class="dtp-month-select" data-action="month"></select>';
        html += '  </div>';
        html += '  <button type="button" class="dtp-nav-btn" data-action="next-month" title="下一月"><i class="bi bi-chevron-right"></i></button>';
        html += '  <button type="button" class="dtp-nav-btn" data-action="next-year" title="下一年"><i class="bi bi-chevron-double-right"></i></button>';
        html += '</div>';

        if (this.mode === 'date' || this.mode === 'datetime') {
            html += '<div class="dtp-body">';
            html += '  <div class="dtp-weekdays">';
            var weekDays = ['日', '一', '二', '三', '四', '五', '六'];
            weekDays.forEach(function(d) {
                html += '<div class="dtp-weekday">' + d + '</div>';
            });
            html += '  </div>';
            html += '  <div class="dtp-days">';

            var firstDay = new Date(year, month, 1);
            var startDay = firstDay.getDay();
            var daysInMonth = new Date(year, month + 1, 0).getDate();
            var prevMonthDays = new Date(year, month, 0).getDate();

            for (var i = startDay - 1; i >= 0; i--) {
                var day = prevMonthDays - i;
                html += '<div class="dtp-day dtp-other-month" data-year="' + (month === 0 ? year - 1 : year) + '" data-month="' + (month === 0 ? 11 : month - 1) + '" data-day="' + day + '">' + day + '</div>';
            }

            for (var d = 1; d <= daysInMonth; d++) {
                var classes = ['dtp-day'];
                var curDate = new Date(year, month, d);
                if (this.selectedDate &&
                    curDate.getFullYear() === this.selectedDate.getFullYear() &&
                    curDate.getMonth() === this.selectedDate.getMonth() &&
                    curDate.getDate() === this.selectedDate.getDate()) {
                    classes.push('dtp-selected');
                }
                if (curDate.getFullYear() === today.getFullYear() &&
                    curDate.getMonth() === today.getMonth() &&
                    curDate.getDate() === today.getDate()) {
                    classes.push('dtp-today');
                }
                if (curDate.getDay() === 0 || curDate.getDay() === 6) {
                    classes.push('dtp-weekend');
                }
                html += '<div class="' + classes.join(' ') + '" data-year="' + year + '" data-month="' + month + '" data-day="' + d + '">' + d + '</div>';
            }

            var remaining = 42 - (startDay + daysInMonth);
            if (remaining < 0) remaining = 7 + remaining;
            for (var j = 1; j <= remaining; j++) {
                html += '<div class="dtp-day dtp-other-month" data-year="' + (month === 11 ? year + 1 : year) + '" data-month="' + (month === 11 ? 0 : month + 1) + '" data-day="' + j + '">' + j + '</div>';
            }

            html += '  </div>';
            html += '</div>';
        }

        if (this.mode === 'datetime' || this.mode === 'time') {
            // 时间选择部分
            var h = (this.selectedDate ? this.selectedDate.getHours() : (this.selectedTime ? this.selectedTime.h : 0));
            var m = (this.selectedDate ? this.selectedDate.getMinutes() : (this.selectedTime ? this.selectedTime.m : 0));
            html += '<div class="dtp-time">';
            html += '  <select class="dtp-hour-select" data-action="hour">';
            for (var hh = 0; hh < 24; hh++) {
                html += '<option value="' + hh + '"' + (hh === h ? ' selected' : '') + '>' + String(hh).padStart(2, '0') + '时</option>';
            }
            html += '  </select>';
            html += '  <span class="dtp-time-colon">:</span>';
            html += '  <select class="dtp-minute-select" data-action="minute">';
            for (var mm = 0; mm < 60; mm++) {
                html += '<option value="' + mm + '"' + (mm === m ? ' selected' : '') + '>' + String(mm).padStart(2, '0') + '分</option>';
            }
            html += '  </select>';
            html += '  <button type="button" class="dtp-btn-now" data-action="now">此刻</button>';
            html += '</div>';
        }

        html += '<div class="dtp-footer">';
        html += '  <button type="button" class="dtp-btn-today" data-action="today">今天</button>';
        html += '  <button type="button" class="dtp-btn-clear" data-action="clear">清空</button>';
        html += '</div>';

        this.panel.innerHTML = html;

        var yearSelect = this.panel.querySelector('.dtp-year-select');
        var monthSelect = this.panel.querySelector('.dtp-month-select');

        for (var y = year - 50; y <= year + 50; y++) {
            var opt = document.createElement('option');
            opt.value = y;
            opt.textContent = y + '年';
            if (y === year) opt.selected = true;
            yearSelect.appendChild(opt);
        }

        var monthNames = ['一月', '二月', '三月', '四月', '五月', '六月', '七月', '八月', '九月', '十月', '十一月', '十二月'];
        for (var m = 0; m < 12; m++) {
            var mopt = document.createElement('option');
            mopt.value = m;
            mopt.textContent = monthNames[m];
            if (m === month) mopt.selected = true;
            monthSelect.appendChild(mopt);
        }

        this.bindEvents();
    };

    DatePicker.prototype.bindEvents = function() {
        var self = this;

        this.panel.querySelectorAll('[data-action]').forEach(function(el) {
            el.addEventListener('click', function(e) {
                e.stopPropagation();
                var action = this.getAttribute('data-action');
                self.handleAction(action, this);
            });
        });

        if (this.mode === 'date' || this.mode === 'datetime') {
            this.panel.querySelectorAll('.dtp-day').forEach(function(day) {
                day.addEventListener('click', function(e) {
                    e.stopPropagation();
                    var y = parseInt(this.getAttribute('data-year'));
                    var m = parseInt(this.getAttribute('data-month'));
                    var d = parseInt(this.getAttribute('data-day'));
                    self.selectDate(y, m, d);
                });
            });
        }

        var hourSelect = this.panel.querySelector('.dtp-hour-select');
        var minuteSelect = this.panel.querySelector('.dtp-minute-select');
        if (hourSelect) {
            hourSelect.addEventListener('change', function(e) {
                e.stopPropagation();
                self.updateTime();
            });
        }
        if (minuteSelect) {
            minuteSelect.addEventListener('change', function(e) {
                e.stopPropagation();
                self.updateTime();
            });
        }

        var yearSelect = this.panel.querySelector('.dtp-year-select');
        var monthSelect = this.panel.querySelector('.dtp-month-select');

        yearSelect.addEventListener('change', function(e) {
            e.stopPropagation();
            self.viewDate.setFullYear(parseInt(this.value));
            self.render();
            if (self.mode === 'month') {
                self.selectMonth();
            }
        });

        monthSelect.addEventListener('change', function(e) {
            e.stopPropagation();
            self.viewDate.setMonth(parseInt(this.value));
            self.render();
            if (self.mode === 'month') {
                self.selectMonth();
            }
        });
    };

    DatePicker.prototype.handleAction = function(action, el) {
        switch (action) {
            case 'prev-year':
                this.viewDate.setFullYear(this.viewDate.getFullYear() - 1);
                this.render();
                break;
            case 'next-year':
                this.viewDate.setFullYear(this.viewDate.getFullYear() + 1);
                this.render();
                break;
            case 'prev-month':
                this.viewDate.setMonth(this.viewDate.getMonth() - 1);
                this.render();
                break;
            case 'next-month':
                this.viewDate.setMonth(this.viewDate.getMonth() + 1);
                this.render();
                break;
            case 'today':
                var t = new Date();
                this.viewDate = new Date(t);
                if (this.mode === 'date') {
                    this.selectedDate = t;
                    this.setInputValue(t);
                } else if (this.mode === 'datetime') {
                    this.selectedDate = t;
                    this.setInputDatetime(t);
                } else if (this.mode === 'time') {
                    this.selectedTime = { h: t.getHours(), m: t.getMinutes() };
                    this.setInputTime(t.getHours(), t.getMinutes());
                } else {
                    this.selectedDate = new Date(t.getFullYear(), t.getMonth(), 1);
                    this.setInputMonth(t.getFullYear(), t.getMonth());
                }
                this.render();
                this.hide();
                break;
            case 'now':
                var n = new Date();
                if (this.mode === 'time') {
                    this.selectedTime = { h: n.getHours(), m: n.getMinutes() };
                    this.setInputTime(n.getHours(), n.getMinutes());
                } else if (this.mode === 'datetime') {
                    this.selectedDate = n;
                    this.setInputDatetime(n);
                }
                this.render();
                this.hide();
                break;
            case 'clear':
                this.selectedDate = null;
                this.selectedTime = null;
                this.input.value = '';
                this.input.dispatchEvent(new Event('change', { bubbles: true }));
                this.render();
                this.hide();
                break;
        }
    };

    DatePicker.prototype.selectDate = function(year, month, day) {
        // datetime 模式保留之前的时间部分
        var prevDate = this.selectedDate;
        var h = prevDate ? prevDate.getHours() : 0;
        var m = prevDate ? prevDate.getMinutes() : 0;
        this.selectedDate = new Date(year, month, day, h, m);
        this.viewDate = new Date(year, month, day, h, m);

        if (this.mode === 'date') {
            this.setInputValue(this.selectedDate);
            this.render();
            this.hide();
        } else if (this.mode === 'datetime') {
            this.setInputDatetime(this.selectedDate);
            this.render();
            // datetime 模式下不关闭，等用户确认时间
        }
    };

    DatePicker.prototype.updateTime = function() {
        var hourSelect = this.panel.querySelector('.dtp-hour-select');
        var minuteSelect = this.panel.querySelector('.dtp-minute-select');
        if (!hourSelect || !minuteSelect) return;
        var h = parseInt(hourSelect.value) || 0;
        var m = parseInt(minuteSelect.value) || 0;

        if (this.mode === 'time') {
            this.selectedTime = { h: h, m: m };
            this.setInputTime(h, m);
        } else if (this.mode === 'datetime') {
            if (!this.selectedDate) {
                var t = new Date();
                this.selectedDate = new Date(this.viewDate.getFullYear(), this.viewDate.getMonth(), this.viewDate.getDate(), h, m);
            } else {
                this.selectedDate.setHours(h, m);
            }
            this.setInputDatetime(this.selectedDate);
        }
    };

    DatePicker.prototype.setInputDatetime = function(date) {
        var y = date.getFullYear();
        var mo = String(date.getMonth() + 1).padStart(2, '0');
        var d = String(date.getDate()).padStart(2, '0');
        var h = String(date.getHours()).padStart(2, '0');
        var mi = String(date.getMinutes()).padStart(2, '0');
        this.input.value = y + '-' + mo + '-' + d + 'T' + h + ':' + mi;
        this.input.dispatchEvent(new Event('change', { bubbles: true }));
        this.input.dispatchEvent(new Event('input', { bubbles: true }));
    };

    DatePicker.prototype.setInputTime = function(h, m) {
        this.input.value = String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0');
        this.input.dispatchEvent(new Event('change', { bubbles: true }));
        this.input.dispatchEvent(new Event('input', { bubbles: true }));
    };

    DatePicker.prototype.selectMonth = function() {
        var y = this.viewDate.getFullYear();
        var m = this.viewDate.getMonth();
        this.selectedDate = new Date(y, m, 1);
        this.setInputMonth(y, m);
    };

    DatePicker.prototype.setInputValue = function(date) {
        var y = date.getFullYear();
        var m = String(date.getMonth() + 1).padStart(2, '0');
        var d = String(date.getDate()).padStart(2, '0');
        this.input.value = y + '-' + m + '-' + d;
        this.input.dispatchEvent(new Event('change', { bubbles: true }));
        this.input.dispatchEvent(new Event('input', { bubbles: true }));
    };

    DatePicker.prototype.setInputMonth = function(year, month) {
        var y = year;
        var m = String(month + 1).padStart(2, '0');
        this.input.value = y + '-' + m;
        this.input.dispatchEvent(new Event('change', { bubbles: true }));
        this.input.dispatchEvent(new Event('input', { bubbles: true }));
        this.hide();
    };

    DatePicker.prototype.show = function() {
        var self = this;
        // 关闭其他已打开的面板
        document.querySelectorAll('.dtp-panel').forEach(function(p) {
            if (p !== self.panel && p.style.display !== 'none') {
                var picker = pickerMap[p.id];
                if (picker) picker.hide();
                else p.style.display = 'none';
            }
        });
        this.isOpen = true;
        this.parseInputValue();
        if (!this.selectedDate && (this.mode === 'date' || this.mode === 'datetime')) {
            this.viewDate = new Date();
        }
        this.render();
        this.panel.style.display = 'block';

        requestAnimationFrame(function() {
            self.positionPanel();
        });
    };

    DatePicker.prototype.hide = function() {
        this.isOpen = false;
        this.panel.style.display = 'none';
    };

    DatePicker.prototype.toggle = function() {
        if (this.isOpen) {
            this.hide();
        } else {
            this.show();
        }
    };

    DatePicker.prototype.positionPanel = function() {
        // 使用 fixed 定位，坐标基于视口，不需要加 scrollTop/scrollLeft
        var rect = this.wrapper.getBoundingClientRect();
        var panelRect = this.panel.getBoundingClientRect();
        var viewportH = window.innerHeight;
        var viewportW = window.innerWidth;

        var top = rect.bottom + 4;
        var left = rect.left;

        // 下方空间不足时，弹出在上方
        if (top + panelRect.height > viewportH) {
            top = rect.top - panelRect.height - 4;
        }
        // 上方也不够时，强制贴底
        if (top < 0) {
            top = Math.max(8, viewportH - panelRect.height - 8);
        }

        // 右侧溢出时左移
        if (left + panelRect.width > viewportW) {
            left = viewportW - panelRect.width - 8;
        }
        if (left < 8) {
            left = 8;
        }

        this.panel.style.top = top + 'px';
        this.panel.style.left = left + 'px';
    };

    function initDatePickers() {
        var selector = 'input[type="date"]:not(.dtp-initialized), ' +
                       'input[type="month"]:not(.dtp-initialized), ' +
                       'input[type="datetime-local"]:not(.dtp-initialized), ' +
                       'input[type="time"]:not(.dtp-initialized)';
        document.querySelectorAll(selector).forEach(function(input) {
            // 已经在 wrapper 内或父级是 wrapper，跳过
            if (input.closest('.dtp-wrapper')) return;
            if (input.parentNode && input.parentNode.classList &&
                input.parentNode.classList.contains('dtp-wrapper')) return;

            input.classList.add('dtp-initialized');
            // 立即把 type 改成 text，彻底禁用浏览器原生日历
            input.setAttribute('data-dtp-type', input.type);
            input.type = 'text';

            new DatePicker(input, {});
        });
    }

    document.addEventListener('click', function(e) {
        // 点击在任意 wrapper 内，不处理（让对应 picker 自己响应）
        if (e.target.closest('.dtp-wrapper')) return;
        // 点击在某个 panel 内，不处理
        if (e.target.closest('.dtp-panel')) return;
        // 否则关闭所有打开的面板
        document.querySelectorAll('.dtp-panel').forEach(function(panel) {
            if (panel.style.display !== 'none') {
                var picker = pickerMap[panel.id];
                if (picker) picker.hide();
                else panel.style.display = 'none';
            }
        });
    });

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            document.querySelectorAll('.dtp-panel').forEach(function(p) {
                p.style.display = 'none';
            });
        }
    });

    window.addEventListener('resize', function() {
        document.querySelectorAll('.dtp-panel').forEach(function(p) {
            p.style.display = 'none';
        });
    });

    window.addEventListener('scroll', function() {
        document.querySelectorAll('.dtp-panel').forEach(function(p) {
            p.style.display = 'none';
        });
    }, true);

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            initDatePickers();
            var observer = new MutationObserver(function() {
                initDatePickers();
            });
            observer.observe(document.body, { childList: true, subtree: true });
        });
    } else {
        initDatePickers();
        var observer = new MutationObserver(function() {
            initDatePickers();
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }

    window.DatePicker = DatePicker;
    window.initDatePickers = initDatePickers;
})();
