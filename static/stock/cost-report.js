document.querySelectorAll('.fbs-classifier').forEach(form => {
    form.addEventListener('submit', async event => {
        event.preventDefault();
        const button = form.querySelector('button');
        const body = new FormData(form);
        button.disabled = true;
        try {
            await window.CheckStockUI.request(form.action, { method: 'POST', body });
            location.reload();
        } catch (error) {
            let message = form.querySelector('[role="alert"]');
            if (!message) { message = document.createElement('p'); message.setAttribute('role', 'alert'); form.append(message); }
            message.textContent = error.message;
            button.disabled = false;
        }
    });
});
