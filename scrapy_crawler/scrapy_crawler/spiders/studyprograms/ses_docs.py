# scrapy_crawler/scrapy_crawler/spiders/ses_docs.py
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote

import scrapy

# -----------------------------
# Helpers
# -----------------------------
DOC_EXT_RE = re.compile(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx)\b", re.IGNORECASE)
ECTS_RE = re.compile(r"(\d{2,3})\s*ECTS", re.IGNORECASE)

# Nebenfach section parsing (e.g. "Nebenfächer BA 30 ECTS", "Nebenfächer MA 30 ECTS", "Lehrfach ... 50 ECTS")
_SECTION_META_RE = re.compile(r"\b(BA|MA)\b.*?\b(\d{2,3})\b", re.IGNORECASE)
_ECTS_ONLY_RE = re.compile(r"\b(\d{2,3})\b")


def clean_text(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%:@")
    query = quote(parts.query, safe="=&%:@/?")
    fragment = quote(parts.fragment, safe="")
    return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))


def abs_href(response, href: str) -> str:
    return safe_url(response.urljoin(href))


def lower_norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def is_doc_href(href: str) -> bool:
    """
    Treat normal doc files AND Calameo reader pages as documents.
    """
    if not href:
        return False
    h = href.strip().lower()
    if DOC_EXT_RE.search(h):
        return True
    if "calameo.com/read/" in h:
        return True
    return False


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


def extract_ects_from_page(response) -> int | None:
    text = " ".join(response.css("main#main *::text").getall())
    nums = [int(m.group(1)) for m in ECTS_RE.finditer(text)]
    nums = [n for n in nums if 30 <= n <= 300]
    return max(nums) if nums else None


def parse_nebenfach_section_meta(h: str) -> tuple[str | None, int | None, str]:
    """
    Returns (level, ects, section_title)
      - level: 'B' for BA, 'M' for MA, else None
      - ects: int|None
      - section_title: cleaned heading text
    """
    h_clean = clean_text(h) or ""
    m = _SECTION_META_RE.search(h_clean)
    level = None
    ects = None
    if m:
        level = "B" if m.group(1).upper() == "BA" else "M"
        ects = int(m.group(2))
    else:
        # fallback: some headings may not contain BA/MA but still have an ECTS (e.g. Lehrfach ... 50 ECTS)
        m2 = _ECTS_ONLY_RE.search(h_clean)
        if m2:
            try:
                ects = int(m2.group(1))
            except Exception:
                ects = None
    return level, ects, h_clean


def minor_name_from_label(label: str) -> str | None:
    """
    Turn 'Studienplan Betriebswirtschaftslehre' -> 'Betriebswirtschaftslehre'
    Turn 'Flyers Bachelor 30 ECTS' -> None (too generic)
    Keep other titles as-is (e.g. 'Data Analytics').
    """
    t = clean_text(label)
    if not t:
        return None

    # Drop very generic flyer labels (they aren't programme names)
    if re.search(r"^\s*flyers?\b", t, flags=re.IGNORECASE):
        return None

    t2 = re.sub(r"^\s*studienplan\s+", "", t, flags=re.IGNORECASE)
    t2 = clean_text(t2) or t
    return t2


# -----------------------------
# Spider: SES
# -----------------------------
class UnifrSesStudyPlansSpider(scrapy.Spider):
    name = "unifr_ses_studyplans"

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1,
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    def __init__(self, lang="de", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lang = (lang or "de").strip().lower()

    # Scrapy 2.13+ compatibility (matches your other spiders)
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

        ses = next(
            (x for x in data if x.get("key") == "ses" and x.get("lang") == self.lang),
            None,
        ) or next((x for x in data if x.get("key") == "ses"), None)

        if not ses:
            raise ValueError("No ses entry found in faculties.json")

        lang_key = f"url_{self.lang}"
        base = (ses.get(lang_key) or ses.get("url_en") or "").strip()
        if not base:
            raise ValueError("SES entry has no usable url_* field in faculties.json")

        # ✅ language-scoped faculty home like /ses/de/
        start_url = safe_url(base.rstrip("/") + f"/{self.lang}/")
        self.logger.info("Starting SES crawl at: %s", start_url)
        yield scrapy.Request(start_url, callback=self.parse)

    def parse(self, response):
        # Find top-menu Studium
        studium_href = (
            response.css('nav.push-menu a.deeper[href*="/ses/de/studium"]::attr(href)').get()
            or response.css('a[href*="/ses/de/studium"]::attr(href)').get()
            or response.css('a:contains("Studium")::attr(href)').get()
        )

        if not studium_href:
            self.logger.warning("Could not find Studium link on %s", response.url)
            yield scrapy.Request("https://www.unifr.ch/ses/de/studium/", callback=self.parse_studium)
            return

        yield response.follow(studium_href, callback=self.parse_studium)

    def parse_studium(self, response):
        """
        Studium page:
        - schedule bachelor hub
        - schedule master hub
        - robustly find Nebenfach/Nebenfächer page by ANCHOR TEXT and crawl it
          (but we split it into multiple emitted items, not one bucket).
        """
        menu_urls = [
            abs_href(response, h)
            for h in response.css("div.sub-menu a::attr(href), a[href]::attr(href)").getall()
            if h
        ]

        bachelor_hub = self._pick_hub(menu_urls, want="bachelor") or "https://www.unifr.ch/ses/de/studium/bachelor/"
        master_hub = self._pick_hub(menu_urls, want="master") or "https://www.unifr.ch/ses/de/studium/master/"

        yield scrapy.Request(bachelor_hub, callback=self.parse_hub, meta={"category": "bachelor"})
        yield scrapy.Request(master_hub, callback=self.parse_hub, meta={"category": "master"})

        # --- Robust Nebenfach discovery ---
        nebenfach_url = self._find_nebenfach_page(response)
        if nebenfach_url:
            yield scrapy.Request(
                nebenfach_url,
                callback=self.parse_nebenfach_page,
                meta={
                    "faculty": "SES",
                    "category": "nebenfach",
                    "page_url_de": nebenfach_url,
                    "page_url_fr": None,
                    "page_url_en": None,
                },
            )
        else:
            self.logger.warning("Could not find Nebenfach/Nebenfächer link on SES Studium page %s", response.url)

    def _pick_hub(self, urls: list[str], want: str) -> str | None:
        want = want.lower()
        candidates = []
        for u in urls:
            p = urlsplit(u).path.lower()
            if "/ses/de/studium" not in p:
                continue

            if want == "bachelor" and any(x in p for x in ["/bachelor", "/ba/"]):
                candidates.append(u)
            if want == "master" and any(x in p for x in ["/master", "/ma/"]):
                candidates.append(u)

        candidates.sort(key=lambda x: len(urlsplit(x).path))
        return candidates[0] if candidates else None

    def _find_nebenfach_page(self, response) -> str | None:
        """
        Find the Nebenfach/Nebenfächer page by link TEXT (primary),
        and URL containing nebenfach (secondary).
        """
        candidates = []

        for a in response.css("div.sub-menu a, a[href]"):
            href = a.attrib.get("href")
            if not href:
                continue
            url = abs_href(response, href)
            path = urlsplit(url).path.lower()

            text = clean_text(" ".join(a.css("::text").getall())) or ""
            t = lower_norm(text)

            score = 0
            if "nebenfach" in t or "nebenfächer" in t:
                score += 100
            if "minor" in t:
                score += 30
            if "nebenfach" in path:
                score += 10
            if "/ses/de/studium" in path:
                score += 5

            # Exclude obvious BA/MA program subpages (we already crawl those),
            # unless explicitly nebenfach by text.
            if any(
                x in path
                for x in [
                    "/ses/de/studium/bachelor",
                    "/ses/de/studium/ba/",
                    "/ses/de/studium/master",
                    "/ses/de/studium/ma/",
                ]
            ):
                if score < 100:
                    continue

            if score > 0:
                candidates.append((score, url))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def parse_hub(self, response):
        """
        Bachelor/Master hub: follow each program from left menu (including nested)
        """
        category = response.meta.get("category") or "unknown"
        hub_url = safe_url(response.url).rstrip("/")

        menu_hrefs = response.css("div.sub-menu a.deeper::attr(href), div.sub-menu a::attr(href)").getall()
        menu_abs = [abs_href(response, h) for h in menu_hrefs if h]

        hub_path = urlsplit(hub_url).path.rstrip("/") + "/"
        programs = []
        seen = set()

        for u in menu_abs:
            u_norm = safe_url(u).rstrip("/")
            if u_norm == hub_url:
                continue
            path = urlsplit(u_norm).path
            if not path.startswith(hub_path):
                continue
            if u_norm not in seen:
                seen.add(u_norm)
                programs.append(u_norm)

        if not programs:
            self.logger.warning("No %s program links found on %s", category, response.url)

        for u in programs:
            yield scrapy.Request(
                u,
                callback=self.parse_program_page,
                meta={
                    "faculty": "SES",
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
        data["page_url_fr"] = alts.get("fr")
        data["page_url_en"] = alts.get("en")

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

    def parse_nebenfach_page(self, response):
        """
        Split Nebenfächer into separate emitted items instead of one big bucket.

        Strategy:
          - Walk DOM in order; each H4 starts a "section" (BA 30, BA 60, MA 30, Lehrfach 50, ...)
          - Collect doc links under that section
          - Emit one item per doc-link with:
              program.name_de = <minor name> (Nebenfach <B/M> <ects> ECTS)
              program.ects = section ects (so it won't stay null)
        """
        data = dict(response.meta)

        page_title = clean_text(response.css("h1::text, h2::text").get()) or "Nebenfächer"
        alts = find_alt_lang_urls(response)
        page_url_fr = alts.get("fr")
        page_url_en = alts.get("en")

        sections: list[dict] = []
        current: dict | None = None

        # Iterate over all elements inside main to keep document order
        for el in response.css("main#main *"):
            tag = getattr(el.root, "tag", None)

            if tag == "h4":
                htxt = clean_text(" ".join(el.css("::text").getall())) or ""
                lvl, ects, heading = parse_nebenfach_section_meta(htxt)
                current = {"heading": heading, "level": lvl, "ects": ects, "links": []}
                sections.append(current)
                continue

            if tag == "a" and current is not None:
                href = el.attrib.get("href")
                if not href:
                    continue
                url = abs_href(response, href)
                if not is_doc_href(url):
                    continue
                label = clean_text(" ".join(el.css("::text").getall()))
                current["links"].append((href, label))

        # Fallback: if no h4 sections were discovered, behave like old version (still better than failing)
        if not sections:
            hrefs = response.css("a[href]::attr(href)").getall()
            docs = self.normalize_docs_with_labels(response, hrefs)
            yield {
                "faculty": data["faculty"],
                "category": "nebenfach",
                "program": {
                    "name_de": page_title,
                    "name_fr": None,
                    "name_en": None,
                    "ects": None,
                    "page_url_de": data.get("page_url_de") or response.url,
                    "page_url_fr": page_url_fr,
                    "page_url_en": page_url_en,
                },
                "documents": docs,
            }
            return

        # Emit one item per doc under each section
        for sec in sections:
            sec_level = sec.get("level")  # 'B'/'M'/None
            sec_ects = sec.get("ects")    # int|None

            for (href, label) in sec.get("links", []):
                url = abs_href(response, href)
                if not is_doc_href(url):
                    continue

                doc_label = clean_text(label) or url.split("/")[-1]
                minor = minor_name_from_label(doc_label) or page_title

                # Build a program name that is not just "Nebenfächer"
                if sec_level in {"B", "M"} and sec_ects:
                    prog_name = f"{minor} (Nebenfach {sec_level} {sec_ects} ECTS)"
                elif sec_ects:
                    prog_name = f"{minor} (Nebenfach {sec_ects} ECTS)"
                else:
                    prog_name = f"{minor} (Nebenfach)"

                doc_item = {"url": url, "label": doc_label}
                if "calameo.com/read/" in url.lower():
                    doc_item["source_type"] = "calameo"

                yield {
                    "faculty": data["faculty"],
                    "category": "nebenfach",
                    "level": sec_level,
                    "program": {
                        "name_de": prog_name,
                        "name_fr": None,
                        "name_en": None,
                        "ects": sec_ects,  # key: not null for most sections
                        "page_url_de": data.get("page_url_de") or response.url,
                        "page_url_fr": page_url_fr,
                        "page_url_en": page_url_en,
                    },
                    "documents": [doc_item],
                }

    def extract_docs_prefer_studyplan(self, response):
        """
        Prefer docs in/near "Dokumente/Documents" and then "Studienplan".
        Fallback: any doc links on the page.
        """
        DOC_SECTION_KW = ["dokument", "documents", "documenti"]
        PLAN_KW = ["studienplan", "plan d'études", "plan detudes", "study plan", "plan d’etudes"]

        def sel_text_has(sel, kws):
            txt = lower_norm(" ".join(sel.css("::text").getall()))
            return any(k in txt for k in kws)

        docs = []

        containers = response.css(
            "main#main div.box, main#main article.box, main#main div.content, main#main section, main#main div"
        )

        doc_container = None
        for c in containers:
            if sel_text_has(c, DOC_SECTION_KW):
                doc_container = c
                break

        if doc_container:
            plan_sub = None
            nested = doc_container.css("div, section, article, p")
            for n in nested:
                if sel_text_has(n, PLAN_KW):
                    plan_sub = n
                    break

            target = plan_sub or doc_container
            hrefs = target.css("a[href]::attr(href)").getall()
            docs = self.normalize_docs_with_labels(response, hrefs, scope_sel=target)

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
                    last = href.split("/")[-1]
                    candidates = scope_sel.xpath(f'.//a[@href and contains(@href, "{last}")]')

            if not candidates:
                candidates = response.css(f'a[href="{href}"]')
                if not candidates:
                    last = href.split("/")[-1]
                    candidates = response.xpath(f'//a[@href and contains(@href, "{last}")]')

            if candidates:
                try:
                    txt = " ".join(candidates[0].css("::text").getall())
                except Exception:
                    txt = " ".join(candidates[0].xpath(".//text()").getall())
                label = clean_text(txt)

            item = {"url": url}
            if label:
                item["label"] = label
            if "calameo.com/read/" in url.lower():
                item["source_type"] = "calameo"
            out.append(item)

        return out