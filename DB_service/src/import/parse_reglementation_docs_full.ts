import "../environments/environment";

/**
 * Parse downloaded UniFR reglementation PDFs into fulltext TXT.
 *
 * Input:
 *  - <root>/_reglementation_docs_manifest.json (from your downloader)
 *  - <root>/pdfs/*.pdf
 *
 * Output:
 *  - <root>/parsed_fulltext/*.txt
 *  - <root>/parsed_fulltext/_index.jsonl
 *
 * Usage:
 *  ts-node parse_reglementation_docs_full.ts --root scrapy_crawler/outputs/reglementation_docs
 *  (add --append-index to append to existing _index.jsonl)
 */

import fs from "fs";
import path from "path";
import * as pdfjsLib from "pdfjs-dist/legacy/build/pdf.mjs";

type ReglementationDocManifestItem = {
  reg_doc_key: string;

  tree: string;
  title: string;

  document_page_url: string;
  pdf_url: string | null;

  local_path: string | null;
  sha256: string | null;
  fetched_at: string | null;

  status: "downloaded" | "already_present" | "failed";
  notes?: string | null;
};

type ParsedRegDocIndexRow = {
  reg_doc_key: string;
  document_page_url: string;
  local_path: string;
  output_path: string;
  title: string | null;
  pages: number;
  sha256: string | null;
  parsed_at: string;
  parse_status: "ok" | "failed";
  parse_notes: string | null;
};

function ensureDir(p: string) {
  fs.mkdirSync(p, { recursive: true });
}

function safeFileName(s: string) {
  return s.replace(/[^a-z0-9._-]+/gi, "_").replace(/^_+|_+$/g, "").slice(0, 180);
}

function collapseWs(s: string) {
  return s.replace(/\s+/g, " ").trim();
}

function isJunkTitle(t: string) {
  const s = collapseWs(t).toLowerCase();
  if (!s) return true;
  if (s.length <= 5) return true;
  if (/^untitled$/i.test(s)) return true;
  if (/microsoft word/i.test(s)) return true;
  return false;
}

function titleFromCoverText(page1: string): string | null {
  const t = collapseWs(page1);
  if (!t || t.length < 12) return null;

  const patterns: RegExp[] = [
    /((R[èe]glement|Reglement|Regulation)[^.]{0,320})\b/i,
    /((Statuten|Statuts)[^.]{0,320})\b/i,
    /((Weisung|Richtlinie|Directive)[^.]{0,320})\b/i,
  ];
  for (const rx of patterns) {
    const m = t.match(rx);
    if (m?.[1]) return collapseWs(m[1]).slice(0, 220);
  }

  const parts = t
    .split(/(?<=[.!?])\s+|\n+|\s{2,}/g)
    .map((x) => collapseWs(x))
    .filter((x) => x.length >= 12);

  if (!parts.length) return null;

  const score = (p: string) => {
    const s = p.toLowerCase();
    let k = 0;
    if (/(règlement|reglement|regulation)/i.test(p)) k += 6;
    if (/(statuten|statuts)/i.test(p)) k += 5;
    if (/(weisung|richtlinie|directive)/i.test(p)) k += 4;
    if (/(universit[aä]t|universit[eé])/i.test(p)) k += 2;
    if (p.length >= 40 && p.length <= 220) k += 2;
    return k;
  };

  parts.sort((a, b) => score(b) - score(a));
  return parts[0].slice(0, 220);
}

function titleFromSourceUrl(url: string): string | null {
  try {
    const u = new URL(url);
    const base = path.basename(u.pathname);
    const cleaned = base.replace(/\.pdf$/i, "").replace(/[_-]+/g, " ").trim();
    return cleaned || null;
  } catch {
    return null;
  }
}

async function extractPdfTextAndTitle(
  filePath: string,
  sourceUrl?: string,
  fallbackTitle?: string
): Promise<{ title: string | null; pagesText: string[] }> {
  const data = new Uint8Array(fs.readFileSync(filePath));

  // Legacy build usually works without worker config in Node.
  const loadingTask = pdfjsLib.getDocument({ data });
  const doc = await loadingTask.promise;

  // Metadata title
  let metaTitle: string | null = null;
  try {
    const meta = await doc.getMetadata();
    const infoAny = (meta as any)?.info ?? {};
    const pdfTitle = (infoAny.Title ?? "") as string;
    const dcTitle = (meta as any)?.metadata?.get?.("dc:title") ?? "";
    const t = String(pdfTitle || dcTitle).trim();
    if (t && !isJunkTitle(t)) metaTitle = collapseWs(t);
  } catch {
    // ignore
  }

  const pagesText: string[] = [];
  for (let pageNo = 1; pageNo <= doc.numPages; pageNo++) {
    const page = await doc.getPage(pageNo);
    const content = await page.getTextContent();
    const strings = content.items.map((it: any) => (it?.str ?? "").toString());
    pagesText.push(collapseWs(strings.join(" ")));
  }

  const coverTitle = titleFromCoverText(pagesText[0] ?? "");
  const urlTitle = sourceUrl ? titleFromSourceUrl(sourceUrl) : null;

  const title = coverTitle ?? metaTitle ?? fallbackTitle ?? urlTitle ?? null;
  return { title: title ? collapseWs(title) : null, pagesText };
}

function renderHeader(m: ReglementationDocManifestItem, inferredTitle: string | null, pages: number) {
  const headerObj = {
    parsed_at: new Date().toISOString(),
    title: inferredTitle,
    pages,

    reg_doc_key: m.reg_doc_key,
    tree: m.tree,

    spider_title: m.title,
    document_page_url: m.document_page_url,
    pdf_url: m.pdf_url,

    local_path: m.local_path,
    sha256: m.sha256,
    fetched_at: m.fetched_at,
    notes: m.notes ?? null,
  };

  return `---METADATA_JSON---\n${JSON.stringify(headerObj, null, 2)}\n---/METADATA_JSON---\n\n`;
}

function getArg(flag: string): string | null {
  const idx = process.argv.indexOf(flag);
  if (idx < 0) return null;
  const v = process.argv[idx + 1];
  if (!v || v.startsWith("--")) return null;
  return v;
}

function hasFlag(flag: string) {
  return process.argv.includes(flag);
}

async function run() {
  const root = getArg("--root") ?? path.resolve(process.cwd(), "scrapy_crawler/outputs/reglementation_docs");

  const manifestPath = path.join(root, "_reglementation_docs_manifest.json");
  if (!fs.existsSync(manifestPath)) {
    throw new Error(`Missing manifest at: ${manifestPath}`);
  }

  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8")) as ReglementationDocManifestItem[];

  const parsedDir = path.join(root, "parsed_fulltext_shorttitles");
  //const parsedDir = path.join(root, "parsed_fulltext");
  ensureDir(parsedDir);

  const indexPath = path.join(parsedDir, "_index.jsonl");
  if (!hasFlag("--append-index") && fs.existsSync(indexPath)) fs.unlinkSync(indexPath);

  const okStatuses = new Set<ReglementationDocManifestItem["status"]>(["downloaded", "already_present"]);

  let ok = 0;
  let fail = 0;

  for (const m of manifest) {
    if (!okStatuses.has(m.status)) continue;
    if (!m.local_path) continue;

    // Your downloader stored a local path pointing into <root>/pdfs/...
    // We reconstruct to be robust across machines.
    const pdfPath = path.join(root, "pdfs", path.basename(m.local_path));

    if (!fs.existsSync(pdfPath)) {
      fail++;
      const row: ParsedRegDocIndexRow = {
        reg_doc_key: m.reg_doc_key,
        document_page_url: m.document_page_url,
        local_path: pdfPath,
        output_path: "",
        title: null,
        pages: 0,
        sha256: m.sha256,
        parsed_at: new Date().toISOString(),
        parse_status: "failed",
        parse_notes: `PDF not found at ${pdfPath}`,
      };
      fs.appendFileSync(indexPath, JSON.stringify(row) + "\n");
      console.warn(`⚠️ Missing PDF: ${pdfPath}`);
      continue;
    }

    try {
      const { title, pagesText } = await extractPdfTextAndTitle(pdfPath, m.document_page_url, m.title);

      // for full filenames
      //const base = safeFileName(`${m.tree || "UNK"}_${m.title}_${m.reg_doc_key}`) + ".txt";
      // for shorter filenames
      const base = `${m.reg_doc_key}.txt`;
      const outputPath = path.join(parsedDir, base);

      const header = renderHeader(m, title, pagesText.length);
      const body = pagesText.map((t, i) => `---PAGE ${i + 1}---\n${t}\n`).join("\n");
      fs.writeFileSync(outputPath, header + body, "utf-8");

      const row: ParsedRegDocIndexRow = {
        reg_doc_key: m.reg_doc_key,
        document_page_url: m.document_page_url,
        local_path: pdfPath,
        output_path: outputPath,
        title,
        pages: pagesText.length,
        sha256: m.sha256,
        parsed_at: new Date().toISOString(),
        parse_status: "ok",
        parse_notes: null,
      };
      fs.appendFileSync(indexPath, JSON.stringify(row) + "\n");

      ok++;
      console.log(`✅ Parsed ${path.basename(pdfPath)} -> ${path.basename(outputPath)} (pages=${pagesText.length})`);
    } catch (e: any) {
      fail++;
      const row: ParsedRegDocIndexRow = {
        reg_doc_key: m.reg_doc_key,
        document_page_url: m.document_page_url,
        local_path: pdfPath,
        output_path: "",
        title: null,
        pages: 0,
        sha256: m.sha256,
        parsed_at: new Date().toISOString(),
        parse_status: "failed",
        parse_notes: e?.message ?? String(e),
      };
      fs.appendFileSync(indexPath, JSON.stringify(row) + "\n");
      console.warn(`⚠️ Failed parsing ${pdfPath}: ${e?.message ?? e}`);
    }
  }

  console.log(`\nDone. ok=${ok} failed=${fail}`);
  console.log(`Parsed files: ${parsedDir}`);
  console.log(`Index: ${indexPath}`);
}

run().catch((e) => {
  console.error("❌ Parse failed:", e);
  process.exit(1);
});