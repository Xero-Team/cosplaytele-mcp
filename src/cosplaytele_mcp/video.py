"""Resolve and download the HLS video streams embedded in CosplayTele posts.

CosplayTele embeds ``cossora.stream/embed/<uuid>``. That page hides the real
``index.m3u8`` URL behind AES-256-CBC (IV = first 16 bytes of the blob, key is
a 32-byte string in the page). The resulting playlist is unencrypted HLS whose
segments live on ``hls-cdn.cossora.stream``. The stream token is short-lived
and IP-bound, so resolution and fetching must happen server-side.
"""

from __future__ import annotations

import asyncio
import base64
import re
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

if TYPE_CHECKING:
    from pathlib import Path

    from cosplaytele_mcp.http import Http

# Only these hosts may be contacted while resolving a stream. Segment URLs are
# read from an untrusted playlist, so every hop is re-validated against this.
VIDEO_HOST = "cossora.stream"
VIDEO_ALLOWED_HOSTS = (f"https://{VIDEO_HOST}",)
EMBED_RE = re.compile(r"https://cossora\.stream/embed/[0-9a-fA-F-]{36}")
VIDEO_URL_RE = re.compile(r"const\s+videoURL\s*=\s*'([^']+)'")
KEY_RE = re.compile(r"decryptLink\(\s*videoURL\s*,\s*'([^']+)'\s*\)")
MASTER_MARKER = "#EXT-X-STREAM-INF"
KEY_MARKER = "#EXT-X-KEY"
SEGMENT_MAX_BYTES = 64 * 1024 * 1024
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024


class VideoError(Exception):
    """A recoverable failure while resolving or downloading a video stream."""


@dataclass
class VideoResult:
    file_path: str
    file_size: int
    mime_type: str
    segment_count: int


def video_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == VIDEO_HOST or host.endswith(f".{VIDEO_HOST}")


def cossora_embed_urls(html: str) -> list[str]:
    """Extract unique, validated cossora embed URLs from post HTML."""
    return list(dict.fromkeys(EMBED_RE.findall(html)))


def extract_encrypted_link(embed_html: str) -> tuple[str, str] | None:
    match = VIDEO_URL_RE.search(embed_html)
    key = KEY_RE.search(embed_html)
    if not match or not key:
        return None
    return match.group(1), key.group(1)


def decrypt_video_url(blob: str, key: str) -> str:
    """Reverse CryptoJS AES-256-CBC: key = utf8(key), IV = blob[:16], PKCS7."""
    raw_key = key.encode("utf-8")
    if len(raw_key) not in (16, 24, 32):
        raise VideoError(f"unexpected video cipher key length {len(raw_key)}")
    try:
        raw = base64.b64decode(blob)
    except ValueError as exc:
        raise VideoError("video stream URL is not valid base64") from exc
    if len(raw) <= 16:
        raise VideoError("video stream ciphertext is too short")
    iv, ciphertext = raw[:16], raw[16:]
    decryptor = Cipher(algorithms.AES(raw_key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    pad = padded[-1]
    if 1 <= pad <= 16 and padded[-pad:] == bytes([pad]) * pad:
        padded = padded[:-pad]
    try:
        text = padded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VideoError("video stream URL did not decrypt to text") from exc
    if not video_allowed(text):
        raise VideoError("decrypted video stream URL is not on the video host")
    return text


@dataclass
class Playlist:
    is_master: bool
    # (bandwidth, absolute url) for master playlists; (0, url) for media playlists.
    entries: list[tuple[int, str]]


def parse_playlist(text: str, base_url: str) -> Playlist:
    is_master = MASTER_MARKER in text
    bandwidth = 0
    entries: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF"):
            match = re.search(r"BANDWIDTH=(\d+)", line)
            bandwidth = int(match.group(1)) if match else 0
            continue
        if line.startswith("#"):
            continue
        absolute = urljoin(base_url, line)
        if not video_allowed(absolute):
            continue
        entries.append((bandwidth if is_master else 0, absolute))
        bandwidth = 0
    return Playlist(is_master=is_master, entries=entries)


async def resolve_embed(http: Http, embed_url: str, headers: dict[str, str]) -> str:
    if not video_allowed(embed_url):
        raise VideoError("video embed URL is not on the video host")
    response = await http.get(embed_url, headers=headers or None, cache=False)
    link = extract_encrypted_link(response.text)
    if link is None:
        raise VideoError("video embed page has no encrypted stream URL")
    return decrypt_video_url(*link)


async def resolve_segments(http: Http, m3u8_url: str) -> list[str]:
    """Resolve a master playlist to its media segments, re-validating each URL."""
    if not video_allowed(m3u8_url):
        raise VideoError("playlist URL is not on the video host")
    text = (await http.get(m3u8_url, cache=False)).text
    playlist = parse_playlist(text, m3u8_url)
    if playlist.is_master:
        if not playlist.entries:
            raise VideoError("master playlist has no variants")
        variant = max(playlist.entries)[1]
        if not video_allowed(variant):
            raise VideoError("variant playlist URL is not on the video host")
        text = (await http.get(variant, cache=False)).text
        playlist = parse_playlist(text, variant)
    if KEY_MARKER in text:
        raise VideoError("encrypted HLS streams are not supported")
    if not playlist.entries:
        raise VideoError("media playlist has no segments")
    segments = [url for _, url in playlist.entries]
    if not all(video_allowed(url) for url in segments):
        raise VideoError("a segment URL is not on the video host")
    return segments


async def download_segments(http: Http, segments: list[str], target: Path) -> int:
    """Stream segments into one MPEG-TS file; never hold the whole video in RAM."""
    total = 0
    with target.open("wb") as stream:
        for url in segments:
            content, _ = await http.get_binary(url, max_bytes=SEGMENT_MAX_BYTES)
            total += len(content)
            if total > MAX_VIDEO_BYTES:
                raise VideoError(f"video exceeds the {MAX_VIDEO_BYTES} byte download limit")
            stream.write(content)
    return total


async def remux_to_mp4(source: Path, target: Path) -> bool:
    """Remux TS to MP4 when ffmpeg is available; return whether it succeeded."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(target),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    ok = await asyncio.to_thread(lambda: target.exists() and target.stat().st_size > 0)
    if process.returncode != 0 or not ok:
        raise VideoError(f"ffmpeg remux failed: {stderr.decode(errors='replace')[:200]}")
    return True


def safe_filename(slug: str, index: int) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", slug).strip("-")[:80] or "video"
    return f"{stem}-{index}.mp4"


async def download_stream(
    http: Http,
    segments: list[str],
    output_dir: Path,
    filename: str,
) -> VideoResult:
    """Download HLS segments to a local file and remux to MP4 when possible."""
    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    ts_path = output_dir / f"{filename}.ts"
    mp4_path = output_dir / filename
    try:
        await download_segments(http, segments, ts_path)
        if await remux_to_mp4(ts_path, mp4_path):
            ts_path.unlink(missing_ok=True)
            path, mime = mp4_path, "video/mp4"
        else:
            path, mime = ts_path, "video/mp2t"
        return VideoResult(
            file_path=str(path),
            file_size=path.stat().st_size,
            mime_type=mime,
            segment_count=len(segments),
        )
    except BaseException:
        ts_path.unlink(missing_ok=True)
        mp4_path.unlink(missing_ok=True)
        raise
