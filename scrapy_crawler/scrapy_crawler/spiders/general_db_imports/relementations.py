import html
import json
import re
from urllib.parse import unquote, urljoin, urlparse

import scrapy
from w3lib.html import remove_tags


def norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def strip_html(value: str | None) -> str:
    return norm(remove_tags(html.unescape(value or "")))


class ReglementationSpider(scrapy.Spider):
    """
    Crawl UNIFR regulations/statutes.

    Important:
    The public legal page URL uses a legal code:
        https://webapps.unifr.ch/legal/de/101.000

    But the PDF viewer/download uses an internal version/document id:
        https://webapps.unifr.ch/legal/de/load/5509668
        https://webapps.unifr.ch/legal/de/download/5509668

    Therefore we first crawl the legal page, extract the selected/current version id,
    then build the load/download URLs from that id.
    """

    name = "reglementation"

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 0.2,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "FEED_EXPORT_ENCODING": "utf-8",
        "DEFAULT_REQUEST_HEADERS": {
            "User-Agent": "Mozilla/5.0 (compatible; reglementation-scraper/3.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    }

    start_urls = [
        "https://www.unifr.ch/uni/de/rechtsetzung/",
        "https://www.unifr.ch/uni/fr/legislation/",
    ]

    LEGAL_PAGE_RE = re.compile(
        r"https?:\\?/\\?/webapps\.unifr\.ch\\?/legal\\?/(de|fr)\\?/([0-9][0-9.]+)"
    )
    LEGAL_PAGE_REL_RE = re.compile(r"/legal/(de|fr)/([0-9][0-9.]+)")
    CURRENT_LOAD_RE = re.compile(r"/legal/(de|fr)/load/(\d+)")
    CURRENT_DOWNLOAD_RE = re.compile(r"/legal/(de|fr)/download/(\d+)")
    CD_FILENAME_RE = re.compile(r"filename\*?=(?:UTF-8''|\"?)([^\";]+)\"?", re.IGNORECASE)

    def parse(self, response):
        html_text = response.text or ""
        docs = []

        scripts = response.xpath("//script/text()").getall()
        self.logger.info("Found %s <script> blocks on %s", len(scripts), response.url)

        for script in scripts:
            docs.extend(self._extract_docs_from_script(script, response))

        docs.extend(self._extract_docs_from_raw_html(html_text, response))

        unique_docs = {}
        for doc in docs:
            key = doc.get("source") or f"{doc.get('lang')}:{doc.get('legal_code')}"
            if key and key not in unique_docs:
                unique_docs[key] = doc

        self.logger.info(
            "Queued %s unique legal pages from %s",
            len(unique_docs),
            response.url,
        )

        for doc in unique_docs.values():
            yield scrapy.Request(
                doc["source"],
                callback=self.parse_legal_page,
                errback=self.legal_page_errback,
                meta={"doc": doc},
                dont_filter=True,
            )

    def parse_legal_page(self, response):
        doc = response.meta["doc"]
        lang = doc.get("lang") or self._lang_from_url(response.url)

        page_title = norm(
            " ".join(
                response.xpath(
                    "//*[@id='legal-doc--info']//p[contains(@class,'lead')]//text() | "
                    "//h1//text() | //h2//text() | //title//text()"
                ).getall()
            )
        )
        if page_title:
            doc["title"] = page_title

        doc["legal_code"] = doc.get("legal_code") or self._legal_code_from_url(response.url)
        doc["lang"] = lang
        doc["source"] = response.url

        version_id = self._extract_current_version_id(response)
        doc["version_id"] = version_id

        if version_id and lang:
            doc["pdf_load_url"] = f"https://webapps.unifr.ch/legal/{lang}/load/{version_id}"
            doc["pdf_url"] = f"https://webapps.unifr.ch/legal/{lang}/download/{version_id}"
        else:
            doc["pdf_load_url"] = None
            doc["pdf_url"] = None
            doc["notes"] = "Could not extract current version/document id"
            yield doc
            return

        # Validate with GET, not HEAD. The server returns 500 for many HEAD requests.
        yield scrapy.Request(
            doc["pdf_url"],
            method="GET",
            callback=self.parse_download_check,
            errback=self.download_errback,
            meta={"doc": doc},
            dont_filter=True,
        )

    def parse_download_check(self, response):
        doc = response.meta["doc"]

        content_type = self._header(response, b"Content-Type").lower()
        content_disposition = self._header(response, b"Content-Disposition")
        body = response.body or b""

        if response.status in {200, 206} and (
            body.startswith(b"%PDF")
            or "application/pdf" in content_type
            or "pdf" in content_type
            or "filename" in content_disposition.lower()
        ):
            filename = self._filename_from_cd(content_disposition)
            if filename:
                doc["pdf_filename"] = filename
            doc["download_status"] = "ok"
            doc["notes"] = None
        else:
            doc["download_status"] = f"unexpected_response_{response.status}"
            doc["notes"] = f"Download URL responded with content-type={content_type}"

        # Do not store body. We only want metadata + stable URLs.
        yield doc

    def download_errback(self, failure):
        doc = failure.request.meta.get("doc", {})
        doc["download_status"] = "failed"
        doc["notes"] = f"Failed to validate download URL: {failure.value}"
        yield doc

    def legal_page_errback(self, failure):
        doc = failure.request.meta.get("doc", {})
        doc["pdf_url"] = None
        doc["pdf_load_url"] = None
        doc["download_status"] = "legal_page_failed"
        doc["notes"] = f"Failed to fetch legal page: {failure.value}"
        yield doc

    def _extract_current_version_id(self, response):
        """
        Preferred source:
          <select id="legal--version_selector">
              <option value="5509668" selected>...</option>
          </select>

        Fallbacks:
          PDFObject.embed(".../load/5509668", ...)
          window.location.href download script ".../download/5509668"
        """
        selected = response.xpath(
            "//select[@id='legal--version_selector']/option[@selected]/@value"
        ).get()
        if selected:
            return norm(selected)

        first_option = response.xpath(
            "//select[@id='legal--version_selector']/option[1]/@value"
        ).get()
        if first_option:
            return norm(first_option)

        html_text = response.text or ""

        load_match = self.CURRENT_LOAD_RE.search(html_text)
        if load_match:
            return load_match.group(2)

        download_match = self.CURRENT_DOWNLOAD_RE.search(html_text)
        if download_match and download_match.group(2) != "9999":
            return download_match.group(2)

        # The actual id can also appear as fallback id = "5509668";
        fallback_id = re.search(r'id\s*=\s*[\"\'](\d{4,})[\"\']', html_text)
        if fallback_id:
            return fallback_id.group(1)

        return None

    def _extract_docs_from_script(self, script: str, response):
        docs = []

        for obj_text in self._iter_json_like_objects(script):
            try:
                obj = json.loads(obj_text)
            except Exception:
                continue

            doc = self._doc_from_href(
                obj.get("href"),
                obj.get("text"),
                obj.get("code"),
                response,
            )
            if doc:
                docs.append(doc)

        for data_json in self._extract_data_arrays(script):
            try:
                tree = json.loads(data_json)
            except Exception:
                continue
            docs.extend(self._walk_nodes(tree, [], response))

        return docs

    def _extract_docs_from_raw_html(self, html_text: str, response):
        docs = []

        for match in self.LEGAL_PAGE_RE.finditer(html_text):
            raw_url = self._unescape_js_url(match.group(0))
            doc = self._doc_from_href(raw_url, None, None, response)
            if doc:
                docs.append(doc)

        for lang, code in self.LEGAL_PAGE_REL_RE.findall(html_text):
            raw_url = f"https://webapps.unifr.ch/legal/{lang}/{code}"
            doc = self._doc_from_href(raw_url, None, None, response)
            if doc:
                docs.append(doc)

        return docs

    def _walk_nodes(self, nodes, parent_path, response):
        if not isinstance(nodes, list):
            return

        for node in nodes:
            if not isinstance(node, dict):
                continue

            href = (node.get("href") or "").strip()
            text = node.get("text") or ""
            code = node.get("code")

            clean_text = strip_html(text)
            is_category = href.startswith("#node-")
            path = parent_path

            if is_category and clean_text:
                path = parent_path + [clean_text]

            doc = self._doc_from_href(href, text, code, response, parent_path=parent_path)
            if doc:
                yield doc

            children = node.get("nodes")
            if children:
                yield from self._walk_nodes(children, path, response)

    def _doc_from_href(self, href, raw_text, code, response, parent_path=None):
        if not href:
            return None

        href = self._unescape_js_url(href)
        full = urljoin(response.url, href)

        parsed = urlparse(full)
        if parsed.netloc != "webapps.unifr.ch":
            return None

        match = re.search(r"/legal/(de|fr)/([0-9][0-9.]+)$", parsed.path)
        if not match:
            return None

        lang, legal_code = match.groups()

        return {
            "title": self._clean_title(raw_text),
            "legal_code": legal_code,
            "tree": " > ".join(parent_path or []),
            "lang": lang,
            "source": full,
            "version_id": None,
            "pdf_load_url": None,
            "pdf_url": None,
            "pdf_filename": None,
            "download_status": None,
            "notes": None,
        }

    def _clean_title(self, raw_text):
        title = strip_html(raw_text)
        if not title:
            return None
        title = re.sub(r"^[0-9][0-9.]*\s+", "", title).strip()
        return title or None

    def _iter_json_like_objects(self, script: str):
        object_re = re.compile(
            r"\{[^{}]*?\"href\"\s*:\s*\"(?:\\.|[^\"])+\"[^{}]*?"
            r"(?:\"text\"\s*:\s*\"(?:\\.|[^\"])*\")?[^{}]*?\}",
            re.DOTALL,
        )
        yield from (m.group(0) for m in object_re.finditer(script))

    def _extract_data_arrays(self, script: str):
        arrays = []
        for match in re.finditer(r"\bdata\s*:", script):
            open_bracket = script.find("[", match.end())
            if open_bracket < 0:
                continue
            data_json = self._extract_balanced_brackets(script, open_bracket)
            if data_json:
                arrays.append(data_json)
        return arrays

    def _extract_balanced_brackets(self, value: str, start_idx: int):
        if start_idx < 0 or start_idx >= len(value) or value[start_idx] != "[":
            return None

        depth = 0
        in_str = False
        esc = False

        for i in range(start_idx, len(value)):
            ch = value[i]

            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue

            if ch == '"':
                in_str = True
                continue

            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return value[start_idx:i + 1]

        return None

    def _unescape_js_url(self, value: str):
        value = html.unescape(value or "")
        value = value.replace("\\/", "/")
        value = value.replace("\\u002F", "/")
        value = value.strip().strip('"').strip("'")
        return unquote(value)

    def _lang_from_url(self, url: str):
        match = re.search(r"/legal/(de|fr)/", url)
        return match.group(1) if match else None

    def _legal_code_from_url(self, url: str):
        match = re.search(r"/legal/(?:de|fr)/([0-9][0-9.]+)", url)
        return match.group(1) if match else None

    def _header(self, response, name: bytes):
        return (response.headers.get(name) or b"").decode("utf-8", "ignore")

    def _filename_from_cd(self, content_disposition: str):
        if not content_disposition:
            return None

        match = self.CD_FILENAME_RE.search(content_disposition)
        if not match:
            return None

        return unquote(match.group(1)).strip().strip('"')
