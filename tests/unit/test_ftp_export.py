import json
from unittest import mock

import pytest

from app import ftp_export


def test_source_configuration_includes_all_google_apps_script_sheets() -> None:
    assert ftp_export.SOURCE_SHEETS["wb"] == (
        "RIMILI WB",
        "SOKOLOFF и TRUSTHOME WB",
        "TOYKA WB",
        "TRIS WB",
        "ROCKKIDDO WB",
        "GOGOL WB",
    )
    assert ftp_export.SOURCE_SHEETS["ozon"][-1] == "GOGOL OZON"
    assert ftp_export.platform_for_job("ftp_wb_export") == "wb"
    assert ftp_export.platform_for_job("unknown") is None


def test_fetches_all_configured_sheets_in_one_google_request(monkeypatch) -> None:
    monkeypatch.setitem(ftp_export.SOURCE_SHEETS, "wb", ("RIMILI WB", "Owner's WB"))
    monkeypatch.setattr(ftp_export.google_service_account, "has_credentials", lambda: True)
    credentials = object()
    monkeypatch.setattr(ftp_export.google_service_account, "get_credentials", lambda: credentials)
    request = mock.Mock()
    request.execute.return_value = {
        "valueRanges": [
            {"values": [["first"]]},
            {"values": [["second"]]},
        ]
    }
    values = mock.Mock()
    values.batchGet.return_value = request
    spreadsheets = mock.Mock()
    spreadsheets.values.return_value = values
    service = mock.Mock()
    service.spreadsheets.return_value = spreadsheets
    from googleapiclient import discovery

    build = mock.Mock(return_value=service)
    monkeypatch.setattr(discovery, "build", build)

    result = ftp_export.fetch_source_rows("wb")

    assert result == {"RIMILI WB": [["first"]], "Owner's WB": [["second"]]}
    build.assert_called_once_with("sheets", "v4", credentials=credentials, cache_discovery=False)
    assert values.batchGet.call_args.kwargs["ranges"] == ["'RIMILI WB'", "'Owner''s WB'"]


def test_google_source_errors_are_explicit(monkeypatch) -> None:
    monkeypatch.setattr(ftp_export.google_service_account, "has_credentials", lambda: False)
    with pytest.raises(ftp_export.FTPExportError, match="сервисный аккаунт"):
        ftp_export.fetch_source_rows("wb")

    monkeypatch.setattr(ftp_export.google_service_account, "has_credentials", lambda: True)
    monkeypatch.setattr(
        ftp_export.google_service_account,
        "get_credentials",
        mock.Mock(
            side_effect=ftp_export.google_service_account.CredentialsUnavailableError("повреждённый ключ")
        ),
    )
    with pytest.raises(ftp_export.FTPExportError, match="повреждённый ключ"):
        ftp_export.fetch_source_rows("wb")

    with pytest.raises(ftp_export.FTPExportError, match="WB и Ozon"):
        ftp_export.fetch_source_rows("yandex")


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (403, "Нет доступа"),
        (404, "не найден"),
        (500, "Google Sheets API"),
    ],
)
def test_google_http_errors_are_translated(monkeypatch, status: int, message: str) -> None:
    from googleapiclient import discovery
    from googleapiclient.errors import HttpError

    monkeypatch.setitem(ftp_export.SOURCE_SHEETS, "wb", ("RIMILI WB",))
    monkeypatch.setattr(ftp_export.google_service_account, "has_credentials", lambda: True)
    monkeypatch.setattr(ftp_export.google_service_account, "get_credentials", lambda: object())
    response = mock.Mock(status=status, reason="failed")
    request = mock.Mock()
    request.execute.side_effect = HttpError(response, b'{"error":{"message":"failed"}}')
    values = mock.Mock()
    values.batchGet.return_value = request
    spreadsheets = mock.Mock()
    spreadsheets.values.return_value = values
    service = mock.Mock()
    service.spreadsheets.return_value = spreadsheets
    monkeypatch.setattr(discovery, "build", mock.Mock(return_value=service))

    with pytest.raises(ftp_export.FTPExportError, match=message):
        ftp_export.fetch_source_rows("wb")


def test_google_response_must_include_every_sheet(monkeypatch) -> None:
    from googleapiclient import discovery

    monkeypatch.setitem(ftp_export.SOURCE_SHEETS, "wb", ("RIMILI WB",))
    monkeypatch.setattr(ftp_export.google_service_account, "has_credentials", lambda: True)
    monkeypatch.setattr(ftp_export.google_service_account, "get_credentials", lambda: object())
    request = mock.Mock()
    request.execute.return_value = {"valueRanges": []}
    values = mock.Mock()
    values.batchGet.return_value = request
    spreadsheets = mock.Mock()
    spreadsheets.values.return_value = values
    service = mock.Mock()
    service.spreadsheets.return_value = spreadsheets
    monkeypatch.setattr(discovery, "build", mock.Mock(return_value=service))

    with pytest.raises(ftp_export.FTPExportError, match="не все запрошенные"):
        ftp_export.fetch_source_rows("wb")


def test_collects_wb_items_and_reports_incomplete_rows(monkeypatch) -> None:
    monkeypatch.setitem(ftp_export.SOURCE_SHEETS, "wb", ("RIMILI WB",))
    source_rows = {
        "RIMILI WB": [
            ["Служебная строка"],
            [
                "АртикулВБ",
                "Себес, руб",
                "Проч.затр, руб",
                "Артикул поставщика внешний",
                "Тег",
            ],
            ["123456", "1 234,50", "25", "SUP-1", "A"],
            ["654321", "#N/A", "10", "SUP-2", "B"],
        ]
    }

    report = ftp_export.collect_platform_data("wb", source_rows)

    assert report["items"] == [
        {
            "nmId": "123456",
            "cost_price": 1234.5,
            "other_costs": 25,
            "supplier_article_number": "SUP-1",
            "tag": "A",
        }
    ]
    assert report["total_rows"] == 2
    assert report["missing_costs"] == {"RIMILI WB": ["654321"]}
    assert report["missing_other_costs"] == {}
    assert ftp_export._clean_number(None) is None
    assert ftp_export._clean_number("") == ""
    assert ftp_export._clean_number("не число") == "не число"


def test_collects_ozon_items_and_excludes_marked_articles(monkeypatch) -> None:
    monkeypatch.setitem(ftp_export.SOURCE_SHEETS, "ozon", ("RIMILI OZON",))
    source_rows = {
        "RIMILI OZON": [
            ["Артикул ОЗОН", "Себес, руб", "Проч.затр, руб", "Тег"],
            ["OZ-1", 100, "20,5", "main"],
            ["Нет на ОЗОН", 50, 10, ""],
        ]
    }

    report = ftp_export.collect_platform_data("ozon", source_rows)

    assert report["items"] == [
        {
            "product_id": "OZ-1",
            "cost_price": 100,
            "other_costs": 20.5,
            "tag": "main",
        }
    ]
    assert report["excluded_rows"] == 1


def test_collection_rejects_missing_sheet_or_headers(monkeypatch) -> None:
    monkeypatch.setitem(ftp_export.SOURCE_SHEETS, "wb", ("RIMILI WB",))
    with pytest.raises(ftp_export.FTPExportError, match="Не получены данные"):
        ftp_export.collect_platform_data("wb", {})
    with pytest.raises(ftp_export.FTPExportError, match="Ошибка листа"):
        ftp_export.collect_platform_data("wb", {"RIMILI WB": [["не те заголовки"]]})
    with pytest.raises(ftp_export.FTPExportError, match="WB и Ozon"):
        ftp_export.collect_platform_data("yandex", {})


def test_uploads_utf8_json_in_passive_plain_ftp(monkeypatch) -> None:
    configured = ftp_export.settings.model_copy(
        update={
            "ftp_host_wb": "ftp.example.test",
            "ftp_user_wb": "user",
            "ftp_password_wb": "password",
            "ftp_timeout_seconds": 12,
            "ftp_mode": "auto",
            "ftp_tls": "off",
        }
    )
    monkeypatch.setattr(ftp_export, "settings", configured)
    connection = mock.Mock()
    ftp_class = mock.Mock(return_value=connection)
    monkeypatch.setattr(ftp_export.ftplib, "FTP", ftp_class)

    filename = ftp_export.upload_to_ftp(
        "wb",
        {"date": "2026-08-31T03:15:00+03:00", "items": [{"nmId": "ТЕСТ"}]},
    )

    assert filename == "data.json"
    ftp_class.assert_called_once_with("ftp.example.test", timeout=12)
    connection.login.assert_called_once_with("user", "password")
    connection.set_pasv.assert_called_once_with(True)
    command, file_object = connection.storbinary.call_args.args
    assert command == "STOR data.json"
    assert json.loads(file_object.getvalue().decode("utf-8"))["items"] == [{"nmId": "ТЕСТ"}]
    connection.quit.assert_called_once_with()


def test_auto_mode_falls_back_to_ftps_when_server_requires_auth(monkeypatch) -> None:
    configured = ftp_export.settings.model_copy(
        update={
            "ftp_host_ozon": "secure.example.test",
            "ftp_user_ozon": "user",
            "ftp_password_ozon": "password",
            "ftp_mode": "passive",
            "ftp_tls": "auto",
            "ftp_prot": "private",
        }
    )
    monkeypatch.setattr(ftp_export, "settings", configured)
    plain_connection = mock.Mock()
    plain_connection.login.side_effect = ftp_export.ftplib.error_perm("534 Use AUTH first")
    tls_connection = mock.Mock()
    monkeypatch.setattr(ftp_export.ftplib, "FTP", mock.Mock(return_value=plain_connection))
    tls_class = mock.Mock(return_value=tls_connection)
    monkeypatch.setattr(ftp_export.ftplib, "FTP_TLS", tls_class)

    filename = ftp_export.upload_to_ftp("ozon", {"date": "now", "items": [{"product_id": "1"}]})

    assert filename == "data_ozon.json"
    tls_class.assert_called_once_with("secure.example.test", timeout=configured.ftp_timeout_seconds)
    tls_connection.prot_p.assert_called_once_with()
    tls_connection.set_pasv.assert_called_once_with(True)
    assert tls_connection.storbinary.call_args.args[0] == "STOR data_ozon.json"


def test_active_mode_and_ftp_errors_are_handled(monkeypatch) -> None:
    configured = ftp_export.settings.model_copy(
        update={
            "ftp_host_wb": "ftp.example.test",
            "ftp_user_wb": "user",
            "ftp_password_wb": "password",
            "ftp_mode": "active",
            "ftp_tls": "off",
        }
    )
    monkeypatch.setattr(ftp_export, "settings", configured)
    connection = mock.Mock()
    monkeypatch.setattr(ftp_export.ftplib, "FTP", mock.Mock(return_value=connection))

    ftp_export.upload_to_ftp("wb", {"items": [{"nmId": "1"}]})

    connection.set_pasv.assert_called_once_with(False)
    connection.login.side_effect = ftp_export.ftplib.error_perm("530 invalid login")
    with pytest.raises(ftp_export.FTPExportError, match="FTP WB: 530 invalid login"):
        ftp_export.upload_to_ftp("wb", {"items": [{"nmId": "1"}]})


def test_auto_transfer_mode_retries_active_after_425(monkeypatch) -> None:
    configured = ftp_export.settings.model_copy(
        update={
            "ftp_host_wb": "ftp.example.test",
            "ftp_user_wb": "user",
            "ftp_password_wb": "password",
            "ftp_mode": "auto",
            "ftp_tls": "off",
        }
    )
    monkeypatch.setattr(ftp_export, "settings", configured)
    passive = mock.Mock()
    passive.storbinary.side_effect = ftp_export.ftplib.error_temp("425 data connection failed")
    active = mock.Mock()
    monkeypatch.setattr(
        ftp_export.ftplib,
        "FTP",
        mock.Mock(side_effect=[passive, active]),
    )

    ftp_export.upload_to_ftp("wb", {"items": [{"nmId": "1"}]})

    passive.set_pasv.assert_called_once_with(True)
    active.set_pasv.assert_called_once_with(False)


def test_explicit_ftps_clear_data_channel(monkeypatch) -> None:
    configured = ftp_export.settings.model_copy(
        update={
            "ftp_host_ozon": "secure.example.test",
            "ftp_user_ozon": "user",
            "ftp_password_ozon": "password",
            "ftp_mode": "passive",
            "ftp_tls": "on",
            "ftp_prot": "clear",
        }
    )
    monkeypatch.setattr(ftp_export, "settings", configured)
    connection = mock.Mock()
    monkeypatch.setattr(ftp_export.ftplib, "FTP_TLS", mock.Mock(return_value=connection))

    ftp_export.upload_to_ftp("ozon", {"items": [{"product_id": "1"}]})

    connection.prot_c.assert_called_once_with()
    connection.prot_p.assert_not_called()


def test_ftps_auto_protection_retries_clear_channel_after_eof(monkeypatch) -> None:
    configured = ftp_export.settings.model_copy(
        update={
            "ftp_host_ozon": "secure.example.test",
            "ftp_user_ozon": "user",
            "ftp_password_ozon": "password",
            "ftp_mode": "passive",
            "ftp_tls": "on",
            "ftp_prot": "auto",
        }
    )
    monkeypatch.setattr(ftp_export, "settings", configured)
    private = mock.Mock()
    private.storbinary.side_effect = ftp_export.ftplib.error_proto("UNEXPECTED_EOF_WHILE_READING")
    clear = mock.Mock()
    monkeypatch.setattr(
        ftp_export.ftplib,
        "FTP_TLS",
        mock.Mock(side_effect=[private, clear]),
    )

    ftp_export.upload_to_ftp("ozon", {"items": [{"product_id": "1"}]})

    private.prot_p.assert_called_once_with()
    clear.prot_c.assert_called_once_with()


def test_missing_credentials_fail_before_connection(monkeypatch) -> None:
    configured = ftp_export.settings.model_copy(
        update={"ftp_host_wb": "", "ftp_user_wb": "", "ftp_password_wb": ""}
    )
    monkeypatch.setattr(ftp_export, "settings", configured)

    with pytest.raises(ftp_export.FTPExportError, match="FTP-реквизиты"):
        ftp_export.upload_to_ftp("wb", {"items": []})

    with pytest.raises(ftp_export.FTPExportError, match="WB и Ozon"):
        ftp_export.upload_to_ftp("yandex", {"items": []})


def test_run_platform_uploads_only_summary_and_releases_lock(monkeypatch) -> None:
    monkeypatch.setattr(
        ftp_export,
        "collect_platform_data",
        lambda platform: {
            "items": [{"nmId": "1"}],
            "total_rows": 2,
            "missing_costs": {"RIMILI WB": ["2"]},
            "missing_other_costs": {},
            "excluded_rows": 0,
        },
    )
    upload = mock.Mock(return_value="data.json")
    monkeypatch.setattr(ftp_export, "upload_to_ftp", upload)

    result = ftp_export.run_platform("wb")

    assert result["ok"] is True
    assert result["items"] == 1
    assert result["missing_costs"] == 1
    payload = upload.call_args.args[1]
    assert payload["items"] == [{"nmId": "1"}]
    assert payload["date"].endswith("+03:00")
    assert not ftp_export.is_running("wb")


def test_run_platform_rejects_empty_export_and_releases_lock(monkeypatch) -> None:
    monkeypatch.setattr(
        ftp_export,
        "collect_platform_data",
        lambda platform: {
            "items": [],
            "total_rows": 1,
            "missing_costs": {},
            "missing_other_costs": {},
            "excluded_rows": 1,
        },
    )

    with pytest.raises(ftp_export.FTPExportError, match="ни одного полного товара"):
        ftp_export.run_platform("ozon")

    assert not ftp_export.is_running("ozon")


def test_run_platform_rejects_unknown_or_concurrent_platform() -> None:
    with pytest.raises(ftp_export.FTPExportError, match="WB и Ozon"):
        ftp_export.run_platform("yandex")

    lock = ftp_export._EXPORT_LOCKS["wb"]
    lock.acquire()
    try:
        assert ftp_export.is_running("wb")
        with pytest.raises(ftp_export.FTPExportBusyError, match="уже выполняется"):
            ftp_export.run_platform("wb")
    finally:
        lock.release()
