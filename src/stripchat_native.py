"""
stripchat_native.py — browserless Stripchat HLS recording.

Stripchat protects its doppiocdn playlists with "MOUFLON": the master
playlist at edge-hls.doppiocdn.com lists accepted key-ids as
`#EXT-X-MOUFLON:PSCH:v2:<keyId>` lines, and the real media playlist (fetched
with `?psch=v2&pkey=<keyId>` appended to a variant URL) carries each true
segment URL inside a `#EXT-X-MOUFLON:URI:` tag, with a dummy line after it
for players that lack the key.

This module resolves a model to a single pinned-quality variant URL and
rewrites its media playlist into clean HLS, so plain `ffmpeg -c copy` can
record it — same lightweight path as Chaturbate, no headless Chromium, and
a fixed resolution (no adaptive-bitrate quality drift).

Key table + algorithm are public knowledge from kesamom/stripchat_mouflon
and lossless1024/StreaMonitor. When Stripchat rotates keys, none of the
master's PSCH ids will match and resolve() returns None — the caller then
falls back to the Playwright recorder.
"""

import base64
import hashlib
import itertools
import json
import os
import re
from typing import Optional

import requests

# keyId → decryption key. Override/extend via stripchat_mouflon_keys.json
# placed next to this file (no code change needed when keys rotate).
MOUFLON_KEYS = {
    "Zokee2OhPh9kugh4": "Quean4cai9boJa5a",
    "Zeechoej4aleeshi": "ubahjae7goPoodi6",
    "Ook7quaiNgiyuhai": "EQueeGh2kaewa3ch",
    "Fq6m2TO2ZeBkRPm9": "xb6di1NF9EFXHUwb",
    "GrRncsoByZmsiT6L": "NigHYyOD9l4rvAEb",
    "1Dzcc6OjP73LKbtI": "Y64UVwX5RrIWnOLp",
    "N2oLovTIXb0o28Uj": "ABE7Sj8jh3oPM2ae",
    "NTK9aqcLmNFMWrpQ": "tOcYOap4Ty1l9Jzb",
}

_KEYS_FILE = os.path.join(os.path.dirname(__file__), "stripchat_mouflon_keys.json")
try:
    if os.path.exists(_KEYS_FILE):
        with open(_KEYS_FILE, encoding="utf-8") as _f:
            MOUFLON_KEYS.update(json.load(_f))
except Exception:
    pass

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Referer": "https://stripchat.com/"}
_session = requests.Session()

_DUMMY = "media.mp4"


def _decode(b64: str, key: str) -> str:
    """MOUFLON cipher: base64 → XOR with cyclic SHA256(key)."""
    h = hashlib.sha256(key.encode("utf-8")).digest()
    data = base64.b64decode(b64 + "==")
    return bytes(a ^ b for a, b in zip(data, itertools.cycle(h))).decode(
        "utf-8", "replace"
    )


def _mouflon_key(text: str):
    """Return (psch, pdkey) for the first PSCH line whose key we know."""
    for line in text.splitlines():
        if line.startswith("#EXT-X-MOUFLON:PSCH:"):
            parts = line.strip().split(":")
            if len(parts) >= 4 and parts[3] in MOUFLON_KEYS:
                return parts[2], MOUFLON_KEYS[parts[3]]
    return None, None


def rewrite_playlist(text: str) -> str:
    """Decrypt MOUFLON segment URLs and strip MOUFLON tags → clean HLS.

    v2: the `#EXT-X-MOUFLON:URI:` value is a near-real URL whose 2nd-to-last
    `_`-delimited token is the segment name reversed + XOR-encrypted; decrypt
    it, then the resulting relative path replaces the dummy `media.mp4` line
    that follows. v1: the `#EXT-X-MOUFLON:FILE:` value decodes to the filename.
    (Algorithm per lossless1024/StreaMonitor.)
    """
    psch, pdkey = _mouflon_key(text)
    if not pdkey:
        return text  # unknown key → caller falls back to Playwright
    attr = "#EXT-X-MOUFLON:URI:" if psch == "v2" else "#EXT-X-MOUFLON:FILE:"

    out = []
    last = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(attr):
            val = s[len(attr):]
            try:
                if psch == "v2":
                    enc = val.split("_")[-2]
                    dec = _decode(enc[::-1], pdkey)
                    last = val.replace(enc, dec).split("/", 4)[4]
                else:
                    last = _decode(val, pdkey)
            except Exception:
                last = None
        elif s.startswith("#EXT-X-MOUFLON"):
            continue  # PSCH / ADVERT markers — drop
        elif last and s.endswith(_DUMMY):
            out.append(line.replace(_DUMMY, last))
            last = None
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def _get_stream_id(model_name: str) -> Optional[str]:
    """Numeric stream id for a model via the cam JSON endpoint, or None if the
    cam isn't available. Publicness (vs group/private/advert) is validated
    later in resolve() against the actual playlist."""
    try:
        r = _session.get(
            f"https://stripchat.com/api/front/v2/models/username/{model_name}/cam",
            headers=_HEADERS, timeout=20,
        )
        if r.status_code != 200:
            return None
        cam = r.json().get("cam", {})
    except (requests.RequestException, ValueError):
        return None
    if not cam.get("isCamAvailable"):
        return None
    sid = cam.get("streamName")
    return str(sid) if sid else None


def _pick_variant(master: str) -> Optional[str]:
    """Highest-BANDWIDTH variant URL from the master playlist."""
    best_url, best_bw = None, -1
    lines = master.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            mbw = re.search(r"BANDWIDTH=(\d+)", line)
            bw = int(mbw.group(1)) if mbw else 0
            url = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if url and not url.startswith("#") and bw > best_bw:
                best_url, best_bw = url, bw
    return best_url


def resolve(model_name: str) -> Optional[str]:
    """Return a keyed, single-quality Stripchat variant URL ready for the relay,
    or None if the model isn't publicly live / no known key matches / only an
    advert loop is being served (caller should fall back to Playwright)."""
    stream_id = _get_stream_id(model_name)
    if not stream_id:
        return None
    master_url = (
        f"https://edge-hls.doppiocdn.com/hls/{stream_id}/master/{stream_id}_auto.m3u8"
    )
    try:
        master = _session.get(master_url, headers=_HEADERS, timeout=20).text
    except requests.RequestException:
        return None

    key_ids = re.findall(r"#EXT-X-MOUFLON:PSCH:v2:(\S+)", master)
    key_id = next((k for k in key_ids if k in MOUFLON_KEYS), None)
    if not key_id:
        return None  # keys rotated — fall back to browser method

    variant = _pick_variant(master)
    if not variant:
        return None
    sep = "&" if "?" in variant else "?"
    keyed = f"{variant}{sep}psch=v2&pkey={key_id}"

    # Validate it's a real public stream, not an advert placeholder loop.
    try:
        media = _session.get(keyed, headers=_HEADERS, timeout=20).text
    except requests.RequestException:
        return None
    if "MOUFLON-ADVERT" in media or "/cpa/" in media:
        return None
    if "#EXT-X-MOUFLON:" not in media and "#EXTINF" not in media:
        return None
    return keyed
