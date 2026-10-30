window.IntegrationPage = (() => {
    'use strict';
    const ui = window.CheckStockUI;
    const backupJobName = 'database_backup_google_drive';
    const backupErrors = {
        backup_configuration: 'Не задана папка резервных копий.',
        backup_source_unavailable: 'Исходная база данных недоступна.',
        backup_dump_failed: 'Не удалось создать или проверить копию базы данных.',
        backup_credentials: 'Не удалось авторизоваться сервисным аккаунтом Google.',
        backup_google_error: 'Ошибка Google Диска. Локальная копия сохранена для повторной отправки.',
        backup_folder_unavailable: 'Нет доступа к папке общего Google Диска или прав добавления файлов.',
        backup_checksum_mismatch: 'Размер или контрольная сумма копии не совпали. Локальный архив сохранён.',
        backup_upload_interrupted: 'Загрузка прервана. Локальный архив сохранён для повтора.',
    };

    function errorMessage(value) {
        const parts = String(value || '').split(': HTTP ');
        return backupErrors[parts[0]] ? backupErrors[parts[0]] + (parts[1] ? ' HTTP ' + parts[1] : '') : value;
    }
    const labels = { wb: 'Wildberries', ozon: 'Ozon', yandex: 'Яндекс Маркет', WB: 'Wildberries', OZON: 'Ozon', 'YANDEX MARKET': 'Яндекс Маркет' };
    const groups = [
        { key: 'general', title: 'Общее', description: 'Все площадки, 1С и Google Таблицы', mark: 'ВСЕ' },
        { key: 'wb', title: 'WB', description: 'Wildberries', mark: 'WB' },
        { key: 'ym', title: 'YM', description: 'Яндекс Маркет', mark: 'YM' },
        { key: 'ozon', title: 'Ozon', description: 'Ozon', mark: 'OZ' },
    ];

    function group(job) {
        if (job.name.startsWith('yandex_')) return 'ym';
        if (job.name.startsWith('ozon_') || job.name === 'ftp_ozon_export') return 'ozon';
        if (job.name.startsWith('wb_') || job.name === 'ftp_wb_export' ||
            (job.name.startsWith('unit_economics_1c_') && job.name !== 'unit_economics_1c_source_sync')) return 'wb';
        return 'general';
    }

    function summary(config) {
        if (!config.environment_enabled) return 'Системно отключена';
        if (!config.configured_enabled) return 'Выключена';
        if (config.enabled_target_count < config.target_count) return 'Частично: ' + config.enabled_target_count + ' из ' + config.target_count;
        return 'Включена';
    }

    function interval(seconds) {
        if (seconds % 86400 === 0) return seconds === 86400 ? 'Каждые сутки' : 'Каждые ' + seconds / 86400 + ' дн.';
        if (seconds % 3600 === 0) return seconds === 3600 ? 'Каждый час' : 'Каждые ' + seconds / 3600 + ' ч.';
        if (seconds % 60 === 0) return seconds === 60 ? 'Каждую минуту' : 'Каждые ' + seconds / 60 + ' мин.';
        return 'Каждые ' + seconds + ' сек.';
    }

    function schedule(value) {
        const time = value.hours.map(hour => String(hour).padStart(2, '0') + ':' + String(value.minute).padStart(2, '0')).join(', ');
        if (value.kind === 'interval') return interval(value.interval_seconds);
        if (value.kind === 'external') return 'Проверка каждую минуту; общее расписание на вкладке Google';
        if (value.kind === 'window') return 'Ежедневно с ' + time + ' до ' + String(value.deadline_hour).padStart(2, '0') + ':00 МСК; повтор каждые ' + value.retry_seconds / 60 + ' мин. при ошибке';
        if (value.kind === 'weekly') return 'По понедельникам в ' + time + ' МСК; пропущенная проверка — после запуска';
        const days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
        const extra = (value.weekly_hours || []).map(([day, hour]) => days[day] + ' ' + String(hour).padStart(2, '0') + ':00').join(', ');
        return 'Ежедневно в ' + time + ' МСК' + (extra ? '; дополнительно ' + extra + ' МСК' : '');
    }

    function toggle(job, label, store = '', marketplace = '') {
        return ui.render('integrations/render/switch', { job, label, store, marketplace });
    }

    function jobRow(job, stores) {
        const config = job.configuration;
        let targets = config.marketplace_settings.map(item => toggle(job.name, 'Все ' + (labels[item.marketplace] || item.marketplace), '', item.marketplace)).join('');
        for (const store of stores) {
            const entries = config.targets.filter(target => target.store_slug === store.slug);
            if (entries.length) targets += ui.render('integrations/render/targets', { name: store.name,
                controls: entries.map(item => toggle(job.name, labels[item.marketplace] || store.name, store.slug, item.marketplace)).join('') });
        }
        return ui.render('integrations/render/job', { ...job, schedule: schedule(job.schedule),
            toggle: toggle(job.name, summary(config)), targets });
    }

    function applyCredential(form, status) {
        const badge = form.querySelector('[data-key-status]');
        badge.textContent = status.error_code ? 'Ошибка подключения' : status.configured ? 'Подключён' : 'Ключ не задан';
        badge.className = 'integration-key-status ' + (status.configured ? 'is-connected' : 'is-empty');
        form.querySelector('[data-delete-key]').disabled = !status.configured;
        form.querySelector('.integration-key-detail').textContent = status.error_code ? ui.errorText(status) :
            status.client_id_suffix ? 'Client ID: ••••' + status.client_id_suffix :
            status.expires_at ? 'Действует до ' + ui.date(status.expires_at) : 'API-ключ не показывается после сохранения';
    }

    async function load() {
        const [context, credentials, catalog, backup] = await Promise.all([
            ui.context(), ui.request('/api/admin/integrations/credentials'), ui.request('/api/admin/integrations/sync-jobs/catalog'),
            ui.request('/api/admin/integrations/database-backup'),
        ]);
        const tabs = document.querySelector('[data-store-tabs]');
        const panels = document.querySelector('[data-store-panels]');
        context.stores.forEach((store, index) => {
            const tab = document.createElement('button');
            tab.type = 'button'; tab.className = 'integration-store-tab';
            tab.dataset.integrationStoreTab = store.slug; tab.textContent = store.name;
            tab.classList.toggle('is-active', index === 0); tab.setAttribute('aria-pressed', String(index === 0));
            tabs.append(tab);
            const cards = credentials.credentials.filter(item => item.store_slug === store.slug);
            panels.insertAdjacentHTML('beforeend', ui.render('integrations/render/store', { ...store,
                cards: cards.map(item => ui.render('integrations/render/credential', { store: store.slug, name: store.name, marketplace: item.marketplace, label: labels[item.marketplace] })).join('') }));
            const panel = panels.lastElementChild;
            panel.hidden = index !== 0;
            panel.querySelectorAll('[data-credential-form]').forEach(form => {
                form.querySelector('[data-client-id-field]').hidden = form.dataset.marketplace !== 'ozon';
                applyCredential(form, cards.find(item => item.marketplace === form.dataset.marketplace));
            });
        });
        const host = document.querySelector('[data-sync-groups]');
        const navigation = document.createElement('nav');
        navigation.className = 'integration-group-filters'; navigation.setAttribute('aria-label', 'Площадки выгрузок');
        function filter(key, title, count) {
            const button = document.createElement('button');
            button.type = 'button'; button.dataset.syncGroupFilter = key;
            button.setAttribute('aria-pressed', String(key === 'all')); button.textContent = title + ' ' + count;
            navigation.append(button);
        }
        const syncJobs = catalog.jobs.filter(job => job.name !== backupJobName);
        filter('all', 'Все', syncJobs.length);
        host.append(navigation);
        for (const section of groups) {
            const jobs = syncJobs.filter(job => group(job) === section.key);
            if (!jobs.length) continue;
            filter(section.key, section.title, jobs.length);
            host.insertAdjacentHTML('beforeend', ui.render('integrations/render/group', { ...section, rows: jobs.map(job => jobRow(job, context.stores)).join('') }));
        }
        const backupJob = catalog.jobs.find(job => job.name === backupJobName);
        if (backupJob) {
            const backupHost = document.querySelector('[data-google-backup-jobs]');
            backupHost.innerHTML = ui.render('integrations/render/group', {
                key: 'backup', title: 'База данных', mark: 'БД',
                description: 'Расписание, ручной запуск и история резервного копирования',
                rows: jobRow(backupJob, context.stores),
            });
            backupHost.querySelector('[data-sync-group-count]').textContent = '1';
        }
        const folder = document.querySelector('[data-backup-folder]');
        if (/^[A-Za-z0-9_-]+$/.test(backup.folder_id || '')) {
            folder.href = 'https://drive.google.com/drive/folders/' + encodeURIComponent(backup.folder_id);
            folder.hidden = false;
        }
        document.querySelector('[data-backup-configuration]').textContent = backup.configured
            ? 'Источник: ' + (backup.database === 'postgresql' ? 'PostgreSQL' : 'SQLite')
            : 'Для выгрузки нужны папка Google Диска и ключ сервисного аккаунта в настройках приложения.';
        for (const job of catalog.jobs) {
            const row = document.querySelector('[data-sync-job="' + job.name + '"]');
            row.querySelector('[data-sync-targets-toggle]').hidden = !job.configuration.targets.length;
            row.querySelector('[data-sync-run]').hidden = !job.manual_run;
        }
        document.querySelector('[data-job-count]').textContent = syncJobs.length;
        document.querySelector('[data-integration-loading]').hidden = true;
        window.CheckStockPage.colors();
        return catalog;
    }
    return { load, summary, applyCredential, errorMessage };
})();
