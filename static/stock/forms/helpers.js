window.CheckStockOperations = (() => {
    function initBlock(name, statusId, fn) {
        try {
            fn();
        } catch (e) {
            var box = document.getElementById(statusId);
            if (box) {
                box.textContent =
                    'Блок не запустился: ' +
                    e.message +
                    '. Обновите страницу с Ctrl+F5; если не помогло — покажите это разработчику.';
                box.classList.add('ff-select-status--bad');
            }
            console.error('CheckStock: блок «' + name + '» не запустился', e);
        }
    }
    function requireRowEditor() {
        if (typeof window.createRowEditor !== 'function') {
            throw new Error('не загрузился /static/stock/row-editor.js');
        }
        return window.createRowEditor;
    }
    return { initBlock, requireRowEditor };
})();
