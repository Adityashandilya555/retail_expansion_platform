"""Files (#51, S2-10) without the stack: keys, slot rules, magic bytes, the Storage wrapper
(faked with `httpx.MockTransport`), the document-key check of submit and start, confirm's
reject path, and the routes' guards. The real signed upload, confirm and download run in
test_files_integration.py (`-m integration`)."""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from zm_api import files, secrets, tasks
from zm_api.context import Resolver, open_context
from zm_api.errors import Conflict, FormInvalid, NotFound, UnprocessableContent
from zm_api.main import create_app
from zm_api.registry import Client
from zm_api.storage import Storage, StorageError

from .conftest import bearer, token
from .test_tasks_unit import FakeRegistry, form, matrix_retail

PDF = b"%PDF-1.7\n" + b"x" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100
JPG = b"\xff\xd8\xff\xe0" + b"x" * 100
EXE = b"MZ\x90\x00" + b"x" * 100  # a Windows program renamed .pdf
NOC = {"accept": ["pdf"], "max_mb": 25, "keep_versions": True}  # Fire NOC (qsr)


# --- keys and rules --------------------------------------------------------------------------


def test_a_file_name_becomes_a_safe_key_part_that_keeps_its_extension() -> None:
    assert files.safe_name("Fire NOC (final).pdf") == "Fire-NOC-final-.pdf"
    assert files.safe_name("résumé.PDF") == "r-sum-.PDF"
    assert files.safe_name("../../etc/passwd") == "etc-passwd"
    assert files.safe_name("...") == "file"
    long = files.safe_name("a" * 300 + ".pdf")
    assert len(long) == 100 and long.endswith(".pdf")


def test_the_rule_takes_accepted_extensions_within_max_mb() -> None:
    assert files.rule_error(NOC, "noc.pdf", 2 * files.MB) is None
    assert files.rule_error(NOC, "NOC.PDF", 25 * files.MB) is None  # case doesn't matter
    assert files.rule_error(NOC, "noc.pdf", 30 * files.MB) == "The file is larger than 25 MB"
    assert files.rule_error(NOC, "noc.exe", 1) == "Only PDF files go here"
    assert files.rule_error(NOC, "noc", 1) == "Only PDF files go here"
    both = {"accept": ["pdf", "png"], "max_mb": 5}
    assert files.rule_error(both, "plan.jpg", 1) == "Only PDF, PNG files go here"


async def chunks(*parts: bytes):
    for p in parts:
        yield p


def measured(*parts: bytes, limit: int = 10_000) -> files.Measured | None:
    return asyncio.run(files.measure(chunks(*parts), limit))


def test_measuring_a_stream_hashes_it_and_stops_past_the_limit() -> None:
    found = measured(PDF[:3], PDF[3:])
    assert found == files.Measured(len(PDF), hashlib.sha256(PDF).hexdigest(), PDF[:8])
    assert measured(b"x" * 10, b"x", limit=10) is None


def test_magic_bytes_refuse_a_renamed_exe_and_pass_real_files() -> None:
    def error(name: str, data: bytes) -> str | None:
        return files.content_error(name, len(data), measured(data))

    assert error("noc.pdf", EXE) == "This isn't a real PDF file"
    assert error("noc.pdf", PDF) is None
    assert error("plan.png", PNG) is None and error("plan.png", JPG) == "This isn't a real PNG file"
    assert error("photo.jpg", JPG) is None and error("photo.JPEG", JPG) is None
    assert error("sheet.xlsx", EXE) is None  # other types by name only (S3-D2)
    # the size announced at upload, which passed max_mb, is the size stored
    assert files.content_error("noc.pdf", 10, measured(PDF)) is not None
    assert files.content_error("noc.pdf", len(PDF), None) is not None


# --- Storage ---------------------------------------------------------------------------------


def signed_token(seconds: int) -> str:
    now = int(time.time())
    return jwt.encode(
        {"url": "x", "iat": now, "exp": now + seconds},
        "storage-jwt-secret-0123456789abcdef0123456789",
    )


class FakeStorage:
    """Supabase Storage's object routes, from memory."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            return httpx.Response(500, json={"error": "boom"})
        path = unquote(request.url.path).removeprefix("/storage/v1/object/")
        route, _, key = path.partition("files/")
        if request.method == "POST" and route == "upload/sign/":
            return httpx.Response(200, json={"url": f"/object/{path}?token={signed_token(7200)}"})
        if request.method == "POST" and route == "sign/":
            ttl = json.loads(request.content)["expiresIn"]
            return httpx.Response(
                200, json={"signedURL": f"/object/{path}?token={signed_token(ttl)}"}
            )
        if key not in self.objects:  # older Storage: 400 with the real code in the body
            return httpx.Response(400, json={"statusCode": "404", "error": "not_found"})
        if request.method == "GET" and route == "authenticated/":
            return httpx.Response(200, content=self.objects[key])
        if request.method == "DELETE" and route == "":
            del self.objects[key]
            return httpx.Response(200, json={"message": "Successfully deleted"})
        return httpx.Response(405)

    def client(self, **kwargs: Any) -> Storage:
        return Storage(
            "http://kong:8000",
            "service-jwt",
            "files",
            client="pilot-local",
            transport=httpx.MockTransport(self.handler),
            **kwargs,
        )


def test_a_signed_upload_url_is_for_the_browser_and_says_when_it_ends() -> None:
    fake = FakeStorage()
    storage = fake.client(public_url="http://127.0.0.1:54321")
    url, expires = asyncio.run(storage.signed_upload("cases/MX-0001/t/f/1-a b.pdf"))
    assert url.startswith("http://127.0.0.1:54321/storage/v1/object/upload/sign/files/cases/")
    assert abs((expires - datetime.now(UTC)).total_seconds() - 7200) < 5  # Storage's own TTL
    request = fake.requests[0]
    assert str(request.url).startswith("http://kong:8000/storage/v1/object/upload/sign/files/")
    assert request.url.raw_path.endswith(b"/1-a%20b.pdf")
    assert request.headers["apikey"] == "service-jwt"
    assert request.headers["authorization"] == "Bearer service-jwt"


def test_a_signed_download_works_60_seconds_inline_or_as_an_attachment() -> None:
    fake = FakeStorage()
    storage = fake.client()
    url, expires = asyncio.run(storage.signed_download("k/noc.pdf"))
    assert json.loads(fake.requests[0].content) == {"expiresIn": 60}
    claims = jwt.decode(
        parse_qs(urlsplit(url).query)["token"][0], options={"verify_signature": False}
    )
    assert claims["exp"] - claims["iat"] == 60 and int(expires.timestamp()) == claims["exp"]
    assert "download" not in parse_qs(urlsplit(url).query)
    attached, _ = asyncio.run(storage.signed_download("k/noc.pdf", "Fire NOC.pdf"))
    assert parse_qs(urlsplit(attached).query)["download"] == ["Fire NOC.pdf"]


def test_reading_streams_the_object_and_a_missing_one_is_none() -> None:
    fake = FakeStorage()
    fake.objects["k/noc.pdf"] = PDF
    storage = fake.client()

    async def read(key: str) -> bytes | None:
        async with storage.read(key) as stream:
            return None if stream is None else b"".join([c async for c in stream])

    assert asyncio.run(read("k/noc.pdf")) == PDF
    assert asyncio.run(read("k/never.pdf")) is None
    asyncio.run(storage.delete("k/noc.pdf"))
    asyncio.run(storage.delete("k/noc.pdf"))  # already gone: fine
    assert fake.objects == {}


def test_a_storage_error_or_silence_is_a_502_and_logs_no_key_or_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeStorage()
    fake.fail = True
    caplog.set_level(logging.INFO, logger="zm_api.storage")
    with pytest.raises(StorageError) as e:
        asyncio.run(fake.client().signed_upload("cases/MX-0001/secret-key.pdf"))
    assert e.value.status == 502

    def silent(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    down = Storage(
        "http://kong:8000", "k", "files", client="x", transport=httpx.MockTransport(silent)
    )
    with pytest.raises(StorageError):
        asyncio.run(down.signed_download("cases/MX-0001/secret-key.pdf"))
    ours = [r for r in caplog.records if r.name == "zm_api.storage"]
    assert [(r.__dict__["op"], r.__dict__["status"]) for r in ours] == [
        ("signed_upload", 500),
        ("signed_download", None),
    ]
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in ours)
    assert "secret-key" not in logged and "kong" not in logged
    # httpx's own request line has the path (this client's key), never the service key
    everything = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "service-jwt" not in everything and "token=" not in everything


def test_signed_urls_are_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    storage = FakeStorage().client()
    upload, _ = asyncio.run(storage.signed_upload("k/a.pdf"))
    download, _ = asyncio.run(storage.signed_download("k/a.pdf", "a.pdf"))
    everything = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for url in (upload, download):
        assert parse_qs(urlsplit(url).query)["token"][0] not in everything


def test_a_new_style_secret_key_goes_only_in_apikey() -> None:
    fake = FakeStorage()
    storage = Storage(
        "http://kong:8000",
        "sb_secret_x",
        "files",
        client="x",
        transport=httpx.MockTransport(fake.handler),
    )
    asyncio.run(storage.signed_upload("k"))
    assert "authorization" not in fake.requests[0].headers


# --- the client's Storage ----------------------------------------------------------------------


def with_secrets(row: Client, **extra: str) -> Client:
    return replace(row, secrets=secrets.seal({**secrets.unseal(row.secrets or b""), **extra}))


def test_storage_comes_from_the_sealed_key_and_the_project_or_local_url(
    rows: dict[str, Client],
) -> None:
    async def storage_of(row: Client) -> tuple[str, str] | None:
        ctx = open_context(row)
        try:
            s = ctx.storage
            return None if s is None else (str(s.http.base_url), s._public)
        finally:
            await ctx.aclose()

    pilot = rows["pilot-local"]
    assert asyncio.run(storage_of(pilot)) is None  # no key: qsr-local's case (D11)
    cloud = with_secrets(replace(pilot, supabase_ref="abc"), service_role_key="k")
    base = "https://abc.supabase.co/storage/v1"
    assert asyncio.run(storage_of(cloud)) == (f"{base}/", base)
    local = with_secrets(
        pilot,
        service_role_key="k",
        storage_url="http://host.docker.internal:54321",
        storage_public_url="http://127.0.0.1:54321",
    )
    assert asyncio.run(storage_of(local)) == (
        "http://host.docker.internal:54321/storage/v1/",
        "http://127.0.0.1:54321/storage/v1",
    )
    assert asyncio.run(storage_of(with_secrets(pilot, storage_url="http://x"))) is None


# --- submit and start: the document-key check ------------------------------------------------


def dd_form() -> dict[str, Any]:
    return form(matrix_retail(), "legal_dd_checklist")


VALUE = {
    "key": "cases/MX-0001/legal_dd_checklist/dd_report/1-dd.pdf",
    "sha256": "a" * 64,
    "name": "dd.pdf",
}


def test_a_document_value_must_be_a_confirmed_upload_of_that_field() -> None:
    spec = dd_form()
    tasks._validate(spec, {"dd_report": VALUE}, documents={"dd_report": [VALUE]})
    tasks._validate(spec, {})  # an optional slot left empty
    refusals = [
        None,  # never confirmed, or of another case or task: the query found nothing
        {"dd_report": []},
        {"dd_notes": [VALUE]},  # another field's upload
        {"dd_report": [{**VALUE, "sha256": "b" * 64}]},  # not the file confirmed
        {"dd_report": [{**VALUE, "name": "other.pdf"}]},
    ]
    for documents in refusals:
        with pytest.raises(FormInvalid) as e:
            tasks._validate(spec, {"dd_report": VALUE}, documents=documents)
        assert [(x.path, x.message) for x in e.value.errors] == [
            ("/dd_report", "Not a file uploaded to this field")
        ]
    with pytest.raises(FormInvalid) as e:  # the schema's own error comes alone
        tasks._validate(spec, {"dd_report": {**VALUE, "sha256": "short"}}, documents={})
    assert [x.path for x in e.value.errors] == ["/dd_report/sha256"]


def test_only_document_fields_with_a_key_are_looked_up() -> None:
    spec = dd_form()
    assert tasks.document_fields(spec) == ["dd_report"]
    assert tasks._document_keys(spec, {"dd_report": VALUE, "dd_notes": "x"}) == [VALUE["key"]]
    assert tasks._document_keys(spec, {"dd_report": "nope"}) == []


class FakeResult:
    def __init__(self, value: Any = None, rowcount: int = 1) -> None:
        self.value, self.rowcount = value, rowcount

    def one_or_none(self) -> Any:
        return self.value

    def scalar_one(self) -> Any:
        return self.value


class FakeDb:
    """`connect()` / `begin()` whose statements are recorded and answered in turn."""

    def __init__(self, *answers: FakeResult) -> None:
        self.answers, self.statements = list(answers), []

    @asynccontextmanager
    async def connect(self):
        yield self

    begin = connect

    async def execute(self, statement: Any, params: Any = None) -> FakeResult:
        self.statements.append(" ".join(str(statement).split()))
        return self.answers.pop(0) if self.answers else FakeResult()


def test_a_start_upload_claimed_by_another_case_first_fails_the_start() -> None:
    db = FakeDb(FakeResult(rowcount=1))
    with pytest.raises(tasks.FileTaken):
        asyncio.run(tasks._claim(db, uuid.uuid4(), ["k1", "k2"], uuid.uuid4()))  # type: ignore[arg-type]
    assert db.statements[0].startswith("update app.files set case_id = :c")
    asyncio.run(
        tasks._claim(FakeDb(FakeResult(rowcount=2)), uuid.uuid4(), ["a", "b"], uuid.uuid4())
    )  # type: ignore[arg-type]


# --- confirm ---------------------------------------------------------------------------------


def pending(data: bytes, name: str = "noc.pdf", **changes: Any) -> SimpleNamespace:
    row = {
        "id": uuid.uuid4(),
        "case_id": uuid.uuid4(),
        "field": "fire_noc",
        "key": f"cases/QS-0001/qsr_licences/fire_noc/1-{name}",
        "name": name,
        "size_bytes": len(data),
        "sha256": None,
        "status": "pending",
        "uploaded_by": uuid.uuid4(),
    }
    return SimpleNamespace(**{**row, **changes})


@pytest.fixture
def confirming(monkeypatch: pytest.MonkeyPatch):
    """Confirm `row` (its `_own` lookup answered from it) against a FakeStorage holding `data`,
    with `db` for the writes."""

    def run(row: SimpleNamespace, data: bytes | None, db: FakeDb, fake: FakeStorage) -> Any:
        if data is not None:
            fake.objects[row.key] = data

        async def own(*_: Any) -> SimpleNamespace:
            return row

        monkeypatch.setattr(files, "_own", own)
        ctx = SimpleNamespace(db=db, storage=fake.client())
        return asyncio.run(files.confirm(ctx, row.uploaded_by, row.id))  # type: ignore[arg-type]

    return run


def test_a_renamed_exe_is_deleted_and_rejected_with_the_reason(confirming: Any) -> None:
    row, db, fake = pending(EXE), FakeDb(), FakeStorage()
    with pytest.raises(UnprocessableContent) as e:
        confirming(row, EXE, db, fake)
    assert e.value.status == 422 and e.value.detail == "This isn't a real PDF file"
    assert fake.objects == {}  # deleted
    assert db.statements == ["update app.files set status = 'rejected' where id = :id"]


def test_a_file_that_isnt_the_announced_size_or_never_came_is_rejected(confirming: Any) -> None:
    fake = FakeStorage()
    with pytest.raises(UnprocessableContent, match="size announced"):
        confirming(pending(PDF, size_bytes=len(PDF) - 1), PDF, FakeDb(), fake)
    assert fake.objects == {}
    db = FakeDb()
    with pytest.raises(UnprocessableContent, match="never arrived"):
        confirming(pending(PDF), None, db, FakeStorage())
    assert db.statements == ["update app.files set status = 'rejected' where id = :id"]


def test_a_real_pdf_is_confirmed_as_the_next_version_of_its_slot(confirming: Any) -> None:
    row, fake = pending(PDF), FakeStorage()
    sha = hashlib.sha256(PDF).hexdigest()
    done = SimpleNamespace(**{**row.__dict__, "status": "confirmed", "sha256": sha})
    db = FakeDb(FakeResult(), FakeResult(done))
    value = confirming(row, PDF, db, fake)
    assert value == {"key": row.key, "sha256": sha, "name": "noc.pdf"}
    assert fake.objects == {row.key: PDF}  # kept
    lock, update = db.statements
    assert lock == "select 1 from app.cases where id = :c for no key update"
    assert "max(f.version)" in update and "status = 'pending'" in update


def test_confirming_again_answers_the_same_value_and_a_rejected_one_is_409(
    confirming: Any,
) -> None:
    sha = hashlib.sha256(PDF).hexdigest()
    done = pending(PDF, status="confirmed", sha256=sha)
    assert confirming(done, None, FakeDb(), FakeStorage())["sha256"] == sha
    with pytest.raises(Conflict):
        confirming(pending(PDF, status="rejected"), None, FakeDb(), FakeStorage())


# --- routes ----------------------------------------------------------------------------------


def with_storage(row: Client) -> Client:
    return with_secrets(row, service_role_key="k", storage_url="http://storage.test")


def test_a_workspace_without_storage_answers_503(rows: dict[str, Client]) -> None:
    """qsr-local has no Storage (D11): the slot's first calls say files aren't set up."""
    with TestClient(create_app(Resolver(FakeRegistry(rows)))) as http:
        user = bearer(token(client="qsr-local"))
        body = {"flow": "store_launch", "field": "fire_noc", "name": "a.pdf", "size_bytes": 1}
        for response in (
            http.post("/app/files/upload", json=body, headers=user),
            http.get("/app/cases/QS-0001/files", headers=user),
        ):
            assert response.status_code == 503
            assert response.json()["detail"] == "Files aren't set up for this workspace"


def test_an_upload_names_a_task_or_a_flow_and_fits_size_limits(rows: dict[str, Client]) -> None:
    rows = {**rows, "pilot-local": with_storage(rows["pilot-local"])}
    with TestClient(create_app(Resolver(FakeRegistry(rows)))) as http:
        user = bearer(token())
        base = {"field": "dd_report", "name": "dd.pdf", "size_bytes": 1}
        for slot in ({}, {"task_id": "t1", "flow": "store_launch"}):
            response = http.post("/app/files/upload", json={**base, **slot}, headers=user)
            assert response.status_code == 400
            assert response.json()["detail"] == "Send either task_id or flow"
        empty = {**base, "task_id": "t1", "size_bytes": 0}
        assert http.post("/app/files/upload", json=empty, headers=user).status_code == 422


def test_file_routes_need_a_client_user_and_refuse_previews(rows: dict[str, Client]) -> None:
    """A preview (#42) reads the draft only: file routes aren't in PREVIEW_ROUTES, so 403 (the
    slot says uploads are off in preview)."""
    preview = bearer(
        token("preview", **{"as": {"department": "legal", "level": 1}, "label": "Legal L1"})
    )
    some = uuid.uuid4()
    with TestClient(create_app(Resolver(FakeRegistry(rows)))) as http:
        for method, path in (
            ("POST", "/app/files/upload"),
            ("POST", f"/app/files/{some}/confirm"),
            ("GET", f"/app/files/{some}/download"),
            ("GET", "/app/cases/MX-0001/files"),
        ):
            assert http.request(method, path).status_code == 401
            assert http.request(method, path, headers=preview).status_code == 403
            owner = bearer(token("owner", client=None))
            assert http.request(method, path, headers=owner).status_code == 403


def test_a_file_id_this_client_doesnt_have_is_404_with_or_without_storage() -> None:
    """Another client's file id (the isolation suite has the real thing): the row is looked up
    first, so it is 404 whatever the workspace's setup."""
    ctx = SimpleNamespace(db=FakeDb(FakeResult(None)), storage=None)
    with pytest.raises(NotFound):
        asyncio.run(files.confirm(ctx, uuid.uuid4(), uuid.uuid4()))  # type: ignore[arg-type]


def test_upload_checks_the_rule_before_anything_is_recorded() -> None:
    slot = files.Slot("store_launch", "qsr_licences", "fire_noc", NOC, uuid.uuid4(), "cases/x")
    db, fake = FakeDb(), FakeStorage()
    ctx = SimpleNamespace(db=db)
    with pytest.raises(UnprocessableContent, match="larger than 25 MB"):
        asyncio.run(files.upload(ctx, fake.client(), uuid.uuid4(), slot, "noc.pdf", 30 * files.MB))  # type: ignore[arg-type]
    assert db.statements == [] and fake.requests == []


def test_an_upload_records_a_pending_row_under_its_key_then_signs_it() -> None:
    case_id, user = uuid.uuid4(), uuid.uuid4()
    slot = files.Slot(
        "store_launch",
        "legal_dd_checklist",
        "dd_report",
        NOC,
        case_id,
        "cases/MX-0001/legal_dd_checklist/dd_report",
    )
    file_id = uuid.uuid4()
    db, fake = FakeDb(FakeResult(file_id)), FakeStorage()
    ctx = SimpleNamespace(db=db)
    upload = files.upload(ctx, fake.client(), user, slot, "DD report.pdf", 2 * files.MB)  # type: ignore[arg-type]
    found = asyncio.run(upload)
    assert found.file_id == file_id
    prefix, _, rest = found.key.rpartition("/")
    assert prefix == "cases/MX-0001/legal_dd_checklist/dd_report"
    assert uuid.UUID(rest[:36]) and rest[36:] == "-DD-report.pdf"
    assert db.statements[0].startswith("insert into app.files")
    assert found.upload_url.startswith("http://kong:8000/storage/v1/object/upload/sign/files/")
    assert "http" not in found.key
