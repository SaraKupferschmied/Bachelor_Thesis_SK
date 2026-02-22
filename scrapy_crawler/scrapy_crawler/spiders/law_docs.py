import re
from urllib.parse import urlsplit, urlunsplit, quote
import scrapy
from pathlib import Path
import json


# -----------------------------
# Helpers
# -----------------------------
DOC_EXT_RE = re.compile(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx)\b", re.IGNORECASE)
ECTS_RE = re.compile(r"(\d{2,3})\s*ECTS", re.IGNORECASE)


def clean_text(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def is_doc_href(href: str) -> bool:
    return bool(DOC_EXT_RE.search(href or ""))


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%:@")
    query = quote(parts.query, safe="=&%:@/?")
    fragment = quote(parts.fragment, safe="")
    return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))


def abs_href(response, href: str) -> str:
    return safe_url(response.urljoin(href))


def find_alt_lang_urls(response) -> dict:
    out = {}
    for link in response.css('link[rel="alternate"][hreflang]'):
        lang = (link.attrib.get("hreflang") or "").strip().lower()
        href = link.attrib.get("href")
        if not href:
            continue
        if href.startswith("//"):
            href = "https:" + href
        out[lang] = safe_url(href)
    return out


def lower_norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def extract_ects_from_page(response) -> int | None:
    text = " ".join(response.css("main#main *::text").getall())
    nums = [int(m.group(1)) for m in ECTS_RE.finditer(text)]
    nums = [n for n in nums if 30 <= n <= 300]
    return max(nums) if nums else None


# -----------------------------
# Spider: LAW (IUS)
# -----------------------------
class UnifrIusStudyPlansSpider(scrapy.Spider):
    name = "unifr_ius_studyplans"

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1,
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    HUBS = {
        "bachelor": "https://www.unifr.ch/ius/de/studium/ba/",
        "master": "https://www.unifr.ch/ius/de/studium/ma/",
    }

    def __init__(self, lang="de", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lang = (lang or "de").strip().lower()

    # Scrapy 2.13+ compatibility (optional but nice to match your SCIMED pattern)
    async def start(self):
        for req in self.start_requests():
            yield req

    def _load_faculties(self):
        candidates = [
            Path("faculties.json"),
            Path("spider_outputs") / "faculties.json",
            Path("scrapy_crawler") / "spider_outputs" / "faculties.json",
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

        ius = next(
            (x for x in data if x.get("key") == "ius" and x.get("lang") == self.lang),
            None,
        ) or next((x for x in data if x.get("key") == "ius"), None)

        if not ius:
            raise ValueError("No ius entry found in faculties.json")

        # pick the url field matching lang; fall back to url_en
        lang_key = f"url_{self.lang}"
        base = (ius.get(lang_key) or ius.get("url_en") or "").strip()
        if not base:
            raise ValueError("IUS entry has no usable url_* field in faculties.json")

        # ✅ LAW pages are language-scoped like /ius/de/
        start_url = base.rstrip("/") + f"/{self.lang}/"
        start_url = safe_url(start_url)

        self.logger.info("Starting IUS crawl at: %s", start_url)
        yield scrapy.Request(start_url, callback=self.parse)

    def parse(self, response):
        studium_href = response.css('nav.push-menu a.deeper[href*="/ius/de/studium/"]::attr(href)').get()
        if not studium_href:
            studium_href = response.css('a[href*="/ius/de/studium/"]::attr(href)').get()

        if not studium_href:
            self.logger.warning("Could not find Studium link, falling back to BA/MA hubs only")
            yield scrapy.Request(self.HUBS["bachelor"], callback=self.parse_hub, meta={"category": "bachelor"})
            yield scrapy.Request(self.HUBS["master"], callback=self.parse_hub, meta={"category": "master"})
            return

        yield response.follow(studium_href, callback=self.parse_studium)

    def parse_studium(self, response):
        """
        From Studium page:
        - schedule BA hub
        - schedule MA hub
        - find Nebenfach page link robustly (by anchor text, not URL prefix)
        """
        yield scrapy.Request(self.HUBS["bachelor"], callback=self.parse_hub, meta={"category": "bachelor"})
        yield scrapy.Request(self.HUBS["master"], callback=self.parse_hub, meta={"category": "master"})

        # --- Robust Nebenfach discovery ---
        candidates = []

        # Look in the left menu first, but fall back to ALL links on the page
        link_selectors = [
            "div.sub-menu a::attr(href)",
            "div.sub-menu a.deeper::attr(href)",
            "a[href]::attr(href)",
        ]

        # We need text too, so iterate anchors rather than href attrs
        anchors = response.css("div.sub-menu a, a[href]")
        for a in anchors:
            href = a.attrib.get("href")
            if not href:
                continue

            text = clean_text(" ".join(a.css("::text").getall())) or ""
            t = lower_norm(text)

            # score by how "nebenfach-y" the label looks
            score = 0
            if "recht im nebenfach" in t:
                score += 100
            if "nebenfach" in t:
                score += 50

            # also consider URLs that contain nebenfach as a weaker signal
            u = abs_href(response, href)
            path = urlsplit(u).path.lower()
            if "nebenfach" in path:
                score += 10

            # exclude BA/MA program pages
            if "/ius/de/studium/ba/" in path or "/ius/de/studium/ma/" in path:
                continue

            # keep only studium subtree links
            if "/ius/de/studium/" not in path:
                continue

            if score > 0:
                candidates.append((score, u, text))

        if not candidates:
            self.logger.warning("Could not find Nebenfach link on Studium page %s", response.url)

            # ✅ Fallback: if the menu is not in HTML, hardcode the known page by searching
            # for a unique PDF name on the Studium page links (often present even if menu isn't)
            # (This still stays on the same response without extra requests.)
            pdf_candidates = []
            for a in response.css('a[href$=".pdf"], a[href*=".pdf"]'):
                href = a.attrib.get("href")
                if not href:
                    continue
                u = abs_href(response, href)
                if "nebenfach" in u.lower():
                    pdf_candidates.append(u)
            if pdf_candidates:
                # If we only see the PDF but not the page, we can still output a Nebenfach item:
                yield {
                    "faculty": "Law",
                    "category": "nebenfach",
                    "program": {
                        "name_de": "Recht im Nebenfach",
                        "name_fr": None,
                        "name_en": None,
                        "ects": None,
                        "page_url_de": response.url,
                        "page_url_fr": None,
                        "page_url_en": None,
                    },
                    "documents": [{"url": safe_url(x)} for x in sorted(set(pdf_candidates))],
                }
            return

        # pick best-scored link
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, nebenfach_url, best_label = candidates[0]
        self.logger.info("Nebenfach link found (score=%s): %s (label=%s)", best_score, nebenfach_url, best_label)

        yield scrapy.Request(
            nebenfach_url,
            callback=self.parse_program_page,
            meta={
                "faculty": "Law",
                "category": "nebenfach",
                "page_url_de": nebenfach_url,
                "page_url_fr": None,
                "page_url_en": None,
                "program_name_de": "Recht im Nebenfach",
                "program_name_fr": None,
                "program_name_en": None,
                "ects": None,
            },
        )


    def parse_hub(self, response):
        category = response.meta.get("category")
        prefix = {
            "bachelor": "/ius/de/studium/ba/",
            "master": "/ius/de/studium/ma/",
        }[category]

        menu_hrefs = response.css("div.sub-menu a.deeper::attr(href), div.sub-menu a::attr(href)").getall()
        menu_hrefs = [h for h in menu_hrefs if h]

        content_hrefs = response.css(f'a[href*="{prefix}"]::attr(href)').getall()
        hrefs = list(menu_hrefs) + list(content_hrefs)

        programs = []
        seen = set()
        hub_url_norm = safe_url(response.url).rstrip("/")

        for h in hrefs:
            u = abs_href(response, h)
            path = urlsplit(u).path

            if prefix not in path:
                continue

            if safe_url(u).rstrip("/") == hub_url_norm:
                continue

            if u.endswith("#"):
                continue

            if u not in seen:
                seen.add(u)
                programs.append(u)

        if not programs:
            self.logger.warning("No %s program links found on %s", category, response.url)

        for u in programs:
            yield scrapy.Request(
                u,
                callback=self.parse_program_page,
                meta={
                    "faculty": "Law",
                    "category": category,
                    "page_url_de": u,
                    "page_url_fr": None,
                    "page_url_en": None,
                    "program_name_de": None,
                    "program_name_fr": None,
                    "program_name_en": None,
                    "ects": None,
                },
            )

    def parse_program_page(self, response):
        data = dict(response.meta)

        title = clean_text(response.css("h1::text, h2::text").get())
        if title:
            data["program_name_de"] = title

        data["ects"] = extract_ects_from_page(response)

        alts = find_alt_lang_urls(response)
        if alts.get("fr"):
            data["page_url_fr"] = alts["fr"]
        if alts.get("en"):
            data["page_url_en"] = alts["en"]

        docs = self.extract_docs_prefer_studyplan(response)

        yield {
            "faculty": data["faculty"],
            "category": data.get("category"),
            "program": {
                "name_de": data.get("program_name_de"),
                "name_fr": data.get("program_name_fr"),
                "name_en": data.get("program_name_en"),
                "ects": data.get("ects"),
                "page_url_de": data.get("page_url_de"),
                "page_url_fr": data.get("page_url_fr"),
                "page_url_en": data.get("page_url_en"),
            },
            "documents": docs,
        }

    def extract_docs_prefer_studyplan(self, response):
        KEYWORDS = ["studienplan", "plan d'études", "plan detudes", "study plan", "plan d’etudes"]

        def contains_keywords(sel) -> bool:
            txt = lower_norm(" ".join(sel.css("::text").getall()))
            return any(k in txt for k in KEYWORDS)

        docs = []

        containers = response.css(
            "main#main div.box, main#main article.box, main#main div.content, main#main section, main#main div"
        )

        best = None
        for c in containers:
            if contains_keywords(c):
                best = c
                break

        if best:
            hrefs = best.css("a[href]::attr(href)").getall()
            docs = self.normalize_docs_with_labels(response, hrefs, scope_sel=best)

        if not docs:
            hrefs = response.css("a[href]::attr(href)").getall()
            docs = self.normalize_docs_with_labels(response, hrefs)

        return docs

    def normalize_docs_with_labels(self, response, hrefs, scope_sel=None):
        seen = set()
        out = []

        for href in hrefs:
            if not href:
                continue
            url = abs_href(response, href)
            if not is_doc_href(url):
                continue
            if url in seen:
                continue
            seen.add(url)

            label = None
            candidates = []

            if scope_sel is not None:
                candidates = scope_sel.css(f'a[href="{href}"]')
                if not candidates:
                    candidates = scope_sel.xpath(f'.//a[@href and contains(@href, "{href.split("/")[-1]}")]')

            if not candidates:
                candidates = response.css(f'a[href="{href}"]')
                if not candidates:
                    candidates = response.xpath(f'//a[@href and contains(@href, "{href.split("/")[-1]}")]')

            if candidates:
                try:
                    txt = " ".join(candidates[0].css("::text").getall())
                except Exception:
                    txt = " ".join(candidates[0].xpath(".//text()").getall())
                label = clean_text(txt)

            item = {"url": url}
            if label:
                item["label"] = label
            out.append(item)

        return out
