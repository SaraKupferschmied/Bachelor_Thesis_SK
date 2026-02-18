import re
import scrapy
from urllib.parse import unquote


ECTS_RE = re.compile(r"(\d+(?:\s*/\s*\d+)*)\s*ects", re.IGNORECASE)


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


class UniFrCurriculaLinksSpider(scrapy.Spider):
    name = "curricula_links_level2_ects"

    start_urls = [
        "https://studies.unifr.ch/en/course-offerings/courses/?ba=1&ma=1&do=1&=undefined"
    ]

    def parse(self, response):
        # Each row describes one programme; each row can have B/M/D links
        for row in response.css("table.studies_list tr"):
            for a in row.css("td.level_link a"):
                level = (a.css("::text").get() or "").strip()  # "B", "M", "D"
                href = a.attrib.get("href")
                if not href or not level:
                    continue

                # Programme name is stored in the "name" attribute on the B/M/D link
                programme = unquote(a.attrib.get("name", "").strip())
                programme_url = response.urljoin(href)

                yield scrapy.Request(
                    programme_url,
                    callback=self.parse_programme_page,
                    cb_kwargs={
                        "programme": programme,
                        "level": level,
                        "programme_url": programme_url,
                    },
                )

    def parse_programme_page(self, response, programme, level, programme_url):
        # --- Curriculum links (unchanged) ---
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

        de = labeled_link("German")
        fr = labeled_link("French")
        en = labeled_link("English")

        unlabeled_hrefs = curriculum_div.xpath(
            ".//a[contains(@class,'inline-fine')][not(following-sibling::i)]/@href"
        ).getall()
        unlabeled_urls = [response.urljoin(h) for h in unlabeled_hrefs]

        curriculum_unspecified = None
        if not any([de, fr, en]) and len(unlabeled_urls) == 1:
            curriculum_unspecified = unlabeled_urls[0]

        # --- NEW: extract ECTS from Structure of studies + Alternatives ---
        # Make headings robust (case-insensitive)
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

        ects_values = []
        ects_values += extract_ects_values(structure_text)
        ects_values += extract_ects_values(alternatives_text)

        # dedupe + stable order
        ects_values = sorted(set(ects_values))

        # If no ECTS found, still emit one entry (ects_points = None)
        if not ects_values:
            ects_values = [None]

        # --- Yield one entry per (programme, level, ects_points) ---
        for ects in ects_values:
            yield {
                "programme": programme,
                "level": level,
                "ects_points": ects,  # <-- NEW
                "programme_url": programme_url,
                "curriculum_de_url": de,
                "curriculum_fr_url": fr,
                "curriculum_en_url": en,
                "curriculum_unspecified_url": curriculum_unspecified,
            }
