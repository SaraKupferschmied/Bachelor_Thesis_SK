import glob
import json
import re
from typing import Iterable, List, Dict, Optional

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
      3) Comma-separated objects as in your sample (optionally wrapped or not)
    """
    decoder = json.JSONDecoder()

    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()

    if not text:
        return

    # If it's a proper JSON array or object, try normal parsing first
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
            # fall back to streaming decode below
            pass

    # Streaming decode: walk through text and decode objects one by one
    i = 0
    n = len(text)
    while i < n:
        # skip whitespace and commas and array brackets
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


def extract_names_from_courses_file(path: str) -> List[str]:
    names: Dict[str, str] = {}

    for row in iter_course_objects(path):
        teaching = (row.get("teaching") or {})
        for field in ["Verantwortliche", "Dozenten-innen"]:
            arr = teaching.get(field) or []
            if not isinstance(arr, list):
                continue
            for n in arr:
                nn = normalize_name(n)
                if nn:
                    names[name_key(nn)] = nn

    return sorted(names.values())


class UnifrDirectorySpider(scrapy.Spider):
    name = "unifr_directory"
    allowed_domains = ["www.unifr.ch"]
    start_urls = ["https://www.unifr.ch/directory/de"]

    def __init__(self, courses_file="courses.json", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.courses_file = courses_file
        self.people_names = extract_names_from_courses_file(self.courses_file)

    def parse(self, response: scrapy.http.Response):
        # CSRF token is in <meta name="csrf-token" content="...">
        token = response.css('meta[name="csrf-token"]::attr(content)').get()
        if not token:
            self.logger.error("Could not find csrf-token meta tag on /directory/de")
            return

        if not self.people_names:
            self.logger.warning("No names found from courses files glob: %s", self.courses_glob)
            return

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
        """
        The search results page layout can change; we try to find links to /directory/de/people/...
        Then choose the best match based on displayed name similarity.
        """
        links = response.css('a[href*="/directory/de/people/"]::attr(href)').getall()
        links = list(dict.fromkeys(links))  # preserve order, dedup

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

        # Build candidate list with their visible link text (best-effort)
        candidates = []
        for a in response.css('a[href*="/directory/de/people/"]'):
            href = a.attrib.get("href")
            txt = normalize_name("".join(a.css("::text").getall()))
            if href:
                candidates.append((href, txt))

        # pick "best" link:
        # 1) exact text match if present
        qk = name_key(query_name)
        best_href = None

        for href, txt in candidates:
            if txt and name_key(txt) == qk:
                best_href = href
                break

        # 2) else fallback to first people link on page
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
        # Name shown on page
        matched_name = normalize_name(response.css("h2::text").get())

        # Email: <a href="mailto:...">
        email = response.css('a[href^="mailto:"]::text').get()
        email = normalize_name(email) if email else None

        # Title: first <strong> inside the main details panel often contains the position/title
        # Example: <p><strong>Wissenschaftliche_r Mitarbeiter_in</strong><br>...
        title = response.css(".directory--details p strong::text").get()
        title = normalize_name(title) if title else None

        # Office/Büro: icon fa-building-o with title "Büro", then adjacent text in same row box
        office = None
        office_row = response.xpath(
            "//i[contains(@class,'fa-building-o') and @title='Büro']/ancestor::div[contains(@class,'row') and contains(@class,'box')][1]"
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