
import json
import re
from typing import Iterable, List, Dict, Any

import scrapy


def normalize_name(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    s = s.replace("‐", "-").replace("–", "-")
    return s


def name_key(full_name: str) -> str:
    return normalize_name(full_name).lower()


def iter_course_objects(path: str) -> Iterable[dict]:
    """
    Robustly iterate objects from:
      1) JSON array: [ {...}, {...} ]
      2) JSONL:      {...}\n{...}\n
      3) Comma-separated objects, optionally partly wrapped
    """
    decoder = json.JSONDecoder()

    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()

    if not text:
        return

    if text[0] in "[{":
        try:
            data = json.loads(text)
            if isinstance(data, list):
                for obj in data:
                    if isinstance(obj, dict):
                        yield obj
                return
            if isinstance(data, dict):
                yield data
                return
        except json.JSONDecodeError:
            pass

    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i] in " \r\n\t,[":
            i += 1
        if i < n and text[i] == "]":
            break
        if i >= n:
            break

        obj, j = decoder.raw_decode(text, i)
        if isinstance(obj, dict):
            yield obj
        i = j


def ensure_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [normalize_name(str(x)) for x in value if normalize_name(str(x))]
    if isinstance(value, str):
        value = normalize_name(value)
        return [value] if value else []
    return []


def extract_names_from_courses_file(path: str) -> List[str]:
    names: Dict[str, str] = {}

    teaching_fields = [
        "Verantwortliche",
        "Dozenten-innen",
        "Responsibles",
        "Teachers",
        "Assistants",
        "Enseignants",
        "Responsables",
        "Chargé-e-s de cours",
    ]

    for row in iter_course_objects(path):
        teaching = row.get("teaching") or {}
        if not isinstance(teaching, dict):
            continue

        for field in teaching_fields:
            for person_name in ensure_list(teaching.get(field)):
                names[name_key(person_name)] = person_name

    return sorted(names.values(), key=lambda s: s.lower())


class UnifrDirectorySpider(scrapy.Spider):
    name = "unifr_directory"
    allowed_domains = ["www.unifr.ch"]
    start_urls = ["https://www.unifr.ch/directory/de"]

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 0.2,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "FEED_EXPORT_ENCODING": "utf-8",
        "DEFAULT_REQUEST_HEADERS": {
            "User-Agent": "Mozilla/5.0 (compatible; unifr-directory-scraper/2.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    }

    def __init__(self, courses_file="courses.json", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.courses_file = courses_file
        self.people_names = extract_names_from_courses_file(self.courses_file)

    def parse(self, response: scrapy.http.Response):
        token = response.css('meta[name="csrf-token"]::attr(content)').get()
        if not token:
            self.logger.error("Could not find csrf-token meta tag on /directory/de")
            return

        if not self.people_names:
            self.logger.warning("No names found in courses_file=%s", self.courses_file)
            return

        self.logger.info(
            "Searching UNIFR directory for %s unique teaching names from %s",
            len(self.people_names),
            self.courses_file,
        )

        for full_name in self.people_names:
            yield scrapy.FormRequest(
                url="https://www.unifr.ch/directory/de/search",
                formdata={
                    "_token": token,
                    "q": full_name,
                    "populations": "EMPLOYE_ACTIF,TIERS",
                },
                callback=self.parse_search,
                cb_kwargs={"query_name": full_name},
                dont_filter=True,
            )

    def parse_search(self, response: scrapy.http.Response, query_name: str):
        links = response.css('a[href*="/directory/de/people/"]::attr(href)').getall()
        links = list(dict.fromkeys(links))

        if not links:
            yield {
                "input_name": query_name,
                "matched_name": None,
                "person_url": None,
                "email": None,
                "title": None,
                "office": None,
                "status": "not_found",
            }
            return

        candidates = []
        for a in response.css('a[href*="/directory/de/people/"]'):
            href = a.attrib.get("href")
            txt = normalize_name("".join(a.css("::text").getall()))
            if href:
                candidates.append((href, txt))

        qk = name_key(query_name)
        best_href = None

        for href, txt in candidates:
            if txt and name_key(txt) == qk:
                best_href = href
                break

        if not best_href:
            reversed_query = self._reverse_name(query_name)
            rqk = name_key(reversed_query)
            for href, txt in candidates:
                if txt and name_key(txt) == rqk:
                    best_href = href
                    break

        if not best_href:
            best_href = links[0]

        person_url = response.urljoin(best_href)
        yield scrapy.Request(
            person_url,
            callback=self.parse_person,
            cb_kwargs={"query_name": query_name, "person_url": person_url},
            dont_filter=True,
        )

    def parse_person(self, response: scrapy.http.Response, query_name: str, person_url: str):
        matched_name = normalize_name(response.css("h2::text").get())

        email = response.css('a[href^="mailto:"]::text').get()
        email = normalize_name(email) if email else None

        title = response.css(".directory--details p strong::text").get()
        title = normalize_name(title) if title else None

        office = None
        office_row = response.xpath(
            "//i[contains(@class,'fa-building-o') and @title='Büro']"
            "/ancestor::div[contains(@class,'row') and contains(@class,'box')][1]"
        )
        if office_row:
            office_txt = office_row.xpath(".//div[contains(@class,'col-xs-10')]//text()").getall()
            office_txt = normalize_name(" ".join(office_txt))
            office = office_txt or None

        yield {
            "input_name": query_name,
            "matched_name": matched_name,
            "person_url": person_url,
            "email": email,
            "title": title,
            "office": office,
            "status": "ok",
        }

    def _reverse_name(self, name: str) -> str:
        parts = normalize_name(name).split()
        if len(parts) < 2:
            return name
        return " ".join(parts[1:] + parts[:1])
