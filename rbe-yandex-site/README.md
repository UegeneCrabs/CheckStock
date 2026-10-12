# RBE Яндекс Маркет

Независимый сайт для отчёта RBE Яндекс Маркета. Он не подключает маршруты,
учётные записи или БД основного CheckStock.

## Что входит

- отчёт RBE по одному магазину или всем магазинам;
- собственная SQLite-база;
- страница ввода API-ключей Яндекс Маркета с проверкой Business ID;
- импорт уже загруженных данных из CheckStock в новую БД одной командой.

## Локальный запуск

```powershell
cd rbe-yandex-site
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe scripts\import_from_checkstock.py ..\data\checkstock.db
..\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8060
```

Откройте `http://127.0.0.1:8060`.

## Изоляция при развёртывании

Папки `data/` и `secrets/` не попадают в Git. Для сервера создайте их рядом с
приложением и задайте `RBE_DATABASE_PATH` и `RBE_CONNECTIONS_PATH` абсолютными
путями. В исходный код и логи API-ключи не попадают.
