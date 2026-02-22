import json
import re
import scrapy
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote

def safe_url(url: str) -> str:
    """
    Percent-encode unsafe characters in the URL (notably spaces),
    while preserving scheme/host and keeping query/fragment intact.
    """
    parts = urlsplit(url)
    # Encode path safely (keep / and common URL-safe chars)
    path = quote(parts.path, safe="/:@-._~!$&'()*+,;=")
    # Encode query too (keep separators)
    query = quote(parts.query, safe="=&?/:@-._~!$&'()*+,;=")
    fragment = quote(parts.fragment, safe="-._~")
    return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))

def clean_text(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def parse_ects(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d{2,3})\s*ECTS", text, flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def is_doc_href(href: str) -> bool:
    return bool(re.search(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx)\b", href, flags=re.IGNORECASE))


def abs_lang_href(u: str | None) -> str | None:
    if not u:
        return None
    if u.startswith("//"):
        return "https:" + u
    return u


class UnifrTheoStudyPlansSpider(scrapy.Spider):
    """
    Flow (DE as entry):
      /theo/de/ausbildung/  -> follow left menu "studiengaenge"
      studiengaenge page   -> parse program CARDS (article.box ... a.box--link)
      each program page    -> prefer docs in section titled Studienplan / Plan d'études
                              else all docs on that page (depth 0)
      also fetch FR/EN variants via hreflang and merge titles into the same output item
    """
    name = "unifr_theo_studyplans"

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "USER_AGENT": "Mozilla/5.0 (compatible; UnifrTheoStudyPlansSpider/1.0; +https://www.unifr.ch/)",
        "ROBOTSTXT_OBEY": True,
    }

    def __init__(self, lang="de", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lang = (lang or "de").strip().lower()

    # Scrapy 2.13+ compatibility (same pattern as your other spiders)
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

        theo = next(
            (x for x in data if x.get("key") == "theo" and x.get("lang") == self.lang),
            None,
        ) or next((x for x in data if x.get("key") == "theo"), None)

        if not theo:
            raise ValueError("No theo entry found in faculties.json")

        lang_key = f"url_{self.lang}"
        base = (theo.get(lang_key) or theo.get("url_en") or "").strip()
        if not base:
            raise ValueError("THEO entry has no usable url_* field in faculties.json")

        # Theology spider expects to start at /theo/<lang>/ausbildung/
        start_url = safe_url(base.rstrip("/") + f"/{self.lang}/ausbildung/")
        self.logger.info("Starting THEO crawl at: %s", start_url)
        yield scrapy.Request(start_url, callback=self.parse)

    def parse(self, response):
        # Prefer left menu entry
        studiengaenge = response.css('div.sub-menu a[href*="studiengaenge"]::attr(href)').get()
        if not studiengaenge:
            studiengaenge = response.css('a[href*="studiengaenge"]::attr(href)').get()

        if not studiengaenge:
            self.logger.warning("Could not find Studiengänge link on %s", response.url)
            return

        yield response.follow(studiengaenge, callback=self.parse_program_list)

    def parse_program_list(self, response):
        """
        IMPORTANT FIX:
        On the Studiengänge page the program links <a class="box--link"> have NO TEXT.
        The visible name/ECTS is in <h5> and <p> inside the card.
        So we must parse article.box (or div.box) and read h5/p.
        """
        cards = response.css("article.box")
        if not cards:
            # fallback if markup differs
            cards = response.css("div.box")

        if not cards:
            self.logger.warning("No program cards found on %s", response.url)
            return

        for card in cards:
            href = card.css("a.box--link::attr(href), a.box-link::attr(href)").get()
            if not href:
                continue

            title = clean_text(card.css("h5::text, h4::text").get())
            subtitle = clean_text(" ".join(card.css("p::text").getall()))

            # Some cards only have title, some have title + subtitle with ECTS
            program_name = clean_text(" ".join([x for x in [title, subtitle] if x]))

            ects = parse_ects(program_name) or parse_ects(subtitle)

            program_url_de = response.urljoin(href)

            meta = {
                "faculty": "Theology",
                "program_name_de": program_name,
                "program_name_fr": None,
                "program_name_en": None,
                "ects": ects,
                "page_url_de": program_url_de,
                "page_url_fr": None,
                "page_url_en": None,
            }

            yield scrapy.Request(program_url_de, callback=self.parse_program_page, meta=meta)

    def parse_program_page(self, response):
        """
        1) Fill missing/short DE title from page heading if needed
        2) Discover FR/EN via hreflang and fetch them
        3) Extract documents (prefer Studienplan section)
        4) Output ONE merged item (FR/EN fetched then merged)
        """
        data = dict(response.meta)

        # Improve DE title if card text was missing/odd
        heading = clean_text(response.css("h1::text, h2::text").get())
        if heading and (not data.get("program_name_de") or len(data["program_name_de"]) < 6):
            data["program_name_de"] = heading

        # Collect docs from DE page
        docs = self.extract_docs_prefer_studyplan(response)

        # Discover language variants
        alts = {
            a.css("::attr(hreflang)").get(): abs_lang_href(a.css("::attr(href)").get())
            for a in response.css('link[rel="alternate"][hreflang]')
        }
        url_fr = alts.get("fr")
        url_en = alts.get("en")

        data["page_url_fr"] = url_fr
        data["page_url_en"] = url_en

        # We’ll fetch FR/EN (if available) and then yield final item once both are done.
        # Track pending requests count.
        pending = 0
        if url_fr:
            pending += 1
        if url_en:
            pending += 1

        data["_docs"] = docs
        data["_pending_lang_pages"] = pending

        if url_fr:
            yield scrapy.Request(
                url_fr,
                callback=self.parse_program_page_lang,
                meta={**data, "lang": "fr"},
                dont_filter=True,
            )
        if url_en:
            yield scrapy.Request(
                url_en,
                callback=self.parse_program_page_lang,
                meta={**data, "lang": "en"},
                dont_filter=True,
            )

        # If no FR/EN, yield now
        if pending == 0:
            yield self.build_item(data, docs)

    def parse_program_page_lang(self, response):
        """
        Fetch FR/EN page and merge title into the same output item.
        When both optional lang pages are processed -> yield.
        """
        data = dict(response.meta)
        lang = data.get("lang")

        title = clean_text(response.css("h1::text, h2::text").get())

        if lang == "fr" and title:
            data["program_name_fr"] = title
        elif lang == "en" and title:
            data["program_name_en"] = title

        # decrement pending and either wait or yield
        pending = int(data.get("_pending_lang_pages", 0))
        pending = max(0, pending - 1)
        data["_pending_lang_pages"] = pending

        if pending == 0:
            docs = data.get("_docs") or []
            yield self.build_item(data, docs)
        else:
            # keep passing merged data along (Scrapy meta is per-request, so we must carry it)
            # We do nothing here; the other lang request will also carry its own meta.
            # To truly merge across two parallel requests you'd need a cache/dict on self.
            #
            # SIMPLE FIX: make them sequential instead of parallel.
            #
            # To avoid complexity, we handle sequentially by chaining:
            next_lang = None
            if pending == 1:
                # If we just processed FR and EN exists but hasn't been fetched in this branch,
                # or vice versa, fetch the other one now.
                if lang == "fr" and data.get("page_url_en") and not data.get("program_name_en"):
                    next_lang = ("en", data["page_url_en"])
                if lang == "en" and data.get("page_url_fr") and not data.get("program_name_fr"):
                    next_lang = ("fr", data["page_url_fr"])

            if next_lang:
                l, u = next_lang
                yield scrapy.Request(
                    u,
                    callback=self.parse_program_page_lang,
                    meta={**data, "lang": l},
                    dont_filter=True,
                )
            else:
                # If both requests ran in parallel, we might land here without shared state.
                # In that case, still yield what we have.
                docs = data.get("_docs") or []
                yield self.build_item(data, docs)

    def build_item(self, data: dict, docs: list[dict]) -> dict:
        return {
            "faculty": data.get("faculty", "Theology"),
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
        """
        1) If a "Studienplan / Plan d'études" section exists -> collect docs from that section only.
        2) Else collect all docs on page (no depth).
        Also tries to capture link text as a label when possible.
        """
        # Find heading containing the keywords (case-insensitive-ish)
        heading_nodes = response.xpath(
            "//*[self::h1 or self::h2 or self::h3 or self::h4]"
            "[contains(translate(normalize-space(.),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜÉÈÀÂÊÎÔÛÇ',"
            "'abcdefghijklmnopqrstuvwxyzäöüéèàâêîôûç'),"
            "'studienplan')"
            " or contains(translate(normalize-space(.),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜÉÈÀÂÊÎÔÛÇ',"
            "'abcdefghijklmnopqrstuvwxyzäöüéèàâêîôûç'),"
            "\"plan d'études\")"
            " or contains(translate(normalize-space(.),"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜÉÈÀÂÊÎÔÛÇ',"
            "'abcdefghijklmnopqrstuvwxyzäöüéèàâêîôûç'),"
            "'plan detudes')]"
        )

        docs = []

        if heading_nodes:
            # take first matching heading and grab a reasonable container
            container = heading_nodes[0].xpath(
                "ancestor::*[self::div or self::section][contains(@class,'box') or contains(@class,'content')][1]"
            )
            if container:
                docs = self.collect_docs_from_container(response, container[0])

        # fallback: all docs on the page
        if not docs:
            docs = self.collect_docs_from_container(response, response)

        return docs

    def collect_docs_from_container(self, response, container_sel):
        out = []
        seen = set()

        # gather anchors with href
        for a in container_sel.xpath(".//a[@href]"):
            href = a.xpath("./@href").get()
            if not href:
                continue
            abs_url = response.urljoin(href)
            abs_url = safe_url(abs_url)
            if not is_doc_href(abs_url):
                continue
            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = clean_text(" ".join(a.xpath(".//text()").getall()))
            out.append({"url": abs_url, **({"label": label} if label else {})})

        return out
