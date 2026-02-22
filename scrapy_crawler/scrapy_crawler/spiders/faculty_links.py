import re
import scrapy

FACULTY_PAGES = {
    "de": "https://www.unifr.ch/faculties/de/",
    "fr": "https://www.unifr.ch/faculties/fr/",
    "en": "https://www.unifr.ch/faculties/en/",
}

def norm_key(href: str) -> str | None:
    href = href or ""
    if "/faculties/" in href and any(x in href for x in ["interfaculty", "interfacult", "interfakul"]):
        return "interfaculty"
    m = re.search(r"https?://www\.unifr\.ch/([^/]+)/?", href)
    if not m:
        return None
    slug = m.group(1).strip().lower()
    if slug in ("faculties", "studies", "campus", "research", "university", "directory"):
        return None
    return slug

class UnifrFacultiesSpider(scrapy.Spider):
    name = "faculty_links"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.faculties = {}  # key -> aggregated record
        self.pending_pages = len(FACULTY_PAGES)

    def start_requests(self):
        for lang, url in FACULTY_PAGES.items():
            yield scrapy.Request(url, callback=self.parse_faculties, cb_kwargs={"lang": lang})

    def parse_faculties(self, response, lang: str):
        for box in response.css("div.box"):
            name = box.css("h4::text").get()
            href = box.css("a.box--link::attr(href)").get()
            if not name or not href:
                continue

            name = " ".join(name.split())
            abs_url = response.urljoin(href)

            key = norm_key(abs_url)
            if not key:
                continue

            rec = self.faculties.setdefault(key, {
                "key": key,
                "name_en": None, "name_de": None, "name_fr": None,
                "url_en": None,  "url_de": None,  "url_fr": None,
                # optional: keep where we found it
                "source_url_en": None, "source_url_de": None, "source_url_fr": None,
            })

            rec[f"name_{lang}"] = name
            rec[f"url_{lang}"] = abs_url
            rec[f"source_url_{lang}"] = response.url

        # after finishing this language page:
        self.pending_pages -= 1

        # if this was the last of the 3 pages, emit everything once
        if self.pending_pages == 0:
            for item in self.faculties.values():
                yield item
