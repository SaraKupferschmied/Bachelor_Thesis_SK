import scrapy
import re

class TimetableCoursesSpiderEn(scrapy.Spider):
    name = "timetable_courses_en"

    custom_settings = {
        "DOWNLOAD_DELAY": 0.7,
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "USER_AGENT": "Mozilla/5.0",
    }

    LIST_ENDPOINT = "https://www.unifr.ch/timetable/assets/components/timetable/connector.php?action=getlist"
    VIEWER = "//www.unifr.ch/timetable/en/course.html"

    def __init__(self, start_page=1, max_pages=0, semestres="252,253,254,255", **kwargs):
        super().__init__(**kwargs)
        self.start_page = int(start_page)
        self.max_pages = int(max_pages)
        self.semestres = (semestres or "").strip()
        self.scrape_started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ---------------------------
    # START
    # ---------------------------
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
            cookiejar=response.meta["cookiejar"]
        )

    def _xhr_list_request(self, page, pages_seen, cookiejar, poll_try=0):
        headers = {
            "Accept": "*/*",
            "Accept-Language": "en,en-US;q=0.9",
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
            meta={
                "page": page,
                "pages_seen": pages_seen,
                "cookiejar": cookiejar,
                "poll_try": poll_try,
            },
            dont_filter=True,
        )

    # ---------------------------
    # LIST PARSING
    # ---------------------------
    def parse_list_xhr(self, response):
        page = response.meta["page"]
        cookiejar = response.meta["cookiejar"]

        show_ids = set(re.findall(r"course\.html\?show=(\d+)", response.text))

        for show_id in show_ids:
            yield scrapy.Request(
                url=f"https://www.unifr.ch/timetable/en/course.html?show={show_id}",
                callback=self.parse_detail,
                meta={"cookiejar": cookiejar, "list_page_num": page},
            )

        if not self.max_pages or page < self.max_pages:
            yield self._xhr_list_request(page + 1, page + 1, cookiejar)

    # ---------------------------
    # DETAIL
    # ---------------------------
    def parse_detail(self, response):
        title = norm(" ".join(response.xpath("//header//h2//text()").getall()))

        code = norm(" ".join(response.xpath("//aside//h3[1]//text()").getall()))
        sidebar_ps = text_list(response.xpath("//aside//p//text()"))

        ects = None
        for p in sidebar_ps:
            if "ects" in p.lower():
                ects = parse_float(p)

        tab1 = parse_tab1_unterricht(response)
        tab2 = parse_tab2_dates(response)
        tab3 = parse_tab3_assessment(response)
        tab4 = parse_tab4_affiliations(response)

        details = tab1.get("details", {}) or {}

        # ---------------------------
        # ENGLISH FIELD MAPPING
        # ---------------------------
        faculty = details.get("Faculty")
        domain = details.get("Domain")

        course_type = details.get("Type of course unit")

        course_level_raw = (
            details.get("Course")
            or details.get("Level")
        )

        course_levels = split_multi_value(course_level_raw)

        semester_raw = details.get("Semester")
        semesters = parse_semesters(semester_raw)

        lang_raw = details.get("Languages")
        languages = split_multi_value(lang_raw) if lang_raw else None

        schedule = tab1.get("schedule", {}) or {}
        lecture_times = schedule.get("Lecture times")

        teaching = tab1.get("teaching", {}) or {}
        responsible = teaching.get("Responsible")
        lecturers = teaching.get("Lecturers")

        item = {
            "course": {
                "code": code or None,
                "name": title,
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
        }

        yield item