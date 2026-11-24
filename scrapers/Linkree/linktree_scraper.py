import html
import json
import re
import sys
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BYTES = 3 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (compatible; Stash-Linktree-Scraper/1.0)"


def is_linktree_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme in ("http", "https")
        and (host == "linktr.ee" or host.endswith(".linktr.ee"))
    )


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, new_url):
        if not is_linktree_url(new_url):
            raise URLError("Redirected away from linktr.ee; stopping.")
        return super().redirect_request(req, fp, code, msg, headers, new_url)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.links = []
        self._in_title = False
        self._title_parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag.lower() == "meta":
            key = (
                attrs.get("property")
                or attrs.get("name")
                or attrs.get("itemprop")
                or ""
            ).lower()
            content = attrs.get("content")
            if key and content:
                self.meta.setdefault(key, content.strip())

        elif tag.lower() == "title":
            self._in_title = True

        elif tag.lower() == "a":
            href = attrs.get("href")
            if href:
                self.links.append(href.strip())

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data.strip())

    @property
    def title(self):
        return " ".join(part for part in self._title_parts if part).strip()


def clean_name(value):
    value = html.unescape(value or "").strip()
    value = re.sub(r"\s*[|–—-]\s*Linktree\s*$", "", value, flags=re.I)
    return value.strip()


def scrape(url):
    if not is_linktree_url(url):
        raise ValueError("Please enter a linktr.ee URL.")

    request = Request(url, headers={"User-Agent": USER_AGENT})
    opener = build_opener(SafeRedirectHandler())
    with opener.open(request, timeout=20) as response:
        final_url = response.geturl()
        if not is_linktree_url(final_url):
            raise ValueError("The final page is not on linktr.ee.")
        raw = response.read(MAX_BYTES + 1)

    if len(raw) > MAX_BYTES:
        raise ValueError("Page exceeded the 3 MB size limit.")

    page = raw.decode("utf-8", errors="replace")
    parser = PageParser()
    parser.feed(page)

    name = clean_name(
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or parser.title
    )

    # If the page has no usable title, use its Linktree username.
    if not name:
        name = urlparse(final_url).path.strip("/").split("/")[0]

    bio = (
        parser.meta.get("og:description")
        or parser.meta.get("description")
        or parser.meta.get("twitter:description")
        or ""
    ).strip()

    urls = [final_url]
    for href in parser.links:
        absolute = urljoin(final_url, html.unescape(href))
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue
        if is_linktree_url(absolute):
            continue
        if absolute not in urls:
            urls.append(absolute)
        if len(urls) >= 101:
            break

    result = {"name": name, "urls": urls}
    if bio:
        result["details"] = bio

    return result


def main():
    try:
        payload = json.load(sys.stdin)
        result = scrape(payload.get("url", ""))
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(f"Linktree scraper: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

