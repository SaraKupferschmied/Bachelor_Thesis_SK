import scrapy
from urllib.parse import unquote


class UniFrCurriculaLinksSpider(scrapy.Spider):
    name = "curricula_links_level2"

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
        # Grab the curriculum block (the first div after the Curriculum h4)
        curriculum_div = response.xpath("//h4[normalize-space()='Curriculum']/following-sibling::div[1]")

        def labeled_link(lang_label: str) -> str | None:
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

        # Fallback: links without any language label (e.g., only one link shown)
        unlabeled_hrefs = curriculum_div.xpath(
            ".//a[contains(@class,'inline-fine')][not(following-sibling::i)]/@href"
        ).getall()
        unlabeled_urls = [response.urljoin(h) for h in unlabeled_hrefs]

        # If no labeled links exist but there is exactly one unlabeled curriculum link,
        # store it as "unspecified"
        curriculum_unspecified = None
        if not any([de, fr, en]) and len(unlabeled_urls) == 1:
            curriculum_unspecified = unlabeled_urls[0]

        yield {
            "programme": programme,
            "level": level,
            "programme_url": programme_url,
            "curriculum_de_url": de,
            "curriculum_fr_url": fr,
            "curriculum_en_url": en,
            "curriculum_unspecified_url": curriculum_unspecified,
        }
