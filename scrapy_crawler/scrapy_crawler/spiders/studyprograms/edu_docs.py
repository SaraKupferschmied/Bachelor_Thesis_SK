import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import scrapy

DOC_EXT_RE = re.compile(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx)\b", re.IGNORECASE)


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    path = quote(parts.path, safe="/:@-._~!$&'()*+,;=")
    query = quote(parts.query, safe="=&?/:@-._~!$&'()*+,;=")
    fragment = quote(parts.fragment, safe="-._~")
    return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))


def clean_text(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def is_doc_url(url: str) -> bool:
    return bool(DOC_EXT_RE.search(url or ""))


class UnifrEduStudyPlansSpider(scrapy.Spider):
    name = "unifr_edu_studyplans"

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 0.8,
        "FEED_EXPORT_ENCODING": "utf-8",
        "ITEM_PIPELINES": {"scrapy.pipelines.files.FilesPipeline": 1},
        "FILES_STORE": "downloads/edu",
        "LOG_LEVEL": "INFO",
    }

    def __init__(self, lang="de", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lang = lang

    # Scrapy 2.13+ (removes the deprecation warning)
    async def start(self):
        for req in self.start_requests():
            yield req

    def _load_faculties(self):
        candidates = [
            Path("faculties.json"),                     # if you run inside scrapy_crawler/
            Path("scrapy_crawler") / "faculties.json",  # if you run from repo root
        ]

        faculties_path = next((p for p in candidates if p.exists()), None)
        if not faculties_path:
            tried = ", ".join(str(p.resolve()) for p in candidates)
            raise FileNotFoundError(f"Could not find faculties.json. Tried: {tried}")

        self.logger.info("Reading faculties from: %s", faculties_path.resolve())

        raw = faculties_path.read_bytes()
        if not raw.strip():
            raise ValueError(f"{faculties_path.resolve()} is empty.")

        text = raw.decode("utf-8-sig")  # handles BOM
        data = json.loads(text)

        if not isinstance(data, list):
            raise ValueError("faculties.json must contain a JSON list (top-level array).")

        return data

    def start_requests(self):
        try:
            data = self._load_faculties()

            edu = next((x for x in data if x.get("key") == "eduform"), None)
            if not edu:
                raise ValueError("No eduform entry found in faculties.json")

            faculty_name = edu.get(f"name_{self.lang}") or edu.get("name_en") or "EDUFORM"

            base_url = edu.get(f"url_{self.lang}") or edu.get("url_en") or edu.get("url_de") or edu.get("url_fr")
            if not base_url:
                raise ValueError(f"No url_{self.lang} (or fallback url) found for eduform")

            start_url = base_url.rstrip("/") + f"/{self.lang}/studium/angebot/"

        except Exception as e:
            self.logger.warning(
                "faculties.json unusable (%s). Falling back to hardcoded eduform URL.", e
            )
            start_url = f"https://www.unifr.ch/eduform/{self.lang}/studium/angebot/"

        self.logger.info("Starting EDUFORM crawl at: %s", start_url)
        yield scrapy.Request(start_url, callback=self.parse_studienangebot_und_plaene)


    def parse_studienangebot_und_plaene(self, response):
        faculty_name = response.meta.get("faculty_name", "EDUFORM")
        boxes = response.css("div.col-sm-6.inner-10")
        self.logger.info("Found %d boxes on %s", len(boxes), response.url)

        if not boxes:
            # Helpful debugging output if something changes
            self.logger.warning("No program boxes found. Title: %s", response.css("title::text").get())
            return

        for box in boxes:
            title = clean_text(box.css("h4::text").get()) or clean_text(
                " ".join(box.css("h4 *::text").getall())
            )

            docs = []
            file_urls = []

            for a in box.css("a[href]"):
                href = a.attrib.get("href")
                if not href:
                    continue

                abs_url = response.urljoin(href)
                if abs_url.startswith("//"):
                    abs_url = "https:" + abs_url

                abs_url = safe_url(abs_url)

                if not is_doc_url(abs_url):
                    continue

                label = clean_text(" ".join(a.css("::text").getall()))
                docs.append({"url": abs_url, **({"label": label} if label else {})})
                file_urls.append(abs_url)

            if not docs:
                continue

            yield {
                "faculty": "EDUFORM",
                "lang": self.lang,
                "title": title,
                "page_url": response.url,
                "file_urls": sorted(set(file_urls)),  # FilesPipeline downloads these
                "documents": docs,
            }
