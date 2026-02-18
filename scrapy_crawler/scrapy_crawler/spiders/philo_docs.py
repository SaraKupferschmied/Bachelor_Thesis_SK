import re
from urllib.parse import urlsplit, urlunsplit, quote
import scrapy


DOC_EXT_RE = re.compile(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx)\b", re.IGNORECASE)

PLAN_TEXT_KEYS = [
    "studienplan",
    "plan d'études",
    "plan d’etudes",
    "plan detudes",
    "study plan",
]


def clean_text(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def lower_norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def safe_url(url: str) -> str:
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%:@")
    query = quote(parts.query, safe="=&%:@/?")
    fragment = quote(parts.fragment, safe="")
    return urlunsplit((parts.scheme, parts.netloc, path, query, fragment))


def abs_href(response, href: str) -> str:
    return safe_url(response.urljoin(href))


def is_doc_href(url: str) -> bool:
    return bool(DOC_EXT_RE.search(url or ""))

def classify_doc_level(label: str | None, url: str) -> str | None:
    t = lower_norm(f"{label or ''} {url}")

    # strong master indicators
    if re.search(r"\b(ma|master|vertief|vp|pa|p2|ps)\b", t):
        return "master"

    # strong bachelor indicators
    if re.search(r"\b(ba|bachelor|bereich\s*i|bereich\s*ii|basi|ldsi)\b", t):
        return "bachelor"

    return None

def find_alt_lang_urls(response) -> dict:
    out = {}
    for link in response.css('link[rel="alternate"][hreflang]'):
        lang = (link.attrib.get("hreflang") or "").strip().lower()
        href = link.attrib.get("href")
        if not href:
            continue
        if href.startswith("//"):
            href = "https:" + href
        out[lang] = safe_url(href)
    return out


class UnifrPhilStudyPlansSpider(scrapy.Spider):
    name = "unifr_phil_studyplans"

    start_urls = ["https://www.unifr.ch/lettres/de/"]

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1,
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    def parse(self, response):
        # Find Studium link
        studium_href = (
            response.css('nav.push-menu a.deeper:contains("Studium")::attr(href)').get()
            or response.css('a:contains("Studium")::attr(href)').get()
            or response.css('a[href*="studium"]::attr(href)').get()
        )
        if not studium_href:
            self.logger.warning("Could not find Studium link on %s", response.url)
            return

        yield response.follow(studium_href, callback=self.parse_studium)

    def parse_studium(self, response):
        """
        From Studium page: find Bachelor link in left menu and crawl that.
        Then we’ll discover Master from the Bachelor page menu.
        """
        ba_url = self._find_level_link(response, want="bachelor")
        if not ba_url:
            self.logger.warning("Could not find Bachelor link on %s", response.url)
            return

        yield scrapy.Request(ba_url, callback=self.parse_level_page, meta={"level": "bachelor", "crawl_master": True})

    def _find_level_link(self, response, want: str) -> str | None:
        """
        Robustly find 'Bachelor' or 'Master' page from the left sub-menu.
        """
        want = want.lower()
        anchors = response.css("div.sub-menu a, main#main a, a")

        best = None
        best_score = 0

        for a in anchors:
            href = a.attrib.get("href")
            if not href:
                continue
            text = clean_text(" ".join(a.css("::text").getall())) or ""
            t = lower_norm(text)
            u = abs_href(response, href)
            p = urlsplit(u).path.lower()

            score = 0
            if want == "bachelor":
                if "bachelor" in t:
                    score += 100
                if "/ba" in p or "/bachelor" in p:
                    score += 30
            if want == "master":
                if "master" in t:
                    score += 100
                if "/ma" in p or "/master" in p:
                    score += 30

            # keep only studium-ish subtree if possible
            if "studium" not in p and score < 100:
                continue

            if score > best_score:
                best_score = score
                best = u

        return best

    def parse_level_page(self, response):
        """
        Bachelor/Master page:
        Parse Studienangebot accordion and follow the *Studienplan* link per entry.
        Also discover & schedule Master (once) from the Bachelor page menu.
        """
        level = response.meta.get("level")

        # ✅ From Bachelor page, schedule Master by reading the same left menu
        if response.meta.get("crawl_master"):
            ma_url = self._find_level_link(response, want="master")
            if ma_url:
                yield scrapy.Request(ma_url, callback=self.parse_level_page, meta={"level": "master", "crawl_master": False})
            else:
                self.logger.warning("Master link not found from Bachelor page menu: %s", response.url)

        # Accordion entries
        lis = response.css("main#main ul.accordion li")
        if not lis:
            lis = response.css("ul.accordion li")

        if not lis:
            self.logger.warning("No accordion items found on %s", response.url)
            return

        for li in lis:
            # Programme title (accordion header)
            prog_title = clean_text(" ".join(li.css('a[data-accordion-toggler]::text').getall()))
            if not prog_title:
                prog_title = clean_text(" ".join(li.css("a::text").getall()[:6]))

            # ✅ CRITICAL FIX: pick the link whose visible text is “Studienplan”
            plan_href = None
            for a in li.css("a[href]"):
                href = a.attrib.get("href")
                if not href:
                    continue
                at = clean_text(" ".join(a.css("::text").getall())) or ""
                if any(k in lower_norm(at) for k in PLAN_TEXT_KEYS):
                    plan_href = href
                    break

            # fallback: if no explicit Studienplan link, try a studies.go link
            if not plan_href:
                plan_href = li.css('a[href*="studies.unifr.ch/go/"]::attr(href)').get()

            if not plan_href:
                continue

            plan_url = abs_href(response, plan_href)

            yield scrapy.Request(
                plan_url,
                callback=self.parse_plan_or_program_page,
                meta={
                    "faculty": "Philosophy",
                    "level": level,
                    "program_name_de": prog_title,
                    "plan_entry_url": plan_url,
                },
            )

    def parse_plan_or_program_page(self, response):
        """
        The “Studienplan” link often lands on a faculty/subject page,
        sometimes you need one more click to reach the downloads page.
        So:
        - if page already has downloadable docs -> collect
        - else find another “Studienplan” link on the page and follow it once
        - else output empty documents but keep the URLs for debugging
        """
        data = dict(response.meta)

        # Improve name if we have a page title
        h = clean_text(response.css("h1::text, h2::text").get())
        if h and (not data.get("program_name_de") or len(data["program_name_de"]) < 4):
            data["program_name_de"] = h

        # If already contains docs, collect and yield
        docs = self._collect_docs(response)
        if docs:
            ba_docs, ma_docs, unknown = [], [], []

            for d in docs:
                lvl = classify_doc_level(d.get("label"), d["url"])
                if lvl == "bachelor":
                    ba_docs.append(d)
                elif lvl == "master":
                    ma_docs.append(d)
                else:
                    unknown.append(d)

            # yield to the entry's own level
            if data.get("level") == "bachelor":
                yield self._build_item(data, response, studienplan_url=response.url, docs=ba_docs or docs)
                # optionally also emit master if we discovered it
                if ma_docs:
                    data2 = dict(data)
                    data2["level"] = "master"
                    yield self._build_item(data2, response, studienplan_url=response.url, docs=ma_docs)
            else:
                yield self._build_item(data, response, studienplan_url=response.url, docs=ma_docs or docs)
                if ba_docs:
                    data2 = dict(data)
                    data2["level"] = "bachelor"
                    yield self._build_item(data2, response, studienplan_url=response.url, docs=ba_docs)

            return

        # Otherwise: search a “Studienplan” link in-page and follow once
        next_plan_href = None
        for a in response.css("a[href]"):
            href = a.attrib.get("href")
            if not href:
                continue
            txt = clean_text(" ".join(a.css("::text").getall())) or ""
            if any(k in lower_norm(txt) for k in PLAN_TEXT_KEYS):
                next_plan_href = href
                break

        if next_plan_href:
            next_url = abs_href(response, next_plan_href)
            # prevent loops
            if safe_url(next_url).rstrip("/") != safe_url(response.url).rstrip("/"):
                yield scrapy.Request(
                    next_url,
                    callback=self.parse_final_documents_page,
                    meta={**data, "studienplan_url": next_url},
                )
                return

        # Nothing else found
        yield self._build_item(data, response, studienplan_url=None, docs=[])

    def parse_final_documents_page(self, response):
        data = dict(response.meta)
        docs = self._collect_docs(response)
        yield self._build_item(data, response, studienplan_url=data.get("studienplan_url"), docs=docs)

    def _collect_docs(self, response):
        hrefs = response.css("a[href]::attr(href)").getall()
        seen = set()
        out = []

        for href in hrefs:
            if not href:
                continue
            url = abs_href(response, href)
            if not is_doc_href(url):
                continue
            if url in seen:
                continue
            seen.add(url)

            label = None
            last = href.split("/")[-1]
            a = response.xpath(f'//a[@href and contains(@href, "{last}")][1]')
            if a:
                label = clean_text(" ".join(a.xpath(".//text()").getall()))

            item = {"url": url}
            if label:
                item["label"] = label
            out.append(item)

        return out

    def _build_item(self, data, response, studienplan_url, docs):
        alts = find_alt_lang_urls(response)
        return {
            "faculty": data.get("faculty"),
            "level": data.get("level"),
            "program": {
                "name_de": data.get("program_name_de"),
                "page_url": data.get("plan_entry_url"),     # the link from the accordion ("Studienplan")
                "studienplan_url": studienplan_url,         # where we ended up collecting docs (if any)
                "page_url_fr": alts.get("fr"),
                "page_url_en": alts.get("en"),
            },
            "documents": docs,
        }
