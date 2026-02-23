import "../environments/environment";

import fs from "fs";
import path from "path";
import * as pdfjsLib from "pdfjs-dist/legacy/build/pdf.mjs";
import { fileURLToPath } from "url";

type ManifestItem = {
  faculty: string | null;
  category: string | null;
  program_name: string | null;
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
  faculty: string | null;
  category: string | null;
  program_name: string | null;
  doc_label: string | null;
  source_url: string;
  local_path: string;
  sha256: string | null;
  parsed_at: string;
  parse_status: "ok" | "failed";
  parse_notes: string | null;
  rows: ParsedStagingRow[];
};

// Tune these for your university’s actual code formats once you’ve seen a few PDFs.
const COURSE_CODE_REGEXES: RegExp[] = [
  /\b[A-Z]{2,6}\.\d{4}\b/g,      // e.g. ABCD.1234
  /\b[A-Z]{2,6}-\d{4}\b/g,       // e.g. ABCD-1234
  /\b[A-Z]{2,6}\s?\d{3,4}\b/g,   // e.g. ABCD 123 or ABCD1234
];

const MANDATORY_HINT = /\b(pflicht|obligatoire|mandatory|obligatorisch)\b/i;
const ELECTIVE_HINT = /\b(wahl|option|elective|facultatif|optional)\b/i;

function inferType(context: string): "Mandatory" | "Elective" | null {
  const s = context.toLowerCase();
  if (MANDATORY_HINT.test(s)) return "Mandatory";
  if (ELECTIVE_HINT.test(s)) return "Elective";
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

async function run() {
  
  // from DB_service/src/import -> repo root is ../../..
  // manifest lives in: scrapy_crawler/scrapy_crawler/spider_outputs/program_docs_v2
  const root = path.resolve(
    __dirname,
    "../../..",
    "scrapy_crawler/scrapy_crawler/spider_outputs/program_docs_v2"
  );

  const manifestPath = path.join(root, "_program_docs_manifest.json");
  if (!fs.existsSync(manifestPath)) throw new Error(`Missing manifest: ${manifestPath}`);

  const manifest: ManifestItem[] = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));

  const parsed: ParsedDoc[] = [];

  for (const m of manifest) {
    if (!m.local_path || (m.status !== "downloaded" && m.status !== "already_present")) continue;

    try {
      const pagesText = await pdfToPagesText(m.local_path);

      const rows: ParsedStagingRow[] = [];
      for (let i = 0; i < pagesText.length; i++) {
        const page_no = i + 1;
        const text = pagesText[i].replace(/\s+/g, " ").trim();
        const lines = text.split(/(?<=[.;:])\s+|\n+/g).map((x) => x.trim()).filter(Boolean);

        for (const line of lines) {
          const code = extractFirstCode(line);
          const title = tryExtractTitle(line, code);
          // use local context in the line (you can expand to surrounding lines later)
          const inferred = inferType(line);

          // Only keep lines that look somewhat relevant:
          // - have a code
          // - or mention mandatory/elective keywords
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

      parsed.push({
        faculty: m.faculty,
        category: m.category,
        program_name: m.program_name,
        doc_label: m.doc_label,
        source_url: m.source_url,
        local_path: m.local_path,
        sha256: m.sha256,
        parsed_at: new Date().toISOString(),
        parse_status: "ok",
        parse_notes: null,
        rows,
      });

      console.log(`✅ Parsed ${path.basename(m.local_path)} rows=${rows.length}`);
    } catch (e: any) {
      parsed.push({
        faculty: m.faculty,
        category: m.category,
        program_name: m.program_name,
        doc_label: m.doc_label,
        source_url: m.source_url,
        local_path: m.local_path!,
        sha256: m.sha256,
        parsed_at: new Date().toISOString(),
        parse_status: "failed",
        parse_notes: e?.message ?? String(e),
        rows: [],
      });
      console.warn(`⚠️ Failed parsing ${m.local_path}: ${e?.message ?? e}`);
    }
  }

  const outPath = path.join(root, "_program_docs_parsed.json");
  fs.writeFileSync(outPath, JSON.stringify(parsed, null, 2), "utf-8");
  console.log(`✅ Wrote parsed output: ${outPath}`);
}

run().catch((e) => {
  console.error("❌ Parse failed:", e);
  process.exit(1);
});