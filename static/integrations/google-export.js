(function () {
    function request(url, body) {
        return fetch(url, {
            method: 'POST',
            body: body,
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'fetch' },
        }).then(function (response) {
            if (response.status === 401) {
                throw new Error('Сессия завершена. Войдите в аккаунт и обновите страницу.');
            }
            if (response.status === 403) {
                throw new Error('Недостаточно прав. Выгрузка доступна суперадминистратору.');
            }
            if (!(response.headers.get('Content-Type') || '').includes('application/json')) {
                if (response.redirected) {
                    throw new Error('Сессия завершена. Войдите в аккаунт и обновите страницу.');
                }
                throw new Error(
                    'Сервер вернул неожиданный ответ (HTTP ' + response.status + '). Обновите страницу и повторите.',
                );
            }
            return response.json().then(
                function (data) {
                    if (!response.ok || !data || !data.ok) {
                        throw new Error((data && (data.error || data.detail)) || 'Ошибка сервера (HTTP ' + response.status + ').');
                    }
                    return data;
                },
                function () {
                    throw new Error('Не удалось прочитать ответ сервера. Обновите страницу и повторите.');
                },
            );
        });
    }

    document.querySelectorAll('[data-export-form]').forEach(function (form) {
        var status = form.querySelector('[data-export-status]');
        var schedule = form.querySelector('[data-schedule-kind]');
        var weekday = form.querySelector('[data-weekday-field]');
        var runButton = form.querySelector('[data-export-now]');
        var scopeButtons = form.querySelectorAll('[data-export-scope]');
        var controls = form.querySelectorAll('input, select, button, textarea');
        var saveHint = form.querySelector('[data-export-save-hint]');
        var saved = form.getAttribute('data-saved') === 'true';
        var busy = false;

        function snapshot() {
            return new URLSearchParams(new FormData(form)).toString();
        }
        var savedSnapshot = snapshot();

        function refreshControls() {
            controls.forEach(function (control) {
                control.disabled = busy;
            });
            form.setAttribute('aria-busy', String(busy));
            if (busy) return;
            var dirty = snapshot() !== savedSnapshot;
            var hasTargets = false;
            scopeButtons.forEach(function (button) {
                var field = form.elements.namedItem(button.getAttribute('data-sheet-field'));
                var url = form.elements.namedItem(button.getAttribute('data-url-field'));
                var configured = Boolean(field && field.value.trim() && url && url.value.trim() && url.validity.valid);
                hasTargets = hasTargets || configured;
                button.disabled = !saved || dirty || !configured;
            });
            runButton.disabled = !saved || dirty || !hasTargets;
            saveHint.textContent =
                !saved || dirty
                    ? 'Перед выгрузкой сохраните ссылки на файлы и названия листов по площадкам кнопкой «Сохранить настройки».'
                    : 'Настройки сохранены. Каждая площадка выгружается в свой файл для всех 7 проектов.';
        }

        function setBusy(value) {
            busy = value;
            refreshControls();
        }

        function refreshSchedule() {
            weekday.hidden = schedule.value !== 'weekly';
        }
        schedule.addEventListener('change', refreshSchedule);
        form.addEventListener('input', refreshControls);
        form.addEventListener('change', refreshControls);
        refreshSchedule();
        refreshControls();

        function show(message, isError) {
            status.textContent = message;
            status.classList.toggle('export-status--error', Boolean(isError));
            status.classList.toggle('export-status--ok', !isError);
        }

        function showError(error) {
            show(
                error instanceof TypeError
                    ? 'Не удалось связаться с сервером. Проверьте соединение и повторите.'
                    : error.message,
                true,
            );
        }

        function showExportResult(data, message) {
            var report = data.report || {};
            var marketplaces = report.marketplaces || [];
            if (!marketplaces.length || marketplaces.every(function (item) { return item.skipped; })) {
                show('Нет настроенных листов для выбранной выгрузки. Укажите ссылку на файл площадки, названия листов и сохраните настройки.', true);
                return;
            }
            if (data.last_success_text) {
                message += '. ' + data.last_success_text;
            }
            var warnings = marketplaces.reduce(function (all, item) {
                return all.concat(item.warnings || []);
            }, report.warnings || []);
            warnings = warnings.filter(function (warning, index) {
                return warnings.indexOf(warning) === index;
            });
            show(
                warnings.length ? message + '. Внимание: ' + warnings.join(' ') : message,
                warnings.length > 0,
            );
        }

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            if (busy) return;
            var body = new FormData(form);
            var submittedSnapshot = snapshot();
            setBusy(true);
            show('Сохраняю общие настройки…', false);
            request('/admin/google-export/settings', body)
                .then(function () {
                    saved = true;
                    savedSnapshot = submittedSnapshot;
                    form.setAttribute('data-saved', 'true');
                    show('Общие настройки сохранены', false);
                })
                .catch(showError)
                .finally(function () {
                    setBusy(false);
                });
        });

        function runExport(body, message) {
            if (busy || !saved || snapshot() !== savedSnapshot) return;
            setBusy(true);
            show('Выгружаю данные всех проектов — это может занять несколько минут…', false);
            request('/admin/google-export/run', body)
                .then(function (data) {
                    showExportResult(data, message);
                })
                .catch(showError)
                .finally(function () {
                    setBusy(false);
                });
        }

        runButton.addEventListener('click', function () {
            runExport(new FormData(), 'Выгрузка завершена');
        });

        scopeButtons.forEach(function (button) {
            button.addEventListener('click', function () {
                var body = new FormData();
                body.set('marketplace', button.getAttribute('data-marketplace'));
                body.set('export_kind', button.getAttribute('data-export-kind'));
                runExport(body, 'Выбранная выгрузка завершена');
            });
        });
    });
})();
