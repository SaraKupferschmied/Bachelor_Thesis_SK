import "../environments/environment";

import fs from "fs";
import path from "path";
import { spawnSync } from "child_process";

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
  chunks_path: string;
  title: string | null;
  pages: number;
  sha256: string | null;
  parsed_at: string;
  parse_status: "ok" | "failed";
  parse_notes: string | null;
};

type DoclingJson = {
  status: string;
  parser: string;
  title?: string | null;
  markdown: string;
  pages: string[];
  headings?: Array<{ level: number; text: string; line_index: number }>;
  tables?: Array<{
    table_index: number;
    markdown: string;
    rows?: Array<{ row_index: number; cells: Record<string, string> }>;
  }>;
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

function renderHeader(m: ProgramDocManifestItem, title: string | null, pages: number) {
  const headerObj = {
    parsed_at: new Date().toISOString(),
    title,
    pages,
    parser: "docling",
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

function parseWithDocling(pdfPath: string, helperPath: string, pythonExec: string): DoclingJson {
  const tempOut = path.join(
    process.cwd(),
    `.docling_${Date.now()}_${Math.random().toString(36).slice(2)}.json`
  );

  const proc = spawnSync(pythonExec, [helperPath, pdfPath, "--out", tempOut], {
    encoding: "utf-8",
    stdio: "pipe",
    maxBuffer: 20 * 1024 * 1024,
  });

  if (proc.status !== 0) {
    throw new Error(`Docling helper failed: ${proc.stderr || proc.stdout || `exit=${proc.status}`}`);
  }

  const raw = fs.readFileSync(tempOut, "utf-8");
  fs.unlinkSync(tempOut);
  return JSON.parse(raw) as DoclingJson;
}

async function run() {
  const outRoot = getArg("--root") ?? path.resolve(process.cwd(), "scrapy_crawler/outputs");
  const helperPath = getArg("--docling-helper") ?? path.resolve(process.cwd(), "parse_with_docling.py");
  const pythonExec = getArg("--python") ?? "python";

  const manifestPath = path.join(outRoot, "_program_docs_manifest.json");
  if (!fs.existsSync(manifestPath)) {
    throw new Error(`Missing manifest at: ${manifestPath}`);
  }

  const manifest: ProgramDocManifestItem[] = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));

  const parsedDir = path.join(outRoot, "parsed_fulltext_docling");
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

    const pdfPath = path.join(outRoot, "pdfs", path.basename(m.local_path));

    if (!fs.existsSync(pdfPath)) {
      fail++;
      const row: ParsedDocIndexRow = {
        doc_key: m.doc_key,
        program_key: m.program_key,
        source_url: m.source_url,
        local_path: pdfPath,
        output_path: "",
        chunks_path: "",
        title: null,
        pages: 0,
        sha256: m.sha256,
        parsed_at: new Date().toISOString(),
        parse_status: "failed",
        parse_notes: `PDF not found at ${pdfPath}`,
      };
      fs.appendFileSync(indexPath, JSON.stringify(row) + "\n");
      continue;
    }

    try {
      const baseName = safeFileName(`${m.degree_level ?? "UNK"}_${m.total_ects ?? "UNK"}_${m.doc_key}`);
      const outputPath = path.join(parsedDir, `${baseName}.txt`);

      if (fs.existsSync(outputPath)) {
        console.log(`⏭️ Skipping already parsed: ${path.basename(pdfPath)}`);
        continue;
      }

      const parsed = parseWithDocling(pdfPath, helperPath, pythonExec);

      const pagesArr =
        parsed.pages?.length && parsed.pages.some((p) => collapseWs(p).length > 0)
          ? parsed.pages
          : [parsed.markdown ?? ""];

      const pages = pagesArr.length;
      const title = collapseWs(parsed.title || "") || m.program_name || null;

      const header = renderHeader(m, title, pages);
      const body = pagesArr.map((t, i) => `---PAGE ${i + 1}---\n${t ?? ""}\n`).join("\n");

      fs.writeFileSync(outputPath, header + body, "utf-8");

      const row: ParsedDocIndexRow = {
        doc_key: m.doc_key,
        program_key: m.program_key,
        source_url: m.source_url,
        local_path: pdfPath,
        output_path: outputPath,
        chunks_path: "",
        title,
        pages,
        sha256: m.sha256,
        parsed_at: new Date().toISOString(),
        parse_status: "ok",
        parse_notes: null,
      };
      fs.appendFileSync(indexPath, JSON.stringify(row) + "\n");

      ok++;
      console.log(`✅ Parsed ${path.basename(pdfPath)} -> ${path.basename(outputPath)} (pages=${pages})`);
    } catch (e: any) {
      fail++;
      const row: ParsedDocIndexRow = {
        doc_key: m.doc_key,
        program_key: m.program_key,
        source_url: m.source_url,
        local_path: pdfPath,
        output_path: "",
        chunks_path: "",
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