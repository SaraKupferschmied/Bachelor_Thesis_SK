import re
import scrapy
from urllib.parse import unquote


ECTS_RE = re.compile(r"(\d+(?:\s*/\s*\d+)*)\s*ects", re.IGNORECASE)
SEMESTERS_RE = re.compile(r"(\d+)\s+semesters?", re.IGNORECASE)

UNIV_BLACKLIST = {
    "universität freiburg",
    "universite de fribourg",
    "université de fribourg",
    "university of fribourg",
}


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


def clean_title(s: str) -> str:
    s = clean_text(s)
    s = re.split(r"\s[|\-–—]\s", s)[0].strip()
    return s


def extract_ects_values(text: str) -> list[int]:
    """
    Extract ECTS numbers from text like:
      - "180 ECTS credits, 6 semesters" -> [180]
      - "(60/30 ECTS credits)"          -> [60, 30]
    Returns unique ints (sorted).
    """
    if not text:
        return []

    values: set[int] = set()

    for m in ECTS_RE.finditer(text):
        raw = m.group(1)
        parts = re.split(r"\s*/\s*", raw.strip())
        for p in parts:
            p = p.strip()
            if p.isdigit():
                values.add(int(p))

    return sorted(values)


def extract_min_semesters(text: str) -> int | None:
    if not text:
        return None
    m = SEMESTERS_RE.search(text)
    return int(m.group(1)) if m else None


def swap_lang(url: str, lang: str) -> str:
    return re.sub(r"/(en|de|fr)/", f"/{lang}/", url, count=1)


def extract_programme_title(response: scrapy.http.Response) -> str | None:
    candidates = []

    xpaths = [
        "//main//h1//text()",
        "//main//h2//text()",
        "//*[@id='content']//h1//text()",
        "//*[@id='content']//h2//text()",
        "//*[contains(@class,'content')]//h1//text()",
        "//*[contains(@class,'content')]//h2//text()",
        "//div[contains(@class,'studies')]//h1//text()",
        "//div[contains(@class,'studies')]//h2//text()",
    ]

    for xp in xpaths:
        txt = " ".join(t.strip() for t in response.xpath(xp).getall() if t.strip())
        txt = clean_title(txt)
        if not txt:
            continue
        if txt.lower() in UNIV_BLACKLIST:
            continue
        candidates.append(txt)

    if candidates:
        candidates = sorted(set(candidates), key=len)
        return candidates[0]

    t = response.xpath("//title/text()").get()
    if t:
        t = clean_title(t)
        if t.lower() not in UNIV_BLACKLIST:
            return t

    return None


def normalize_key(label: str) -> str:
    label = clean_text(label).lower()
    label = label.replace("&", " and ")
    label = re.sub(r"[^a-z0-9]+", "_", label)
    return label.strip("_")


def extract_sidebar_key_points(response: scrapy.http.Response) -> dict:
    """
    Extract the right sidebar 'Key points' blocks as:
      {
        "degree_conferred": "Bachelor of Theology",
        "languages_of_study": "Study in English",
        ...
      }
    """
    sidebar = response.xpath(
        "//h3[normalize-space()='Key points']/ancestor::div[contains(@class,'panel-default')][1]"
    )
    if not sidebar:
        return {}

    data = {}

    blocks = sidebar.xpath(
        ".//div[contains(@class,'inner-10') and contains(@class,'evident-0')][./h4]"
    )

    for block in blocks:
        title = clean_text(" ".join(block.xpath("./h4//text()").getall()))
        if not title:
            continue

        content_div = block.xpath("./div[contains(@class,'inner-10')][1]")
        if content_div:
            content = clean_text(" ".join(content_div.xpath(".//text()").getall()))
        else:
            content = clean_text(
                " ".join(block.xpath("./p//text() | ./div[not(.//h4)]//text()").getall())
            )

        if not content:
            continue

        data[normalize_key(title)] = content

    return data


def extract_contact_info(response: scrapy.http.Response) -> dict:
    """
    Extract contact block into:
      {
        "faculty": ...,
        "department": ...,
        "study_director": ...,
        "contact_mail": ...
      }

    Supported patterns:

    1) With department:
       Faculty
       Department
       Person name
       mail

    2) Without department:
       Faculty
       Person name or authority
       mail
    """
    result = {
        "faculty": None,
        "department": None,
        "study_director": None,
        "contact_mail": None,
    }

    contact_block = response.xpath(
        "//h3[normalize-space()='Contact']/following-sibling::div[contains(@class,'inner-10') and contains(@class,'evident-0')][1]"
    )
    if not contact_block:
        return result

    p = contact_block.xpath(".//p[1]")
    if not p:
        return result

    lines = [clean_text(t) for t in p.xpath("./text()").getall()]
    lines = [t for t in lines if t]

    email = clean_text(p.xpath(".//a[starts-with(@href, 'mailto:')]/text()").get())
    if not email:
        href = p.xpath(".//a[starts-with(@href, 'mailto:')]/@href").get()
        if href:
            email = clean_text(href.replace("mailto:", ""))

    result["contact_mail"] = email or None

    if not lines:
        return result

    # faculty = first line, preferably one containing "faculty"
    faculty = None
    for t in lines:
        if "faculty" in t.lower():
            faculty = t
            break
    if not faculty:
        faculty = lines[0]
    result["faculty"] = faculty

    remaining = lines[1:]

    if not remaining:
        return result

    # Heuristic:
    # if second line looks like a department, treat it as department,
    # then third line as director
    second = remaining[0]
    second_l = second.lower()

    looks_like_department = any(
        marker in second_l
        for marker in [
            "department",
            "departement",
            "département",
            "chair of",
            "institute",
            "institut",
            "school of",
            "seminar",
            "seminar for",
        ]
    )

    if looks_like_department:
        result["department"] = second
        if len(remaining) >= 2:
            result["study_director"] = remaining[1]
    else:
        # no department: second line is already director / office / authority
        result["study_director"] = second

    return result


class UniFrCurriculaLinksSpider(scrapy.Spider):
    name = "curricula_links_level2_enriched"

    start_urls = [
        "https://studies.unifr.ch/en/course-offerings/courses/?ba=1&ma=1&do=1&=undefined"
    ]

    def parse(self, response):
        for row in response.css("table.studies_list tr"):
            for a in row.css("td.level_link a"):
                level = (a.css("::text").get() or "").strip()
                href = a.attrib.get("href")
                if not href or not level:
                    continue

                programme_en = unquote(a.attrib.get("name", "").strip())
                programme_url_en = response.urljoin(href)

                yield scrapy.Request(
                    programme_url_en,
                    callback=self.parse_programme_page_en,
                    cb_kwargs={
                        "programme_url_en": programme_url_en,
                        "programme_name_en": programme_en,
                        "level": level,
                    },
                )

    def parse_programme_page_en(self, response, programme_url_en, programme_name_en, level):
        curriculum_div = response.xpath(
            "//h4[normalize-space()='Curriculum']/following-sibling::div[1]"
        )

        def labeled_link(lang_label: str):
            href = curriculum_div.xpath(
                ".//a[contains(@class,'inline-fine')]"
                "[following-sibling::i[1][contains(., $lang)]]"
                "/@href",
                lang=lang_label,
            ).get()
            return response.urljoin(href) if href else None

        de_pdf = labeled_link("German")
        fr_pdf = labeled_link("French")
        en_pdf = labeled_link("English")

        unlabeled_hrefs = curriculum_div.xpath(
            ".//a[contains(@class,'inline-fine')][not(following-sibling::i)]/@href"
        ).getall()
        unlabeled_urls = [response.urljoin(h) for h in unlabeled_hrefs]

        curriculum_unspecified = None
        if not any([de_pdf, fr_pdf, en_pdf]) and len(unlabeled_urls) == 1:
            curriculum_unspecified = unlabeled_urls[0]

        structure_div = response.xpath(
            "//h4[contains(translate(normalize-space(.),"
            " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),"
            " 'structure of studies')]/following-sibling::div[1]"
        )
        alternatives_div = response.xpath(
            "//h4[contains(translate(normalize-space(.),"
            " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),"
            " 'alternatives')]/following-sibling::div[1]"
        )
        structure_text = clean_text(" ".join(structure_div.xpath(".//text()").getall()))
        alternatives_text = clean_text(" ".join(alternatives_div.xpath(".//text()").getall()))

        ects_values = sorted(
            set(extract_ects_values(structure_text) + extract_ects_values(alternatives_text))
        )
        if not ects_values:
            ects_values = [None]

        min_semesters = extract_min_semesters(structure_text)

        sidebar_key_points = extract_sidebar_key_points(response)
        contact_info = extract_contact_info(response)

        programme_url_de = swap_lang(programme_url_en, "de")
        programme_url_fr = swap_lang(programme_url_en, "fr")

        meta = {
            "programme_url_en": programme_url_en,
            "programme_url_de": programme_url_de,
            "programme_url_fr": programme_url_fr,
            "programme_name_en": programme_name_en,
            "level": level,
            "ects_values": ects_values,
            "min_semesters": min_semesters,
            "sidebar_key_points": sidebar_key_points,
            "faculty": contact_info.get("faculty"),
            "department": contact_info.get("department"),
            "study_director": contact_info.get("study_director"),
            "contact_mail": contact_info.get("contact_mail"),
            "curriculum_de_url": de_pdf,
            "curriculum_fr_url": fr_pdf,
            "curriculum_en_url": en_pdf,
            "curriculum_unspecified_url": curriculum_unspecified,
        }

        yield scrapy.Request(
            programme_url_de,
            callback=self.parse_programme_page_de,
            cb_kwargs={"meta": meta},
        )

    def parse_programme_page_de(self, response, meta):
        meta["programme_name_de"] = extract_programme_title(response)
        yield scrapy.Request(
            meta["programme_url_fr"],
            callback=self.parse_programme_page_fr,
            cb_kwargs={"meta": meta},
        )

    def parse_programme_page_fr(self, response, meta):
        meta["programme_name_fr"] = extract_programme_title(response)

        if not meta.get("programme_name_de") or meta["programme_name_de"].lower() in UNIV_BLACKLIST:
            meta["programme_name_de"] = meta.get("programme_name_en")

        if not meta.get("programme_name_fr") or meta["programme_name_fr"].lower() in UNIV_BLACKLIST:
            meta["programme_name_fr"] = meta.get("programme_name_en")

        for ects in meta["ects_values"]:
            yield {
                "programme_name_en": meta.get("programme_name_en"),
                "programme_name_de": meta.get("programme_name_de"),
                "programme_name_fr": meta.get("programme_name_fr"),

                "programme": meta.get("programme_name_en"),
                "level": meta["level"],
                "ects_points": ects,
                "min_semesters": meta.get("min_semesters"),

                "studyplan_metadata": meta.get("sidebar_key_points") or {},
                "faculty": meta.get("faculty"),
                "department": meta.get("department"),
                "study_director": meta.get("study_director"),
                "contact_mail": meta.get("contact_mail"),

                "programme_url_en": meta["programme_url_en"],
                "programme_url_de": meta["programme_url_de"],
                "programme_url_fr": meta["programme_url_fr"],

                "programme_url": meta["programme_url_en"],
                "curriculum_de_url": meta["curriculum_de_url"],
                "curriculum_fr_url": meta["curriculum_fr_url"],
                "curriculum_en_url": meta["curriculum_en_url"],
                "curriculum_unspecified_url": meta["curriculum_unspecified_url"],
            }