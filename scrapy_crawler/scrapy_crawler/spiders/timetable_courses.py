import re
import scrapy

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def parse_float(s: str):
    if not s:
        return None
    s = s.replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None

def text_list(sel):
    return [norm(x) for x in sel.getall() if norm(x)]

def string_of(sel):
    """string(.) of selector (safe)"""
    if not sel:
        return ""
    return norm(sel.xpath("string(.)").get())

def tab_block(response, tab_id: str):
    # ✅ correct attribute: data-accordion-content
    return response.xpath(f"//div[@data-accordion-content='{tab_id}']")

def parse_2col_table(tbl_sel):
    """
    Parses a 2-col table (label/value) into dict.
    Works for <table class="table-condensed"> etc.
    """
    out = {}
    for tr in tbl_sel.xpath(".//tr"):
        key = norm(" ".join(tr.xpath("./td[1]//text()").getall()))
        val = norm(" ".join(tr.xpath("./td[2]//text()").getall()))
        if key:
            out[key] = val or None
    return out

def parse_people_ul(td_sel):
    """
    Handles cases like:
      <td><ul class='liprof'><li>...</li></ul></td>
    """
    names = td_sel.xpath(".//li//text()").getall()
    names = [norm(x) for x in names if norm(x)]
    if names:
        return names
    # fallback: any text
    t = norm(" ".join(td_sel.xpath(".//text()").getall()))
    return [t] if t else []

def parse_tab1_unterricht(response):
    """
    Tab-1 contains multiple <h3> sections, each followed by a table.
    We parse:
      - Details (2-col)
      - Zeitplan und Räume (2-col)
      - Unterricht (2-col; some values are list-of-people)
    """
    block = tab_block(response, "tab-1")
    if not block:
        return {"sections": [], "details": {}, "schedule": {}, "teaching": {}, "raw_text": ""}

    details = {}
    schedule = {}
    teaching = {}

    # For each h3 in tab-1, take the first following table within the same block
    h3s = block.xpath(".//h3")
    for h3 in h3s:
        title = norm(" ".join(h3.xpath(".//text()").getall()))
        tbl = h3.xpath("following-sibling::table[1] | following-sibling::*[1]//table[1]")
        # ensure table is still inside tab-1 block
        if not tbl or not tbl.xpath("ancestor::div[@data-accordion-content='tab-1']"):
            continue


        kv = parse_2col_table(tbl)

        # special handling for Unterricht lists (Verantwortliche / Dozenten-innen)
        # These rows have <ul class='liprof'>...
        if title.lower().startswith("unterricht"):
            for tr in tbl.xpath(".//tr"):
                key = norm(" ".join(tr.xpath("./td[1]//text()").getall()))
                td2 = tr.xpath("./td[2]")
                if not key:
                    continue
                # if there are li entries, store list
                li = td2.xpath(".//li")
                if li:
                    teaching[key] = parse_people_ul(td2)
                else:
                    val = norm(" ".join(td2.xpath(".//text()").getall()))
                    teaching[key] = val or None

        elif title.lower().startswith("details"):
            details = kv

        elif title.lower().startswith("zeitplan"):
            schedule = kv

    return {
        "details": details,
        "schedule": schedule,
        "teaching": teaching,
    }

def parse_tab2_dates(response):
    """
    Tab-2: table with headers Datum / Zeit / Art / Ort
    """
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
    """
    Tab-3: has one or more sections like:
      <h3>Schriftliche Arbeit ...</h3>
      <table class="table-condensed">...</table>
    """
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
    """
    Tab-4: table with 1 column, each row contains:
      - <strong>Studienplan</strong>
      - <small><strong>Version: ...</strong></small>
      - <div class="...">Modulpfad</div>
    """
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
        # version looks like "Version: SA16_BA..."
        version = version.replace("Version:", "").strip() if version else None

        path = norm(" ".join(td.xpath(".//div[contains(@class,'bg-grey-light')]//text()").getall())) or None

        if study_plan:
            out.append({"study_plan": study_plan, "version": version, "path": path})

    return {"rows": out}

class TimetableCoursesSpider(scrapy.Spider):
    """
    Crawl per XHR endpoint (no Playwright, no cookies):
      - POST https://www.unifr.ch/timetable/assets/components/timetable/connector.php?action=getlist
      - parse returned HTML fragment for show=IDs
      - then GET detail pages /vorlesungsbeschreibung.html?show=...

    Run:
      scrapy crawl timetable_courses -a start_page=1 -a max_pages=3 -a semestres="252,253,254,255" -O out.json
    """
    name = "timetable_courses"

    custom_settings = {
        "DOWNLOAD_DELAY": 0.7,
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    }

    LIST_ENDPOINT = "https://www.unifr.ch/timetable/assets/components/timetable/connector.php?action=getlist"
    VIEWER = "//www.unifr.ch/timetable/de/vorlesungsbeschreibung.html"

    def __init__(self, start_page=1, max_pages=0, semestres="252,253,254,255", **kwargs):
        super().__init__(**kwargs)
        self.start_page = int(start_page)
        self.max_pages = int(max_pages)
        self.semestres = (semestres or "").strip()

    # ---------------------------
    # LIST (XHR) REQUESTS
    # ---------------------------
    def start_requests(self):
        # 1) Seite einmal laden -> Session Cookie bekommen
        url = f"https://www.unifr.ch/timetable/de/?&page={self.start_page}"
        yield scrapy.Request(
            url=url,
            callback=self._after_bootstrap,
            meta={"cookiejar": 1},
            dont_filter=True,
        )

    def _after_bootstrap(self, response):
        # 2) Dann XHR mit derselben Cookie-Session
        yield self._xhr_list_request(page=self.start_page, pages_seen=1, cookiejar=response.meta["cookiejar"])


    def _xhr_list_request(self, page: int, pages_seen: int, cookiejar: int, poll_try: int = 0):
        headers = {
            "Accept": "*/*",
            "Accept-Language": "de,de-DE;q=0.9,en;q=0.8",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.unifr.ch",
            "Referer": f"https://www.unifr.ch/timetable/de/?&page={page}",
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
            },
            dont_filter=True,
        )


    def errback_xhr(self, failure):
        self.logger.error("XHR list request failed: %r", failure)

    def parse_list_xhr(self, response):
        page = response.meta["page"]
        pages_seen = response.meta.get("pages_seen", 1)
        cookiejar = response.meta["cookiejar"]
        poll_try = response.meta.get("poll_try", 0)

        body = response.text or ""

        # Polling: Server ist noch nicht ready
        if "Still loading" in body or "searchForm" in body:
            if poll_try < 10:  # max ~10 Versuche
                self.logger.info(
                    "XHR page=%s still loading (try %s) -> retry",
                    page, poll_try + 1
                )
                yield self._xhr_list_request(
                    page=page,
                    pages_seen=pages_seen,
                    cookiejar=cookiejar,
                    poll_try=poll_try + 1
                )
            else:
                self.logger.warning(
                    "XHR page=%s still loading after %s tries. Give up.",
                    page, poll_try
                )
            return

        self.logger.info("XHR LIST page=%s status=%s len(body)=%s", page, response.status, len(body))

        show_ids = set(re.findall(r"vorlesungsbeschreibung\.html\?show=(\d+)", body))
        self.logger.info("XHR page %s: found %s show IDs", page, len(show_ids))

        if not show_ids:
            self.logger.warning("No show IDs on page %s. First 300 chars: %r", page, body[:300])
            return

        for show_id in sorted(show_ids):
            detail_url = f"https://www.unifr.ch/timetable/de/vorlesungsbeschreibung.html?show={show_id}"
            yield scrapy.Request(
                url=detail_url,
                callback=self.parse_detail,
                meta={
                    "list_page_url": f"XHR:{self.LIST_ENDPOINT}",
                    "list_page_num": page,
                    "cookiejar": cookiejar,  # optional, aber schadet nicht
                },
            )

        # Pagination
        if self.max_pages and pages_seen >= self.max_pages:
            return

        next_page = page + 1
        yield self._xhr_list_request(
            page=next_page,
            pages_seen=pages_seen + 1,
            cookiejar=cookiejar,
            poll_try=0
        )


    # ---------------------------
    # DETAIL 
    # ---------------------------
    def parse_detail(self, response):
        title = norm(" ".join(response.xpath("//header//h2//text()").getall()))

        # Sidebar basics
        code = norm(" ".join(response.xpath("//aside[contains(@class,'inner-30')]//h3[1]//text()").getall()))
        sidebar_ps = [norm(x) for x in response.xpath("//aside[contains(@class,'inner-30')]//p//text()").getall()]
        sidebar_ps = [x for x in sidebar_ps if x]

        degree_level = None
        semester = None
        ects = None

        for p in sidebar_ps:
            pl = p.lower()
            if pl in {"bachelor", "master", "doctorat", "doktorat", "doctorate"}:
                if pl.startswith("bach"):
                    degree_level = "Bachelor"
                elif pl.startswith("mast"):
                    degree_level = "Master"
                else:
                    degree_level = "Doctorate"
            if "ects" in pl:
                ects = parse_float(p)
            if re.match(r"^(FS|HS)-\d{4}$", p):
                semester = p

        # --- Tabs ---
        tab1 = parse_tab1_unterricht(response)
        tab2 = parse_tab2_dates(response)
        tab3 = parse_tab3_assessment(response)
        tab4 = parse_tab4_affiliations(response)

        # extracted fields from Details table
        details = tab1.get("details", {}) or {}
        faculty = details.get("Fakultät") or details.get("Faculté")
        domain = details.get("Bereich") or details.get("Domaine")

        # languages: Details row "Sprachen" might contain multiple languages in text
        lang_raw = details.get("Sprachen") or details.get("Langues")
        languages = None
        if lang_raw:
            # split safely: sometimes "Französisch Deutsch" or "Französisch, Deutsch"
            parts = re.split(r"[,\n/]+", lang_raw)
            parts = [norm(x) for x in parts if norm(x)]
            languages = parts or [lang_raw]

        # room/time information: from Zeitplan und Räume section
        schedule = tab1.get("schedule", {}) or {}
        lecture_times = schedule.get("Vorlesungszeiten")

        # professors: from Unterricht section
        teaching = tab1.get("teaching", {}) or {}
        responsible = teaching.get("Verantwortliche")  # list or string
        lecturers = teaching.get("Dozenten-innen") or teaching.get("Dozentinnen und Dozenten")  # site variants

        item = {
            "source": {
                "type": "timetable",
                "list_page_url": response.meta.get("list_page_url"),
                "list_page_num": response.meta.get("list_page_num"),
                "detail_page_url": response.url,
            },
            "course": {
                "code": code or None,
                "name": title or None,
                "ects": ects,
                "degree_level": degree_level,
                "semester": semester,
            },
            
            # canonical tables
            "details": details,
            "schedule": schedule,
            "teaching": teaching,

            # structured tabs
            "einzeltermine_raeume": tab2["rows"],
            "leistungskontrolle": tab3["sections"],
            "zuordnung": tab4["rows"],
            
        }

        if not item["course"]["code"]:
            item["warnings"] = ["missing_course_code_in_sidebar"]

        yield item
