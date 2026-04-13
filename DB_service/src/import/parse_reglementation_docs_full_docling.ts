import "../environments/environment";

import fs from "fs";
import path from "path";
import { spawnSync } from "child_process";

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

type DoclingJson = {
  status: string;
  parser: string;
  title?: string | null;
  markdown: string;
  pages: string[];
};

function ensureDir(p: string) {
  fs.mkdirSync(p, { recursive: true });
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

function renderHeader(m: ReglementationDocManifestItem, inferredTitle: string | null, pages: number) {
  const headerObj = {
    parsed_at: new Date().toISOString(),
    title: inferredTitle,
    pages,
    parser: "docling",
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

async function run() {
  const root = getArg("--root") ?? path.resolve(process.cwd(), "scrapy_crawler/outputs/reglementation_docs");
  const helperPath = getArg("--docling-helper") ?? path.resolve(process.cwd(), "parse_with_docling.py");
  const pythonExec = getArg("--python") ?? "python";

  const manifestPath = path.join(root, "_reglementation_docs_manifest.json");
  if (!fs.existsSync(manifestPath)) {
    throw new Error(`Missing manifest at: ${manifestPath}`);
  }

  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8")) as ReglementationDocManifestItem[];

  const parsedDir = path.join(root, "parsed_fulltext_docling");
  ensureDir(parsedDir);

  const indexPath = path.join(parsedDir, "_index.jsonl");
  if (!hasFlag("--append-index") && fs.existsSync(indexPath)) {
    fs.unlinkSync(indexPath);
  }

  const okStatuses = new Set<ReglementationDocManifestItem["status"]>(["downloaded", "already_present"]);
  let ok = 0;
  let fail = 0;

  for (const m of manifest) {
    if (!okStatuses.has(m.status) || !m.local_path) continue;

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
      continue;
    }

    try {
      const parsed = parseWithDocling(pdfPath, helperPath, pythonExec);

      const pagesArr =
        parsed.pages?.length && parsed.pages.some((p) => collapseWs(p).length > 0)
          ? parsed.pages
          : [parsed.markdown ?? ""];

      const pages = pagesArr.length;
      const title = collapseWs(parsed.title || "") || collapseWs(m.title || "") || null;

      const outputPath = path.join(parsedDir, `${m.reg_doc_key}.txt`);
      const header = renderHeader(m, title, pages);
      const body = pagesArr.map((t, i) => `---PAGE ${i + 1}---\n${t ?? ""}\n`).join("\n");

      fs.writeFileSync(outputPath, header + body, "utf-8");

      const row: ParsedRegDocIndexRow = {
        reg_doc_key: m.reg_doc_key,
        document_page_url: m.document_page_url,
        local_path: pdfPath,
        output_path: outputPath,
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