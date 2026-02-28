import json
import re
import scrapy
from urllib.parse import urljoin, urlparse
from w3lib.html import remove_tags


class ReglementationSpider(scrapy.Spider):
    name = "reglementation"

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 0.2,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "FEEDS": {
            "spider_outputs/reglementation_docs_v2.jsonl": {
                "format": "jsonlines",
                "encoding": "utf8",
                "overwrite": True,
            }
        },
        # helps with some servers that dislike default UA
        "DEFAULT_REQUEST_HEADERS": {
            "User-Agent": "Mozilla/5.0 (compatible; reglementation-scraper/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    }

    start_urls = ["https://www.unifr.ch/uni/de/rechtsetzung/"]

    DOC_RE = re.compile(r"/legal/(?:de|fr)/document/(\d+)")
    CD_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)

    def parse(self, response):
        scripts = response.xpath("//script/text()").getall()
        self.logger.info(f"Found {len(scripts)} <script> blocks")

        target = None
        for s in scripts:
            if ("treeview" in s) and ("data" in s) and ("nodes" in s):
                target = s
                break

        if not target:
            self.logger.error("Could not find any script containing the legal tree data.")
            return

        data_pos = target.find("data")
        colon_pos = target.find(":", data_pos)
        if data_pos < 0 or colon_pos < 0:
            self.logger.error("Found candidate script, but couldn't locate `data:`.")
            return

        open_bracket = target.find("[", colon_pos)
        if open_bracket < 0:
            self.logger.error("Found `data:` but no '[' after it.")
            return

        data_json = self._extract_balanced_brackets(target, open_bracket)
        if not data_json:
            self.logger.error("Failed to bracket-extract the JSON array after `data:`.")
            return

        try:
            tree = json.loads(data_json)
        except Exception as e:
            preview = data_json[:400].replace("\n", "\\n")
            self.logger.error(f"json.loads failed: {e}. Preview: {preview}")
            return

        emitted = 0
        for doc in self._walk_nodes(tree, parent_path=[], response=response):
            emitted += 1
            # Follow the doc page so we can compute/verify the /download/<id> endpoint
            yield scrapy.Request(
                doc["source"],
                callback=self.parse_doc_page,
                meta={"doc": doc},
                dont_filter=True,
            )

        self.logger.info(f"Queued {emitted} document pages")

    def parse_doc_page(self, response):
        doc = response.meta["doc"]
        source = doc["source"]

        m = self.DOC_RE.search(source)
        if not m:
            doc["pdf_url"] = None
            doc["notes"] = "Could not extract document id from source URL"
            yield doc
            return

        doc_id = m.group(1)
        pdf_url = f"https://webapps.unifr.ch/legal/de/download/{doc_id}"
        doc["pdf_url"] = pdf_url
        doc["notes"] = None

        # Verify endpoint (HEAD first, fallback to GET with small range)
        yield scrapy.Request(
            pdf_url,
            method="HEAD",
            callback=self._verify_pdf_head,
            errback=self._verify_pdf_err,
            meta={"doc": doc, "pdf_url": pdf_url},
            dont_filter=True,
        )

    def _verify_pdf_head(self, response):
        doc = response.meta["doc"]
        ctype = (response.headers.get(b"Content-Type") or b"").decode("utf-8", "ignore").lower()
        cd = (response.headers.get(b"Content-Disposition") or b"").decode("utf-8", "ignore")

        if "application/pdf" in ctype or "pdf" in ctype:
            # nice-to-have: capture filename
            fn = self._filename_from_cd(cd)
            if fn:
                doc["pdf_filename"] = fn
            yield doc
            return

        # Some servers return generic content-type on HEAD; do a small GET check
        yield scrapy.Request(
            response.meta["pdf_url"],
            method="GET",
            headers={"Range": "bytes=0-4095"},
            callback=self._verify_pdf_get,
            errback=self._verify_pdf_err,
            meta={"doc": doc},
            dont_filter=True,
        )

    def _verify_pdf_get(self, response):
        doc = response.meta["doc"]
        body = response.body or b""
        ctype = (response.headers.get(b"Content-Type") or b"").decode("utf-8", "ignore").lower()
        cd = (response.headers.get(b"Content-Disposition") or b"").decode("utf-8", "ignore")

        if body.startswith(b"%PDF") or "application/pdf" in ctype:
            fn = self._filename_from_cd(cd)
            if fn:
                doc["pdf_filename"] = fn
            yield doc
            return

        doc["pdf_url"] = None
        doc["notes"] = f"Download endpoint did not look like PDF (status={response.status}, content-type={ctype})"
        yield doc

    def _verify_pdf_err(self, failure):
        request = failure.request
        doc = request.meta.get("doc", {})
        doc["pdf_url"] = None
        doc["notes"] = f"Failed to verify download endpoint: {failure.value}"
        yield doc

    def _filename_from_cd(self, cd: str) -> str | None:
        if not cd:
            return None
        m = self.CD_FILENAME_RE.search(cd)
        if not m:
            return None
        return m.group(1).strip().strip('"')

    def _extract_balanced_brackets(self, s: str, start_idx: int) -> str | None:
        if start_idx < 0 or start_idx >= len(s) or s[start_idx] != "[":
            return None

        depth = 0
        in_str = False
        esc = False

        for i in range(start_idx, len(s)):
            ch = s[i]

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
                    return s[start_idx : i + 1]
        return None

    def _walk_nodes(self, nodes, parent_path, response):
        if not isinstance(nodes, list):
            return

        for n in nodes:
            if not isinstance(n, dict):
                continue

            href = (n.get("href") or "").strip()
            raw_text = n.get("text") or ""
            text = remove_tags(raw_text).strip()

            is_category = href.startswith("#node-")
            this_path = parent_path
            if is_category and text:
                this_path = parent_path + [text]

            if "/legal/de/document/" in href or "/legal/fr/document/" in href:
                link = urljoin(response.url, href)

                title = text
                if title and title[0].isdigit():
                    parts = title.split(None, 1)
                    if len(parts) == 2:
                        title = parts[1].strip()

                yield {
                    "title": title,
                    "tree": " > ".join(parent_path),
                    "source": link,     # first link (document page)
                    "pdf_url": None,    # filled later
                    "notes": None,
                }
                continue

            children = n.get("nodes")
            if children:
                yield from self._walk_nodes(children, this_path, response)