import "../environments/environment";

import fs from "fs";
import path from "path";
import * as pdfjsLib from "pdfjs-dist/legacy/build/pdf.mjs";

/**
 * Input: <out>/_program_docs_manifest.json from the download step
 * Output:
 *  - <out>/_program_docs_parsed.json   (all docs)
 *  - <out>/parsed/<doc_key>.json       (per doc, easier to inspect)
 *  - <out>/_program_docs_review.json   (edit this to exclude/override wrong docs before import)
 *
 * Usage:
 *   ts-node 02_parse_program_docs_v2.ts --out ./scrapy_crawler/outputs/program_docs_v2
 */

type ManifestItem = {
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
  source_type: string;

  local_path: string | null;
  sha256: string | null;
  fetched_at: string | null;
  status: string;
  notes?: string | null;
};

type ParsedStagingRow = {
  raw_text: string;
  extracted_code: string | null;
  extracted_title: string | null;
  inferred_type: "Mandatory" | "Elective" | null;
  page_no: number;
  section: string | null;
};

type ParsedDoc = {
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
  local_path: string;
  sha256: string | null;

  parsed_at: string;
  parse_status: "ok" | "failed";
  parse_notes: string | null;
  rows: ParsedStagingRow[];
};

type ReviewItem = {
  doc_key: string;
  include: boolean;

  // if a doc is attached to the wrong program, override here
  override_program_key?: string | null;

  // optional notes for humans
  note?: string | null;
};

const COURSE_CODE_REGEXES: RegExp[] = [
  /\b[A-Z]{2,6}\.\d{4}\b/g,     // e.g. ABCD.1234
  /\b[A-Z]{2,6}-\d{4}\b/g,      // e.g. ABCD-1234
  /\b[A-Z]{2,6}\s?\d{3,4}\b/g,  // e.g. ABCD 123 or ABCD1234
];

const MANDATORY_HINT = /\b(pflicht|obligatoire|mandatory|obligatorisch)\b/i;
const ELECTIVE_HINT = /\b(wahl|option|elective|facultatif|optional)\b/i;

function inferType(context: string): "Mandatory" | "Elective" | null {
  if (MANDATORY_HINT.test(context)) return "Mandatory";
  if (ELECTIVE_HINT.test(context)) return "Elective";
  return null;
}

function extractFirstCode(line: string): string | null {
  for (const rx of COURSE_CODE_REGEXES) {
    const m = line.match(rx);
    if (m && m[0]) return m[0].replace(/\s+/g, "");
  }
  return null;
}

function tryExtractTitle(line: string, code: string | null): string | null {
  if (!code) return null;
  const idx = line.indexOf(code);
  if (idx < 0) return null;
  const rest = line.slice(idx + code.length).trim();
  const cleaned = rest.replace(/^[:\-–—]+/, "").trim();
  return cleaned || null;
}

async function pdfToPagesText(filePath: string): Promise<string[]> {
  const data = new Uint8Array(fs.readFileSync(filePath));
  const doc = await pdfjsLib.getDocument({ data }).promise;

  const pages: string[] = [];
  for (let pageNo = 1; pageNo <= doc.numPages; pageNo++) {
    const page = await doc.getPage(pageNo);
    const content = await page.getTextContent();
    const strings = content.items.map((it: any) => (it.str ?? "").toString());
    pages.push(strings.join(" "));
  }
  return pages;
}

function getArg(flag: string): string | null {
  const idx = process.argv.indexOf(flag);
  if (idx < 0) return null;
  const v = process.argv[idx + 1];
  if (!v || v.startsWith("--")) return null;
  return v;
}

function hasFlag(flag: string): boolean {
  return process.argv.includes(flag);
}

function ensureDir(p: string) {
  fs.mkdirSync(p, { recursive: true });
}

function readJsonIfExists<T>(p: string): T | null {
  if (!fs.existsSync(p)) return null;
  return JSON.parse(fs.readFileSync(p, "utf-8")) as T;
}

async function run() {
  const defaultOut = path.resolve(process.cwd(), "./scrapy_crawler/outputs/program_docs_v2");
  const outRoot = path.resolve(process.cwd(), getArg("--out") ?? defaultOut);

  const manifestPath = path.join(outRoot, "_program_docs_manifest.json");
  if (!fs.existsSync(manifestPath)) throw new Error(`Missing manifest: ${manifestPath}`);

  const manifest: ManifestItem[] = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));

  const parsedDir = path.join(outRoot, "parsed");
  ensureDir(parsedDir);

  const parsed: ParsedDoc[] = [];

  for (const m of manifest) {
    if (!m.local_path) continue;
    if (m.status !== "downloaded" && m.status !== "already_present") continue;

    try {
      const pagesText = await pdfToPagesText(m.local_path);

      const rows: ParsedStagingRow[] = [];
      for (let i = 0; i < pagesText.length; i++) {
        const page_no = i + 1;
        const text = pagesText[i].replace(/\s+/g, " ").trim();

        // Split into sentence-ish fragments; tune later if needed
        const lines = text
          .split(/(?<=[.;:])\s+|\n+/g)
          .map((x) => x.trim())
          .filter(Boolean);

        for (const line of lines) {
          const code = extractFirstCode(line);
          const title = tryExtractTitle(line, code);
          const inferred = inferType(line);

          // keep only relevant-ish lines
          if (!code && inferred == null) continue;

          rows.push({
            raw_text: line,
            extracted_code: code,
            extracted_title: title,
            inferred_type: inferred,
            page_no,
            section: null,
          });
        }
      }

      const doc: ParsedDoc = {
        program_key: m.program_key,
        doc_key: m.doc_key,
        faculty: m.faculty,
        degree_level: m.degree_level ?? null,
        total_ects: m.total_ects ?? null,
        program_name: m.program_name,
        programme_url: m.programme_url ?? null,
        curriculum_url: m.curriculum_url ?? null,
        doc_label: m.doc_label,
        source_url: m.source_url,
        local_path: m.local_path,
        sha256: m.sha256 ?? null,
        parsed_at: new Date().toISOString(),
        parse_status: "ok",
        parse_notes: null,
        rows,
      };

      parsed.push(doc);

      fs.writeFileSync(path.join(parsedDir, `${m.doc_key}.json`), JSON.stringify(doc, null, 2), "utf-8");
      console.log(`✅ Parsed ${path.basename(m.local_path)} rows=${rows.length}`);
    } catch (e: any) {
      const doc: ParsedDoc = {
        program_key: m.program_key,
        doc_key: m.doc_key,
        faculty: m.faculty,
        degree_level: m.degree_level ?? null,
        total_ects: m.total_ects ?? null,
        program_name: m.program_name,
        programme_url: m.programme_url ?? null,
        curriculum_url: m.curriculum_url ?? null,
        doc_label: m.doc_label,
        source_url: m.source_url,
        local_path: m.local_path!,
        sha256: m.sha256 ?? null,
        parsed_at: new Date().toISOString(),
        parse_status: "failed",
        parse_notes: e?.message ?? String(e),
        rows: [],
      };
      parsed.push(doc);

      fs.writeFileSync(path.join(parsedDir, `${m.doc_key}.json`), JSON.stringify(doc, null, 2), "utf-8");
      console.warn(`⚠️ Failed parsing ${m.local_path}: ${e?.message ?? e}`);
    }
  }

  const outParsed = path.join(outRoot, "_program_docs_parsed.json");
  fs.writeFileSync(outParsed, JSON.stringify(parsed, null, 2), "utf-8");
  console.log(`✅ Wrote parsed output: ${outParsed}`);

  // Create / update review file (do NOT overwrite user's edits unless --forceReview)
  const reviewPath = path.join(outRoot, "_program_docs_review.json");
  const existing = readJsonIfExists<ReviewItem[]>(reviewPath);

  if (existing && !hasFlag("--forceReview")) {
    console.log(`ℹ️ Review file exists (not overwriting): ${reviewPath}`);
    console.log(`   Tip: pass --forceReview to regenerate it (will reset include/overrides).`);
    return;
  }

  const review: ReviewItem[] = parsed.map((d) => ({
    doc_key: d.doc_key,
    include: d.parse_status === "ok",
    override_program_key: null,
    note:
      d.parse_status === "failed"
        ? `auto-excluded: parse failed (${d.parse_notes ?? "unknown error"})`
        : null,
  }));

  fs.writeFileSync(reviewPath, JSON.stringify(review, null, 2), "utf-8");
  console.log(`✅ Wrote review file: ${reviewPath}`);
}

run().catch((e) => {
  console.error("❌ Parse failed:", e);
  process.exit(1);
});
