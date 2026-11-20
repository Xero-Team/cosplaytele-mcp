import base64

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from cosplaytele_mcp import video
from cosplaytele_mcp.http import AssetTooLargeError, Http, OutboundUrlError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _encrypt(plaintext: str, key: str) -> str:
    raw_key = key.encode()
    iv = bytes(range(16))
    pad = 16 - (len(plaintext) % 16)
    padded = (plaintext + chr(pad) * pad).encode()
    encryptor = Cipher(algorithms.AES(raw_key), modes.CBC(iv)).encryptor()
    blob = iv + encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(blob).decode()


def test_decrypt_video_url_roundtrip() -> None:
    key = "0f89f1e917284cd2a90fa1a13e196729"
    url = "https://cossora.stream/api-embed/abc/index.m3u8?token=x"
    assert video.decrypt_video_url(_encrypt(url, key), key) == url


def test_decrypt_video_url_rejects_off_host() -> None:
    key = "0f89f1e917284cd2a90fa1a13e196729"
    with pytest.raises(video.VideoError, match="not on the video host"):
        video.decrypt_video_url(_encrypt("https://evil.example/x.m3u8", key), key)


def test_decrypt_video_url_rejects_bad_key() -> None:
    # A non-16/24/32-byte key must be rejected before Cipher() is constructed.
    blob = base64.b64encode(bytes(range(16)) + b"x" * 16).decode()
    with pytest.raises(video.VideoError, match="key length"):
        video.decrypt_video_url(blob, "short-key")


def test_cossora_embed_urls_are_unique_and_validated() -> None:
    html = (
        '<iframe src="https://cossora.stream/embed/423af01b-3fa5-4ae9-8154-34ebd6cda6c1">'
        '<iframe src="https://cossora.stream/embed/423af01b-3fa5-4ae9-8154-34ebd6cda6c1">'
        '<iframe src="https://evil.example/embed/x">'
    )
    assert video.cossora_embed_urls(html) == [
        "https://cossora.stream/embed/423af01b-3fa5-4ae9-8154-34ebd6cda6c1"
    ]


def test_parse_master_playlist_picks_bandwidth_and_ignores_off_host() -> None:
    text = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=1080x1900\n"
        "https://cossora.stream/api-embed/a/master_1080.m3u8?token=x\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=8021122,RESOLUTION=1900p\n"
        "https://cossora.stream/api-embed/a/master_1900.m3u8?token=x\n"
        "https://evil.example/steal.m3u8\n"
    )
    playlist = video.parse_playlist(text, "https://cossora.stream/api-embed/a/index.m3u8")
    assert playlist.is_master is True
    assert max(playlist.entries)[1].endswith("master_1900.m3u8?token=x")
    assert all(video.video_allowed(url) for _, url in playlist.entries)


def test_parse_media_playlist_resolves_relative_segments() -> None:
    text = (
        "#EXTM3U\n"
        "#EXTINF:15.0,\n"
        "seg_000.ts\n"
        "#EXTINF:15.0,\n"
        "https://hls-cdn.cossora.stream/a/seg_001.ts\n"
    )
    playlist = video.parse_playlist(text, "https://cossora.stream/api-embed/a/media.m3u8")
    assert playlist.is_master is False
    assert [url for _, url in playlist.entries] == [
        "https://cossora.stream/api-embed/a/seg_000.ts",
        "https://hls-cdn.cossora.stream/a/seg_001.ts",
    ]


def test_video_allowed_restricts_hosts() -> None:
    assert video.video_allowed("https://cossora.stream/x")
    assert video.video_allowed("https://hls-cdn.cossora.stream/seg.ts")
    assert not video.video_allowed("https://evil.example/x")
    assert not video.video_allowed("https://cossora.stream.evil.example/x")


@pytest.mark.anyio
async def test_get_binary_enforces_cap_before_buffering() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"x" * 100)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        http = Http(client)
        with pytest.raises(AssetTooLargeError) as exc:
            await http.get_binary("https://example.com/img.jpg", max_bytes=10)
        assert exc.value.size >= 100
        assert exc.value.limit == 10


@pytest.mark.anyio
async def test_get_binary_rejects_off_allowlist_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        http = Http(client, allowed_hosts=("https://example.com",))
        with pytest.raises(OutboundUrlError):
            await http.get_binary("https://example.com/img.jpg", max_bytes=1000)


@pytest.mark.anyio
async def test_get_binary_returns_content_and_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "video/mp2t"}, content=b"G@")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content, content_type = await Http(client).get_binary(
            "https://example.com/seg.ts", max_bytes=1000
        )
    assert content == b"G@"
    assert content_type == "video/mp2t"
