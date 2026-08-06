/* Редактор списка позиций — общий для перемещения и отгрузки.
 *
 * Оба блока делают одно и то же: строка «код товара + количество», поиск по
 * каталогу с подсказками, подсказка «есть N» из остатка ячейки-источника и
 * запрет дублей. Разница только в том, откуда берётся источник: у перемещения
 * это пара «откуда», у отгрузки — единственная пара полей.
 *
 * Раньше это было двумя копиями одного кода. Копии разъезжаются: правку
 * находят в одной и забывают в другой, и блоки начинают вести себя по-разному
 * на одинаковых действиях.
 *
 * Классы строк намеренно оставлены mv-*, чтобы не плодить одинаковые правила
 * в CSS — они описывают вид строки, а не принадлежность к блоку.
 */
window.createRowEditor = function (options) {
    var rowsBox = options.rowsBox;
    var status = options.status;
    var submitBtn = options.submitBtn;
    var storeSlug = options.storeSlug;
    var getSource = options.getSource;
    var emptyHint = options.emptyHint || 'Сначала выберите склад';

    var sourceStock = {};   // {article: количество} в выбранной ячейке
    var suggestTimer = null;

    /* Своё ли сообщение сейчас в строке состояния.
       Без этого проверка строк затирала ответ сервера: операция падала,
       текст ошибки появлялся, а следующий же пересчёт строк его стирал —
       и выглядело так, будто ничего не произошло. */
    var ownMessage = false;

    /* Разрешён ли минус в количестве. По умолчанию нет: отрицательное
       количество осмысленно только в мусорке (излишек), в остальных
       операциях это опечатка. */
    var allowNegative = false;

    function loadSourceStock() {
        var src = getSource();
        if (!src.ff || !src.mp) { sourceStock = {}; refreshHints(); return; }
        /* catch стоит ДО обработки ответа, а не после: иначе он ловил бы и
           ошибки самой отрисовки, молча обнуляя остатки склада — подсказки
           «есть N» тогда просто исчезали, и понять почему было нельзя. */
        fetch('/stock/' + storeSlug + '/ff-cell?ff=' + encodeURIComponent(src.ff) +
              '&mp=' + encodeURIComponent(src.mp))
            .then(function (r) { return r.json(); })
            .catch(function () { return {}; })
            .then(function (d) {
                sourceStock = (d && d.stock) || {};
                refreshHints();
                /* склад сменили — то, что было допустимо, могло стать перебором */
                validateRows();
            });
    }

    function refreshHints() {
        rowsBox.querySelectorAll('.mv-row').forEach(updateHint);
    }

    /* Сколько лежит на выбранном складе по введённому коду.
       null — если код не введён или это баркод: в остатках ключ артикул,
       баркод разрешает уже сервер, и до ответа мы честно не знаем остаток. */
    function availableFor(row) {
        var code = row.querySelector('.mv-code').value.trim();
        if (!code) return null;
        return Object.prototype.hasOwnProperty.call(sourceStock, code)
            ? sourceStock[code] : null;
    }

    function updateHint(row) {
        var hint = row.querySelector('.mv-hint');
        var qtyInput = row.querySelector('.mv-qty');
        var available = availableFor(row);

        if (available === null) {
            hint.textContent = '';
            hint.className = 'mv-hint';
            qtyInput.removeAttribute('max');
            return;
        }

        /* max подсказывает стрелкам поля и мобильной клавиатуре предел,
           но сам по себе ввод не запрещает — руками вписать больше можно,
           поэтому ниже ещё и проверка перед отправкой */
        qtyInput.setAttribute('max', String(available));
        qtyInput.setAttribute('min', allowNegative ? '-999999' : '1');

        var want = parseInt(qtyInput.value, 10) || 0;
        hint.textContent = 'есть ' + available;
        hint.className = 'mv-hint' + (want > available ? ' mv-hint--bad' : '');
    }

    function closeSuggest(row) {
        var box = row.querySelector('.suggest-box');
        if (box) box.classList.remove('open');
    }

    /* Живая проверка строк: дубли и перебор остатка.
       Обе ошибки видно сразу и обе гасят кнопку — отправлять заведомо
       неверные данные, чтобы получить отказ от сервера, незачем. */
    function validateRows() {
        var seen = {};
        var dupes = [];
        var over = [];

        Array.prototype.forEach.call(rowsBox.children, function (row) {
            var code = row.querySelector('.mv-code').value.trim();
            row.classList.remove('mv-row--dupe');
            if (!code) return;

            if (Object.prototype.hasOwnProperty.call(seen, code)) {
                row.classList.add('mv-row--dupe');
                seen[code].classList.add('mv-row--dupe');
                if (dupes.indexOf(code) === -1) dupes.push(code);
            } else {
                seen[code] = row;
            }

            var available = availableFor(row);
            var want = parseInt(row.querySelector('.mv-qty').value, 10) || 0;
            if (want < 0 && !allowNegative) {
                over.push(code + ': отрицательное количество здесь недопустимо');
            }
            if (available !== null && want > available) {
                over.push(code + ': просят ' + want + ', есть ' + available);
            }
        });

        var problems = [];
        if (dupes.length) {
            problems.push('один и тот же товар в нескольких строках: ' + dupes.join(', ') +
                ' — оставьте одну строку с итоговым количеством');
        }
        if (over.length) {
            problems.push('на складе столько нет — ' + over.join('; '));
        }

        if (problems.length) {
            /* с большой буквы, потому что это единственный текст в поле */
            var text = problems.join('. ');
            status.textContent = text.charAt(0).toUpperCase() + text.slice(1);
            status.classList.add('ff-select-status--bad');
            ownMessage = true;
            submitBtn.disabled = true;
        } else {
            /* убираем только СВОЁ сообщение: ошибку от сервера трогать нельзя */
            if (ownMessage) {
                status.textContent = '';
                status.classList.remove('ff-select-status--bad');
                ownMessage = false;
            }
            submitBtn.disabled = false;
        }
        return dupes.concat(over);
    }

    function renderSuggest(row, box, code, qty, items) {
        if (!items.length) {
            var src = getSource();
            box.innerHTML = '<div class="tf-values-empty">' +
                (src.ff ? 'На этом складе такого товара нет' : emptyHint) + '</div>';
            box.classList.add('open');
            return;
        }

        box.innerHTML = items.map(function (it) {
            var have = it.stock !== undefined ? it.stock : (sourceStock[it.article] || 0);
            return '<button type="button" class="suggest-item" data-code="' + it.article + '">' +
                '<span class="suggest-code">' + it.article + '</span>' +
                '<span class="suggest-bc">' + it.barcode + ' · есть ' + have + '</span>' +
                '<span class="suggest-name">' + it.name + '</span>' +
                '</button>';
        }).join('');
        box.classList.add('open');

        box.querySelectorAll('.suggest-item').forEach(function (b) {
            b.addEventListener('mousedown', function (e) {
                e.preventDefault();
                code.value = b.getAttribute('data-code');
                box.classList.remove('open');
                updateHint(row);
                validateRows();   /* подстановка тоже может создать дубль */
                qty.focus();
            });
        });
    }

    function wireRow(row) {
        var code = row.querySelector('.mv-code');
        var qty = row.querySelector('.mv-qty');
        var box = row.querySelector('.suggest-box');

        code.addEventListener('input', function () {
            updateHint(row);
            validateRows();

            var q = code.value.trim();
            if (suggestTimer) clearTimeout(suggestTimer);
            if (q.length < 2) { closeSuggest(row); return; }

            suggestTimer = setTimeout(function () {
                var src = getSource();
                /* ff и mp -> сервер отдаст только то, что реально лежит
                   в этой ячейке, вместе с количеством */
                var url = '/stock/' + storeSlug + '/catalog-search?q=' + encodeURIComponent(q) +
                    '&ff=' + encodeURIComponent(src.ff || '') +
                    '&mp=' + encodeURIComponent(src.mp || '');
                fetch(url)
                    .then(function (r) { return r.json(); })
                    .then(function (d) { renderSuggest(row, box, code, qty, d.items || []); })
                    .catch(function () { closeSuggest(row); });
            }, 200);
        });

        code.addEventListener('blur', function () {
            setTimeout(function () { closeSuggest(row); }, 120);
        });
        qty.addEventListener('input', function () {
            updateHint(row);
            validateRows();   /* перебор гасит кнопку так же, как дубль */
        });

        row.querySelector('.mv-remove').addEventListener('click', function () {
            if (rowsBox.children.length > 1) {
                row.remove();
            } else {
                code.value = ''; qty.value = ''; updateHint(row);
            }
            validateRows();   /* после удаления дубля кнопка снова оживает */
        });
    }

    function addRow() {
        var row = document.createElement('div');
        row.className = 'mv-row';
        row.innerHTML =
            '<div class="manual-code-wrap">' +
            '<input class="select-control mv-code" type="text" placeholder="Артикул или баркод" autocomplete="off">' +
            '<div class="suggest-box"></div>' +
            '</div>' +
            '<input class="select-control mv-qty" type="number" placeholder="Кол-во" min="1">' +
            '<span class="mv-hint"></span>' +
            '<button type="button" class="manual-remove mv-remove" title="Убрать строку">&times;</button>';
        rowsBox.appendChild(row);
        wireRow(row);
        return row;
    }

    function collectItems() {
        var items = [];
        Array.prototype.forEach.call(rowsBox.children, function (row) {
            var code = row.querySelector('.mv-code').value.trim();
            var q = row.querySelector('.mv-qty').value.trim();
            if (!code && !q) return;
            items.push({ code: code, quantity: parseInt(q, 10) });
        });
        return items;
    }

    function reset() {
        rowsBox.innerHTML = '';
        addRow();
    }

    /* При смене склада подсказки относятся к прежней ячейке — закрываем */
    function onSourceChange() {
        rowsBox.querySelectorAll('.mv-row').forEach(closeSuggest);
        loadSourceStock();
    }

    addRow();

    /* Сообщение от сервера: помечаем как чужое, чтобы проверка строк
       его не стёрла при ближайшем пересчёте. */
    function showServerMessage(text, isError) {
        status.textContent = text;
        status.classList.toggle('ff-select-status--bad', !!isError);
        ownMessage = false;
    }

    function setAllowNegative(value) {
        allowNegative = !!value;
        rowsBox.querySelectorAll('.mv-qty').forEach(function (input) {
            input.setAttribute('min', allowNegative ? '-999999' : '1');
        });
        validateRows();
    }

    return {
        addRow: addRow,
        setAllowNegative: setAllowNegative,
        showServerMessage: showServerMessage,
        validateRows: validateRows,
        collectItems: collectItems,
        loadSourceStock: loadSourceStock,
        onSourceChange: onSourceChange,
        reset: reset
    };
};


/* Переключатель способа ввода в подблоке «Товары и количества».
 *
 * Три способа — альтернативы, поэтому показываем только выбранный: так блок
 * занимает втрое меньше места и не остаётся сомнений, что именно уйдёт на
 * сервер. При переключении поля остальных способов очищаются: иначе забытая
 * в скрытом поле ссылка ушла бы вместе с выбранным файлом, и попробуй пойми,
 * почему провелось не то.
 */
window.initIoBlock = function (root) {
    if (!root) return { current: function () { return 'manual'; } };

    var tabs = root.querySelectorAll('.io-tab');
    var panes = root.querySelectorAll('.io-method');
    var current = 'manual';

    function clearPane(pane) {
        pane.querySelectorAll('input').forEach(function (input) {
            if (input.type === 'file') {
                input.value = '';
                var label = pane.querySelector('.file-drop-text');
                if (label) label.textContent = 'Выбрать файл .xlsx';
            } else if (input.type !== 'number' && !input.classList.contains('mv-code')
                       && !input.classList.contains('manual-code')
                       && !input.classList.contains('mv-qty')
                       && !input.classList.contains('manual-qty')) {
                /* строки товаров не трогаем: их пользователь набирал руками,
                   и потерять их при случайном клике по вкладке обиднее всего */
                input.value = '';
            }
        });
    }

    function apply(method) {
        current = method;
        tabs.forEach(function (tab) {
            tab.classList.toggle('is-active', tab.getAttribute('data-method') === method);
        });
        panes.forEach(function (pane) {
            var mine = pane.getAttribute('data-method') === method;
            pane.classList.toggle('is-hidden', !mine);
            if (!mine) clearPane(pane);
        });
    }

    tabs.forEach(function (tab) {
        tab.addEventListener('click', function () {
            apply(tab.getAttribute('data-method'));
        });
    });

    return { current: function () { return current; } };
};
