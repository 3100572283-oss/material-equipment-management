/**
 * Chart Loading Placeholder + Fade-in
 * Shows spinner while chart data loads, then fades in
 */
(function () {
    'use strict';

    // Add CSS for chart loading
    var style = document.createElement('style');
    style.textContent = '' +
        '.chart-loading-wrapper {' +
        '  position: relative;' +
        '  min-height: 200px;' +
        '}' +
        '.chart-loading-placeholder {' +
        '  position: absolute;' +
        '  top: 0; left: 0; right: 0; bottom: 0;' +
        '  display: flex;' +
        '  flex-direction: column;' +
        '  align-items: center;' +
        '  justify-content: center;' +
        '  background: #f8f9fa;' +
        '  border-radius: 4px;' +
        '  z-index: 1;' +
        '  transition: opacity 0.4s ease;' +
        '}' +
        '.chart-loading-placeholder.hidden {' +
        '  opacity: 0;' +
        '  pointer-events: none;' +
        '}' +
        '.chart-loading-placeholder .spinner-border {' +
        '  width: 2rem;' +
        '  height: 2rem;' +
        '  color: #409eff;' +
        '}' +
        '.chart-loading-placeholder .chart-loading-text {' +
        '  margin-top: 10px;' +
        '  font-size: 13px;' +
        '  color: #909399;' +
        '}' +
        '.chart-content-fade {' +
        '  opacity: 0;' +
        '  transition: opacity 0.5s ease;' +
        '}' +
        '.chart-content-fade.show {' +
        '  opacity: 1;' +
        '}' +
        // Skeleton screen variant
        '.chart-skeleton {' +
        '  background: linear-gradient(90deg, #f0f0f0 25%, #e0e0e0 50%, #f0f0f0 75%);' +
        '  background-size: 200% 100%;' +
        '  animation: skeleton-loading 1.5s infinite;' +
        '  border-radius: 4px;' +
        '}' +
        '@keyframes skeleton-loading {' +
        '  0% { background-position: 200% 0; }' +
        '  100% { background-position: -200% 0; }' +
        '}';
    document.head.appendChild(style);

    /**
     * Wrap a chart container with loading placeholder
     * @param {HTMLElement} container - The chart container element
     * @param {string} text - Loading text
     */
    window.wrapChartWithLoading = function (container, text) {
        if (!container || container.dataset.chartLoadingWrapped) return;
        container.dataset.chartLoadingWrapped = '1';

        var wrapper = document.createElement('div');
        wrapper.className = 'chart-loading-wrapper';
        wrapper.style.minHeight = container.offsetHeight > 0 ? container.offsetHeight + 'px' : '200px';

        var placeholder = document.createElement('div');
        placeholder.className = 'chart-loading-placeholder';
        placeholder.innerHTML = '' +
            '<div class="spinner-border" role="status"></div>' +
            '<div class="chart-loading-text">' + (text || '图表加载中...') + '</div>';

        // Move container content into wrapper
        var parent = container.parentNode;
        parent.insertBefore(wrapper, container);
        wrapper.appendChild(container);
        wrapper.appendChild(placeholder);

        container.classList.add('chart-content-fade');

        // Return a function to hide loading and show chart
        return function () {
            placeholder.classList.add('hidden');
            container.classList.add('show');
            setTimeout(function () {
                placeholder.style.display = 'none';
            }, 500);
        };
    };

    /**
     * Auto-wrap chart canvases and containers on page load
     */
    function autoWrapCharts() {
        // Wrap canvas elements inside card-body that look like charts
        document.querySelectorAll('canvas').forEach(function (canvas) {
            if (canvas.dataset.chartLoadingWrapped) return;
            // Skip tiny canvases
            if (canvas.offsetWidth < 100) return;
            var revealFn = window.wrapChartWithLoading(canvas, '数据加载中...');
            if (revealFn) {
                // Auto-reveal after a delay if no explicit call
                canvas._revealChart = revealFn;
                setTimeout(function () {
                    if (canvas._revealChart) {
                        canvas._revealChart();
                        canvas._revealChart = null;
                    }
                }, 3000);
            }
        });

        // Wrap elements with data-chart-loading attribute
        document.querySelectorAll('[data-chart-loading="true"]').forEach(function (el) {
            if (el.dataset.chartLoadingWrapped) return;
            var revealFn = window.wrapChartWithLoading(el, el.getAttribute('data-chart-loading-text') || '图表加载中...');
            if (revealFn) {
                el._revealChart = revealFn;
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', autoWrapCharts);
    } else {
        autoWrapCharts();
    }
})();
