"""
Unit tests for the shared upload helpers.

These run against the functions directly rather than through a router, because
both routers that use them are tested separately and what matters here is the
behaviour they both rely on: which extension a file gets, which types are
refused, and that a body past the ceiling leaves nothing behind.

Coroutines are driven through asyncio.run(), the same style as the WebSocket
tests, so no pytest-asyncio marker is needed.
"""
import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from app.media import upload


def _upload_file(filename, content_type=None, content=b"") -> UploadFile:
    """Build an UploadFile the way Starlette hands one to a route."""
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers={"content-type": content_type} if content_type else None,
    )


# ─── resolve_extension ────────────────────────────────────────────────────────


def test_extension_comes_from_the_filename():
    assert upload.resolve_extension(_upload_file("photo.PNG")) == ".png"


def test_extension_falls_back_to_the_content_type():
    file = _upload_file("noextension", content_type="image/png")
    assert upload.resolve_extension(file) == ".png"


def test_filename_wins_over_the_content_type():
    """
    A .jpeg must stay a .jpeg.

    mimetypes maps image/jpeg to .jpg, so deriving from the content type alone
    would rewrite the extension and break every URL already issued for the file.
    """
    file = _upload_file("holiday.jpeg", content_type="image/jpeg")
    assert upload.resolve_extension(file) == ".jpeg"


def test_missing_filename_and_type_yields_empty_extension():
    assert upload.resolve_extension(_upload_file(None)) == ""


def test_unknown_content_type_yields_empty_extension():
    file = _upload_file("blob", content_type="application/x-not-a-real-type")
    assert upload.resolve_extension(file) == ""


def test_multi_dot_filename_takes_only_the_last_suffix():
    assert upload.resolve_extension(_upload_file("archive.tar.gz")) == ".gz"


def test_a_path_in_the_filename_does_not_escape_the_suffix():
    """A traversal attempt still yields nothing but an extension."""
    assert upload.resolve_extension(_upload_file("../../etc/passwd.png")) == ".png"


# ─── assert_type_permitted ────────────────────────────────────────────────────


@pytest.mark.parametrize("ext", [".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp"])
def test_image_extensions_are_accepted(ext):
    upload.assert_type_permitted(ext, "image")  # must not raise


@pytest.mark.parametrize("ext", [".svg", ".html", ".htm", ".exe", ".mp4", ".pdf", ""])
def test_non_image_extensions_are_refused_for_images(ext):
    with pytest.raises(HTTPException) as exc:
        upload.assert_type_permitted(ext, "image")
    assert exc.value.status_code == 415


def test_svg_is_refused_everywhere():
    """An SVG is a document that can carry script; no media category takes one."""
    for category in ("image", "video", "audio", "pdf"):
        with pytest.raises(HTTPException):
            upload.assert_type_permitted(".svg", category)


def test_categories_without_a_list_accept_anything():
    """'file' and 'drive' hold arbitrary attachments by design."""
    upload.assert_type_permitted(".exe", "file")
    upload.assert_type_permitted(".exe", "drive")
    upload.assert_type_permitted("", "file")


def test_cover_category_is_the_image_list():
    assert upload.COVER_CATEGORY in upload.CATEGORY_EXTENSIONS
    upload.assert_type_permitted(".png", upload.COVER_CATEGORY)
    with pytest.raises(HTTPException):
        upload.assert_type_permitted(".svg", upload.COVER_CATEGORY)


# ─── write_stream ─────────────────────────────────────────────────────────────
#
# The ceiling lives here. stream_to_disk is the multipart adapter on top, and
# the WOPI save path feeds it a raw request body, so both share these limits.


async def _chunks(*pieces):
    for piece in pieces:
        yield piece


def test_write_stream_writes_every_chunk(tmp_path):
    async def _run():
        dest = tmp_path / "joined.bin"
        size = await upload.write_stream(_chunks(b"one", b"two", b"three"), dest)
        assert size == 11
        assert dest.read_bytes() == b"onetwothree"
    asyncio.run(_run())


def test_write_stream_tolerates_empty_chunks(tmp_path):
    """A request body stream can yield an empty final chunk."""
    async def _run():
        dest = tmp_path / "trailing.bin"
        size = await upload.write_stream(_chunks(b"data", b""), dest)
        assert size == 4
        assert dest.read_bytes() == b"data"
    asyncio.run(_run())


def test_write_stream_with_no_chunks_creates_an_empty_file(tmp_path):
    async def _run():
        dest = tmp_path / "nothing.bin"
        assert await upload.write_stream(_chunks(), dest) == 0
        assert dest.exists()
    asyncio.run(_run())


def test_write_stream_stops_at_the_ceiling(tmp_path):
    async def _run():
        dest = tmp_path / "over.bin"
        with pytest.raises(HTTPException) as exc:
            await upload.write_stream(
                _chunks(b"x" * 8, b"x" * 8), dest, max_bytes=10
            )
        assert exc.value.status_code == 413
        assert not dest.exists()
    asyncio.run(_run())


def test_write_stream_ceiling_is_read_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 4)

    async def _run():
        dest = tmp_path / "patched.bin"
        with pytest.raises(HTTPException) as exc:
            await upload.write_stream(_chunks(b"x" * 32), dest)
        assert exc.value.status_code == 413
    asyncio.run(_run())


def test_write_stream_refusal_message_matches_the_shared_constant(tmp_path):
    """The WOPI path used to carry its own copy of this string."""
    async def _run():
        dest = tmp_path / "msg.bin"
        with pytest.raises(HTTPException) as exc:
            await upload.write_stream(_chunks(b"x" * 32), dest, max_bytes=1)
        assert exc.value.detail == upload.TOO_LARGE
    asyncio.run(_run())


# ─── stream_to_disk ───────────────────────────────────────────────────────────


def test_writes_the_body_and_returns_its_size(tmp_path):
    async def _run():
        dest = tmp_path / "out.bin"
        size = await upload.stream_to_disk(_upload_file("a.bin", content=b"hello"), dest)
        assert size == 5
        assert dest.read_bytes() == b"hello"
    asyncio.run(_run())


def test_writes_a_body_larger_than_one_chunk(tmp_path):
    async def _run():
        payload = b"x" * (upload.UPLOAD_CHUNK_BYTES * 2 + 17)
        dest = tmp_path / "big.bin"
        size = await upload.stream_to_disk(_upload_file("b.bin", content=payload), dest)
        assert size == len(payload)
        assert dest.stat().st_size == len(payload)
    asyncio.run(_run())


def test_an_empty_body_is_written_as_an_empty_file(tmp_path):
    async def _run():
        dest = tmp_path / "empty.bin"
        assert await upload.stream_to_disk(_upload_file("c.bin", content=b""), dest) == 0
        assert dest.exists()
    asyncio.run(_run())


def test_body_over_the_ceiling_raises_413(tmp_path):
    async def _run():
        dest = tmp_path / "too-big.bin"
        with pytest.raises(HTTPException) as exc:
            await upload.stream_to_disk(
                _upload_file("d.bin", content=b"x" * 100), dest, max_bytes=10
            )
        assert exc.value.status_code == 413
    asyncio.run(_run())


def test_refused_body_leaves_no_partial_file(tmp_path):
    """The write has already put bytes on disk by the time the limit is crossed."""
    async def _run():
        dest = tmp_path / "partial.bin"
        with pytest.raises(HTTPException):
            await upload.stream_to_disk(
                _upload_file("e.bin", content=b"x" * 100), dest, max_bytes=10
            )
        assert not dest.exists()
    asyncio.run(_run())


def test_a_body_exactly_at_the_ceiling_is_accepted(tmp_path):
    async def _run():
        dest = tmp_path / "exact.bin"
        size = await upload.stream_to_disk(
            _upload_file("f.bin", content=b"x" * 10), dest, max_bytes=10
        )
        assert size == 10
    asyncio.run(_run())


def test_the_ceiling_is_read_at_call_time(tmp_path, monkeypatch):
    """
    Patching the module attribute has to change the limit.

    Binding MAX_UPLOAD_BYTES as a default argument value would freeze it at
    import, and every test that lowers the ceiling would silently pass against
    the real one.
    """
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 8)

    async def _run():
        dest = tmp_path / "ceiling.bin"
        with pytest.raises(HTTPException) as exc:
            await upload.stream_to_disk(_upload_file("g.bin", content=b"x" * 64), dest)
        assert exc.value.status_code == 413
    asyncio.run(_run())


def test_an_explicit_max_bytes_overrides_the_module_default(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 1)

    async def _run():
        dest = tmp_path / "override.bin"
        assert await upload.stream_to_disk(
            _upload_file("h.bin", content=b"x" * 32), dest, max_bytes=64
        ) == 32
    asyncio.run(_run())


# ─── staging_path ─────────────────────────────────────────────────────────────


def test_staging_path_is_inside_the_given_directory(tmp_path):
    assert upload.staging_path(tmp_path).parent == tmp_path


def test_staging_paths_do_not_collide(tmp_path):
    paths = {upload.staging_path(tmp_path) for _ in range(50)}
    assert len(paths) == 50


def test_staging_path_is_not_matched_by_a_stem_glob(tmp_path):
    """
    The cover cleanup globs on the block id. A partial file it could match
    would be a file it could delete out from under a concurrent upload.
    """
    block_id = "11111111-2222-3333-4444-555555555555"
    staging = upload.staging_path(tmp_path)
    staging.write_bytes(b"partial")
    assert list(tmp_path.glob(f"{block_id}.*")) == []
