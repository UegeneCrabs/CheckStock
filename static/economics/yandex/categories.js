(function () {
    'use strict';
    var catalogs = {};
    function catalog(store) {
        if (!catalogs[store]) catalogs[store] = window.YandexEconomicsFields.request('categories/' + encodeURIComponent(store))
            .catch(function (error) { delete catalogs[store]; throw error; });
        return catalogs[store];
    }
    function mount(container, store, categoryId, onChange) {
        return window.MarketCategoryPicker.mount(container, {
            load: function () { return catalog(store); }, categoryId: categoryId, onChange: onChange,
            heading: 'Категория Яндекс Маркета', searchLabel: 'Поиск категории ЯМ',
            placeholder: 'Название или ID категории', emptyTitle: 'Выберите категорию Яндекс Маркета',
            loadingText: 'Загружаем категории ЯМ…', emptySearch: 'Ничего не найдено. Попробуйте другое название или ID.',
        });
    }
    window.YandexCategoryPicker = { mount: mount };
})();
