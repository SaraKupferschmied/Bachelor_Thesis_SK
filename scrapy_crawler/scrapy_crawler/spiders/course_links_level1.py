'''
This is a first test with scrapy in a simplified version, only extracting the course links for each programme and degree level (B/M/D). It became redundant after curricula_links_level2_ects.py was created, but is kept for demonstration purposes.
'''
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
                level = (a.css("::text").get() or "").strip()   # Find degree level B / M / D
                href = a.attrib.get("href") # Find the link to the course offerings for this degree level

                programme = a.attrib.get("name", "").strip() # Find the name of the programme
                programme = unquote(programme)  # produce a nice Structure without stuff like %20

                if href and level: # produce structured json output
                    yield {
                        "programme": programme,
                        "level": level,
                        "url": response.urljoin(href),
                    }

