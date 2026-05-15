from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urlparse

import scrapy
from parsel import Selector


class UnifrExaminationsFacultyRulesSpider(scrapy.Spider):
    """
    Start at the UNIFR examinations page, extract the overview text, then follow
    only the faculty-specific examination/rules links found in the page content.

    The spider follows relevant HTML pages recursively with a small depth limit.
    PDF links are preserved as metadata links but are not parsed as text here.

    Run:
        scrapy crawl unifr_examinations_faculty_rules -O spider_outputs/unifr_examinations_faculty_rules.json
    """

    name = "unifr_examinations_faculty_rules"
    allowed_domains = ["www.unifr.ch", "unifr.ch"]
    start_urls = [
        "https://www.unifr.ch/studies/en/organisation/during-studies/examinations.html",
    ]

    custom_settings = {
        "FEED_EXPORT_ENCODING": "utf-8",
        "FEED_EXPORT_INDENT": 2,
        "DOWNLOAD_DELAY": 0.25,
        "AUTOTHROTTLE_ENABLED": True,
        "ROBOTSTXT_OBEY": True,
    }

    MAX_RECURSIVE_DEPTH = 2

    RELEVANT_LINK_KEYWORDS = {
        "exam", "examination", "examinations", "assessment", "assessments",
        "rule", "rules", "regulation", "regulations", "directive", "directives",
        "faculty", "faculties", "deadline", "deadlines", "fee", "fees",
        "validation", "validations", "transcript", "transcripts", "ects",
        "preuve", "examens", "reglement", "règlement", "pruefung", "prüfung",
        "pruefungen", "prüfungen", "gebuehr", "gebühr", "gebuehren", "gebühren",
    }

    def parse(self, response: scrapy.http.Response):
        content = self._main_content_selector(response)
        if content is None:
            self.logger.error("Could not locate main examination content on %s", response.url)
            return

        title = self._clean(response.css("title::text").get())
        sections = self._extract_sections(content, response)
        faculty_links = self._faculty_links(content, response)

        yield {
            "doc_key": "unifr_examinations_overview_en",
            "record_type": "overview_page",
            "source_type": "unifr_examinations_overview_page",
            "source_url": response.url,
            "source_reference": response.url,
            "language": self._language_from_response(response) or "en",
            "title": title,
            "page_heading": self._page_heading(content) or "Examinations",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sections": sections,
            "full_text": "\n\n".join(s["text"] for s in sections if s.get("text")),
            "faculty_links": faculty_links,
        }

        for link in faculty_links:
            url = link["url"]
            if self._is_parseable_html_url(url):
                yield response.follow(
                    url,
                    callback=self.parse_faculty_page,
                    meta={
                        "root_url": response.url,
                        "parent_url": response.url,
                        "depth_from_examinations": 1,
                        "faculty": link.get("faculty"),
                        "origin_link_text": link.get("text"),
                        "origin_link_type": link.get("link_type"),
                    },
                )

    def parse_faculty_page(self, response: scrapy.http.Response):
        depth = int(response.meta.get("depth_from_examinations", 1))
        faculty = response.meta.get("faculty")
        content = self._generic_main_content_selector(response)
        if content is None:
            self.logger.warning("Could not locate content on %s", response.url)
            return

        sections = self._extract_sections(content, response)
        content_links = self._extract_links(content, response)
        relevant_links = [link for link in content_links if self._is_relevant_recursive_link(link)]

        yield {
            "doc_key": self._doc_key(response.url, faculty),
            "record_type": "faculty_rule_page",
            "source_type": "unifr_faculty_examination_rule_page",
            "source_url": response.url,
            "source_reference": response.url,
            "originating_page_url": response.meta.get("root_url"),
            "parent_url": response.meta.get("parent_url"),
            "crawl_depth": depth,
            "faculty": faculty,
            "origin_link_text": response.meta.get("origin_link_text"),
            "origin_link_type": response.meta.get("origin_link_type"),
            "language": self._language_from_response(response),
            "title": self._clean(response.css("title::text").get()),
            "page_heading": self._page_heading(content),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sections": sections,
            "full_text": "\n\n".join(s["text"] for s in sections if s.get("text")),
            "relevant_links": relevant_links,
        }

        if depth >= self.MAX_RECURSIVE_DEPTH:
            return

        for link in relevant_links:
            url = link["url"]
            if not self._is_parseable_html_url(url):
                continue
            yield response.follow(
                url,
                callback=self.parse_faculty_page,
                meta={
                    "root_url": response.meta.get("root_url"),
                    "parent_url": response.url,
                    "depth_from_examinations": depth + 1,
                    "faculty": faculty,
                    "origin_link_text": link.get("text"),
                    "origin_link_type": link.get("link_type"),
                },
            )

    def _main_content_selector(self, response: scrapy.http.Response) -> Selector | None:
        # The Unifr template normally puts the article body in <main>.
        main = response.css("main")
        if main:
            return main[0]
        heading = response.xpath('//*[self::h1 or self::h2][normalize-space()="Examinations"]')
        if heading:
            container = heading[0].xpath('./ancestor::div[contains(concat(" ", normalize-space(@class), " "), " container ")][1]')
            if container:
                return container[0]
        body = response.css("body")
        return body[0] if body else None

    def _generic_main_content_selector(self, response: scrapy.http.Response) -> Selector | None:
        for css in ("main", "article", "div.col-md-8", "div.col-sm-8", "div.content", "#content"):
            found = response.css(css)
            if found:
                for candidate in found:
                    if candidate.xpath('.//*[self::h1 or self::h2 or self::h3]') and candidate.xpath('.//p|.//li'):
                        return candidate
                return found[0]
        body = response.css("body")
        return body[0] if body else None

    def _faculty_links(self, content: Selector, response: scrapy.http.Response) -> list[dict[str, str]]:
        links: list[dict[str, str]] = []
        articles = content.xpath('.//article[contains(concat(" ", normalize-space(@class), " "), " box ")]')
        if not articles:
            articles = content.xpath('.//*[self::section or self::div][.//h3 or .//h4][.//a]')

        for article in articles:
            faculty = self._clean(" ".join(article.xpath('.//*[self::h3 or self::h4][1]//text()').getall()))
            if not faculty:
                continue
            for a in article.xpath('.//a[@href]'):
                label = self._node_text(a)
                href = a.attrib.get("href", "")
                url = response.urljoin(href)
                if not label or self._is_non_content_link(url, label):
                    continue
                links.append({
                    "faculty": faculty,
                    "text": label,
                    "url": url,
                    "link_type": self._classify_link(label, url),
                })
        return self._dedupe_links(links)

    def _extract_sections(self, content: Selector, response: scrapy.http.Response) -> list[dict[str, Any]]:
        nodes = content.xpath(
            './/*[not(ancestor::nav) and not(ancestor::footer) and not(ancestor::header) '
            'and not(ancestor::script) and not(ancestor::style) '
            'and (self::h1 or self::h2 or self::h3 or self::h4 or self::h5 or self::p or self::ul or self::ol or self::table)]'
        )
        sections: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for node in nodes:
            tag = self._tag_name(node)
            if tag in {"ul", "ol"} and node.xpath('ancestor::*[self::ul or self::ol]'):
                continue
            if tag in {"h1", "h2", "h3", "h4", "h5"}:
                heading = self._node_text(node)
                if not heading or self._looks_like_navigation_heading(heading):
                    continue
                current = {"heading": heading, "heading_level": int(tag[1]), "text_blocks": [], "links": []}
                sections.append(current)
                continue
            block_text = self._node_text(node)
            links = self._extract_links(node, response)
            if not block_text and not links:
                continue
            if current is None:
                current = {"heading": None, "heading_level": None, "text_blocks": [], "links": []}
                sections.append(current)
            if block_text:
                current["text_blocks"].append(block_text)
            current["links"].extend(links)

        cleaned = []
        for idx, section in enumerate(sections, start=1):
            heading = section.get("heading")
            body = "\n".join(section.pop("text_blocks", [])).strip()
            text = f"{heading}\n{body}".strip() if heading else body
            links = self._dedupe_links(section.get("links", []))
            if text or links:
                cleaned.append({
                    "section_id": f"section::{idx:03d}",
                    "heading": heading,
                    "heading_level": section.get("heading_level"),
                    "source_reference": response.url,
                    "text": text,
                    "links": links,
                })
        return cleaned

    def _extract_links(self, node: Selector, response: scrapy.http.Response) -> list[dict[str, str]]:
        out = []
        for a in node.xpath('.//a[@href]'):
            label = self._node_text(a)
            href = a.attrib.get("href", "")
            url = response.urljoin(href)
            if not label or self._is_non_content_link(url, label):
                continue
            out.append({"text": label, "url": url, "link_type": self._classify_link(label, url)})
        return self._dedupe_links(out)

    def _is_relevant_recursive_link(self, link: dict[str, str]) -> bool:
        text = f"{link.get('text', '')} {link.get('url', '')}".lower()
        return any(keyword in text for keyword in self.RELEVANT_LINK_KEYWORDS)

    def _is_parseable_html_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.netloc.endswith("unifr.ch"):
            return False
        low = url.lower()
        if any(low.endswith(ext) or f"{ext}?" in low for ext in (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip")):
            return False
        return not self._is_non_content_link(url, "")

    def _classify_link(self, label: str, url: str) -> str:
        s = f"{label} {url}".lower()
        if ".pdf" in s:
            return "pdf"
        if "fee" in s or "tax" in s or "gebühr" in s or "gebuehr" in s:
            return "examination_fees"
        if "transcript" in s or "validation" in s:
            return "transcripts_validations"
        if "deadline" in s or "date" in s:
            return "deadline_information"
        return "examination_rules"

    def _is_non_content_link(self, url: str, label: str) -> bool:
        low_url = url.lower().strip()
        low_label = label.lower().strip()
        if not low_url or low_url.startswith(("mailto:", "tel:", "javascript:")) or low_url.endswith("#"):
            return True
        if "my.unifr.ch" in low_url or "outlook.com" in low_url:
            return True
        return low_label in {"back", "quick links", "schnellzugriff", "fr", "de", "en"}

    def _page_heading(self, content: Selector) -> str:
        for xpath in ('.//h1[1]', './/h2[1]', './/h3[1]'):
            found = content.xpath(xpath)
            if found:
                heading = self._node_text(found[0])
                if heading and not self._looks_like_navigation_heading(heading):
                    return heading
        return ""

    def _language_from_response(self, response: scrapy.http.Response) -> str | None:
        lang = response.xpath('/html/@lang').get() or response.css('html::attr(lang)').get()
        if lang:
            return lang.lower().split("-")[0]
        path = urlparse(response.url).path.lower()
        for code in ("/en/", "/fr/", "/de/", "/it/"):
            if code in path:
                return code.strip("/")
        return None

    def _looks_like_navigation_heading(self, heading: str) -> bool:
        return heading.strip().lower() in {
            "course offerings", "organisation of studies", "beginning of studies", "during studies",
            "quick links", "university", "faculties", "you are", "ressources", "resources",
        }

    def _doc_key(self, url: str, faculty: str | None) -> str:
        parsed = urlparse(url)
        raw = f"unifr {faculty or 'faculty'} {parsed.netloc} {parsed.path}"
        return re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")[:180]

    def _dedupe_links(self, links: list[dict[str, str]]) -> list[dict[str, str]]:
        seen = set()
        out = []
        for link in links:
            key = (link.get("text"), link.get("url"), link.get("link_type"), link.get("faculty"))
            if key in seen:
                continue
            seen.add(key)
            out.append(link)
        return out

    def _tag_name(self, node: Selector) -> str:
        return (node.xpath("name()").get() or "").lower()

    def _node_text(self, node: Selector) -> str:
        tag = self._tag_name(node)
        if tag == "ul":
            items = [self._clean(" ".join(li.xpath('.//text()').getall())) for li in node.xpath('./li')]
            return "\n".join(f"- {item}" for item in items if item)
        if tag == "ol":
            items = [self._clean(" ".join(li.xpath('.//text()').getall())) for li in node.xpath('./li')]
            return "\n".join(f"{i}. {item}" for i, item in enumerate(items, start=1) if item)
        if tag == "table":
            rows = []
            for tr in node.xpath('.//tr'):
                cells = [self._clean(" ".join(c.xpath('.//text()').getall())) for c in tr.xpath('./th|./td')]
                cells = [c for c in cells if c]
                if cells:
                    rows.append(" | ".join(cells))
            return "\n".join(rows)
        return self._clean(" ".join(node.xpath('.//text()').getall()))

    def _clean(self, value: str | None) -> str:
        if not value:
            return ""
        value = value.replace("\xa0", " ")
        value = re.sub(r"\s+", " ", value)
        return value.strip()
