import io
import urllib.error
from unittest import mock

import openpyxl
import pytest

from app.ff_import import importer, shipment, transfer


def workbook_bytes(rows: list[list[object]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


@pytest.mark.unit
def test_import_rows_headers_quantities_and_urls() -> None:
    rows = [
        ["metadata"],
        ["BARCODE", "ARTICLE", "QTY"],
        ["bc", "A", "2,9"],
        ["", "B", "-3"],
        ["", "", "5"],
        [],
    ]
    entries, negative = importer._rows_to_entries(rows)
    assert entries == [("bc", "A", 2)]
    assert negative == [("", "B", -3)]
    assert importer._parse_quantity("bad") == 0
    assert importer._parse_quantity("") == 0
    with pytest.raises(importer.FFImportError):
        importer._rows_to_entries([])
    with pytest.raises(importer.FFImportError):
        importer._find_header_row([["wrong"]])

    url = "https://docs.google.com/spreadsheets/d/abc-123/edit#gid=42"
    assert importer._parse_sheet_id_and_gid(url) == ("abc-123", "42")
    assert "gid=42" in importer._extract_sheet_export_url(url)
    with pytest.raises(importer.FFImportError):
        importer._parse_sheet_id_and_gid("https://example.test")
    assert "abc" in importer._fallback_title("abcdefghijk")


class Response:
    headers = {"Content-Type": "text/csv"}

    def __init__(self, content: bytes) -> None:
        self.content = content

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def read(self, *_args: object) -> bytes:
        return self.content


@pytest.mark.unit
def test_public_title_and_csv_download() -> None:
    url = "https://docs.google.com/spreadsheets/d/abcdefgh/edit#gid=0"
    with mock.patch.object(
        importer.urllib.request,
        "urlopen",
        return_value=Response(b"<title>My table - Google Sheets</title>"),
    ):
        assert importer._fetch_public_sheet_title(url) == "My table"
    with mock.patch.object(importer.urllib.request, "urlopen", side_effect=OSError("x")):
        assert "abcdefgh" in importer._fetch_public_sheet_title(url)
    with mock.patch.object(
        importer.urllib.request,
        "urlopen",
        return_value=Response(b"BARCODE,ARTICLE,QTY\nbc,A,2\n"),
    ):
        assert importer.fetch_google_sheet_rows(url)[1] == ["bc", "A", "2"]

    html_response = Response(b"<!doctype html><html></html>")
    html_response.headers = {"Content-Type": "text/html"}
    with mock.patch.object(importer.urllib.request, "urlopen", return_value=html_response):
        with pytest.raises(importer._SheetAccessDenied):
            importer.fetch_google_sheet_rows(url)
    for error, error_type in (
        (urllib.error.HTTPError(url, 403, "forbidden", {}, None), importer._SheetAccessDenied),
        (urllib.error.HTTPError(url, 500, "bad", {}, None), importer.FFImportError),
        (urllib.error.URLError("offline"), importer.FFImportError),
    ):
        with mock.patch.object(importer.urllib.request, "urlopen", side_effect=error):
            with pytest.raises(error_type):
                importer.fetch_google_sheet_rows(url)


@pytest.mark.unit
def test_xlsx_and_transfer_parsers() -> None:
    rows = [
        ["meta"],
        ["BARCODE", "ARTICLE", "QTY"],
        ["bc", "A", "2"],
        ["", "B", "3"],
        ["x", "", "0"],
        [],
    ]
    content = workbook_bytes(rows[1:3])
    assert importer._parse_xlsx_rows(content)[1] == ["bc", "A", "2"]
    with pytest.raises(importer.FFImportError):
        importer._parse_xlsx_rows(b"not-a-workbook")

    parsed = transfer._parse_transfer_rows(rows)
    assert parsed.model_dump(mode="json") == [
        {"code": "bc", "quantity": 2},
        {"code": "B", "quantity": 3},
    ]
    for invalid in ([], [["wrong"]], [["BARCODE", "QTY"], ["bc", "0"]]):
        with pytest.raises(importer.FFImportError):
            transfer._parse_transfer_rows(invalid)
    with mock.patch.object(transfer, "_parse_xlsx_rows", return_value=rows):
        assert len(transfer.entries_from_xlsx(b"x").root) == 2
    with mock.patch.object(transfer, "fetch_google_sheet_rows", return_value=rows):
        assert len(transfer.entries_from_sheet("url").root) == 2
    with (
        mock.patch.object(
            transfer,
            "fetch_google_sheet_rows",
            side_effect=importer._SheetAccessDenied(),
        ),
        mock.patch.object(
            transfer,
            "fetch_google_sheet_rows_via_api",
            return_value=(rows, "Title"),
        ),
    ):
        assert len(transfer.entries_from_sheet("url").root) == 2
    assert shipment.entries_from_rows([["BARCODE", "QTY"], ["bc", "1"]]).root[0].code == "bc"


@pytest.mark.unit
def test_import_delivery_application_and_wrappers() -> None:
    catalog = [
        {"article": "A", "barcode": "bc", "name": "Alpha"},
        {"article": "B", "barcode": "bb", "name": "Beta"},
    ]
    with (
        mock.patch.object(importer.db, "get_catalog_items", return_value=catalog),
        mock.patch.object(
            importer.db,
            "apply_ff_import_snapshot",
            return_value={"A": 5},
        ) as apply_snapshot,
    ):
        result = importer._apply_entries(
            "store",
            "FF",
            [("bc", "", 2), ("", "A", 3), ("x", "missing", 4)],
            marketplace="WB",
            source_type="file",
            sheet_url=None,
            table_title="",
            negative_skipped=[("bb", "", -1)],
        )
    assert result["matched"] == 1
    assert result["unmatched"] == 1
    assert result["items"] == []
    assert result["unchanged"][0]["article"] == "A"
    assert result["negative_skipped"] == [{"article": "B", "quantity": -1}]
    apply_snapshot.assert_called_once()

    with (
        mock.patch.object(importer.db, "get_catalog_items", return_value=catalog),
        mock.patch.object(
            importer.db,
            "apply_ff_import_snapshot",
            return_value={"A": 2},
        ),
    ):
        changed = importer._apply_entries(
            "store",
            "FF",
            [("bc", "", 5), ("bb", "", 3)],
            marketplace="WB",
            source_type="file",
            sheet_url=None,
            table_title="delivery.xlsx",
        )
    assert changed["added_quantity"] == 6
    assert changed["increased"][0]["quantity"] == 3
    assert changed["new_items"][0]["article"] == "B"

    rows = [["BARCODE", "ARTICLE", "QTY"], ["bc", "A", "2"]]
    with (
        mock.patch.object(importer, "fetch_google_sheet_rows", return_value=rows),
        mock.patch.object(importer, "_fetch_public_sheet_title", return_value="Title"),
        mock.patch.object(importer, "_apply_entries", return_value={"matched": 1}) as apply,
    ):
        assert importer.import_ff_stock_from_sheet("store", "FF", "url")["matched"] == 1
    assert apply.call_args.kwargs["table_title"] == "Title"
