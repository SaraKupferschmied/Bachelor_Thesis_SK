import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import scrapy

DOC_EXT_RE = re.compile(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx)\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
ECTS_RE = re.compile(r"\b(\d{1,3})\s*ECTS\b", re.IGNORECASE)


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


def extract_year(text: str | None) -> int | None:
    if not text:
        return None
    m = YEAR_RE.search(text)
    return int(m.group(0)) if m else None


def extract_ects(text: str | None) -> int | None:
    if not text:
        return None
    m = ECTS_RE.search(text)
    return int(m.group(1)) if m else None


class UnifrSciMedStudyPlansSpider(scrapy.Spider):
    """
    SCIMED:
      Start at /scimed/<lang>/plans
      Crawl each subcategory link in left menu
      On each subcategory page: pick most recent year accordion item and download docs
    """
    name = "unifr_scimed_studyplans"

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 0.8,
        "FEED_EXPORT_ENCODING": "utf-8",
        "ITEM_PIPELINES": {"scrapy.pipelines.files.FilesPipeline": 1},
        "FILES_STORE": "downloads/scimed",
        "LOG_LEVEL": "INFO",
    }

    def __init__(self, lang="de", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lang = lang

    # Scrapy 2.13+ compatibility
    async def start(self):
        for req in self.start_requests():
            yield req

    def _load_faculties(self):
        candidates = [
            Path("faculties.json"),
            Path("spider_outputs") / "faculties.json",
            Path("scrapy_crawler") / "spider_outputs" / "faculties.json"
        ]
        faculties_path = next((p for p in candidates if p.exists()), None)
        if not faculties_path:
            tried = ", ".join(str(p.resolve()) for p in candidates)
            raise FileNotFoundError(f"Could not find faculties.json. Tried: {tried}")

        self.logger.info("Reading faculties from: %s", faculties_path.resolve())
        raw = faculties_path.read_bytes()
        if not raw.strip():
            raise ValueError(f"{faculties_path.resolve()} is empty.")

        text = raw.decode("utf-8-sig")
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("faculties.json must be a JSON list (top-level array).")
        return data

    def start_requests(self):
        data = self._load_faculties()
        scimed = next(
            (x for x in data if x.get("key") == "scimed" and x.get("lang") == self.lang),
            None,
        ) or next((x for x in data if x.get("key") == "scimed"), None)

        if not scimed or not scimed.get("url_en"):
            raise ValueError("No scimed entry with a valid url found in faculties.json")

        # Go directly to Studienpläne hub (skip clicking “Ausbildung”)
        start_url = scimed["url_en"].rstrip("/") + f"/{self.lang}/plans"
        self.logger.info("Starting SCIMED crawl at: %s", start_url)
        yield scrapy.Request(start_url, callback=self.parse_plans_hub)

    def parse_plans_hub(self, response):
        """
        From /plans hub, take each submenu entry under Studienpläne.
        Based on your DOM, these links look like:
          /scimed/de/plans/bachelor
          /scimed/de/plans/prop
          /scimed/de/plans/minor (Zusatzfächer)
          /scimed/de/plans/master
          ...
        We collect all unique /plans/<something> links in the left sub-menu.
        """
        # left menu area
        menu = response.css("div.col-md-3.sub-menu")
        links = menu.css('a[href*="/plans/"]::attr(href)').getall()

        # normalize + de-duplicate
        targets = []
        for href in links:
            u = response.urljoin(href)
            if u.startswith("//"):
                u = "https:" + u
            u = safe_url(u)
            # keep only /plans/<slug> (not /plans itself)
            if re.search(r"/plans/[^/]+/?$", u):
                targets.append(u)

        targets = sorted(set(targets))
        if not targets:
            self.logger.warning("No /plans/<slug> submenu links found on %s", response.url)
            return

        for url in targets:
            yield scrapy.Request(url, callback=self.parse_category_page)

    def parse_category_page(self, response):
        """
        Each category page has an accordion with years.
        We select the most recent year (max).
        """
        category = self._category_from_url(response.url)

        # accordion items
        items = response.css("ul.accordion li")
        if not items:
            self.logger.warning("No accordion found on %s", response.url)
            return

        year_candidates = []
        for li in items:
            # year text is usually in the toggle link text (e.g., "2025")
            toggle_text = clean_text(" ".join(li.css('[data-accordion-toggler] *::text, [data-accordion-toggler]::text').getall()))
            year = extract_year(toggle_text)
            if year:
                year_candidates.append((year, li))

        if not year_candidates:
            self.logger.warning("Could not detect years in accordion on %s", response.url)
            return

        latest_year, latest_li = max(year_candidates, key=lambda x: x[0])
        self.logger.info("Category %s: using latest year %s on %s", category, latest_year, response.url)

        # parse downloads inside that latest accordion block
        yield from self._extract_downloads_from_year_block(
            response=response,
            category=category,
            year=latest_year,
            year_block=latest_li,
        )

    def _extract_downloads_from_year_block(self, response, category: str, year: int, year_block):
        """
        Inside the latest year block:
        programs are usually announced by <strong>...</strong> in a <p>,
        followed by one or more <a class="link download" ...>.
        We'll group links by their nearest preceding strong label if possible.
        """
        # Build a list of nodes in order inside the accordion content
        content = year_block.css('[data-accordion-content], div[data-accordion-content]')
        if not content:
            # fallback: just search within the li
            content = year_block

        # Iterate paragraphs in order and track last seen program header
        current_program = None

        # We walk all <p> in order
        for p in content.css("p"):
            strong_txt = clean_text(" ".join(p.css("strong *::text, strong::text").getall()))
            if strong_txt:
                current_program = strong_txt

            # collect doc links inside this <p>
            for a in p.css('a.link.download[href]'):
                href = a.attrib.get("href")
                if not href:
                    continue

                abs_url = response.urljoin(href)
                if abs_url.startswith("//"):
                    abs_url = "https:" + abs_url
                abs_url = safe_url(abs_url)

                if not is_doc_url(abs_url):
                    continue

                link_text = clean_text(" ".join(a.css("::text").getall()))
                ects = extract_ects(link_text) or extract_ects(current_program)

                # program name: prefer <strong> label, fallback to link title/text
                program = current_program or clean_text(a.attrib.get("title")) or link_text or category

                item = {
                    "faculty": "SCIMED",
                    "lang": self.lang,
                    "category": category,
                    "year": year,
                    "program": program,
                    "ects": ects,
                    "page_url": response.url,
                    "documents": [{"url": abs_url, "label": link_text}],
                    "file_urls": [abs_url],  # FilesPipeline
                }
                yield item

    def _category_from_url(self, url: str) -> str:
        m = re.search(r"/plans/([^/?#]+)", url)
        slug = m.group(1) if m else "plans"
        # pretty mapping (optional)
        pretty = {
            "bachelor": "Bachelor",
            "prop": "Propädeutika",
            "minor": "Zusatzfächer",
            "master": "Master",
            "teaching": "Unterrichtsfächer",
        }
        return pretty.get(slug, slug)
