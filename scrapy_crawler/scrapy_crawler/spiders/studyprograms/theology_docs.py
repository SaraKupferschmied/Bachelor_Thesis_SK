import json
import re
import scrapy
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote


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


def parse_ects(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d{2,3})\s*ECTS", text, flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def is_doc_href(href: str) -> bool:
    return bool(re.search(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx)\b", href or "", flags=re.IGNORECASE))


def abs_lang_href(u: str | None) -> str | None:
    if not u:
        return None
    if u.startswith("//"):
        return "https:" + u
    return safe_url(u)


class UnifrTheoStudyPlansSpider(scrapy.Spider):
    name = "unifr_theo_studyplans"

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "USER_AGENT": "Mozilla/5.0 (compatible; UnifrTheoStudyPlansSpider/1.0; +https://www.unifr.ch/)",
        "ROBOTSTXT_OBEY": True,
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    def __init__(self, lang="de", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lang = (lang or "de").strip().lower()

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

        raw = faculties_path.read_bytes()
        if not raw.strip():
            raise ValueError(f"{faculties_path.resolve()} is empty.")

        data = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(data, list):
            raise ValueError("faculties.json must be a JSON list.")
        return data

    def start_requests(self):
        data = self._load_faculties()

        theo = next(
            (x for x in data if x.get("key") == "theo" and x.get("lang") == self.lang),
            None,
        ) or next((x for x in data if x.get("key") == "theo"), None)

        if not theo:
            raise ValueError("No theo entry found in faculties.json")

        base = (theo.get(f"url_{self.lang}") or theo.get("url_de") or "https://www.unifr.ch/theo").strip()
        base = base.rstrip("/")

        if base.endswith(f"/{self.lang}"):
            start_url = f"{base}/ausbildung/"
        else:
            start_url = f"{base}/{self.lang}/ausbildung/"

        yield scrapy.Request(safe_url(start_url), callback=self.parse)

    def parse(self, response):
        base = f"https://www.unifr.ch/theo/{self.lang}/ausbildung/"

        hubs = [
            (base + "bachelor/", "bachelor"),
            (base + "master/", "master"),
            (base + "studiengaenge/", "weitere"),
        ]

        for url, category in hubs:
            yield scrapy.Request(
                url,
                callback=self.parse_program_list,
                meta={"category": category},
                dont_filter=True,
            )

    def theo_abs_url(self, response, href: str) -> str:
        href = href.strip()

        if href.startswith("//"):
            return safe_url("https:" + href)

        if href.startswith("http://") or href.startswith("https://"):
            return safe_url(href)

        if href.startswith("ausbildung/"):
            return safe_url(f"https://www.unifr.ch/theo/{self.lang}/{href}")

        return safe_url(response.urljoin(href))

    def parse_program_list(self, response):
        category = response.meta.get("category")

        cards = response.css("article.box, div.box")

        if not cards:
            self.logger.warning("No program cards found on %s", response.url)
            return

        seen = set()

        for card in cards:
            href = card.css("a.box--link::attr(href), a.box-link::attr(href), a[href]::attr(href)").get()
            if not href:
                continue

            program_url_de = self.theo_abs_url(response, href)

            if program_url_de in seen:
                continue
            seen.add(program_url_de)

            title = clean_text(card.css("h5::text, h4::text, h3::text").get())
            subtitle = clean_text(" ".join(card.css("p::text").getall()))
            program_name = clean_text(" ".join(x for x in [title, subtitle] if x))

            meta = {
                "faculty": "Theology",
                "category": category,
                "program_name_de": program_name,
                "program_name_fr": None,
                "program_name_en": None,
                "ects": parse_ects(program_name) or parse_ects(subtitle),
                "page_url_de": program_url_de,
                "page_url_fr": None,
                "page_url_en": None,
            }

            yield scrapy.Request(
                program_url_de,
                callback=self.parse_program_page,
                meta=meta,
                dont_filter=True,
            )

    def parse_program_page(self, response):
        data = dict(response.meta)

        heading = clean_text(response.css("h1::text, h2::text").get())
        if heading and (not data.get("program_name_de") or len(data["program_name_de"]) < 6):
            data["program_name_de"] = heading

        docs = self.extract_docs_prefer_studyplan(response)

        alts = {
            a.css("::attr(hreflang)").get(): abs_lang_href(a.css("::attr(href)").get())
            for a in response.css('link[rel="alternate"][hreflang]')
        }

        data["page_url_fr"] = alts.get("fr")
        data["page_url_en"] = alts.get("en")

        # Keep it simple and reliable: output DE item with FR/EN URLs.
        # No parallel merge issue.
        yield self.build_item(data, docs)

    def build_item(self, data: dict, docs: list[dict]) -> dict:
        return {
            "faculty": data.get("faculty", "Theology"),
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
            container = heading_nodes[0].xpath(
                "ancestor::*[self::div or self::section]"
                "[contains(@class,'box') or contains(@class,'content')][1]"
            )
            if container:
                docs = self.collect_docs_from_container(response, container[0])

        if not docs:
            docs = self.collect_docs_from_container(response, response)

        return docs

    def collect_docs_from_container(self, response, container_sel):
        out = []
        seen = set()

        for a in container_sel.xpath(".//a[@href]"):
            href = a.xpath("./@href").get()
            if not href:
                continue

            abs_url = safe_url(response.urljoin(href))

            if not is_doc_href(abs_url):
                continue

            if abs_url in seen:
                continue
            seen.add(abs_url)

            label = clean_text(" ".join(a.xpath(".//text()").getall()))
            item = {"url": abs_url}
            if label:
                item["label"] = label
            out.append(item)

        return out