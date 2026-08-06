(function () {
    function initCustomSelect(select) {
        var wrap = document.createElement('div');
        wrap.className = 'cs-wrap';
        select.parentNode.insertBefore(wrap, select);
        wrap.appendChild(select);
        select.classList.add('cs-native');
        select.setAttribute('tabindex', '-1');
        select.setAttribute('aria-hidden', 'true');

        var trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'cs-trigger';

        var label = document.createElement('span');
        label.className = 'cs-label';

        var chevron = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        chevron.setAttribute('class', 'cs-chevron');
        chevron.setAttribute('width', '16');
        chevron.setAttribute('height', '16');
        chevron.setAttribute('viewBox', '0 0 24 24');
        chevron.innerHTML = '<path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>';

        trigger.appendChild(label);
        trigger.appendChild(chevron);
        wrap.appendChild(trigger);

        var panel = document.createElement('div');
        panel.className = 'cs-panel';
        wrap.appendChild(panel);

        function closePanel() {
            wrap.classList.remove('open');
        }

        function openPanel() {
            document.querySelectorAll('.cs-wrap.open').forEach(function (el) {
                if (el !== wrap) el.classList.remove('open');
            });
            wrap.classList.add('open');
        }

        function renderOptions() {
            panel.innerHTML = '';
            Array.prototype.forEach.call(select.options, function (opt) {
                var item = document.createElement('div');
                item.className = 'cs-option' + (opt.value === select.value ? ' selected' : '');
                item.textContent = opt.textContent;
                item.addEventListener('click', function () {
                    if (select.value !== opt.value) {
                        select.value = opt.value;
                        select.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    syncLabel();
                    closePanel();
                });
                panel.appendChild(item);
            });
        }

        function syncLabel() {
            var opt = select.options[select.selectedIndex];
            label.textContent = opt ? opt.textContent : '';
            trigger.classList.toggle('placeholder', !select.value);
            renderOptions();
        }

        trigger.addEventListener('click', function (e) {
            e.stopPropagation();
            wrap.classList.contains('open') ? closePanel() : openPanel();
        });

        document.addEventListener('click', function (e) {
            if (!wrap.contains(e.target)) closePanel();
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') closePanel();
        });

        new MutationObserver(syncLabel).observe(select, { childList: true, subtree: true });

        syncLabel();
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('select.select-control').forEach(initCustomSelect);
    });
})();
