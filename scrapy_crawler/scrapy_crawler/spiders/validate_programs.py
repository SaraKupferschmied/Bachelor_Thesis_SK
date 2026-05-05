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
    s = re.split(r"\s[|\-–—]\s", s)[0].strip()
    return s


def extract_ects_values(text: str) -> list[int]:
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


def normalize_level(level: str) -> str:
    level = (level or "").strip().lower()

    if "bachelor" in level:
        return "bachelor"
    if "master" in level:
        return "master"
    if "doctor" in level:
        return "doctorate"

    return level


def split_main_and_variant_ects(
    level: str,
    structure_text: str,
    alternatives_text: str,
) -> tuple[int | None, list[int]]:
    level_norm = normalize_level(level)

    if level_norm == "doctorate":
        return None, []

    structure_ects = extract_ects_values(structure_text)
    alternatives_ects = extract_ects_values(alternatives_text)

    main_ects = max(structure_ects) if structure_ects else None

    variants = sorted(
        ects
        for ects in set(alternatives_ects)
        if ects != main_ects
    )

    return main_ects, variants


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

    title = response.xpath("//title/text()").get()
    if title:
        title = clean_title(title)
        if title.lower() not in UNIV_BLACKLIST:
            return title

    return None


class ExpectedProgramsSpider(scrapy.Spider):
    name = "expected_programs"

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

                programme_name_en = unquote(a.attrib.get("name", "").strip())
                programme_url_en = response.urljoin(href)

                yield scrapy.Request(
                    programme_url_en,
                    callback=self.parse_programme_page_en,
                    cb_kwargs={
                        "programme_url_en": programme_url_en,
                        "programme_name_en": programme_name_en,
                        "level": level,
                    },
                )

    def parse_programme_page_en(self, response, programme_url_en, programme_name_en, level):
        structure_div = response.xpath(
            "//h4[contains("
            "translate(normalize-space(.), "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
            "'abcdefghijklmnopqrstuvwxyz'), "
            "'structure of studies'"
            ")]/following-sibling::div[1]"
        )

        alternatives_div = response.xpath(
            "//h4[contains("
            "translate(normalize-space(.), "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
            "'abcdefghijklmnopqrstuvwxyz'), "
            "'alternatives'"
            ")]/following-sibling::div[1]"
        )

        structure_text = " ".join(structure_div.xpath(".//text()").getall())
        alternatives_text = " ".join(alternatives_div.xpath(".//text()").getall())

        main_ects, variant_ects = split_main_and_variant_ects(
            level=level,
            structure_text=structure_text,
            alternatives_text=alternatives_text,
        )

        programme_url_de = swap_lang(programme_url_en, "de")
        programme_url_fr = swap_lang(programme_url_en, "fr")

        meta = {
            "name": programme_name_en,
            "level": normalize_level(level),
            "url": programme_url_en,
            "programme_url_en": programme_url_en,
            "programme_url_de": programme_url_de,
            "programme_url_fr": programme_url_fr,
            "main_ects": main_ects,
            "variant_ects": variant_ects,
            "programme_name_en": programme_name_en,
        }

        yield scrapy.Request(
            programme_url_de,
            callback=self.parse_programme_page_de,
            cb_kwargs={"meta": meta},
            dont_filter=True,
        )

    def parse_programme_page_de(self, response, meta):
        meta["programme_name_de"] = extract_programme_title(response)

        yield scrapy.Request(
            meta["programme_url_fr"],
            callback=self.parse_programme_page_fr,
            cb_kwargs={"meta": meta},
            dont_filter=True,
        )

    def parse_programme_page_fr(self, response, meta):
        meta["programme_name_fr"] = extract_programme_title(response)

        if not meta.get("programme_name_de") or meta["programme_name_de"].lower() in UNIV_BLACKLIST:
            meta["programme_name_de"] = meta.get("programme_name_en")

        if not meta.get("programme_name_fr") or meta["programme_name_fr"].lower() in UNIV_BLACKLIST:
            meta["programme_name_fr"] = meta.get("programme_name_en")

        yield {
            "name": meta.get("name"),
            "level": meta.get("level"),
            "ects": {
                "main": meta.get("main_ects"),
                "variants": meta.get("variant_ects", []),
            },
            "programme_name_en": meta.get("programme_name_en"),
            "programme_name_de": meta.get("programme_name_de"),
            "programme_name_fr": meta.get("programme_name_fr"),
            "url": meta.get("url"),
            "programme_url_en": meta.get("programme_url_en"),
            "programme_url_de": meta.get("programme_url_de"),
            "programme_url_fr": meta.get("programme_url_fr"),
        }