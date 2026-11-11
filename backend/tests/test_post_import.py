from __future__ import annotations

from datetime import datetime, timedelta, timezone


def future() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()


def csv_file(*rows: str) -> bytes:
    return ("\ufeffbody,scheduled_at,platforms,blog_url\n" + "\n".join(rows)).encode("utf-8")


def test_preview_valid_csv_does_not_create_posts(client):
    content = csv_file(f'"Hello, world",{future()},x;bluesky,')
    response = client.post("/api/posts/import", content=content, headers={"content-type": "text/csv"})

    assert response.status_code == 200
    assert response.json() == {"valid": True, "posts": 1, "errors": []}
    assert client.get("/api/posts").json() == []


def test_import_creates_all_rows_in_existing_post_pipeline(client):
    content = csv_file(f'"First post",{future()},x,', f'"Second post",{future()},bluesky,')
    response = client.post("/api/posts/import?preview=false", content=content,
                           headers={"content-type": "text/csv"})

    assert response.status_code == 200
    assert response.json() == {"valid": True, "posts": 2, "created": 2, "errors": []}
    posts = client.get("/api/posts").json()
    assert [post["body"] for post in posts] == ["First post", "Second post"]
    assert [post["targets"][0]["platform"] for post in posts] == ["x", "bluesky"]
    assert all(target["status"] == "scheduled" for post in posts for target in post["targets"])


def test_invalid_row_prevents_the_entire_import(client):
    content = csv_file(f"Good post,{future()},x,", "Bad post,not-a-time,x,")
    response = client.post("/api/posts/import?preview=false", content=content,
                           headers={"content-type": "text/csv"})

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False
    assert result["created"] == 0
    assert result["errors"][0]["row"] == 3
    assert client.get("/api/posts").json() == []


def test_import_requires_template_columns(client):
    response = client.post("/api/posts/import", content=b"text,time,platform\nHello,2030-01-01,x",
                           headers={"content-type": "text/csv"})

    assert response.json()["valid"] is False
    assert "Missing columns" in response.json()["errors"][0]["message"]


def test_import_validates_platform_and_timezone(client):
    content = csv_file("Post,2030-01-01T10:00:00+00:00,nope,", "Post,2030-01-01T10:00:00,x,")
    response = client.post("/api/posts/import", content=content, headers={"content-type": "text/csv"})

    errors = response.json()["errors"]
    assert [error["row"] for error in errors] == [2, 3]
    assert "Unknown platform" in errors[0]["message"]
    assert "timezone" in errors[1]["message"].lower()


def preview(client, text: str, tz: str | None = None) -> dict:
    url = "/api/posts/import" + (f"?tz={tz}" if tz else "")
    return client.post(url, content=text.encode("utf-8"), headers={"content-type": "text/csv"}).json()


def test_times_without_offset_use_the_browser_timezone(client):
    content = csv_file("Post,2030-01-01 09:00,x,")
    client.post("/api/posts/import?preview=false&tz=Africa/Harare", content=content,
                headers={"content-type": "text/csv"})

    assert client.get("/api/posts").json()[0]["scheduled_at"] == "2030-01-01T07:00:00+00:00"


def test_spreadsheet_style_files_are_accepted(client):
    text = "Body;Scheduled At;Platforms\nCafé post;2030-01-01T09:00:00Z;\"LinkedIn; Bluesky\"\n;;\n"
    result = client.post("/api/posts/import", content=text.encode("cp1252"),
                         headers={"content-type": "text/csv"}).json()

    assert result == {"valid": True, "posts": 1, "errors": []}


def test_unreadable_time_gets_a_plain_message(client):
    errors = preview(client, "body,scheduled_at,platforms\nPost,next tuesday,x\n")["errors"]

    assert errors[0]["message"].startswith("Couldn't read the time")


def test_row_numbers_match_the_spreadsheet_when_text_spans_lines(client):
    errors = preview(client, 'body,scheduled_at,platforms\n"line 1\nline 2",2030-01-01T09:00:00Z,x\nB,bad,x\n')["errors"]

    assert errors[0]["row"] == 3


# ---- Excel workbooks ------------------------------------------------------------

from backend.app.services.xlsx import Sheet, excel_serial_to_datetime, read_xlsx, write_xlsx  # noqa: E402

HEADER = ["body", "scheduled_at", "platforms", "blog_url"]


def workbook(*rows: list, help_first: bool = False) -> bytes:
    sheets = [Sheet("Posts", [HEADER, *rows])]
    if help_first:
        sheets.insert(0, Sheet("How to fill", [["Column", "What to put in it"]]))
    return write_xlsx(sheets)


def post_xlsx(client, content: bytes, preview: bool = True, tz: str = "Africa/Harare") -> dict:
    return client.post(f"/api/posts/import?preview={str(preview).lower()}&tz={tz}", content=content,
                       headers={"content-type": "application/octet-stream"}).json()


def test_excel_date_cells_import_in_the_browser_timezone(client):
    # 47119.375 is how Excel stores 2029-01-01 09:00.
    result = post_xlsx(client, workbook(["From Excel", 47119.375, "LinkedIn;Bluesky", ""]), preview=False)

    assert result == {"valid": True, "posts": 1, "created": 1, "errors": []}
    post = client.get("/api/posts").json()[0]
    assert post["scheduled_at"] == "2029-01-01T07:00:00+00:00"
    assert [t["platform"] for t in post["targets"]] == ["linkedin", "bluesky"]


def test_excel_uses_the_sheet_with_the_template_columns(client):
    assert post_xlsx(client, workbook(["Post", "2030-01-01 09:00", "x", ""], help_first=True))["valid"] is True


def without_empty_rows(content: bytes) -> bytes:
    """Excel leaves empty rows out of the file entirely; write_xlsx keeps them, so drop them."""
    import io
    import re
    import zipfile

    src, out = zipfile.ZipFile(io.BytesIO(content)), io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name in src.namelist():
            data = src.read(name)
            z.writestr(name, re.sub(rb'<row r="\d+"></row>', b"", data) if "worksheets" in name else data)
    return out.getvalue()


def test_excel_row_numbers_count_blank_rows(client):
    content = without_empty_rows(workbook(["Good", "2030-01-01 09:00", "x", ""], [], ["Bad", "soon", "x", ""]))
    assert b'<row r="3"></row>' not in zipfile_sheet(content)
    errors = post_xlsx(client, content)["errors"]

    assert errors[0]["row"] == 4


def zipfile_sheet(content: bytes) -> bytes:
    import io
    import zipfile

    return zipfile.ZipFile(io.BytesIO(content)).read("xl/worksheets/sheet1.xml")


def test_old_xls_files_get_a_save_as_hint(client):
    response = client.post("/api/posts/import", content=b"\xd0\xcf\x11\xe0" + b"\0" * 100)

    assert response.status_code == 422
    assert "Save As" in response.json()["detail"]


def test_excel_serial_rounds_to_the_minute():
    assert excel_serial_to_datetime(46357.375).isoformat() == "2026-12-01T09:00:00"
    assert excel_serial_to_datetime(46357.37499999).isoformat() == "2026-12-01T09:00:00"


def test_read_xlsx_rejects_a_zip_that_is_not_a_workbook():
    import io
    import zipfile

    import pytest

    from backend.app.services.errors import InvalidInput

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(InvalidInput):
        read_xlsx(buf.getvalue())
