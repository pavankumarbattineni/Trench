"""B2StorageProvider's boto3 call shapes, verified against botocore's
Stubber -- no real B2 credentials or network access involved."""

import io
from urllib.parse import parse_qs, urlparse

import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from app.config import BackblazeConfig
from app.service.document_storage_service import (
    B2StorageProvider,
    DocumentStorageError,
    LocalFilesystemStorageProvider,
    get_storage_provider,
)

BUCKET = "Trench"
KEY = "personal/user-id/doc-id/notes.txt"


@pytest.fixture
def provider() -> B2StorageProvider:
    return B2StorageProvider(
        BackblazeConfig(
            endpoint_url="https://s3.us-west-004.backblazeb2.com",
            region="us-west-004",
            bucket_name=BUCKET,
            application_key_id="fake-key-id",
            application_key="fake-application-key",
        )
    )


@pytest.fixture
def stubber(provider: B2StorageProvider):
    with Stubber(provider._client) as stub:
        yield stub
        stub.assert_no_pending_responses()


@pytest.mark.asyncio
async def test_save_puts_object_under_bucket_and_key(provider, stubber):
    stubber.add_response(
        "put_object", {}, {"Bucket": BUCKET, "Key": KEY, "Body": b"hello"}
    )
    await provider.save(KEY, b"hello")


@pytest.mark.asyncio
async def test_read_returns_object_body(provider, stubber):
    body = b"stored bytes"
    stubber.add_response(
        "get_object",
        {"Body": StreamingBody(io.BytesIO(body), len(body))},
        {"Bucket": BUCKET, "Key": KEY},
    )
    assert await provider.read(KEY) == body


@pytest.mark.asyncio
async def test_delete_deletes_every_version_of_the_object(provider, stubber):
    # The bucket has versioning enabled, so a correct delete must list
    # every version (and delete marker) of the key, then remove them all
    # by (Key, VersionId) -- not just issue a plain unversioned delete,
    # which would only add another delete marker and leave the actual
    # bytes (and any prior ones) still stored.
    stubber.add_response(
        "list_object_versions",
        {
            "Versions": [{"Key": KEY, "VersionId": "v1"}],
            "DeleteMarkers": [{"Key": KEY, "VersionId": "v2"}],
        },
        {"Bucket": BUCKET, "Prefix": KEY},
    )
    stubber.add_response(
        "delete_objects",
        {},
        {
            "Bucket": BUCKET,
            "Delete": {
                "Objects": [
                    {"Key": KEY, "VersionId": "v1"},
                    {"Key": KEY, "VersionId": "v2"},
                ]
            },
        },
    )
    await provider.delete(KEY)


@pytest.mark.asyncio
async def test_delete_is_a_noop_when_no_versions_exist(provider, stubber):
    stubber.add_response(
        "list_object_versions", {}, {"Bucket": BUCKET, "Prefix": KEY}
    )
    await provider.delete(KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("put_object", lambda p: p.save(KEY, b"x")),
        ("get_object", lambda p: p.read(KEY)),
        ("list_object_versions", lambda p: p.delete(KEY)),
    ],
)
async def test_client_errors_are_wrapped_in_document_storage_error(
    provider, stubber, method, call
):
    stubber.add_client_error(method, service_error_code="AccessDenied")
    with pytest.raises(DocumentStorageError):
        await call(provider)


@pytest.mark.asyncio
async def test_connection_errors_are_wrapped_in_document_storage_error(
    provider, monkeypatch
):
    def _unreachable(**kwargs):
        raise EndpointConnectionError(endpoint_url="https://s3.example.invalid")

    monkeypatch.setattr(provider._client, "put_object", _unreachable)
    with pytest.raises(DocumentStorageError):
        await provider.save(KEY, b"x")


@pytest.mark.asyncio
async def test_generate_presigned_url_signs_get_object_with_response_overrides(
    provider,
):
    url = await provider.generate_presigned_url(
        KEY,
        filename="notes.txt",
        mime_type="text/plain",
        disposition="attachment",
        expires_in=120,
    )

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc.startswith("s3.us-west-004.backblazeb2.com")
    assert parsed.path == f"/{BUCKET}/{KEY}"
    assert query["response-content-type"] == ["text/plain"]
    assert query["response-content-disposition"][0].startswith(
        'attachment; filename="notes.txt"'
    )
    assert query["X-Amz-Expires"] == ["120"]


@pytest.mark.asyncio
async def test_presigned_content_disposition_is_header_safe_for_odd_filenames(
    provider,
):
    url = await provider.generate_presigned_url(
        KEY,
        filename='résumé "final".pdf',
        mime_type="application/pdf",
        disposition="inline",
    )
    disposition = parse_qs(urlparse(url).query)["response-content-disposition"][0]
    assert disposition.startswith('inline; filename="r_sum_ _final_.pdf"')
    assert "filename*=UTF-8''r%C3%A9sum%C3%A9%20%22final%22.pdf" in disposition


@pytest.mark.asyncio
async def test_local_provider_cannot_generate_presigned_urls(tmp_path):
    with pytest.raises(NotImplementedError):
        await LocalFilesystemStorageProvider(tmp_path).generate_presigned_url(
            KEY, filename="a.txt", mime_type="text/plain", disposition="inline"
        )


def test_default_provider_is_b2():
    assert isinstance(get_storage_provider(), B2StorageProvider)
