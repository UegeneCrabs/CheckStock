(() => {
    function displayDate(iso) {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
        return match ? `${match[3]}.${match[2]}.${match[1]}` : '';
    }
    function isoDate(value) {
        const match = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec((value || '').trim());
        if (!match) return null;
        const iso = `${match[3]}-${match[2]}-${match[1]}`;
        const parsed = new Date(`${iso}T12:00:00Z`);
        return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === iso ? iso : null;
    }
    function bindDatePicker(input) {
        const native = document.getElementById(`${input.id}-native`);
        const open = () => {
            native.value = isoDate(input.value) || '';
            if (native.showPicker) native.showPicker();
            else native.click();
        };
        input.addEventListener('click', open);
        input.parentElement.querySelector('button').addEventListener('click', open);
        native.addEventListener('change', () => {
            if (!native.value) return;
            input.value = displayDate(native.value);
            input.dispatchEvent(new Event('change', { bubbles: true }));
        });
    }
    function segments(days, value, start, end) {
        const result = [];
        let current = [];
        for (let index = start; index <= end; index++) {
            const measured = value(days[index]);
            if (measured === null || measured === undefined) {
                if (current.length) result.push(current);
                current = [];
            } else current.push({ index, value: measured });
        }
        if (current.length) result.push(current);
        return result;
    }
    window.CheckStockReportDaily = { displayDate, isoDate, bindDatePicker, segments };
})();
