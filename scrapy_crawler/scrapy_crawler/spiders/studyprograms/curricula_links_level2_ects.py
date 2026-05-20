import re
import scrapy
from urllib.parse import unquote


ECTS_RE = re.compile(r"(\d+(?:\s*/\s*\d+)*)\s*ects", re.IGNORECASE)

UNIV_BLACKLIST = {
    "universität freiburg",
    "universite de fribourg",
    "université de fribourg",
    "university of fribourg",
}

def clean_title(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    # remove common separators if they appear
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
        raw = m.group(1)  # e.g. "60/30" or "180"
        parts = re.split(r"\s*/\s*", raw.strip())
        for p in parts:
            p = p.strip()
            if p.isdigit():
                values.add(int(p))

    return sorted(values)

def swap_lang(url: str, lang: str) -> str:
    # expects .../en/... etc
    return re.sub(r"/(en|de|fr)/", f"/{lang}/", url, count=1)

def extract_programme_title(response: scrapy.http.Response) -> str | None:
    # Try multiple robust selectors
    # Prefer headings inside main content
    candidates = []

    xpaths = [
        # common content containers
        "//main//h1//text()",
        "//main//h2//text()",
        "//*[@id='content']//h1//text()",
        "//*[@id='content']//h2//text()",
        "//*[contains(@class,'content')]//h1//text()",
        "//*[contains(@class,'content')]//h2//text()",
        # sometimes the actual programme title is in a specific block
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

    # If we got multiple, pick the shortest non-university one (often the programme name)
    if candidates:
        candidates = sorted(set(candidates), key=len)
        return candidates[0]

    # Last resort: <title> (but often contains university name)
    t = response.xpath("//title/text()").get()
    if t:
        t = clean_title(t)
        if t.lower() not in UNIV_BLACKLIST:
            return t

    return None

class UniFrCurriculaLinksSpider(scrapy.Spider):
    name = "curricula_links_level2_ects"

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
        # Curriculum links
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

        # ECTS extraction
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
        structure_text = " ".join(structure_div.xpath(".//text()").getall())
        alternatives_text = " ".join(alternatives_div.xpath(".//text()").getall())

        ects_values = sorted(set(extract_ects_values(structure_text) + extract_ects_values(alternatives_text)))
        if not ects_values:
            ects_values = [None]

        # Fetch DE + FR pages to get localized titles
        programme_url_de = swap_lang(programme_url_en, "de")
        programme_url_fr = swap_lang(programme_url_en, "fr")

        meta = {
            "programme_url_en": programme_url_en,
            "programme_url_de": programme_url_de,
            "programme_url_fr": programme_url_fr,
            "programme_name_en": programme_name_en,
            "level": level,
            "ects_values": ects_values,
            "curriculum_de_url": de_pdf,
            "curriculum_fr_url": fr_pdf,
            "curriculum_en_url": en_pdf,
            "curriculum_unspecified_url": curriculum_unspecified,
        }

        # Chain: fetch DE, then FR, then yield items
        yield scrapy.Request(programme_url_de, callback=self.parse_programme_page_de, cb_kwargs={"meta": meta})

    def parse_programme_page_de(self, response, meta):
        meta["programme_name_de"] = extract_programme_title(response)
        yield scrapy.Request(meta["programme_url_fr"], callback=self.parse_programme_page_fr, cb_kwargs={"meta": meta})

    def parse_programme_page_fr(self, response, meta):
        meta["programme_name_fr"] = extract_programme_title(response)

        if not meta.get("programme_name_de") or meta["programme_name_de"].lower() in UNIV_BLACKLIST:
            meta["programme_name_de"] = meta.get("programme_name_en")

        if not meta.get("programme_name_fr") or meta["programme_name_fr"].lower() in UNIV_BLACKLIST:
            meta["programme_name_fr"] = meta.get("programme_name_en")

        for ects in meta["ects_values"]:
            yield {
                # names in 3 languages
                "programme_name_en": meta.get("programme_name_en"),
                "programme_name_de": meta.get("programme_name_de"),
                "programme_name_fr": meta.get("programme_name_fr"),

                # keep your existing "programme" for backward compatibility
                "programme": meta.get("programme_name_en"),

                "level": meta["level"],
                "ects_points": ects,

                # urls in 3 languages (optional but super useful)
                "programme_url_en": meta["programme_url_en"],
                "programme_url_de": meta["programme_url_de"],
                "programme_url_fr": meta["programme_url_fr"],

                "programme_url": meta["programme_url_en"],  # backward compatible
                "curriculum_de_url": meta["curriculum_de_url"],
                "curriculum_fr_url": meta["curriculum_fr_url"],
                "curriculum_en_url": meta["curriculum_en_url"],
                "curriculum_unspecified_url": meta["curriculum_unspecified_url"],
            }