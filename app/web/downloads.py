import urllib.parse


def _download_headers(filename: str) -> dict:

    ascii_name = filename.encode("ascii", "ignore").decode("ascii").strip() or "export.xlsx"
    quoted = urllib.parse.quote(filename)
    return {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"}
