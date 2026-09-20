(function () {
    var tabs = document.querySelectorAll('[data-export-store-tab]');
    var forms = document.querySelectorAll('[data-export-form]');
    tabs.forEach(function (tab) {
        tab.addEventListener('click', function () {
            var selected = tab.getAttribute('data-export-store-tab');
            tabs.forEach(function (item) {
                item.classList.toggle('is-active', item === tab);
                item.setAttribute('aria-pressed', String(item === tab));
            });
            forms.forEach(function (form) {
                form.hidden = form.getAttribute('data-store') !== selected;
            });
        });
    });

    function request(url, body) {
        return fetch(url, {
            method: 'POST',
            body: body,
            headers: { 'X-Requested-With': 'fetch' },
        }).then(function (response) {
            return response.json().then(function (data) {
                return { ok: response.ok && data.ok, data: data };
            });
        });
    }

    document.querySelectorAll('[data-export-form]').forEach(function (form) {
        var store = form.getAttribute('data-store');
        var status = form.querySelector('[data-export-status]');
        var schedule = form.querySelector('[data-schedule-kind]');
        var weekday = form.querySelector('[data-weekday-field]');
        var runButton = form.querySelector('[data-export-now]');
        var scopeButtons = form.querySelectorAll('[data-export-scope]');

        function refreshSchedule() {
            weekday.hidden = schedule.value !== 'weekly';
        }
        schedule.addEventListener('change', refreshSchedule);
        refreshSchedule();

        function show(message, isError) {
            status.textContent = message;
            status.classList.toggle('export-status--error', Boolean(isError));
            status.classList.toggle('export-status--ok', !isError);
        }

        function showExportResult(result, message) {
            if (result.data.last_success_text) {
                message += '. ' + result.data.last_success_text;
            }
            var reports = (result.data.report || {}).marketplaces || [];
            var warnings = reports.reduce(function (all, report) {
                return all.concat(report.warnings || []);
            }, []);
            show(
                warnings.length ? message + '. Внимание: ' + warnings.join(' ') : message,
                warnings.length > 0,
            );
        }

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var submit = form.querySelector('[type="submit"]');
            submit.disabled = true;
            show('Сохраняю настройки…', false);
            request('/admin/google-export/' + store, new FormData(form))
                .then(function (result) {
                    if (!result.ok) throw new Error(result.data.error || 'Не удалось сохранить');
                    show('Настройки сохранены', false);
                })
                .catch(function (error) {
                    show(error.message, true);
                })
                .finally(function () {
                    submit.disabled = false;
                });
        });

        runButton.addEventListener('click', function () {
            runButton.disabled = true;
            show('Выгружаю данные — первый запуск может занять несколько минут…', false);
            request('/admin/google-export/' + store + '/run', new FormData())
                .then(function (result) {
                    if (!result.ok) throw new Error(result.data.error || 'Выгрузка не выполнена');
                    showExportResult(result, 'Выгрузка завершена');
                })
                .catch(function (error) {
                    show(error.message, true);
                })
                .finally(function () {
                    runButton.disabled = false;
                });
        });

        scopeButtons.forEach(function (button) {
            button.addEventListener('click', function () {
                var body = new FormData();
                body.set('marketplace', button.getAttribute('data-marketplace'));
                body.set('export_kind', button.getAttribute('data-export-kind'));
                button.disabled = true;
                show('Выполняю выбранную выгрузку…', false);
                request('/admin/google-export/' + store + '/run', body)
                    .then(function (result) {
                        if (!result.ok) throw new Error(result.data.error || 'Выгрузка не выполнена');
                        showExportResult(result, 'Выбранная выгрузка завершена');
                    })
                    .catch(function (error) {
                        show(error.message, true);
                    })
                    .finally(function () {
                        button.disabled = false;
                    });
            });
        });
    });
})();
