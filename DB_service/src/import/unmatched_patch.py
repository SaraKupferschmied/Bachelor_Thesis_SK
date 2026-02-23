#!/usr/bin/env python3
# Enrich unmatched programmes by scraping curriculum pages and storing document links only.

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import (
    urljoin,
    urlparse,
    urlsplit,
    urlunsplit,
    quote,
)

import requests

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None


# ----------------------------
# Config
# ----------------------------

DIRECT_FILE_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".zip", ".rar", ".7z",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-CH,de;q=0.9,en;q=0.8,fr;q=0.7",
    "Connection": "keep-alive",
}

DEFAULT_TIMEOUT = 30


# ----------------------------
# Utility Functions
# ----------------------------

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_http_url(u: str) -> bool:
    try:
        p = urlparse(u)
        return p.scheme in {"http", "https"} and bool(p.netloc)
    except Exception:
        return False


def url_ext(u: str) -> str:
    try:
        return Path(urlparse(u).path).suffix.lower()
    except Exception:
        return ""


def looks_like_direct_file(u: str) -> bool:
    return url_ext(u) in DIRECT_FILE_EXTS


# ----------------------------
# URL Fixing & Encoding
# ----------------------------

def normalize_unifr_asset_url(u: str) -> str:
    """
    Fix common wrong absolute paths like:
      /sr/de/studium/assets/... -> /sr/de/assets/...
      /sr/fr/etudes/assets/...  -> /sr/fr/assets/...
    """
    if not u:
        return u

    try:
        parsed = urlparse(u)
        if "unifr.ch" not in (parsed.netloc or ""):
            return u
    except Exception:
        return u

    u = u.replace("/sr/de/studium/assets/", "/sr/de/assets/")
    u = u.replace("/sr/fr/etudes/assets/", "/sr/fr/assets/")

    return u


def url_sanitize(u: str) -> str:
    """
    Percent-encode unsafe characters (like spaces) in path and query.
    """
    parts = urlsplit(u)
    path = quote(parts.path, safe="/%")
    query = quote(parts.query, safe="=&?/%")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


# ----------------------------
# Networking
# ----------------------------

def fetch(session: requests.Session, url: str, referer: Optional[str] = None):
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer

    try:
        return session.get(
            url,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=True,
        )
    except Exception:
        return None


def is_reachable_doc(session: requests.Session, url: str, referer: Optional[str] = None) -> bool:
    headers = dict(HEADERS)
    if referer:
        headers["Referer"] = referer

    try:
        r = session.get(
            url,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=True,
            stream=True,
        )
        r.close()
        return r.status_code < 400
    except Exception:
        return False


# ----------------------------
# HTML Processing
# ----------------------------

def effective_base_url_from_html(html: str, fallback: str) -> str:
    if not BeautifulSoup:
        return fallback

    soup = BeautifulSoup(html, "html.parser")
    base_tag = soup.find("base", href=True)
    if base_tag and base_tag.get("href"):
        return urljoin(fallback, base_tag["href"].strip())

    return fallback


def extract_links_from_html(html: str, base_url: str) -> List[Tuple[str, str]]:
    links = []

    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")

        base_tag = soup.find("base", href=True)
        effective_base = (
            urljoin(base_url, base_tag["href"].strip())
            if (base_tag and base_tag.get("href"))
            else base_url
        )

        for a in soup.find_all("a"):
            href = a.get("href")
            if not href:
                continue

            abs_url = urljoin(effective_base, href.strip())
            abs_url = normalize_unifr_asset_url(abs_url)
            abs_url = url_sanitize(abs_url)

            text = a.get_text(" ", strip=True)
            links.append((abs_url, text))

        return links

    # Regex fallback
    for m in re.finditer(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        html,
        re.I | re.S,
    ):
        href = m.group(1).strip()
        abs_url = urljoin(base_url, href)
        abs_url = normalize_unifr_asset_url(abs_url)
        abs_url = url_sanitize(abs_url)

        txt = re.sub(r"<[^>]+>", " ", m.group(2)).strip()
        links.append((abs_url, txt))

    return links


# ----------------------------
# Document Extraction
# ----------------------------

def extract_document_links(
    session: requests.Session,
    url: str,
    validate: bool = True,
) -> List[Dict[str, Any]]:

    docs = []

    r = fetch(session, url)
    if not r or r.status_code >= 400:
        return docs

    content_type = (r.headers.get("Content-Type") or "").lower()

    # Direct PDF page
    if "application/pdf" in content_type or looks_like_direct_file(r.url):
        final_url = url_sanitize(normalize_unifr_asset_url(r.url))

        if not validate or is_reachable_doc(session, final_url, referer=url):
            docs.append({
                "url": final_url,
                "label": Path(urlparse(final_url).path).name,
                "source_type": "curriculum_page",
            })
        return docs

    html = r.text or ""
    eff_base = effective_base_url_from_html(html, r.url)

    for link, text in extract_links_from_html(html, eff_base):
        if not is_http_url(link):
            continue

        if looks_like_direct_file(link):
            if not validate or is_reachable_doc(session, link, referer=r.url):
                docs.append({
                    "url": link,
                    "label": text or Path(urlparse(link).path).name,
                    "source_type": "curriculum_page",
                })

        if "calameo.com/read/" in link.lower():
            if not validate or is_reachable_doc(session, link, referer=r.url):
                docs.append({
                    "url": link,
                    "label": text or "Calameo",
                    "source_type": "calameo",
                })

    return docs


# ----------------------------
# Main Logic
# ----------------------------

def iter_curriculum_urls(entry: Dict[str, Any]) -> List[str]:
    urls = []
    for k in [
        "curriculum_de_url",
        "curriculum_fr_url",
        "curriculum_en_url",
        "curriculum_unspecified_url",
    ]:
        u = entry.get(k)
        if isinstance(u, str) and u.strip():
            urls.append(u.strip())
    return urls


def dedupe_documents(existing, new_docs):
    seen = set()
    out = []
    for d in existing + new_docs:
        url = str(d.get("url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="outp", required=True)
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    data = load_json(Path(args.inp))
    if not isinstance(data, list):
        print("Input must be a list.")
        return 1

    session = requests.Session()
    validate = not args.no_validate

    processed = 0
    enriched = 0

    for entry in data:
        if entry.get("match_type") != "unmatched":
            continue

        urls = iter_curriculum_urls(entry)
        if not urls:
            continue

        processed += 1
        new_docs = []

        for u in urls:
            new_docs.extend(
                extract_document_links(session, u, validate=validate)
            )

        existing_docs = entry.get("documents") or []
        merged = dedupe_documents(existing_docs, new_docs)

        if len(merged) > len(existing_docs):
            entry["documents"] = merged
            enriched += 1

    save_json(Path(args.outp), data)

    print(f"Processed unmatched programmes: {processed}")
    print(f"Enriched: {enriched}")
    print(f"Validation: {'ON' if validate else 'OFF'}")
    print(f"Output written to: {args.outp}")


if __name__ == "__main__":
    raise SystemExit(main())