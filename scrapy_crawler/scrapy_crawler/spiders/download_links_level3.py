import re
from urllib.parse import urljoin
import scrapy


KEYWORDS = {
    "study_plan": [
        "study plan", "plan d'études", "plans d'études", "curriculum",
        "studienplan", "plan detudes", "plans detudes",
        "ects", "bachelor", "master"
    ],
    "regulation": [
        "regulation", "règlement", "reglements", "règlements",
        "ordnung", "award of the master"
    ],
    "brochure": ["brochure", "guide", "flyer"],
}

LANG_BY_FIELD = {
    "curriculum_de_url": "de",
    "curriculum_fr_url": "fr",
    "curriculum_en_url": "en",
    "curriculum_unspecified_url": "unspecified",
}

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()

def classify(label: str, section: str, context: str) -> str:
    text = " ".join([norm(label), norm(section), norm(context)])

    if any(k in text for k in KEYWORDS["regulation"]):
        return "regulations"
    if any(k in text for k in KEYWORDS["brochure"]):
        return "brochures"
    if any(k in text for k in KEYWORDS["study_plan"]):
        return "study_plans"
    return "other"

def extract_links_near_heading(response, heading_xpath: str):
    """
    Finds heading nodes via heading_xpath and extracts links in the same container block.
    """
    results = []
    for h in response.xpath(heading_xpath):
        section_title = " ".join(h.xpath(".//text()").getall()).strip()

        # heuristic: nearest container div/section/article
        container = h.xpath(
            "ancestor-or-self::*[self::section or self::div or self::article][1]"
        )
        if not container:
            container = h.xpath("..")

        links = container.xpath(".//a[@href]")
        for a in links:
            href = a.xpath("./@href").get()
            label = " ".join(a.xpath(".//text()").getall()).strip()
            if not href or not label:
                continue

            # context: parent text (li/p/div)
            parent = a.xpath("ancestor-or-self::*[self::li or self::p or self::div][1]")
            context = " ".join(parent.xpath(".//text()").getall()).strip() if parent else ""

            results.append({
                "url": response.urljoin(href),
                "label": label,
                "section": section_title,
                "context": context[:300],
            })
    return results

def extract_all_documentish_links(response):
    """
    Fallback: grab likely document links even without headings.
    """
    results = []
    for a in response.xpath("//a[@href]"):
        href = a.xpath("./@href").get()
        label = " ".join(a.xpath(".//text()").getall()).strip()
        if not href or not label:
            continue

        href_abs = response.urljoin(href)
        if any(x in href_abs.lower() for x in [".pdf", "calameo.com", "/go/"]):
            parent = a.xpath("ancestor-or-self::*[self::li or self::p or self::div][1]")
            context = " ".join(parent.xpath(".//text()").getall()).strip() if parent else ""
            results.append({
                "url": href_abs,
                "label": label,
                "section": "",
                "context": context[:300],
            })
    return results


class DownloadLinksLevel3Spider(scrapy.Spider):
    name = "download_links_level3"
    custom_settings = {"DOWNLOAD_DELAY": 1, "ROBOTSTXT_OBEY": True}

    def __init__(self, input_json_path="programmes_with_curricula.json", **kwargs):
        super().__init__(**kwargs)
        self.input_json_path = input_json_path

    def start_requests(self):
        import json
        from collections import defaultdict

        with open(self.input_json_path, "r", encoding="utf-8") as f:
            items = json.load(f)

        url_to_refs = defaultdict(list)

        for item in items:
            for k in ["curriculum_de_url", "curriculum_fr_url", "curriculum_en_url", "curriculum_unspecified_url"]:
                url = item.get(k)
                if not url:
                    continue
                url_to_refs[url].append({
                    "programme": item.get("programme"),
                    "level": item.get("level"),
                    "programme_url": item.get("programme_url"),
                    "curriculum_source_field": k,
                    "language": LANG_BY_FIELD.get(k, "unspecified"),
                })

        for url, refs in url_to_refs.items():
            yield scrapy.Request(
                url=url,
                callback=self.parse_curriculum_page,
                errback=self.handle_curriculum_error,
                meta={
                    "curriculum_url": url,
                    "refs": refs,
                },
            )

    def parse_curriculum_page(self, response):
        refs = response.meta["refs"]          # list of programmes sharing this page
        curriculum_url = response.meta["curriculum_url"]

        # 1) heading-driven extraction (documents / plans / regulations)
        links = []
        links += extract_links_near_heading(response, "//h1[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'documents') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'document')]")
        links += extract_links_near_heading(response, "//h2[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'documents') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'document')]")
        links += extract_links_near_heading(response, "//h3[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'documents') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'document')]")
        links += extract_links_near_heading(response, "//h4[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'documents') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'document')]")

        links += extract_links_near_heading(response, "//*[self::h1 or self::h2 or self::h3 or self::h4][contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'plan') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'études') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'etudes') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'curriculum') or contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'studien')]")

        # 2) fallback
        if not links:
            links = extract_all_documentish_links(response)

        # dedupe by url+label
        seen = set()
        deduped = []
        for l in links:
            key = (l["url"], l["label"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(l)

        # classify
        doc_bucket = {"study_plans": [], "regulations": [], "brochures": [], "other": []}
        for l in deduped:
            bucket = classify(l["label"], l.get("section", ""), l.get("context", ""))
            doc_bucket[bucket].append({
                "label": l["label"],
                "url": l["url"],
                "section": l.get("section") or None,
            })

        yield {
            "curriculum_page_url": curriculum_url,
            "programmes": refs,          # ← ALL programmes pointing to this page
            "documents": doc_bucket,
       
        }

    def handle_curriculum_error(self, failure):
        """
        Called when the curriculum page request fails (robots, DNS, timeout, etc.).
        If the request was redirected to Calaméo (or any other external doc host),
        store that final URL as a document link under 'other'.
        """
        request = failure.request
        curriculum_url = request.meta.get("curriculum_url")
        refs = request.meta.get("refs", [])

        # Collect redirect chain + last attempted URL
        redirect_chain = request.meta.get("redirect_urls", [])  # previous URLs
        final_url = request.url  # last attempted URL (often the blocked one)
        all_urls = redirect_chain + [final_url]

        # If any URL in the chain is a Calaméo URL, store it.
        calameo_urls = [u for u in all_urls if "calameo.com" in (u or "").lower()]

        doc_bucket = {"study_plans": [], "regulations": [], "brochures": [], "other": []}

        if calameo_urls:
            # pick the last calameo url (most likely the real target)
            target = calameo_urls[-1]
            doc_bucket["other"].append({
                "label": "External document (Calaméo)",
                "url": target,
                "section": None,
            })

        # Optional: also keep the final URL even if it's not calameo
        # (helps debugging / completeness)
        else:
            doc_bucket["other"].append({
                "label": "Unfetched curriculum page (request failed)",
                "url": final_url,
                "section": None,
            })

        # Emit the record so it won't be "missing" anymore
        yield {
            "curriculum_page_url": curriculum_url,
            "programmes": refs,
            "documents": doc_bucket,
            "error": repr(failure.value),
            "redirect_chain": redirect_chain or None,
        }