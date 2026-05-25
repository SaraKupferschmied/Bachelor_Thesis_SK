# -*- coding: utf-8 -*-
"""
Spider: University of Fribourg – Faculty of Humanities/Letters (lettres) – Philosophy faculty programmes

Fixes:
- Prevent accidentally following events.unifr.ch/masterdays instead of the actual master study page
- Prefer master URLs under https://www.unifr.ch/lettres/<lang>/studium/
- Add deterministic fallbacks: .../studium/master.html and .../studium/master/

Also:
- Accordion "Studienangebot" extraction uses the THIRD <p> inside accordion content:
  .//div[@data-accordion-content]//p[3]//a[@href]
"""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote

import scrapy

DOC_EXT_RE = re.compile(r"\.(pdf|doc|docx|xls|xlsx|ppt|pptx)\b", re.IGNORECASE)

PLAN_TEXT_KEYS = [
    "studienplan",
    "studienpläne",
    "plans d'études",
    "plan d'études",
    "plan d’etudes",
    "plan detudes",
    "study plan",
    "study plans",
    "reglement",
    "règlement",
    "reglements",
    "règlements",
    "downloads",
    "download",
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
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, fragment))


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

def tokens_for_program(name: str | None) -> list[str]:
    if not name:
        return []
    s = lower_norm(name)
    s = re.sub(r"\([^)]*\)", " ", s)
    parts = re.split(r"\s+|,|/|-|–", s)
    stop = {
        "und", "oder", "als", "in", "der", "die", "das", "zu", "für",
        "bachelor", "master", "studienprogramm", "hauptstudienprogramm",
        "nebenstudienprogramm", "ects"
    }
    return [p for p in parts if len(p) >= 4 and p not in stop]


def doc_matches_program(doc: dict, program_name: str | None) -> bool:
    hay = lower_norm(f"{doc.get('label', '')} {doc.get('url', '')}")
    pname = lower_norm(program_name or "")

    if "griech" in pname:
        return "griech" in hay or "grec" in hay
    if "latein" in pname:
        return "latein" in hay or "latin" in hay
    if "klassische philologie" in pname:
        return "klassische" in hay or "philologie" in hay

    toks = tokens_for_program(program_name)
    return any(t in hay for t in toks) if toks else True


class UnifrPhilStudyPlansSpider(scrapy.Spider):
    name = "unifr_phil_studyplans"

    custom_settings = {
        "LOG_LEVEL": "INFO",
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1,
        "FEED_EXPORT_ENCODING": "utf-8",
    }

    def __init__(self, lang="de", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lang = (lang or "de").strip().lower()
        # url -> list of "program contexts" waiting for a single fetch
        self._pending_program_ctx: dict[str, list[dict]] = {}

        # url -> cached parse result from that page (so duplicates can emit without refetch)
        self._program_page_cache: dict[str, dict] = {}

        self._emitted_keys = set()

    # Scrapy 2.13+ compatibility
    async def start(self):
        for req in self.start_requests():
            yield req

    def _load_faculties(self):
        candidates = [
            Path("faculties.json"),
            Path("spider_outputs") / "faculties.json",
            Path("scrapy_crawler") / "spider_outputs" / "faculties.json",
        ]
        faculties_path = next((p for p in candidates if p.exists()), None)
        if not faculties_path:
            tried = ", ".join(str(p.resolve()) for p in candidates)
            raise FileNotFoundError(f"Could not find faculties.json. Tried: {tried}")

        self.logger.info("Reading faculties from: %s", faculties_path.resolve())
        raw = faculties_path.read_bytes()
        if not raw.strip():
            raise ValueError(f"{faculties_path.resolve()} is empty.")

        text = raw.decode("utf-8-sig")
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("faculties.json must be a JSON list (top-level array).")
        return data

    def start_requests(self):
        data = self._load_faculties()

        lettres = next(
            (x for x in data if x.get("key") == "lettres" and x.get("lang") == self.lang),
            None,
        ) or next((x for x in data if x.get("key") == "lettres"), None)

        if not lettres:
            raise ValueError("No lettres entry found in faculties.json")

        lang_key = f"url_{self.lang}"
        base = (lettres.get(lang_key) or lettres.get("url_en") or "").strip()
        if not base:
            raise ValueError("LETTRES entry has no usable url_* field in faculties.json")

        start_url = safe_url(base.rstrip("/") + f"/{self.lang}/")
        self.logger.info("Starting PHIL crawl at: %s", start_url)
        yield scrapy.Request(start_url, callback=self.parse)

    def parse(self, response):
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
        From Studium page: find Bachelor page and crawl that.
        Master is discovered from Bachelor page menu, but MUST be under /lettres/<lang>/studium/
        """
        ba_url = self._find_level_link(response, want="bachelor")
        if not ba_url:
            self.logger.warning("Could not find Bachelor link on %s", response.url)
            return

        yield scrapy.Request(
            ba_url,
            callback=self.parse_level_page,
            meta={"level": "bachelor", "crawl_master": True},
        )

    # ---------------------------
    # Level link finding (FIXED)
    # ---------------------------

    def _is_valid_studium_level_url(self, url: str) -> bool:
        """
        Only allow level pages under:
        https://www.unifr.ch/lettres/<lang>/studium/...
        This prevents selecting events.unifr.ch/masterdays, etc.
        """
        try:
            parts = urlsplit(url)
        except Exception:
            return False
        if parts.scheme not in ("http", "https"):
            return False
        if parts.netloc.lower() != "www.unifr.ch":
            return False
        p = parts.path.lower()
        # must be in lettres/<lang>/studium subtree
        return f"/lettres/{self.lang}/studium/" in p

    def _find_level_link(self, response, want: str) -> str | None:
        """
        Robustly find 'Bachelor' or 'Master' page from sub-menu.
        FIX: reject off-domain / off-subtree links (e.g. masterdays event site).
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
                if "/bachelor" in p:
                    score += 40
                if p.endswith("/studium/bachelor/") or p.endswith("/studium/bachelor.html"):
                    score += 50

            if want == "master":
                if "master" in t:
                    score += 100
                if "/master" in p:
                    score += 40
                if p.endswith("/studium/master/") or p.endswith("/studium/master.html"):
                    score += 80

            if score == 0:
                continue

            # Critical fix: only accept real lettres studium pages on www.unifr.ch
            if not self._is_valid_studium_level_url(u):
                continue

            if score > best_score:
                best_score = score
                best = u

        # If we didn't find the master page in the menu, try known fallbacks
        if not best and want == "master":
            candidates = [
                f"https://www.unifr.ch/lettres/{self.lang}/studium/master.html",
                f"https://www.unifr.ch/lettres/{self.lang}/studium/master/",
            ]
            best = candidates[0]  # try master.html first
        if not best and want == "bachelor":
            candidates = [
                f"https://www.unifr.ch/lettres/{self.lang}/studium/bachelor/",
                f"https://www.unifr.ch/lettres/{self.lang}/studium/bachelor.html",
            ]
            best = candidates[0]

        return best

    # ---------------------------
    # Studienangebot extraction
    # ---------------------------

    def parse_level_page(self, response):
        level = response.meta.get("level")

        # From Bachelor page, schedule Master once (and ensure it's the real /studium/master...)
        if response.meta.get("crawl_master"):
            ma_url = self._find_level_link(response, want="master")
            if ma_url:
                yield scrapy.Request(
                    ma_url,
                    callback=self.parse_level_page,
                    meta={"level": "master", "crawl_master": False},
                )
            else:
                self.logger.warning("Master link not found from Bachelor page menu: %s", response.url)

        items = self._extract_program_items_from_studienangebot(response)
        if not items:
            self.logger.warning("No programme items found on %s", response.url)
            return

        self.logger.info("Found %s programme items on %s (%s)", len(items), response.url, level)

        for it in items:
            program_name = it["program_name"]
            program_url = safe_url(it["program_url"]).rstrip("/")

            ctx = {
                "faculty": "Philosophy",
                "level": level,
                "program_name_de": program_name,
                "plan_entry_url": program_url,
            }

            # If we already parsed this URL once, emit immediately for THIS program too
            cached = self._program_page_cache.get(program_url)
            if cached:
                item = self._build_item(
                    ctx,
                    response=None,  # we'll use cached alts
                    studienplan_url=cached.get("studienplan_url"),
                    docs=cached.get("docs", []),
                    alts=cached.get("alts", {}),
                )
                if item:
                    yield item
                continue

            # If it's already pending, just enqueue this program and don't refetch
            if program_url in self._pending_program_ctx:
                self._pending_program_ctx[program_url].append(ctx)
                continue

            # First time we see this URL: create pending list and fetch once
            self._pending_program_ctx[program_url] = [ctx]
            yield scrapy.Request(
                program_url,
                callback=self.parse_plan_or_program_page,
                meta={"program_url_key": program_url},
            )

    def _extract_program_items_from_studienangebot(self, response) -> list[dict]:
        heading = response.xpath(
            '//main[@id="main"]//*[self::h2 or self::h3 or self::h4]'
            '[contains(normalize-space(.), "Studienangebot")]'
        )
        if not heading:
            heading = response.xpath(
                '//*[self::h2 or self::h3 or self::h4][contains(normalize-space(.), "Studienangebot")]'
            )
        if not heading:
            return []

        # Accordion layout
        accordion_lis = heading[0].xpath("following::ul[contains(@class,'accordion')][1]/li")
        if accordion_lis:
            out = []
            for li in accordion_lis:
                name = clean_text(" ".join(li.css("a[data-accordion-toggler]::text").getall()))
                if not name:
                    continue

                # Primary: 3rd paragraph link inside accordion content
                href = li.xpath(".//div[@data-accordion-content]//p[3]//a[@href][1]/@href").get()

                # Fallback: any studies.unifr.ch/go/ link in accordion content
                if not href:
                    candidates = li.xpath(".//div[@data-accordion-content]//a[@href]/@href").getall()
                    href = next((h for h in candidates if "studies.unifr.ch/go/" in (h or "")), None)

                # Another fallback: any Studienplan/Download-ish anchor in this li
                if not href:
                    href = self._find_studienplan_href_in_li_selector(li)

                if not href:
                    continue

                out.append({"program_name": name, "program_url": abs_href(response, href)})
            return out

        # Plain list fallback
        plain_lis = heading[0].xpath("following::ul[1]/li")
        out = []
        for li in plain_lis:
            name = self._extract_program_title_from_li_selector(li)
            if not name:
                continue
            href = self._find_studienplan_href_in_li_selector(li)
            if not href:
                continue
            out.append({"program_name": name, "program_url": abs_href(response, href)})
        return out

    def _extract_program_title_from_li_selector(self, li_sel) -> str | None:
        title = clean_text(" ".join(li_sel.css("a::text").getall()[:3]))
        return title

    def _find_studienplan_href_in_li_selector(self, li_sel) -> str | None:
        for a in li_sel.css("a[href]"):
            href = a.attrib.get("href")
            if not href:
                continue
            at = clean_text(" ".join(a.css("::text").getall())) or ""
            if any(k in lower_norm(at) for k in PLAN_TEXT_KEYS):
                return href

        href = li_sel.css('a[href*="studies.unifr.ch/go/"]::attr(href)').get()
        return href

    # ---------------------------
    # Programme page -> docs
    # ---------------------------

    def parse_plan_or_program_page(self, response):
        program_url_key = response.meta.get("program_url_key") or safe_url(response.url).rstrip("/")
        ctx_list = self._pending_program_ctx.get(program_url_key, [])

        # If somehow called without pending context, fall back to a single context
        if not ctx_list:
            ctx_list = [{
                "faculty": response.meta.get("faculty"),
                "level": response.meta.get("level"),
                "program_name_de": response.meta.get("program_name_de"),
                "plan_entry_url": response.meta.get("plan_entry_url") or response.url,
            }]

        # Try docs on this page
        docs = self._collect_docs(response)
        if docs:
            studienplan_url = response.url
            yield from self._finalize_and_emit(program_url_key, response, ctx_list, docs, studienplan_url)
            return

        # Try next plan-like link
        next_plan_href = self._find_next_plan_like_link(response)
        if next_plan_href:
            next_url = safe_url(abs_href(response, next_plan_href)).rstrip("/")
            cur_url = safe_url(response.url).rstrip("/")
            if next_url != cur_url:
                yield scrapy.Request(
                    next_url,
                    callback=self.parse_final_documents_page,
                    meta={
                        "program_url_key": program_url_key,
                        "studienplan_url": next_url,
                    },
                )
                return

        # No docs found anywhere -> emit empty docs for all contexts
        yield from self._finalize_and_emit(program_url_key, response, ctx_list, [], studienplan_url=None)

    def _find_next_plan_like_link(self, response) -> str | None:
        best_href = None
        best_score = 0

        for a in response.css("a[href]"):
            href = a.attrib.get("href")
            if not href:
                continue

            txt = clean_text(" ".join(a.css("::text").getall())) or ""
            t = lower_norm(txt)
            u = abs_href(response, href)
            p = urlsplit(u).path.lower()

            score = 0
            if any(k in t for k in PLAN_TEXT_KEYS):
                score += 100
            if "download" in p or "downloads" in p:
                score += 80
            if "studienplan" in t:
                score += 100
            if "vollständige fassung" in t:
                score -= 40
            if "website" in t:
                score -= 80
            if "studium" in p or "studies" in p:
                score += 10
            if "download" in p or "downloads" in p:
                score += 10
            if "reglement" in p or "reglemente" in p:
                score += 10
            if "plans" in p or "plaene" in p or "pläne" in t:
                score += 10

            if score > best_score:
                best_score = score
                best_href = href

        return best_href

    def parse_final_documents_page(self, response):
        program_url_key = response.meta.get("program_url_key") or safe_url(response.url).rstrip("/")
        ctx_list = self._pending_program_ctx.get(program_url_key, [])
        docs = self._collect_docs(response)
        studienplan_url = response.meta.get("studienplan_url") or response.url
        yield from self._finalize_and_emit(program_url_key, response, ctx_list, docs, studienplan_url)

    def _finalize_and_emit(self, program_url_key, response, ctx_list, docs, studienplan_url):
        alts = find_alt_lang_urls(response) if response is not None else {}

        self._program_page_cache[program_url_key] = {
            "docs": docs,
            "studienplan_url": studienplan_url,
            "alts": alts,
        }

        self._pending_program_ctx.pop(program_url_key, None)

        for ctx in ctx_list:
            program_docs = [
                d for d in docs
                if doc_matches_program(d, ctx.get("program_name_de"))
            ]

            # fallback: don't lose everything if labels are too generic
            if not program_docs:
                program_docs = docs

            if program_docs:
                yield from self._yield_split_levels_if_needed(
                    ctx,
                    response,
                    program_docs,
                    studienplan_url or response.url,
                )
            else:
                item = self._build_item(
                    ctx,
                    response,
                    studienplan_url=studienplan_url,
                    docs=[],
                    alts=alts,
                )
                if item:
                    yield item

    def _yield_split_levels_if_needed(self, data, response, docs, studienplan_url: str):
        ba_docs, ma_docs, unknown = [], [], []
        for d in docs:
            lvl = classify_doc_level(d.get("label"), d["url"])
            if lvl == "bachelor":
                ba_docs.append(d)
            elif lvl == "master":
                ma_docs.append(d)
            else:
                unknown.append(d)

        if not ba_docs and not ma_docs:
            item = self._build_item(data, response, studienplan_url=studienplan_url, docs=docs)
            if item:
                yield item
            return

        requested = data.get("level")
        if requested == "bachelor":
            item = self._build_item(data, response, studienplan_url=studienplan_url, docs=ba_docs or unknown or docs)
            if item:
                yield item
            if ma_docs:
                data2 = dict(data)
                data2["level"] = "master"
                item = self._build_item(data2, response, studienplan_url=studienplan_url, docs=ma_docs)
                if item:
                    yield item
        else:
            item = self._build_item(data, response, studienplan_url=studienplan_url, docs=ma_docs or unknown or docs)
            if item:
                yield item
            if ba_docs:
                data2 = dict(data)
                data2["level"] = "bachelor"
                item = self._build_item(data2, response, studienplan_url=studienplan_url, docs=ba_docs)
                if item:
                    yield item

    # ---------------------------
    # Doc collection + output
    # ---------------------------

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

    def _build_item(self, data, response, studienplan_url, docs, alts=None):
        if alts is None:
            alts = find_alt_lang_urls(response) if response is not None else {}

        item = {
            "faculty": data.get("faculty"),
            "level": data.get("level"),
            "program": {
                "name_de": data.get("program_name_de"),
                "page_url": data.get("plan_entry_url"),
                "studienplan_url": studienplan_url,
                "page_url_fr": alts.get("fr"),
                "page_url_en": alts.get("en"),
            },
            "documents": docs,
        }

        key = (
            item["level"],
            item["program"]["name_de"],
            item["program"]["page_url"],
            tuple(sorted(d["url"] for d in docs)),
        )

        if key in self._emitted_keys:
            return None

        self._emitted_keys.add(key)
        return item