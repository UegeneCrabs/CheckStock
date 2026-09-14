(function () {
    'use strict';

    var viewLinks = Array.from(document.querySelectorAll('[data-integration-view]'));
    var viewPanels = Array.from(document.querySelectorAll('[data-integration-view-panel]'));
    function showViewFromUrl() {
        var url = new URL(window.location.href);
        var selected = url.searchParams.get('tab') || 'sync';
        var anchor;
        try {
            anchor = document.getElementById(decodeURIComponent(url.hash.slice(1)));
        } catch (_) {
            /* Ignore malformed fragments. */
        }
        var anchorPanel = anchor && anchor.closest('[data-integration-view-panel]');
        if (anchorPanel) selected = anchorPanel.dataset.integrationViewPanel;
        else if (!url.searchParams.has('tab') && url.searchParams.has('ym_article')) selected = 'cabinets';
        if (
            !viewPanels.some(function (panel) {
                return panel.dataset.integrationViewPanel === selected;
            })
        )
            selected = 'sync';
        viewPanels.forEach(function (panel) {
            panel.hidden = panel.dataset.integrationViewPanel !== selected;
        });
        viewLinks.forEach(function (link) {
            if (link.dataset.integrationView === selected) link.setAttribute('aria-current', 'page');
            else link.removeAttribute('aria-current');
        });
        if (anchor)
            window.requestAnimationFrame(function () {
                anchor.scrollIntoView({ block: 'start' });
            });
    }
    viewLinks.forEach(function (link) {
        link.addEventListener('click', function (event) {
            if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            var url = new URL(window.location.href);
            url.searchParams.set('tab', link.dataset.integrationView);
            url.hash = '';
            window.history.pushState(null, '', url);
            showViewFromUrl();
        });
    });
    window.addEventListener('popstate', showViewFromUrl);
    window.addEventListener('hashchange', showViewFromUrl);
    showViewFromUrl();

    var search = document.querySelector('[data-sync-search]');
    var statusFilter = document.querySelector('[data-sync-filter]');
    var jobRows = Array.from(document.querySelectorAll('[data-sync-job]'));
    var jobGroups = Array.from(document.querySelectorAll('[data-sync-group]'));
    var groupFilters = Array.from(document.querySelectorAll('[data-sync-group-filter]'));
    var selectedGroup = 'all';
    function filterJobs() {
        var query = search.value.trim().toLocaleLowerCase('ru');
        var count = 0;
        jobRows.forEach(function (row) {
            var group = row.closest('[data-sync-group]');
            var searchable =
                row.children[0].textContent +
                ' ' +
                group.querySelector('h3').textContent +
                ' ' +
                group.querySelector('.integration-job-group-head p').textContent;
            var matches =
                (selectedGroup === 'all' || group.dataset.syncGroup === selectedGroup) &&
                searchable.toLocaleLowerCase('ru').includes(query) &&
                (statusFilter.value === 'all' ||
                    row.querySelector('.sync-status').classList.contains('is-' + statusFilter.value));
            row.hidden = !matches;
            if (matches) count += 1;
            var button = row.querySelector('[data-sync-targets-toggle]');
            var detail = document.querySelector('[data-sync-targets-row="' + row.dataset.syncJob + '"]');
            if (detail) detail.hidden = !matches || button.getAttribute('aria-expanded') !== 'true';
        });
        document.querySelector('[data-sync-count]').textContent =
            'Показано: ' + count + ' из ' + jobRows.length;
        document.querySelector('[data-sync-empty]').hidden = count !== 0;
        jobGroups.forEach(function (group) {
            var rows = Array.from(group.querySelectorAll('[data-sync-job]'));
            var visible = rows.filter(function (row) {
                return !row.hidden;
            }).length;
            group.hidden = visible === 0;
            group.querySelector('[data-sync-group-count]').textContent =
                visible === rows.length
                    ? 'Выгрузок: ' + visible
                    : 'Выгрузок: ' + visible + ' из ' + rows.length;
        });
    }
    function selectGroup(value) {
        selectedGroup = value;
        groupFilters.forEach(function (button) {
            button.setAttribute('aria-pressed', String(button.dataset.syncGroupFilter === value));
        });
    }
    groupFilters.forEach(function (button) {
        button.addEventListener('click', function () {
            selectGroup(button.dataset.syncGroupFilter);
            filterJobs();
        });
    });
    search.addEventListener('input', filterJobs);
    statusFilter.addEventListener('change', filterJobs);
    document.querySelector('[data-sync-reset]').addEventListener('click', function () {
        search.value = '';
        statusFilter.value = 'all';
        selectGroup('all');
        filterJobs();
        search.focus();
    });
    filterJobs();

    var tabs = document.querySelectorAll('[data-integration-store-tab]');
    var panels = document.querySelectorAll('[data-integration-store]');
    tabs.forEach(function (tab) {
        tab.addEventListener('click', function () {
            var selected = tab.getAttribute('data-integration-store-tab');
            tabs.forEach(function (item) {
                item.classList.toggle('is-active', item === tab);
                item.setAttribute('aria-pressed', String(item === tab));
            });
            panels.forEach(function (panel) {
                panel.hidden = panel.getAttribute('data-integration-store') !== selected;
            });
        });
    });

    function request(url, options) {
        return fetch(url, options).then(function (response) {
            return response.json().then(function (data) {
                if (!response.ok || !data.ok)
                    throw new Error(data.error || data.detail || 'Не удалось выполнить действие');
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
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
                body: JSON.stringify({ api_key: keyInput.value, client_id: client ? client.value : '' }),
            })
                .then(function () {
                    keyInput.value = '';
                    if (client) client.value = '';
                    status.textContent = 'Подключён';
                    status.className = 'integration-key-status is-connected';
                    remove.disabled = false;
                    show('Ключ сохранён', false);
                })
                .catch(function (error) {
                    show(error.message, true);
                })
                .finally(function () {
                    submit.disabled = false;
                });
        });

        remove.addEventListener('click', function () {
            var confirmation =
                window.Modal && window.Modal.confirm
                    ? window.Modal.confirm({
                          title: 'Удалить API-ключ?',
                          message: 'Маркетплейс перестанет обновляться для этого магазина.',
                          confirmText: 'Удалить',
                          danger: true,
                      })
                    : Promise.resolve(window.confirm('Удалить API-ключ?'));
            confirmation.then(function (approved) {
                if (!approved) return;
                remove.disabled = true;
                show('Удаляю…', false);
                request('/api/admin/integrations/' + store + '/' + marketplace, {
                    method: 'DELETE',
                    headers: { 'X-Requested-With': 'fetch' },
                })
                    .then(function () {
                        status.textContent = 'Ключ не задан';
                        status.className = 'integration-key-status is-empty';
                        show('Ключ удалён', false);
                    })
                    .catch(function (error) {
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
                var globalLabel = input
                    .closest('.integration-sync-toggle')
                    .querySelector('.integration-sync-toggle-label');
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
            input.disabled = !config.environment_enabled && !store && !marketplace;
        });
        var targetButton = document.querySelector('[data-sync-targets-toggle="' + config.name + '"]');
        if (targetButton) {
            targetButton.textContent =
                'Магазины · ' + config.enabled_target_count + '/' + config.target_count;
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
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
                body: JSON.stringify({
                    enabled: input.checked,
                    store_slug: input.getAttribute('data-store') || '',
                    marketplace: input.getAttribute('data-marketplace') || '',
                }),
            })
                .then(function (data) {
                    applySyncConfiguration(data.configuration);
                    syncMessage(job, 'Сохранено', false);
                    window.setTimeout(function () {
                        syncMessage(job, '', false);
                    }, 1800);
                })
                .catch(function (error) {
                    input.checked = previous;
                    input.disabled = false;
                    syncMessage(job, error.message, true);
                });
        });
    });

    var pendingSubmissions = {};
    var launchErrors = {};
    var stateGeneration = 0;
    var polling = false;
    function runMessage(job, text, isError) {
        var row = document.querySelector('[data-sync-job="' + job + '"]');
        var message = row && row.querySelector('[data-sync-run-message]');
        if (message) {
            message.textContent = text;
            message.classList.toggle('is-error', Boolean(isError));
        }
    }
    function applyRunState(state) {
        var row = document.querySelector('[data-sync-job="' + state.name + '"]');
        if (!row || pendingSubmissions[state.name]) return;
        var labels = {
            running: ['Выполняется', 'is-running'],
            success: ['Успешно', 'is-success'],
            error: ['Ошибка', 'is-error'],
        };
        var label = labels[state.status] || ['Ещё не запускалась', 'is-empty'];
        var badge = row.querySelector('.sync-status');
        badge.textContent = label[0];
        badge.className = 'sync-status ' + label[1];
        row.querySelector('[data-sync-last-run]').textContent = runDate(
            state.last_finished_at || state.last_started_at,
        );
        row.querySelector('[data-sync-trigger]').textContent =
            state.last_trigger === 'manual'
                ? 'Вручную'
                : state.last_trigger === 'scheduled'
                  ? 'По расписанию'
                  : '—';
        var next = row.querySelector('[data-sync-next-run]');
        next.textContent =
            'Следующая: ' + (state.next_run_at ? runDate(state.next_run_at) : 'Рассчитывается');
        var button = row.querySelector('[data-sync-run]');
        if (button) {
            button.disabled = Boolean(state.running);
            button.textContent = state.running ? 'Выполняется…' : 'Выгрузить вручную';
        }
        if (state.running) delete launchErrors[state.name];
        if (launchErrors[state.name]) {
            runMessage(state.name, launchErrors[state.name], true);
            return;
        }
        runMessage(state.name, '', false);
    }
    function refreshRunStates() {
        if (polling) return Promise.resolve();
        polling = true;
        var generation = stateGeneration;
        return request('/api/admin/integrations/sync-jobs', {
            headers: { Accept: 'application/json', 'X-Requested-With': 'fetch' },
        })
            .then(function (data) {
                if (generation === stateGeneration) {
                    (data.states || []).forEach(applyRunState);
                    filterJobs();
                }
            })
            .catch(function () {
                document.querySelectorAll('[data-sync-run]:disabled').forEach(function (button) {
                    runMessage(
                        button.dataset.syncRun,
                        'Не удалось получить статус. Повторяем проверку…',
                        true,
                    );
                });
            })
            .finally(function () {
                polling = false;
            });
    }
    document.querySelectorAll('[data-sync-run]').forEach(function (button) {
        button.addEventListener('click', function () {
            var job = button.getAttribute('data-sync-run');
            stateGeneration += 1;
            pendingSubmissions[job] = true;
            delete launchErrors[job];
            button.disabled = true;
            button.textContent = 'Запускаем…';
            runMessage(job, 'Запускаем выгрузку…', false);
            request('/api/admin/integrations/sync-jobs/' + encodeURIComponent(job) + '/run', {
                method: 'POST',
                headers: { Accept: 'application/json', 'X-Requested-With': 'fetch' },
            })
                .then(function (data) {
                    button.textContent = 'Выполняется…';
                    runMessage(job, data.message || 'Выгрузка выполняется в фоне', false);
                })
                .catch(function () {
                    launchErrors[job] = 'Не удалось запустить выгрузку. Попробуйте ещё раз.';
                    runMessage(job, launchErrors[job], true);
                    button.disabled = false;
                    button.textContent = 'Выгрузить вручную';
                })
                .finally(function () {
                    delete pendingSubmissions[job];
                    refreshRunStates();
                });
        });
    });
    function pollRunStates() {
        refreshRunStates().finally(function () {
            window.setTimeout(pollRunStates, 5000);
        });
    }
    pollRunStates();

    var historyDialog = document.getElementById('integration-history-dialog');
    if (!historyDialog) return;
    var historyTitle = document.getElementById('integration-history-title');
    var historyMessage = historyDialog.querySelector('[data-history-message]');
    var historyTable = historyDialog.querySelector('[data-history-table]');
    var historyRows = historyDialog.querySelector('[data-history-rows]');
    var historySummary = historyDialog.querySelector('[data-history-summary]');
    var historyGeneration = 0;
    var statusLabels = {
        running: ['Выполняется', 'is-running'],
        success: ['Успешно', 'is-success'],
        error: ['Ошибка', 'is-error'],
        interrupted: ['Прервано', 'is-interrupted'],
    };

    function runDate(value) {
        if (!value) return '—';
        var parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return String(value);
        return (
            parsed.toLocaleString('ru-RU', {
                timeZone: 'Europe/Moscow',
                day: '2-digit',
                month: '2-digit',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
            }) + ' МСК'
        );
    }

    function runDuration(milliseconds) {
        if (milliseconds === null || milliseconds === undefined) return '—';
        var seconds = Number(milliseconds) / 1000;
        if (seconds < 1) return Math.max(0, Number(milliseconds)) + ' мс';
        if (seconds < 60) return seconds.toLocaleString('ru-RU', { maximumFractionDigits: 1 }) + ' сек.';
        var rounded = Math.round(seconds);
        var minutes = Math.floor(rounded / 60);
        var remainder = rounded % 60;
        return minutes + ' мин. ' + remainder + ' сек.';
    }

    function cell(text, label) {
        var node = document.createElement('td');
        node.textContent = text;
        node.dataset.label = label;
        return node;
    }

    function renderHistory(runs) {
        historyRows.replaceChildren();
        historySummary.hidden = !runs.length;
        if (!runs.length) {
            historyTable.hidden = true;
            historyMessage.textContent = 'Запусков пока нет. Здесь появится история после первой выгрузки.';
            return;
        }
        historyDialog.querySelector('[data-history-count]').textContent = runs.length;
        historyDialog.querySelector('[data-history-success]').textContent = runs.filter(function (run) {
            return run.status === 'success';
        }).length;
        historyDialog.querySelector('[data-history-error]').textContent = runs.filter(function (run) {
            return run.status === 'error';
        }).length;
        var interruptedCount = runs.filter(function (run) {
            return run.status === 'interrupted';
        }).length;
        historyDialog.querySelector('[data-history-interrupted]').textContent = interruptedCount;
        historyDialog.querySelector('[data-history-interrupted-summary]').hidden = interruptedCount === 0;
        runs.forEach(function (run, index) {
            var row = document.createElement('tr');
            var dateCell = document.createElement('td');
            dateCell.dataset.label = 'Начало';
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
            statusCell.dataset.label = 'Результат';
            var status = statusLabels[run.status] || ['Неизвестно', 'is-empty'];
            var badge = document.createElement('span');
            badge.className = 'sync-status ' + status[1];
            badge.textContent = status[0];
            statusCell.appendChild(badge);
            row.appendChild(statusCell);
            row.appendChild(
                cell(
                    run.trigger === 'manual'
                        ? 'Вручную'
                        : run.trigger === 'scheduled'
                          ? 'По расписанию'
                          : '—',
                    'Запуск',
                ),
            );
            row.appendChild(
                cell(run.status === 'running' ? 'В процессе' : runDuration(run.duration_ms), 'Длительность'),
            );
            historyRows.appendChild(row);
            if (run.status === 'error' || run.status === 'interrupted') {
                var detailRow = document.createElement('tr');
                detailRow.className = 'integration-history-detail-row';
                detailRow.hidden = true;
                detailRow.id = 'integration-history-detail-' + index;
                var detailCell = document.createElement('td');
                detailCell.colSpan = 4;
                var detail = document.createElement('pre');
                detail.className = 'integration-history-error-detail';
                detail.textContent = run.error || 'Текст ошибки для этого запуска не сохранился.';
                detailCell.appendChild(detail);
                detailRow.appendChild(detailCell);
                var toggle = document.createElement('button');
                toggle.type = 'button';
                toggle.className = 'integration-history-error-toggle';
                var showLabel = run.status === 'interrupted' ? 'Почему прервано' : 'Показать ошибку';
                toggle.textContent = showLabel;
                toggle.setAttribute('aria-expanded', 'false');
                toggle.setAttribute('aria-controls', detailRow.id);
                toggle.addEventListener('click', function () {
                    detailRow.hidden = !detailRow.hidden;
                    toggle.setAttribute('aria-expanded', String(!detailRow.hidden));
                    toggle.textContent = detailRow.hidden ? showLabel : 'Скрыть подробности';
                });
                statusCell.appendChild(toggle);
                historyRows.appendChild(detailRow);
            }
        });
        historyMessage.textContent = '';
        historyTable.hidden = false;
    }

    document.querySelectorAll('[data-sync-history]').forEach(function (button) {
        button.addEventListener('click', function () {
            var job = button.getAttribute('data-sync-history');
            var generation = ++historyGeneration;
            historyTitle.textContent = button.getAttribute('data-sync-title') || 'История выгрузки';
            historyRows.replaceChildren();
            historyTable.hidden = true;
            historySummary.hidden = true;
            historyMessage.textContent = 'Загружаю историю…';
            historyDialog.showModal();
            document.documentElement.classList.add('integration-history-open');
            button.disabled = true;
            request('/api/admin/integrations/sync-jobs/' + encodeURIComponent(job) + '/history?limit=50', {
                headers: { Accept: 'application/json', 'X-Requested-With': 'fetch' },
            })
                .then(function (data) {
                    if (generation === historyGeneration && historyDialog.open)
                        renderHistory(data.runs || []);
                })
                .catch(function () {
                    if (generation === historyGeneration && historyDialog.open) {
                        historyMessage.textContent =
                            'Не удалось загрузить историю. Закройте окно и попробуйте ещё раз.';
                    }
                })
                .finally(function () {
                    button.disabled = false;
                });
        });
    });

    historyDialog.querySelector('[data-history-close]').addEventListener('click', function () {
        historyDialog.close();
    });
    historyDialog.addEventListener('close', function () {
        historyGeneration += 1;
        document.documentElement.classList.remove('integration-history-open');
    });
    historyDialog.addEventListener('click', function (event) {
        var bounds = historyDialog.getBoundingClientRect();
        if (
            event.target === historyDialog &&
            (event.clientX < bounds.left ||
                event.clientX > bounds.right ||
                event.clientY < bounds.top ||
                event.clientY > bounds.bottom)
        )
            historyDialog.close();
    });
})();
