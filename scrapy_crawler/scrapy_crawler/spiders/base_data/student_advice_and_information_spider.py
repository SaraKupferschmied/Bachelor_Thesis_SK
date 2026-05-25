from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

import scrapy
from parsel import Selector


class StudentAdviceAndInformationSpider(scrapy.Spider):
    """
    Crawl the University of Fribourg "Student Advice and Information" page and
    export only the main content area, including accordion content that is
    already present in the static HTML.

    Example:
        scrapy crawl student_advice_and_information -O spider_outputs/student_advice_and_information.json
    """

    name = "student_advice_and_information"
    allowed_domains = ["www.unifr.ch"]
    start_urls = [
        "https://www.unifr.ch/studies/en/organisation/beginning-of-studies/student-advice-and-information.html"
    ]

    custom_settings = {
        "FEED_EXPORT_ENCODING": "utf-8",
        "FEED_EXPORT_INDENT": 2,
    }

    page_heading_expected = "Student Advice and Information"
    doc_key = "unifr_student_advice_and_information_en"

    def parse(self, response: scrapy.http.Response):
        content = self._main_content_selector(response)
        if content is None:
            self.logger.error("Could not locate main content area on %s", response.url)
            return

        title = self._clean(response.css("title::text").get())
        page_heading = self._clean(content.xpath(".//h2[1]//text()").get()) or self.page_heading_expected
        sections = self._extract_sections(content, response)
        full_text = "\n\n".join(section["text"] for section in sections if section.get("text"))

        yield {
            "doc_key": self.doc_key,
            "source_type": "unifr_general_info_page",
            "source_url": response.url,
            "language": "en",
            "title": title,
            "page_heading": page_heading,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sections": sections,
            "full_text": full_text,
        }

    def _main_content_selector(self, response: scrapy.http.Response) -> Selector | None:
        candidates = response.xpath(
            f'//h2[normalize-space()="{self.page_heading_expected}"]'
            '/ancestor::div[contains(concat(" ", normalize-space(@class), " "), " row ")][1]'
            '/following-sibling::div[contains(concat(" ", normalize-space(@class), " "), " row ")][1]'
            '//div[contains(concat(" ", normalize-space(@class), " "), " col-md-8 ")][1]'
        )
        if candidates:
            return candidates[0]

        fallback = response.xpath(
            f'//h2[normalize-space()="{self.page_heading_expected}"]/ancestor::main[1]'
        )
        return fallback[0] if fallback else None

    def _extract_sections(self, content: Selector, response: scrapy.http.Response) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None

        nodes = content.xpath(
            './/*[self::h2 or self::h3 or self::h4 or self::h5 or self::p or self::ol or self::ul or self::figure or self::table]'
        )

        for node in nodes:
            tag = self._tag_name(node)

            if tag in {"ul", "ol"} and node.xpath('ancestor::*[self::ul or self::ol]'):
                continue
            if tag == "table" and node.xpath('ancestor::table'):
                continue

            if tag in {"h2", "h3", "h4", "h5"}:
                heading = self._node_text(node)
                if not heading:
                    continue
                anchor = self._anchor_for_heading(node)
                current = {
                    "heading": heading,
                    "heading_level": int(tag[1]),
                    "anchor": anchor,
                    "source_reference": self._source_reference(response.url, anchor),
                    "text_blocks": [],
                    "links": [],
                }
                sections.append(current)
                continue

            block_text = self._node_text(node)
            links = self._extract_links(node, response)
            if not block_text and not links:
                continue

            if current is None:
                current = {
                    "heading": None,
                    "heading_level": None,
                    "anchor": None,
                    "source_reference": response.url,
                    "text_blocks": [],
                    "links": [],
                }
                sections.append(current)

            if block_text:
                current["text_blocks"].append(block_text)
            current["links"].extend(links)

        cleaned_sections: list[dict[str, Any]] = []
        for idx, section in enumerate(sections, start=1):
            heading = section.get("heading")
            body = "\n".join(section.pop("text_blocks", [])).strip()
            text = f"{heading}\n{body}".strip() if heading else body
            links = self._dedupe_links(section.get("links", []))
            if not text and not links:
                continue

            cleaned_sections.append(
                {
                    "section_id": f"{self.doc_key}::section::{idx:03d}",
                    "heading": heading,
                    "heading_level": section.get("heading_level"),
                    "anchor": section.get("anchor"),
                    "source_url": response.url,
                    "source_reference": section.get("source_reference") or response.url,
                    "text": text,
                    "links": links,
                }
            )

        return cleaned_sections

    def _extract_links(self, node: Selector, response: scrapy.http.Response) -> list[dict[str, str]]:
        out = []
        for a in node.xpath('.//a[@href]'):
            label = self._node_text(a)
            href = a.attrib.get("href")
            if href:
                out.append({"text": label, "url": response.urljoin(href)})
        return out

    def _dedupe_links(self, links: list[dict[str, str]]) -> list[dict[str, str]]:
        seen = set()
        out = []
        for link in links:
            key = (link.get("text", ""), link.get("url", ""))
            if key not in seen:
                seen.add(key)
                out.append(link)
        return out

    def _anchor_for_heading(self, node: Selector) -> str | None:
        own_id = node.attrib.get("id")
        if own_id:
            return own_id
        parent_id = node.xpath('./ancestor::*[@id][1]/@id').get()
        if parent_id:
            return parent_id
        toggler = node.xpath('./preceding::a[@data-accordion-toggler][1]/@id').get()
        return toggler or None

    def _source_reference(self, url: str, anchor: str | None) -> str:
        return f"{url}#{anchor}" if anchor else url

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
        if tag == "figure":
            alt = self._clean(" ".join(node.xpath('.//img/@alt').getall()))
            caption = self._clean(" ".join(node.xpath('.//figcaption//text()').getall()))
            return " ".join(part for part in [alt, caption] if part).strip()
        if tag == "table":
            return self._table_text(node)
        return self._clean(" ".join(node.xpath('.//text()').getall()))

    def _table_text(self, node: Selector) -> str:
        rows = []
        for tr in node.xpath('.//tr'):
            cells = [self._clean(" ".join(c.xpath('.//text()').getall())) for c in tr.xpath('./th|./td')]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(" | ".join(cells))
        return "\n".join(rows)

    def _clean(self, value: str | None) -> str:
        if not value:
            return ""
        value = value.replace("\xa0", " ")
        value = re.sub(r"\s+", " ", value)
        return value.strip()
