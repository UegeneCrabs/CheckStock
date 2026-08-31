(function () {
    'use strict';

    var tabs = document.querySelectorAll('[data-integration-store-tab]');
    var panels = document.querySelectorAll('[data-integration-store]');
    tabs.forEach(function (tab) {
        tab.addEventListener('click', function () {
            var selected = tab.getAttribute('data-integration-store-tab');
            tabs.forEach(function (item) { item.classList.toggle('is-active', item === tab); });
            panels.forEach(function (panel) {
                panel.hidden = panel.getAttribute('data-integration-store') !== selected;
            });
        });
    });

    function request(url, options) {
        return fetch(url, options).then(function (response) {
            return response.json().then(function (data) {
                if (!response.ok || !data.ok) throw new Error(data.error || data.detail || 'Не удалось выполнить действие');
                return data;
            });
        });
    }

    document.querySelectorAll('[data-credential-form]').forEach(function (form) {
        var store = form.getAttribute('data-store');
        var marketplace = form.getAttribute('data-marketplace');
        var keyInput = form.querySelector('[name="api_key"]');
        var reveal = form.querySelector('[data-reveal-key]');
        var remove = form.querySelector('[data-delete-key]');
        var message = form.querySelector('[data-form-message]');
        var status = form.querySelector('[data-key-status]');

        function show(text, isError) {
            message.textContent = text;
            message.classList.toggle('is-error', Boolean(isError));
        }

        reveal.addEventListener('click', function () {
            var visible = keyInput.type === 'text';
            keyInput.type = visible ? 'password' : 'text';
            reveal.textContent = visible ? 'Показать' : 'Скрыть';
        });

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var submit = form.querySelector('[type="submit"]');
            var client = form.querySelector('[name="client_id"]');
            submit.disabled = true;
            show('Сохраняю…', false);
            request('/api/admin/integrations/' + store + '/' + marketplace, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
                body: JSON.stringify({api_key: keyInput.value, client_id: client ? client.value : ''})
            }).then(function () {
                keyInput.value = '';
                if (client) client.value = '';
                status.textContent = 'Подключён';
                status.className = 'integration-key-status is-connected';
                remove.disabled = false;
                show('Ключ сохранён', false);
            }).catch(function (error) {
                show(error.message, true);
            }).finally(function () { submit.disabled = false; });
        });

        remove.addEventListener('click', function () {
            var confirmation = window.Modal && window.Modal.confirm
                ? window.Modal.confirm({title: 'Удалить API-ключ?', message: 'Маркетплейс перестанет обновляться для этого магазина.', confirmText: 'Удалить', danger: true})
                : Promise.resolve(window.confirm('Удалить API-ключ?'));
            confirmation.then(function (approved) {
                if (!approved) return;
                remove.disabled = true;
                show('Удаляю…', false);
                request('/api/admin/integrations/' + store + '/' + marketplace, {
                    method: 'DELETE', headers: {'X-Requested-With': 'fetch'}
                }).then(function () {
                    status.textContent = 'Ключ не задан';
                    status.className = 'integration-key-status is-empty';
                    show('Ключ удалён', false);
                }).catch(function (error) {
                    remove.disabled = false;
                    show(error.message, true);
                });
            });
        });
    });

    function syncInputs(job) {
        return document.querySelectorAll('[data-sync-setting-toggle][data-job="' + job + '"]');
    }

    function syncMessage(job, text, isError) {
        var row = document.querySelector('[data-sync-job="' + job + '"]');
        var detail = document.querySelector('[data-sync-targets-row="' + job + '"]');
        var messages = [];
        if (row) messages.push(row.querySelector('[data-sync-setting-inline-message]'));
        if (detail) messages.push(detail.querySelector('[data-sync-setting-message]'));
        messages.filter(Boolean).forEach(function (message) {
            message.textContent = text;
            message.classList.toggle('is-error', Boolean(isError));
        });
    }

    function applySyncConfiguration(config) {
        syncInputs(config.name).forEach(function (input) {
            var store = input.getAttribute('data-store') || '';
            var marketplace = input.getAttribute('data-marketplace') || '';
            if (!store && !marketplace) {
                input.checked = Boolean(config.configured_enabled);
                var globalLabel = input.closest('.integration-sync-toggle').querySelector('.integration-sync-toggle-label');
                globalLabel.textContent = config.summary;
            } else if (!store) {
                var marketplaceSetting = (config.marketplace_settings || []).find(function (item) {
                    return item.marketplace === marketplace;
                });
                if (marketplaceSetting) input.checked = Boolean(marketplaceSetting.enabled);
            } else {
                var target = (config.targets || []).find(function (item) {
                    return item.store_slug === store && item.marketplace === marketplace;
                });
                if (target) input.checked = Boolean(target.configured_enabled);
            }
            input.disabled = !config.environment_enabled;
        });
        var targetButton = document.querySelector('[data-sync-targets-toggle="' + config.name + '"]');
        if (targetButton) {
            targetButton.textContent = 'Магазины · ' + config.enabled_target_count + '/' + config.target_count;
        }
    }

    document.querySelectorAll('[data-sync-targets-toggle]').forEach(function (button) {
        button.addEventListener('click', function () {
            var job = button.getAttribute('data-sync-targets-toggle');
            var row = document.querySelector('[data-sync-targets-row="' + job + '"]');
            if (!row) return;
            row.hidden = !row.hidden;
            button.setAttribute('aria-expanded', row.hidden ? 'false' : 'true');
            button.textContent = row.hidden
                ? button.textContent.replace('Скрыть · ', 'Магазины · ')
                : button.textContent.replace('Магазины · ', 'Скрыть · ');
        });
    });

    document.querySelectorAll('[data-sync-setting-toggle]').forEach(function (input) {
        input.addEventListener('change', function () {
            var job = input.getAttribute('data-job');
            var previous = !input.checked;
            input.disabled = true;
            syncMessage(job, 'Сохраняю…', false);
            request('/api/admin/integrations/sync-jobs/' + encodeURIComponent(job) + '/settings', {
                method: 'PUT',
                headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
                body: JSON.stringify({
                    enabled: input.checked,
                    store_slug: input.getAttribute('data-store') || '',
                    marketplace: input.getAttribute('data-marketplace') || ''
                })
            }).then(function (data) {
                applySyncConfiguration(data.configuration);
                syncMessage(job, 'Сохранено', false);
                window.setTimeout(function () { syncMessage(job, '', false); }, 1800);
            }).catch(function (error) {
                input.checked = previous;
                input.disabled = false;
                syncMessage(job, error.message, true);
            });
        });
    });

    document.querySelectorAll('[data-sync-run]').forEach(function (button) {
        button.addEventListener('click', function () {
            var job = button.getAttribute('data-sync-run');
            button.disabled = true;
            syncMessage(job, 'Выгрузка выполняется…', false);
            request('/api/admin/integrations/sync-jobs/' + encodeURIComponent(job) + '/run', {
                method: 'POST',
                headers: {'Accept': 'application/json', 'X-Requested-With': 'fetch'}
            }).then(function (data) {
                var result = data.result || {};
                var suffix = result.items === undefined ? '' : ': ' + result.items + ' товаров';
                syncMessage(job, 'FTP-выгрузка завершена' + suffix, false);
                window.setTimeout(function () { window.location.reload(); }, 900);
            }).catch(function (error) {
                syncMessage(job, error.message, true);
                button.disabled = false;
            });
        });
    });

    var historyDialog = document.getElementById('integration-history-dialog');
    if (!historyDialog) return;
    var historyTitle = document.getElementById('integration-history-title');
    var historyMessage = historyDialog.querySelector('[data-history-message]');
    var historyTable = historyDialog.querySelector('[data-history-table]');
    var historyRows = historyDialog.querySelector('[data-history-rows]');
    var statusLabels = {
        running: ['Выполняется', 'is-running'],
        success: ['Успешно', 'is-success'],
        error: ['Ошибка', 'is-error']
    };

    function runDate(value) {
        if (!value) return '—';
        var parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return String(value);
        return parsed.toLocaleString('ru-RU', {
            timeZone: 'Europe/Moscow', day: '2-digit', month: '2-digit', year: 'numeric',
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        }) + ' МСК';
    }

    function runDuration(milliseconds) {
        if (milliseconds === null || milliseconds === undefined) return '—';
        var seconds = Number(milliseconds) / 1000;
        if (seconds < 1) return Math.max(0, Number(milliseconds)) + ' мс';
        if (seconds < 60) return seconds.toLocaleString('ru-RU', {maximumFractionDigits: 1}) + ' сек.';
        var minutes = Math.floor(seconds / 60);
        var remainder = Math.round(seconds % 60);
        return minutes + ' мин. ' + remainder + ' сек.';
    }

    function cell(text) {
        var node = document.createElement('td');
        node.textContent = text;
        return node;
    }

    function renderHistory(runs) {
        historyRows.replaceChildren();
        if (!runs.length) {
            historyTable.hidden = true;
            historyMessage.textContent = 'Запусков пока нет. Новые запуски появятся здесь автоматически.';
            return;
        }
        runs.forEach(function (run) {
            var row = document.createElement('tr');
            var dateCell = document.createElement('td');
            var started = document.createElement('strong');
            started.textContent = runDate(run.started_at);
            dateCell.appendChild(started);
            if (run.finished_at) {
                var finished = document.createElement('small');
                finished.textContent = 'Завершено: ' + runDate(run.finished_at);
                dateCell.appendChild(finished);
            }
            row.appendChild(dateCell);

            var statusCell = document.createElement('td');
            var status = statusLabels[run.status] || ['Неизвестно', 'is-empty'];
            var badge = document.createElement('span');
            badge.className = 'sync-status ' + status[1];
            badge.textContent = status[0];
            statusCell.appendChild(badge);
            row.appendChild(statusCell);
            row.appendChild(cell(run.trigger === 'manual' ? 'Вручную' : run.trigger === 'scheduled' ? 'По расписанию' : '—'));
            row.appendChild(cell(runDuration(run.duration_ms)));

            var errorCell = document.createElement('td');
            if (run.error) {
                var error = document.createElement('pre');
                error.className = 'integration-history-error';
                error.textContent = run.error;
                errorCell.appendChild(error);
            } else {
                errorCell.textContent = '—';
            }
            row.appendChild(errorCell);
            historyRows.appendChild(row);
        });
        historyMessage.textContent = '';
        historyTable.hidden = false;
    }

    document.querySelectorAll('[data-sync-history]').forEach(function (button) {
        button.addEventListener('click', function () {
            var job = button.getAttribute('data-sync-history');
            historyTitle.textContent = button.getAttribute('data-sync-title') || 'История выгрузки';
            historyRows.replaceChildren();
            historyTable.hidden = true;
            historyMessage.textContent = 'Загружаю историю…';
            historyDialog.showModal();
            button.disabled = true;
            request('/api/admin/integrations/sync-jobs/' + encodeURIComponent(job) + '/history?limit=50', {
                headers: {'Accept': 'application/json', 'X-Requested-With': 'fetch'}
            }).then(function (data) {
                renderHistory(data.runs || []);
            }).catch(function (error) {
                historyMessage.textContent = error.message;
            }).finally(function () {
                button.disabled = false;
            });
        });
    });

    historyDialog.querySelector('[data-history-close]').addEventListener('click', function () {
        historyDialog.close();
    });
    historyDialog.addEventListener('click', function (event) {
        if (event.target === historyDialog) historyDialog.close();
    });
})();
