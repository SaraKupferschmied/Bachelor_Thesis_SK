import re
from datetime import datetime, timezone

import scrapy


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def parse_float(s: str):
    if not s:
        return None
    s = s.replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def split_multi_value(s: str):
    if not s:
        return []
    parts = re.split(r"\s*,\s*|\s*/\s*|\s*;\s*", s)
    return [norm(p) for p in parts if norm(p)]


def parse_semesters(s: str):
    if not s:
        return []
    return re.findall(r"\b(?:HS|FS)-\d{4}\b", s)


def text_list(sel):
    return [norm(x) for x in sel.getall() if norm(x)]


def tab_block(response, tab_id: str):
    return response.xpath(f"//div[@data-accordion-content='{tab_id}']")


def parse_2col_table(tbl_sel):
    out = {}
    for tr in tbl_sel.xpath(".//tr"):
        key = norm(" ".join(tr.xpath("./td[1]//text()").getall()))
        val = norm(" ".join(tr.xpath("./td[2]//text()").getall()))
        if key:
            out[key] = val or None
    return out


def parse_people_ul(td_sel):
    names = td_sel.xpath(".//li//text()").getall()
    names = [norm(x) for x in names if norm(x)]
    if names:
        return names

    text = norm(" ".join(td_sel.xpath(".//text()").getall()))
    return [text] if text else []


def title_matches(title: str, *needles: str) -> bool:
    title_l = title.lower()
    return any(needle.lower() in title_l for needle in needles)


def parse_tab1_teaching(response):
    """
    Tab 1 contains several h3 sections followed by tables.
    This version accepts German, French and English headings so the spider
    remains usable even if UNIFR falls back to a mixed-language detail page.
    """
    block = tab_block(response, "tab-1")
    if not block:
        return {"sections": [], "details": {}, "schedule": {}, "teaching": {}, "raw_text": ""}

    details = {}
    schedule = {}
    teaching = {}

    for h3 in block.xpath(".//h3"):
        title = norm(" ".join(h3.xpath(".//text()").getall()))
        tbl = h3.xpath("following-sibling::table[1] | following-sibling::*[1]//table[1]")

        if not tbl or not tbl.xpath("ancestor::div[@data-accordion-content='tab-1']"):
            continue

        kv = parse_2col_table(tbl)

        if title_matches(title, "Teaching", "Unterricht", "Enseignement"):
            for tr in tbl.xpath(".//tr"):
                key = norm(" ".join(tr.xpath("./td[1]//text()").getall()))
                td2 = tr.xpath("./td[2]")
                if not key:
                    continue
                if td2.xpath(".//li"):
                    teaching[key] = parse_people_ul(td2)
                else:
                    val = norm(" ".join(td2.xpath(".//text()").getall()))
                    teaching[key] = val or None

        elif title_matches(title, "Details", "Détails"):
            details = kv

        elif title_matches(title, "Schedule", "Lecture times", "Zeitplan", "Horaires"):
            schedule = kv

    return {
        "details": details,
        "schedule": schedule,
        "teaching": teaching,
    }


def parse_tab2_dates(response):
    block = tab_block(response, "tab-2")
    if not block:
        return {"rows": [], "raw_text": ""}

    rows = []
    for tr in block.xpath(".//table//tbody/tr"):
        cols = [norm(x) for x in tr.xpath("./td//text()").getall() if norm(x)]
        if len(cols) >= 4:
            rows.append({
                "date": cols[0],
                "time": cols[1],
                "unit_type": cols[2],
                "location": cols[3],
            })

    return {"rows": rows}


def parse_tab3_assessment(response):
    block = tab_block(response, "tab-3")
    if not block:
        return {"sections": [], "raw_text": ""}

    sections = []
    for h3 in block.xpath(".//h3"):
        title = norm(" ".join(h3.xpath(".//text()").getall()))
        tbl = h3.xpath("following-sibling::table[1] | following-sibling::*[1]//table[1]")

        if not tbl or not tbl.xpath("ancestor::div[@data-accordion-content='tab-3']"):
            continue

        sections.append({"title": title or None, "kv": parse_2col_table(tbl)})

    return {"sections": sections}


def parse_tab4_affiliations(response):
    block = tab_block(response, "tab-4")
    if not block:
        return {"rows": [], "raw_text": ""}

    out = []
    for tr in block.xpath(".//table//tbody/tr"):
        td = tr.xpath("./td[1]")
        if not td:
            continue

        study_plan = norm(" ".join(td.xpath(".//strong[1]//text()").getall()))
        version = norm(" ".join(td.xpath(".//small//strong//text()").getall()))
        version = version.replace("Version:", "").strip() if version else None
        path = norm(" ".join(td.xpath(".//div[contains(@class,'bg-grey-light')]//text()").getall())) or None

        if study_plan:
            out.append({"study_plan": study_plan, "version": version, "path": path})

    return {"rows": out}


def first_value(mapping: dict, *keys: str):
    for key in keys:
        value = mapping.get(key)
        if value:
            return value
    return None


class TimetableCoursesSpiderEn(scrapy.Spider):
    """
    English timetable spider.

    Important fixes compared with the previous English adaptation:
      - stops when a list page has no course links
      - handles "Still loading"/search-form XHR responses by polling the same page
      - logs show-id counts per page
      - includes all helper parsers locally, so parse_detail has no hidden imports
      - supports common English/German/French detail labels
    """

    name = "timetable_courses_en"

    custom_settings = {
        "DOWNLOAD_DELAY": 0.7,
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        ),
    }

    LIST_ENDPOINT = "https://www.unifr.ch/timetable/assets/components/timetable/connector.php?action=getlist"
    VIEWER = "//www.unifr.ch/timetable/en/course.html"
    DETAIL_URL = "https://www.unifr.ch/timetable/en/course.html?show={show_id}"

    def __init__(
        self,
        start_page=1,
        max_pages=0,
        semestres="253,254,255,256",
        max_empty_pages=1,
        max_poll_tries=10,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.start_page = int(start_page)
        self.max_pages = int(max_pages)
        self.semestres = (semestres or "").strip()
        self.max_empty_pages = int(max_empty_pages)
        self.max_poll_tries = int(max_poll_tries)
        self.scrape_started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def start_requests(self):
        url = f"https://www.unifr.ch/timetable/en/?&page={self.start_page}"
        yield scrapy.Request(
            url=url,
            callback=self._after_bootstrap,
            meta={"cookiejar": 1},
            dont_filter=True,
        )

    def _after_bootstrap(self, response):
        yield self._xhr_list_request(
            page=self.start_page,
            pages_seen=1,
            cookiejar=response.meta["cookiejar"],
            poll_try=0,
            empty_pages_seen=0,
        )

    def _xhr_list_request(
        self,
        page: int,
        pages_seen: int,
        cookiejar: int,
        poll_try: int = 0,
        empty_pages_seen: int = 0,
    ):
        headers = {
            "Accept": "*/*",
            "Accept-Language": "en,en-US;q=0.9,de;q=0.8,fr;q=0.7",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.unifr.ch",
            "Referer": f"https://www.unifr.ch/timetable/en/?&page={page}",
            "X-Requested-With": "XMLHttpRequest",
        }

        formdata = {
            "texte": "",
            "jour": "",
            "heure": "",
            "domaines": "",
            "semestres": self.semestres,
            "langues": "",
            "niveaux": "",
            "facultes": "",
            "public": "",
            "viewer": self.VIEWER,
            "page": str(page),
        }

        return scrapy.FormRequest(
            url=self.LIST_ENDPOINT,
            method="POST",
            headers=headers,
            formdata=formdata,
            callback=self.parse_list_xhr,
            errback=self.errback_xhr,
            meta={
                "page": page,
                "pages_seen": pages_seen,
                "cookiejar": cookiejar,
                "poll_try": poll_try,
                "empty_pages_seen": empty_pages_seen,
            },
            dont_filter=True,
        )

    def errback_xhr(self, failure):
        self.logger.error("XHR list request failed: %r", failure)

    def extract_show_ids(self, body: str) -> set[str]:
        """
        The English timetable uses /en/course.html?show=...
        This regex is intentionally tolerant of HTML escaping and absolute URLs.
        """
        patterns = [
            r"(?:course|vorlesungsbeschreibung)\.html\?show=(\d+)",
            r"(?:course|vorlesungsbeschreibung)\.html&amp;show=(\d+)",
            r"show=(\d+)",
            r"show%3D(\d+)",
        ]

        show_ids = set()
        for pattern in patterns:
            show_ids.update(re.findall(pattern, body))

        return show_ids

    def parse_list_xhr(self, response):
        page = response.meta["page"]
        pages_seen = response.meta.get("pages_seen", 1)
        cookiejar = response.meta["cookiejar"]
        poll_try = response.meta.get("poll_try", 0)
        empty_pages_seen = response.meta.get("empty_pages_seen", 0)

        body = response.text or ""

        if "Still loading" in body or "searchForm" in body:
            if poll_try < self.max_poll_tries:
                self.logger.info("XHR page=%s still loading/search form (try %s) -> retry same page", page, poll_try + 1)
                yield self._xhr_list_request(
                    page=page,
                    pages_seen=pages_seen,
                    cookiejar=cookiejar,
                    poll_try=poll_try + 1,
                    empty_pages_seen=empty_pages_seen,
                )
            else:
                self.logger.warning(
                    "XHR page=%s still loading/search form after %s tries. Stopping.",
                    page,
                    poll_try,
                )
            return

        show_ids = self.extract_show_ids(body)
        self.logger.info("XHR page=%s status=%s len(body)=%s found_show_ids=%s", page, response.status, len(body), len(show_ids))

        if not show_ids:
            empty_pages_seen += 1
            self.logger.warning(
                "No show IDs on page %s. empty_pages_seen=%s/%s. First 300 chars: %r",
                page,
                empty_pages_seen,
                self.max_empty_pages,
                body[:300],
            )

            if empty_pages_seen >= self.max_empty_pages:
                self.logger.info("Stopping pagination after %s empty page(s).", empty_pages_seen)
                return

        else:
            empty_pages_seen = 0

            for show_id in sorted(show_ids):
                yield scrapy.Request(
                    url=self.DETAIL_URL.format(show_id=show_id),
                    callback=self.parse_detail,
                    meta={
                        "cookiejar": cookiejar,
                        "list_page_num": page,
                        "show_id": show_id,
                    },
                )

        if self.max_pages and pages_seen >= self.max_pages:
            self.logger.info("Reached max_pages=%s. Stopping.", self.max_pages)
            return

        yield self._xhr_list_request(
            page=page + 1,
            pages_seen=pages_seen + 1,
            cookiejar=cookiejar,
            poll_try=0,
            empty_pages_seen=empty_pages_seen,
        )

    def parse_detail(self, response):
        title = norm(" ".join(response.xpath("//header//h2//text()").getall()))

        code = norm(" ".join(response.xpath("//aside[contains(@class,'inner-30')]//h3[1]//text() | //aside//h3[1]//text()").getall()))
        sidebar_ps = text_list(response.xpath("//aside[contains(@class,'inner-30')]//p//text() | //aside//p//text()"))

        ects = None
        for p in sidebar_ps:
            if "ects" in p.lower():
                ects = parse_float(p)
                break

        tab1 = parse_tab1_teaching(response)
        tab2 = parse_tab2_dates(response)
        tab3 = parse_tab3_assessment(response)
        tab4 = parse_tab4_affiliations(response)

        details = tab1.get("details", {}) or {}
        schedule = tab1.get("schedule", {}) or {}
        teaching = tab1.get("teaching", {}) or {}

        faculty = first_value(details, "Faculty", "Fakultät", "Faculté")
        domain = first_value(details, "Domain", "Bereich", "Domaine")
        course_code_from_details = first_value(details, "Code", "Internal code", "Code interne")
        course_type = first_value(details, "Type of course unit", "Art der Unterrichtseinheit", "Type d'unité d'enseignement")
        course_level_raw = first_value(details, "Course", "Level", "Kursus", "Cours", "Niveau")
        course_levels = split_multi_value(course_level_raw)

        semester_raw = first_value(details, "Semester", "Semestre")
        semesters = parse_semesters(semester_raw)

        lang_raw = first_value(details, "Languages", "Language", "Sprache", "Langue")
        languages = split_multi_value(lang_raw) if lang_raw else None

        item = {
            "course": {
                "code": course_code_from_details or code or None,
                "name": title or None,
                "ects": ects,
                "degree_level_raw": course_level_raw,
                "degree_level": course_levels[0] if course_levels else None,
                "semester_raw": semester_raw,
                "semester": semesters[0] if semesters else None,
                "faculty": faculty,
                "domain": domain,
                "course_type": course_type,
                "languages": languages,
            },
            "details": details,
            "schedule": schedule,
            "teaching": teaching,
            "dates": tab2["rows"],
            "assessment": tab3["sections"],
            "affiliations": tab4["rows"],
            "source": {
                "detail_url": response.url,
                "show_id": response.meta.get("show_id"),
                "list_page_num": response.meta.get("list_page_num"),
                "scrape_started_at": self.scrape_started_at,
            },
        }

        yield item
