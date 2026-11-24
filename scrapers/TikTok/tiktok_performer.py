#!/usr/bin/env python3

import json
import re
import sys

try:
    import requests
    import yt_dlp
except ImportError:
    requests = None
    yt_dlp = None


def first_value(value):
    """Get the first non-empty value, handling nulls and nested arrays."""
    if isinstance(value, (list, tuple)):
        for item in value:
            result = first_value(item)
            if result:
                return result
        return None

    if isinstance(value, dict):
        for key in ("url_list", "url", "name", "value"):
            if key in value:
                result = first_value(value[key])
                if result:
                    return result
        return None

    if value is None or isinstance(value, bool):
        return None

    text = str(value).strip()
    return text or None


def profile_handle(url):
    match = re.search(r"/@([^/?#]+)", url)
    return match.group(1) if match else None


def find_avatar(data):
    """Search embedded profile JSON for an avatar URL, never using a video cover."""
    avatar_keys = (
        "avatarLarger", "avatar_larger",
        "avatarMedium", "avatar_medium",
        "avatarThumb", "avatar_thumb",
        "avatar",
    )

    def walk(node):
        if isinstance(node, dict):
            # Check likely profile-image fields before searching other values.
            for key in avatar_keys:
                if key in node:
                    candidate = first_value(node[key])
                    if candidate and candidate.startswith(("http://", "https://")):
                        return candidate

            for value in node.values():
                candidate = walk(value)
                if candidate:
                    return candidate

        elif isinstance(node, (list, tuple)):
            for item in node:
                candidate = walk(item)
                if candidate:
                    return candidate

        return None

    return walk(data)


def profile_page_metadata(url):
    """Fallback: inspect public profile-page JSON for the display name and avatar."""
    if requests is None:
        return {}

    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    html = response.text

    metadata = {}

    # TikTok may embed profile data in either of these JSON script blocks.
    script_pattern = re.compile(
        r'<script\b[^>]*\bid=["\']'
        r'(__UNIVERSAL_DATA_FOR_REHYDRATION__|SIGI_STATE)'
        r'["\'][^>]*>(.*?)</script>',
        re.IGNORECASE | re.DOTALL,
    )

    for match in script_pattern.finditer(html):
        try:
            data = json.loads(match.group(2))
        except (json.JSONDecodeError, TypeError):
            continue

        avatar = find_avatar(data)
        if avatar and not metadata.get("image"):
            metadata["image"] = avatar

        # Look for profile/user display-name fields without assuming a fixed
        # nesting structure in TikTok's embedded JSON.
        if not metadata.get("name"):
            name = find_named_value(data, ("nickname", "displayName", "uniqueId"))
            if name:
                metadata["name"] = name

    # Last-resort image fallback from profile-page metadata.
    if not metadata.get("image"):
        og_image = re.search(
            r'<meta\b[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)',
            html,
            re.IGNORECASE,
        )
        if og_image:
            metadata["image"] = og_image.group(1).strip()

    return metadata


def find_named_value(data, keys):
    """Recursively find the first non-empty matching name field."""
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                value = first_value(data[key])
                if value:
                    return value

        for value in data.values():
            result = find_named_value(value, keys)
            if result:
                return result

    elif isinstance(data, (list, tuple)):
        for item in data:
            result = find_named_value(item, keys)
            if result:
                return result

    return None


def yt_dlp_profile_metadata(url):
    """Use yt-dlp's TikTok profile extractor, limiting profile entries to one."""
    if yt_dlp is None:
        return {}

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "playlist_items": "1",
        "socket_timeout": 20,
        "retries": 1,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    if not isinstance(info, dict):
        return {}

    result = {
        "alias": first_value(info.get("uploader")),        
        "name": first_value(info.get("uploader")),
        "image": first_value(info.get("thumbnail")),
        "details": first_value(info.get("description")),
    }

    # Some extractor versions put useful account fields on the first entry.
    entries = info.get("entries")
    if isinstance(entries, (list, tuple)):
        entry = next((item for item in entries if isinstance(item, dict)), None)
    else:
        try:
            entry = next((item for item in entries if isinstance(item, dict)), None)
        except (TypeError, StopIteration):
            entry = None

    if isinstance(entry, dict):
        result["alias"] = result["alias"] or first_value(entry.get("uploader"))
        result["name"] = result["name"] or first_value(entry.get("uploader"))
      

        result["details"] = result["details"] or first_value(entry.get("description"))
        # Do not use the entry's thumbnail: it is the video cover, not the
        # performer's profile image.

    return {key: value for key, value in result.items() if value}


def scrape(url):
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Missing TikTok profile URL")

    url = url.strip()
    match = re.match(r"^https?://([^/]+)(/[^?#]*)", url, re.IGNORECASE)
    if not match:
        raise ValueError("Enter a valid TikTok profile URL")

    host = match.group(1).lower()
    path = match.group(2)

    if not (host == "tiktok.com" or host.endswith(".tiktok.com")):
        raise ValueError("Only TikTok URLs are supported")

    handle = profile_handle(url)
    if not handle or "/video/" in path.lower():
        raise ValueError("Use a TikTok profile URL, not a video URL")

    # Try yt-dlp first, then use the profile page JSON for the avatar/name.
    result = {}
    try:
        result.update(yt_dlp_profile_metadata(url))
    except Exception as exc:
        print(f"yt-dlp profile lookup failed: {exc}", file=sys.stderr)

    try:
        fallback = profile_page_metadata(url)
        for key, value in fallback.items():
            if value and not result.get(key):
                result[key] = value
    except Exception as exc:
        print(f"TikTok profile-page lookup failed: {exc}", file=sys.stderr)

    # The handle is a safe name fallback if TikTok does not provide a display name.
    performer = {
        "name": result.get("name") or handle,
        "url": url,
    }

    if result.get("image"):
        performer["image"] = result["image"]
    if result.get("details"):
        performer["details"] = result["details"]

    return performer


def main():
    try:
        request = json.loads(sys.stdin.read() or "{}")
        if not isinstance(request, dict):
            raise ValueError("Stash input must be a JSON object")

        print(json.dumps(scrape(request.get("url")), ensure_ascii=False))

    except Exception as exc:
        print(f"TikTok performer scraper error: {exc}", file=sys.stderr)
        print("{}")


if __name__ == "__main__":
    main()

