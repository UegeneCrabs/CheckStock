(function () {
    'use strict';
    var root = document.getElementById('yandex-economics-settings');
    var node = document.getElementById('yandex-buyout-config');
    if (!root || !node) return;
    var config = JSON.parse(node.textContent),
        fields = window.YandexEconomicsFields;
    var esc = fields.esc,
        request = fields.request,
        selector = root.querySelector('[data-ym-settings-selector]');
    var store = root.querySelector('[data-ym-settings-store]'),
        scheme = root.querySelector('[data-ym-settings-scheme]');
    var scope = root.querySelector('[data-ym-settings-scope]'),
        article = root.querySelector('[data-ym-settings-article]');
    var editor = root.querySelector('[data-ym-settings-editor]'),
        save = root.querySelector('[data-ym-settings-save]');
    var refresh = root.querySelector('[data-ym-settings-refresh]'),
        status = root.querySelector('[data-ym-settings-message]');
    var data,
        changed = {},
        sequence = 0,
        loadedTarget,
        busy = false;
    var specs = [].concat.apply(
        [],
        fields.groups.map(function (group) {
            return group[1];
        }),
    );
    var groups = [
        ['Платежи', ['acquiring_percent']],
        [
            'Закупка и собственные расходы',
            [
                'purchase_price',
                'transit_cost',
                'company_commission_percent',
            ],
        ],
        [
            'Налоги и потери',
            ['vat_percent', 'usn_percent', 'loss_percent', 'disposal_cost'],
        ],
        [
            'Ручная замена данных API',
            [
                'commission_percent',
                'payment_acceptance',
                'delivery_cost',
                'buyout_percent',
                'category_name',
                'length',
                'width',
                'height',
                'weight',
                'campaign_id',
            ],
        ],
    ];
    function message(text, error) {
        status.textContent = text;
        status.classList.toggle('is-error', !!error);
    }
    function target() {
        return {
            store: store.value,
            scheme: scheme.value,
            article: scope.value === 'product' ? article.value.trim() : '',
        };
    }
    function path(t) {
        return (
            'settings/' +
            encodeURIComponent(t.store) +
            '?article=' +
            encodeURIComponent(t.article) +
            '&scheme=' +
            t.scheme
        );
    }
    function sameTarget() {
        return loadedTarget && JSON.stringify(loadedTarget) === JSON.stringify(target());
    }
    function controls() {
        if (!busy && !Object.keys(changed).length)
            document.querySelector('[data-ym-cabinet-status]').hidden = true;
        save.disabled = busy || !sameTarget() || !config.canEdit || !Object.keys(changed).length;
        refresh.disabled = busy || !sameTarget() || !config.canEdit || !!Object.keys(changed).length;
        root.querySelector('[data-ym-settings-reload]').disabled = busy;
        selector.querySelectorAll('input,select,button').forEach(function (input) {
            input.disabled = busy;
        });
        editor.querySelectorAll('[data-ym-setting],[data-ym-setting-reset]').forEach(function (input) {
            input.disabled = busy || !config.canEdit;
        });
    }
    function field(key) {
        var spec = specs.find(function (item) {
                return item[0] === key;
            }),
            val = data.values[key];
        var custom = Object.prototype.hasOwnProperty.call(data.overrides, key),
            control;
        if (spec[3] && typeof spec[3] === 'object') {
            control = window.CheckStockUI.render('economics/yandex/settings/field-2', {
                key: key,
                content: spec[1],
                content_2: Object.keys(spec[3])
                    .map(function (v) {
                        return window.CheckStockUI.render('common/ui/select-option', {
                            v: v,
                            content: String(val) === v ? ' selected' : '',
                            content_2: spec[3][v],
                        });
                    })
                    .join(''),
            });
        } else {
            var integer = key === 'campaign_id';
            control = window.CheckStockUI.render('economics/yandex/settings/field-3', {
                key: key,
                content: spec[1],
                content_2: spec[3] === 'text' ? 'text' : 'number',
                content_3: key === 'campaign_id' ? 1 : 0,
                content_4: integer ? '1' : 'any',
                content_5: spec[2] === '%' ? ' max="100"' : '',
                val: val,
            });
        }
        return window.CheckStockUI.render('economics/yandex/settings/field-4', {
            content: spec[1],
            control: control,
            content_2: spec[2],
            key: key,
            content_3: spec[1],
            key_2: key,
            content_4: custom ? 'Сохранено на сайте' : data.origins[key] || 'Не задано',
        });
    }
    function draw() {
        var item = config.items.find(function (item) {
            return item.store_slug === loadedTarget.store;
        });
        root.querySelector('[data-ym-settings-heading]').textContent =
            (item ? item.store_name : loadedTarget.store) +
            ' · ' +
            loadedTarget.scheme +
            ' · ' +
            (loadedTarget.article ? 'Артикул ' + loadedTarget.article : 'Общие параметры кабинета');
        root.querySelector('[data-ym-settings-fields]').innerHTML = groups
            .map(function (group) {
                var keys = group[1].filter(function (key) {
                    if (loadedTarget.article && key === 'company_commission_percent') return false;
                    return loadedTarget.article || fields.cabinetFields.indexOf(key) >= 0;
                });
                if (!keys.length) return '';
                var advanced = group[0] === 'Ручная замена данных API',
                    tag = advanced ? 'details' : 'fieldset',
                    heading = advanced ? 'summary' : 'legend';
                return window.CheckStockUI.render('economics/yandex/settings/draw-2', {
                    tag: tag,
                    heading: heading,
                    content: group[0],
                    heading_2: heading,
                    content_2: advanced ? window.CheckStockUI.render('economics/yandex/settings/draw') : '',
                    content_3: keys.map(field).join(''),
                    tag_2: tag,
                });
            })
            .join('');
        editor.hidden = false;
        refresh.hidden = !loadedTarget.article;
        editor.querySelectorAll('[data-ym-setting]').forEach(function (input) {
            input.oninput = function () {
                var key = input.dataset.ymSetting;
                changed[key] =
                    input.value === ''
                        ? null
                        : input.type === 'number' || key === 'payment_delay_weeks'
                          ? Number(input.value)
                          : input.value;
                root.querySelector('[data-ym-setting-origin="' + key + '"]').textContent =
                    input.value === '' ? 'После сохранения — из источника' : 'Изменено · не сохранено';
                message('Есть несохранённые изменения.');
                controls();
            };
        });
        editor.querySelectorAll('[data-ym-setting-reset]').forEach(function (button) {
            button.onclick = function () {
                var input = editor.querySelector('[data-ym-setting="' + button.dataset.ymSettingReset + '"]');
                input.value = '';
                input.oninput();
            };
        });
        controls();
    }
    async function load(t) {
        var current = ++sequence;
        busy = true;
        editor.hidden = true;
        controls();
        message('Загружаем параметры…');
        try {
            var result = await request(path(t));
            if (current !== sequence) return;
            data = result;
            loadedTarget = t;
            changed = {};
            root.querySelector('#ym-settings-articles').innerHTML = result.articles
                .map(function (key) {
                    return window.CheckStockUI.render('economics/yandex/settings/load', { key: key });
                })
                .join('');
            draw();
            message('Параметры загружены.');
        } catch (error) {
            if (current === sequence) message(error.message, true);
        } finally {
            if (current === sequence) {
                busy = false;
                controls();
            }
        }
    }
    selector.onsubmit = function (event) {
        event.preventDefault();
        if (scope.value === 'product' && !article.value.trim()) {
            message('Введите артикул товара.', true);
            article.focus();
            return;
        }
        load(target());
    };
    selector.oninput = function () {
        root.querySelector('[data-ym-article-wrap]').hidden = scope.value !== 'product';
        if (!sameTarget()) {
            editor.hidden = true;
            message('Нажмите «Открыть параметры» для выбранного кабинета и товара.');
        }
        controls();
    };
    editor.onsubmit = async function (event) {
        event.preventDefault();
        if (busy || !sameTarget() || !Object.keys(changed).length || !editor.reportValidity()) return;
        busy = true;
        controls();
        message('Сохраняем параметры…');
        try {
            data = await request(path(loadedTarget), 'PUT', {
                scheme: loadedTarget.scheme,
                revision: data.revision,
                values: changed,
            });
            changed = {};
            draw();
            message('Параметры сохранены в базе. Они применятся при следующем открытии расчёта ЯМ.');
        } catch (error) {
            message(error.message, true);
        } finally {
            busy = false;
            controls();
        }
    };
    root.querySelector('[data-ym-settings-reload]').onclick = function () {
        load(loadedTarget);
    };
    refresh.onclick = async function () {
        if (busy || !sameTarget() || Object.keys(changed).length) return;
        busy = true;
        controls();
        message('Обновляем категорию, габариты, цену продавца и тариф из API…');
        try {
            await request(
                'refresh-tariff/' +
                    encodeURIComponent(loadedTarget.store) +
                    '/' +
                    encodeURIComponent(loadedTarget.article),
                'POST',
                { scheme: loadedTarget.scheme },
            );
            data = await request(path(loadedTarget));
            draw();
            message('Данные API обновлены. Сохранённые ручные значения имеют приоритет.');
        } catch (error) {
            message(error.message, true);
        } finally {
            busy = false;
            controls();
        }
    };
    store.innerHTML = config.items
        .map(function (item) {
            return window.CheckStockUI.render('economics/yandex/settings/fragment', {
                store_slug: item.store_slug,
                store_name: item.store_name,
            });
        })
        .join('');
    if (!config.items.length) {
        message('Нет доступных кабинетов ЯМ.');
        return;
    }
    var query = new URLSearchParams(window.location.search);
    if (
        config.items.some(function (item) {
            return item.store_slug === query.get('ym_store');
        })
    )
        store.value = query.get('ym_store');
    if (query.get('ym_scheme') === 'FBS') scheme.value = 'FBS';
    if (query.get('ym_article')) {
        article.value = query.get('ym_article');
        scope.value = 'product';
    }
    root.querySelector('[data-ym-article-wrap]').hidden = scope.value !== 'product';
    document.getElementById('ym-cabinet-panel').addEventListener('ym-cabinet-change', function (event) {
        if (event.detail.store === store.value) return;
        if (busy || Object.keys(changed).length) {
            event.preventDefault();
            var note = busy ? 'Дождитесь загрузки параметров.' : 'Сохраните или отмените изменения в расходах перед сменой кабинета.';
            var cabinetStatus = document.querySelector('[data-ym-cabinet-status]');
            cabinetStatus.textContent = note;
            cabinetStatus.hidden = false;
            message(note, !busy);
            return;
        }
        store.value = event.detail.store;
        // A product article belongs to its cabinet; open cabinet defaults on a store change.
        scope.value = 'cabinet';
        article.value = '';
        root.querySelector('[data-ym-article-wrap]').hidden = true;
        load(target());
    });
    load(target());
})();
