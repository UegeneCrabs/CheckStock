window.CheckStockPage = (() => {
    'use strict';
    const handlers = [];
    const ui = window.CheckStockUI;
    const commonScripts = ['identifier-copy', 'modal', 'collapse', 'select', 'table-filter', 'system-alerts', 'navigation'];
    const storeColors = { rimili: '#E63946', tris: '#2A9D8F', rockkiddo: '#E9C46A', trusthome: '#8338EC', sokoloff: '#3A86FF', gogol: '#06D6A0', toyka: '#FF6D00' };

    function register(matches, prepare) { handlers.push({ matches, prepare }); }

    function script(source) {
        return new Promise((resolve, reject) => {
            const element = document.createElement('script');
            element.src = source; element.onload = resolve;
            element.onerror = () => reject(new Error('Не удалось загрузить интерфейс'));
            document.body.append(element);
        });
    }

    function navigation(context) {
        document.querySelector('[data-ui-user]').textContent = context.user.full_name;
        document.querySelector('[data-ui-initials]').textContent = context.user.full_name.split(/\s+/).slice(0, 2).map(part => part[0]).join('').toUpperCase();


        document.querySelectorAll('[data-ui-admin], [data-ui-integrations]').forEach(item => { item.hidden = !context.user.is_active; });
        const links = Array.from(document.querySelectorAll('.side-nav a, .nav-utility a'));
        const selected = links.filter(link => location.pathname === new URL(link.href).pathname || location.pathname.startsWith(new URL(link.href).pathname + '/'))
            .sort((left, right) => right.pathname.length - left.pathname.length)[0];
        if (selected) {
            selected.classList.add('active');
            const group = selected.closest('[data-nav-group]');
            if (group) group.querySelector('[data-nav-toggle]').classList.add('active');
        }
        const operations = links.find(link => link.textContent.trim() === 'История операций');
        if (operations && context.stores.length) operations.href = '/stock/' + context.stores[0].slug + '/operations';
        if (location.pathname.endsWith('/ozon') || location.pathname.includes('/ozon/reports/')) {
            document.getElementById('app-header').classList.add('marketplace-ozon');
            document.querySelector('[data-ui-marketplace]').textContent = 'Ozon';
        }
    }

    function colors() {
        document.querySelectorAll('[data-store-color]').forEach(item => {
            item.style.setProperty('--store-color', storeColors[item.dataset.storeColor] || '#64748b');
            item.style.setProperty('--store-text', item.dataset.storeColor === 'rockkiddo' ? '#1f2a44' : '#fff');
        });
    }

    async function start() {
        const loading = document.querySelector('[data-page-loading]');
        loading.textContent = 'Загружаем данные…';
        try {
            const url = new URL(location.href);
            const context = await ui.context();
            navigation(context);
            const handler = handlers.find(item => item.matches(url));
            if (!handler) throw new Error('Неизвестная страница');
            const page = await handler.prepare(context, url);
            document.title = page.title;
            document.body.dataset.section = page.section || '';
            document.querySelector('main').className = 'content ' + (page.contentClass || '');
            const template = page.template || JSON.parse(document.querySelector('[data-page-template]').textContent);
            const markup = template.replace(/\$([a-zA-Z_]\w*)/g, (_, key) => {
                if (!Object.hasOwn(page.values, key)) throw new Error('Отсутствуют данные страницы: ' + key);
                return page.values[key];
            });
            const root = document.querySelector('[data-page-content]');
            root.innerHTML = markup;
            const scripts = Array.from(root.querySelectorAll('script[src]'));
            await ui.loadTemplates([...scripts, ...['ui', ...commonScripts].map(name => ({ src: '/static/common/' + name + '.js' }))]);
            await script('/static/common/modal.js?v=20261014');
            for (const element of scripts) {
                const source = element.src;
                element.remove();
                await script(source);
            }
            colors();
            const alerts = page.alertMarketplace
                ? context.alerts.filter(alert => alert.marketplace === page.alertMarketplace)
                : context.alerts;
            if (page.showAlerts !== false && alerts.length) {
                const banner = document.createElement('section');
                banner.className = 'token-banner system-alerts'; banner.dataset.systemAlerts = '';
                banner.setAttribute('role', 'status'); banner.setAttribute('aria-live', 'polite');
                banner.innerHTML = '<span class="system-alert-dots" aria-hidden="true">' + alerts.map((_, index) => '<i class="system-alert-dot' + (index ? '' : ' is-active') + '"></i>').join('') + '</span><span class="token-banner-icon" aria-hidden="true">!</span><div class="system-alert-items">' + alerts.map((alert, index) => {
                    const store = context.stores.find(store => store.slug === alert.store_slug)?.name || alert.store_slug;
                    const title = store + ' · ' + alert.marketplace;
                    const message = alert.code === 'token_expiring' ? 'Срок действия ключа: ' + ui.date(alert.expires_at) : alert.code === 'credentials_missing' ? 'Проверьте API-ключ в настройках интеграции' : alert.code === 'access_denied' ? 'Проверьте права API-ключа: ' + alert.scope : 'Ошибка обновления: ' + alert.scope;
                    return '<article class="system-alert-item" data-system-alert-item' + (index ? ' hidden' : '') + '><strong>' + ui.escapeHtml(title) + '</strong><span>' + ui.escapeHtml(message) + '</span></article>';
                }).join('') + '</div>';
                document.querySelector('main').prepend(banner);
            }
            for (const name of commonScripts.filter(name => name !== 'modal')) await script('/static/common/' + name + '.js?v=20261014');
            if (page.ready) await page.ready(root);
            loading.remove();
        } catch (error) {
            loading.textContent = error.message + '. ';
            loading.setAttribute('role', 'alert');
            const retry = document.createElement('button');
            retry.type = 'button'; retry.className = 'btn-secondary'; retry.textContent = 'Повторить';
            retry.addEventListener('click', () => location.reload()); loading.append(retry);
        }
    }
    return { register, start, colors };
})();
