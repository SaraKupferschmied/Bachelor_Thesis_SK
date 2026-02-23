import "../environments/environment";

import fs from "fs";
import path from "path";
import crypto from "crypto";
import * as pdfjsLib from "pdfjs-dist/legacy/build/pdf.mjs";

type ProgramDocManifestItem = {
  program_key: string;
  doc_key: string;

  faculty: string | null;
  degree_level: "Bachelor" | "Master" | "Doctorate" | null;
  total_ects: number | null;
  program_name: string | null;

  programme_url: string | null;
  curriculum_url: string | null;

  doc_label: string | null;
  source_url: string;
  source_type: "pdf" | "calameo" | "unknown";

  local_path: string | null;
  sha256: string | null;
  fetched_at: string | null;
  status: "downloaded" | "already_present" | "skipped_non_pdf" | "calameo_no_direct_pdf" | "failed";
  notes?: string | null;
};

type ParsedDocIndexRow = {
  doc_key: string;
  program_key: string;
  source_url: string;
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
  return s.replace(/[^a-z0-9._-]+/gi, "_").slice(0, 180);
}

function collapseWs(s: string) {
  return s.replace(/\s+/g, " ").trim();
}

function sha256String(s: string): string {
  return crypto.createHash("sha256").update(s, "utf-8").digest("hex");
}

/**
 * Title heuristics for UniFR docs:
 * - Prefer cover-page text (often better than PDF metadata)
 * - Handle: bachelor/master/doctorate, regulation/reglement, studienplan/plan d'études,
 *   zusatzfach/minor, propädeutik/propaedeutic, etc.
 */
function isJunkTitle(t: string) {
  const s = collapseWs(t).toLowerCase();
  if (!s) return true;
  if (s.length <= 5) return true; // e.g. "BSc-IN"
  if (/^plan[_-]?[a-z0-9-]+$/i.test(s)) return true;
  if (/^(bsc|msc|ba|ma|phd|dr)\b/.test(s) && s.length <= 10) return true;
  if (/^untitled$/i.test(s)) return true;
  if (/microsoft word/i.test(s)) return true;
  return false;
}

/**
 * Extracts a good-looking title from cover page text.
 * Works even if cover is "graphic heavy" but some text is still extractable (as in your example).
 */
function titleFromCoverText(page1: string): string | null {
  const t = collapseWs(page1);
  if (!t || t.length < 20) return null;

  // Prefer phrases that start with plan/studienplan/reglement/regulation and contain degree/minor/etc.
  const patterns: RegExp[] = [
    // German: Studienplan ... Bachelor/Master/... in XYZ
    /(Studienplan[^.]{0,250}?\b(Bachelor|Master|Doctorate|Doktorat|Doktor)\b[^.]{0,250}?\bin\s+[A-Za-zÀ-ÿ0-9ÄÖÜäöüß \-\/]+)\b/i,

    // German: Studienplan ... Zusatzfach / Nebenfach / Minor
    /(Studienplan[^.]{0,250}?\b(Zusatzfach|Nebenfach|Minor)\b[^.]{0,250})\b/i,

    // German: Studienplan ... propädeutisch / propädeutik
    /(Studienplan[^.]{0,250}?\b(propädeut|propaedeut)\w*[^.]{0,250})\b/i,

    // French: Plan d’études ... Bachelor/Master/... en XYZ
    /(Plan d[’']études[^.]{0,250}?\b(Bachelor|Master|Doctorat)\b[^.]{0,250}?\ben\s+[A-Za-zÀ-ÿ0-9 \-\/]+)\b/i,

    // French: Plan d’études ... mineure/minor
    /(Plan d[’']études[^.]{0,250}?\b(mineure|min[eé]or|minor)\b[^.]{0,250})\b/i,

    // Generic: Règlement / Reglement / Regulation
    /((R[èe]glement|Reglement|Regulation)[^.]{0,300})\b/i,
  ];

  for (const rx of patterns) {
    const m = t.match(rx);
    if (m?.[1]) return collapseWs(m[1]).slice(0, 220);
  }

  // Fallback: choose best candidate chunk from early cover text
  const parts = t
    .split(/(?<=[.!?])\s+|\n+|\s{2,}/g)
    .map((x) => collapseWs(x))
    .filter((x) => x.length >= 20);

  if (!parts.length) return null;

  const score = (p: string) => {
    const s = p.toLowerCase();
    let k = 0;
    if (s.includes("studienplan") || s.includes("plan d")) k += 5;
    if (/(bachelor|master|doctor|doktor)/i.test(p)) k += 4;
    if (/(zusatzfach|nebenfach|minor|mineure)/i.test(p)) k += 3;
    if (/(reglement|règlement|regulation)/i.test(p)) k += 3;
    if (/(informatik|computer|science|médecine|medizin|physik|mathematik|biologie)/i.test(p)) k += 1;
    // prefer not-too-long but informative
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
    // not a valid URL; fall back to raw basename
    try {
      const base = path.basename(url);
      const cleaned = base.replace(/\.pdf$/i, "").replace(/[_-]+/g, " ").trim();
      return cleaned || null;
    } catch {
      return null;
    }
  }
}

/**
 * Extract full text page-by-page, and infer a document title.
 * Title preference:
 *  1) cover text derived title
 *  2) metadata title (if not junk)
 *  3) filename-ish title from source_url
 */
async function extractPdfTextAndTitle(
  filePath: string,
  sourceUrl?: string
): Promise<{
  title: string | null;
  pagesText: string[];
}> {
  const data = new Uint8Array(fs.readFileSync(filePath));
  const loadingTask = pdfjsLib.getDocument({ data });
  const doc = await loadingTask.promise;

  // Metadata title (best-effort)
  let metaTitle: string | null = null;
  try {
    const meta = await doc.getMetadata();
    const infoAny = (meta as any)?.info ?? {};
    const pdfTitle = (infoAny.Title ?? "") as string;
    const dcTitle = (meta as any)?.metadata?.get?.("dc:title") ?? "";
    const t = String(pdfTitle || dcTitle).trim();
    if (t && !isJunkTitle(t)) metaTitle = collapseWs(t);
  } catch {
    // ignore metadata failures
  }

  const pagesText: string[] = [];
  for (let pageNo = 1; pageNo <= doc.numPages; pageNo++) {
    const page = await doc.getPage(pageNo);
    const content = await page.getTextContent();

    const strings = content.items.map((it: any) => (it?.str ?? "").toString());
    const pageText = collapseWs(strings.join(" "));
    pagesText.push(pageText);
  }

  const coverTitle = titleFromCoverText(pagesText[0] ?? "");

  let title: string | null = null;
  if (coverTitle) title = coverTitle;
  else if (metaTitle) title = metaTitle;
  else if (sourceUrl) title = titleFromSourceUrl(sourceUrl);
  else title = null;

  return { title, pagesText };
}

function renderHeader(m: ProgramDocManifestItem, title: string | null, pages: number) {
  const headerObj = {
    parsed_at: new Date().toISOString(),
    title,
    pages,
    doc_key: m.doc_key,
    program_key: m.program_key,
    faculty: m.faculty,
    degree_level: m.degree_level,
    total_ects: m.total_ects,
    program_name: m.program_name,
    doc_label: m.doc_label,
    source_url: m.source_url,
    source_type: m.source_type,
    programme_url: m.programme_url,
    curriculum_url: m.curriculum_url,
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
  // Expect root to be a folder containing:
  //  - _program_docs_manifest.json
  //  - pdfs/ (downloaded PDFs)
  const outRoot = getArg("--root") ?? path.resolve(process.cwd(), "scrapy_crawler/outputs");

  const manifestPath = path.join(outRoot, "_program_docs_manifest.json");
  if (!fs.existsSync(manifestPath)) {
    throw new Error(`Missing manifest at: ${manifestPath}\nPass --root <folderContainingManifest>`);
  }

  const manifest: ProgramDocManifestItem[] = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));

  const parsedDir = path.join(outRoot, "parsed_fulltext");
  ensureDir(parsedDir);

  const indexPath = path.join(parsedDir, "_index.jsonl");
  if (!hasFlag("--append-index") && fs.existsSync(indexPath)) {
    fs.unlinkSync(indexPath);
  }

  const okStatuses = new Set(["downloaded", "already_present"]);

  let ok = 0;
  let fail = 0;

  for (const m of manifest) {
    if (!okStatuses.has(m.status)) continue;
    if (!m.local_path) continue;

    // Manual path fix: always load from <outRoot>/pdfs/<filename>
    const pdfPath = path.join(outRoot, "pdfs", path.basename(m.local_path));

    if (!fs.existsSync(pdfPath)) {
      fail++;
      const row: ParsedDocIndexRow = {
        doc_key: m.doc_key,
        program_key: m.program_key,
        source_url: m.source_url,
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
      const { title, pagesText } = await extractPdfTextAndTitle(pdfPath, m.source_url);

      const base =
        safeFileName(
          `${m.faculty ?? "UNK"}_${m.degree_level ?? "UNK"}_${m.total_ects ?? "UNK"}_${m.program_name ?? "UNK"}_${m.doc_label ?? "doc"}_${m.doc_key}`
        ) + ".txt";

      const outputPath = path.join(parsedDir, base);

      const header = renderHeader(m, title, pagesText.length);

      const body = pagesText.map((t, i) => `---PAGE ${i + 1}---\n${t}\n`).join("\n");

      fs.writeFileSync(outputPath, header + body, "utf-8");

      const row: ParsedDocIndexRow = {
        doc_key: m.doc_key,
        program_key: m.program_key,
        source_url: m.source_url,
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
      const row: ParsedDocIndexRow = {
        doc_key: m.doc_key,
        program_key: m.program_key,
        source_url: m.source_url,
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