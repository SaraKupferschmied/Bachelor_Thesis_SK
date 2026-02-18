import scrapy
from urllib.parse import unquote

class UniFrLinksSpider(scrapy.Spider):
    name = "course_links_level1"
    start_urls = [
        "https://studies.unifr.ch/en/course-offerings/courses/?ba=1&ma=1&do=1&=undefined"
    ]

    def parse(self, response):
        for row in response.css("table.studies_list tr"):
            for a in row.css("td.level_link a"):
                level = (a.css("::text").get() or "").strip()   # B / M / D
                href = a.attrib.get("href")

                programme = a.attrib.get("name", "").strip()
                programme = unquote(programme)  # turns Theological%20Studies -> Theological Studies

                if href and level:
                    yield {
                        "programme": programme,
                        "level": level,
                        "url": response.urljoin(href),
                    }

