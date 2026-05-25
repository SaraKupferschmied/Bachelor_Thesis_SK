import re
from urllib.parse import quote, urlsplit, urlunsplit

import scrapy

DOC_EXT_RE = re.compile(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx)\b", re.IGNORECASE)


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    path = quote(parts.path, safe="/:@-._~!$&'()*+,;=")
    query = quote(parts.query, safe="=&?/:@-._~!$&'()*+,;=")
    fragment = quote(parts.fragment, safe="-._~")
    return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))


def clean_text(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def is_doc_url(url: str) -> bool:
    return bool(DOC_EXT_RE.search(url or ""))


class UnifrInterfacultyStudyplansSpider(scrapy.Spider):
    name = "unifr_interfaculty_studyplans"

    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 0.5,
        "FEED_EXPORT_ENCODING": "utf-8",
        "LOG_LEVEL": "INFO",
        # Keep disabled so items never get dropped
        "ITEM_PIPELINES": {},
        # Helps avoid bot-different HTML sometimes
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
    }

    def start_requests(self):
        programs = [
            {"page_url": "https://studies.unifr.ch/de/master/int/digitalsociety"},
            {"page_url": "https://studies.unifr.ch/de/master/int/familystudies"},
            {"page_url": "https://studies.unifr.ch/de/master/int/islamsociety"},
        ]
        for p in programs:
            yield scrapy.Request(p["page_url"], callback=self.parse_program)

    def parse_program(self, response):
        title = (
            clean_text(response.css("h2::text").get())
            or clean_text(" ".join(response.css("h2 *::text").getall()))
        )

        # Find Studienplan link on the studies.unifr.ch page (often /go/..)
        studienplan_href = response.xpath(
            '//h4[contains(normalize-space(.), "Studienplan")]/following::a[1]/@href'
        ).get()

        if not studienplan_href:
            studienplan_href = response.xpath(
                '//a[contains(translate(normalize-space(.), "STUDIENPLAN", "studienplan"), "studienplan")]/@href'
            ).get()

        documents = []
        file_urls = []

        if studienplan_href:
            sp_url = safe_url(response.urljoin(studienplan_href))
            # always store the intermediate link (useful provenance)
            documents.append({"url": sp_url, "label": "Studienplan (Webseite)"})

            # If it's already a PDF, done
            if is_doc_url(sp_url):
                documents[-1]["label"] = "Studienplan"
                file_urls.append(sp_url)
                yield {
                    "faculty": "INTERFACULTY",
                    "lang": "de",
                    "title": title,
                    "page_url": response.url,
                    "documents": documents,
                    "file_urls": sorted(set(file_urls)),
                }
                return

            # Otherwise follow one level deeper to resolve /go/.. and scrape PDFs there
            yield response.follow(
                sp_url,
                callback=self.parse_studienplan_target,
                meta={
                    "faculty": "INTERFACULTY",
                    "lang": "de",
                    "title": title,
                    "page_url": response.url,
                    "documents": documents,
                },
                dont_filter=True,
            )
        else:
            # No Studienplan link at all
            yield {
                "faculty": "INTERFACULTY",
                "lang": "de",
                "title": title,
                "page_url": response.url,
                "documents": [],
                "file_urls": [],
            }

    def parse_studienplan_target(self, response):
        """
        We are now on the resolved target page (faculty site like human-ist.unifr.ch or unifr.ch/szig)
        Extract direct document links from here.
        """
        title = response.meta["title"]
        documents = list(response.meta.get("documents", []))
        file_urls = []

        # If the resolved URL itself is a doc, store it
        if is_doc_url(response.url):
            url = safe_url(response.url)
            documents.append({"url": url, "label": "Studienplan"})
            file_urls.append(url)
        else:
            # Otherwise collect all doc links on the page
            for a in response.css("a[href]"):
                href = (a.attrib.get("href") or "").strip()
                if not href:
                    continue
                url = safe_url(response.urljoin(href))
                if not is_doc_url(url):
                    continue

                label = clean_text(" ".join(a.css("::text").getall())) or "Dokument"
                documents.append({"url": url, "label": label})
                file_urls.append(url)

        item = {
            "faculty": response.meta["faculty"],
            "lang": response.meta["lang"],
            "title": title,
            "page_url": response.meta["page_url"],
            "documents": documents,
            "file_urls": sorted(set(file_urls)),
        }

        self.logger.info("Yielding item: %s | files=%d", title, len(item["file_urls"]))
        yield item
