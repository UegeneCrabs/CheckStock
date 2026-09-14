document.querySelectorAll('.file-drop input[type=file]').forEach(function (input) {
    input.addEventListener('change', function () {
        var label = input.closest('.file-drop').querySelector('.file-drop-text');
        label.textContent = input.files.length ? input.files[0].name : 'Выбрать файл .xlsx';
    });
});
