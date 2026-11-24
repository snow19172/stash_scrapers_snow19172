#!/usr/bin/env python3

import json
import re
import sys
from datetime import datetime
from urllib.parse import urlparse

try:
    import yt_dlp
except ImportError:
    yt_dlp = None


def first_value(value):
    """Return the first useful scalar, handling nulls and nested arrays."""
    if isinstance(value, (list, tuple)):
        for item in value:
            result = first_value(item)
            if result:
                return result
        return None

    if isinstance(value, dict):
        # yt-dlp sometimes represents a value as a structured object.
        for key in ("name", "url", "url_list", "value"):
            if key in value:
                result = first_value(value[key])
                if result:
                    return result
        return None

    if value is None or isinstance(value, bool):
        return None

    text = str(value).strip()
    return text or None


def thumbnail_url(info):
    """Handle thumbnail as a URL, object, array, or missing/null value."""
    for key in ("thumbnail", "thumbnails", "cover", "origin_cover"):
        result = first_value(info.get(key))
        if result and result.startswith(("http://", "https://")):
            return result

    return None


def normalize_date(info):
    """Convert yt-dlp's YYYYMMDD or timestamp to Stash's YYYY-MM-DD."""
    upload_date = first_value(info.get("upload_date"))
    if upload_date:
        for fmt in ("%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(upload_date, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass

    timestamp = info.get("timestamp")
    if isinstance(timestamp, (int, float)):
        try:
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            pass

    return None


def validate_tiktok_url(url):
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Missing TikTok URL")

    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()

    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https")

    if not (host == "tiktok.com" or host.endswith(".tiktok.com")):
        raise ValueError("Only TikTok URLs are supported")

    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not supported")

    return url.strip()


def extract_video(url):
    if yt_dlp is None:
        raise RuntimeError(
            "yt-dlp is not installed in the Python environment Stash uses"
        )

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 1,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    # Defensively handle playlist-like results, nulls, and unexpected shapes.
    if not isinstance(info, dict):
        raise ValueError("TikTok returned no usable metadata")

    entries = info.get("entries")
    if isinstance(entries, (list, tuple)):
        info = next((entry for entry in entries if isinstance(entry, dict)), None)

    if not isinstance(info, dict):
        raise ValueError("TikTok returned no usable video entry")

    title = first_value(info.get("title"))
    description = first_value(info.get("description"))
    uploader = (
        first_value(info.get("uploader"))
        or first_value(info.get("channel"))
        or first_value(info.get("uploader_id"))
    )
    video_id = first_value(info.get("id"))

    # Use the URL's video ID only if the extractor did not provide one.
    if not video_id:
        match = re.search(r"/video/(\d+)", url)
        if match:
            video_id = match.group(1)

    scene = {
        "title": title,
        "code": video_id,
        "date": normalize_date(info),
        "details": description,
        "url": first_value(info.get("webpage_url")) or url,
        "image": thumbnail_url(info),
    }

    # Stash expects the scene's studio and performers as objects.
    if uploader:
        scene["studio"] = {"name": uploader}
        scene["performers"] = [{"name": uploader}]

    # Omit fields with no value instead of returning nulls.
    return {key: value for key, value in scene.items() if value is not None}


def main():
    try:
        request = json.loads(sys.stdin.read() or "{}")
        if not isinstance(request, dict):
            raise ValueError("Stash input must be a JSON object")

        url = validate_tiktok_url(request.get("url"))
        result = extract_video(url)
        print(json.dumps(result, ensure_ascii=False))

    except Exception as exc:
        # Stash expects JSON on stdout; send diagnostic details to stderr.
        print(f"TikTok scraper error: {exc}", file=sys.stderr)
        print("{}")


if __name__ == "__main__":
    main()
