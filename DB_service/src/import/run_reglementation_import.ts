import "../environments/environment";

/**
 * Import UniFR reglementation documents into DB.
 *
 * Expects:
 *  - <root>/_reglementation_docs_manifest.json  (from downloader)
 *  - <root>/pdfs/*.pdf
 *  - <root>/parsed_fulltext/*.txt              (from parser)
 *
 * Usage:
 *  npx ts-node DB_service/src/import/run_reglementation_import.ts --root scrapy_crawler/outputs/reglementation_docs
 *
 * Optional:
 *  --defaultFacultyId 100
 */

import fs from "fs";
import path from "path";
import { DataAccessController } from "../control/data_access_controller";

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

type FacultyRow = {
  faculty_id: number;
  faculty_key: string | null;
  name_de: string | null;
  name_fr: string | null;
  name_en: string | null;
};

function getArg(flag: string): string | null {
  const idx = process.argv.indexOf(`--${flag}`);
  if (idx < 0) return null;
  const v = process.argv[idx + 1];
  if (!v || v.startsWith("--")) return null;
  return v;
}

function parseIntArg(flag: string, def: number): number {
  const v = getArg(flag);
  if (!v) return def;
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : def;
}

async function ensureTable(db: any) {
  await db.query(`
    CREATE TABLE IF NOT EXISTS ReglementationDocument (
      reg_doc_id SERIAL PRIMARY KEY,

      title TEXT NOT NULL,
      tree TEXT NOT NULL,

      document_page_url TEXT NOT NULL UNIQUE,
      pdf_url TEXT NULL,

      faculty_id INT NULL REFERENCES Faculty(faculty_id),

      doc_pdf BYTEA NULL,
      doc_parsed TEXT NULL,

      sha256 TEXT NULL,
      fetched_at TIMESTAMPTZ NULL,
      parsed_at TIMESTAMPTZ NULL,

      notes TEXT NULL
    );
  `);

  await db.query(`CREATE INDEX IF NOT EXISTS idx_regdoc_faculty_id ON ReglementationDocument(faculty_id);`);
}

async function loadFaculties(db: any): Promise<FacultyRow[]> {
  const res = await db.query(`SELECT faculty_id, faculty_key, name_de, name_fr, name_en FROM Faculty;`);
  return (res.rows ?? []) as FacultyRow[];
}

/**
 * Your parser output files end with _<reg_doc_key>.txt (40 hex chars).
 * This builds reg_doc_key -> filepath map.
 */
function buildParsedMap(parsedDir: string): Map<string, string> {
  const m = new Map<string, string>();
  if (!fs.existsSync(parsedDir)) return m;

  const files = fs.readdirSync(parsedDir).filter((f) => f.endsWith(".txt"));
  for (const f of files) {
    const mm = f.match(/_([a-f0-9]{40})\.txt$/i);
    if (mm?.[1]) m.set(mm[1].toLowerCase(), path.join(parsedDir, f));
  }
  return m;
}

function normalize(s: string | null | undefined) {
  return (s ?? "").toLowerCase();
}

/**
 * Only assign faculty_id when tree contains "400 Fakultäten".
 * Then try to match faculty by presence of faculty names/keys in tree.
 */
function resolveFacultyIdForTree(tree: string, faculties: FacultyRow[], defaultFacultyId: number): number | null {
  const t = normalize(tree);

  if (!t.includes("400 fakultäten")) return null; // your requirement

  // Try match by faculty_key or any translated faculty name appearing in the tree.
  for (const f of faculties) {
    const key = normalize(f.faculty_key);
    const de = normalize(f.name_de);
    const fr = normalize(f.name_fr);
    const en = normalize(f.name_en);

    // ignore the "default" row for matching
    if (f.faculty_id === defaultFacultyId) continue;

    if (key && t.includes(key)) return f.faculty_id;
    if (de && t.includes(de)) return f.faculty_id;
    if (fr && t.includes(fr)) return f.faculty_id;
    if (en && t.includes(en)) return f.faculty_id;
  }

  // No match although tree indicates "400 Fakultäten"
  return defaultFacultyId;
}

async function run() {
  const root = getArg("root") ?? path.resolve(process.cwd(), "scrapy_crawler/outputs/reglementation_docs");
  const defaultFacultyId = parseIntArg("defaultFacultyId", 100);

  const manifestPath = path.join(root, "_reglementation_docs_manifest.json");
  if (!fs.existsSync(manifestPath)) throw new Error(`Missing manifest: ${manifestPath}`);

  const manifest: ReglementationDocManifestItem[] = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));

  const parsedDir = path.join(root, "parsed_fulltext");
  const parsedMap = buildParsedMap(parsedDir);

  const okStatuses = new Set<ReglementationDocManifestItem["status"]>(["downloaded", "already_present"]);
  const docs = manifest.filter((m) => okStatuses.has(m.status) && m.local_path);

  if (!docs.length) {
    console.log("Nothing to import (no downloaded docs in manifest).");
    return;
  }

  const client = await DataAccessController.pool.connect();
  const db: any = client;

  try {
    await db.query("BEGIN;");
    await ensureTable(db);

    const faculties = await loadFaculties(db);

    let imported = 0;
    let missingParsed = 0;
    let missingPdf = 0;
    let facultyLinked = 0;

    for (const d of docs) {
      const pdfPath = path.join(root, "pdfs", path.basename(d.local_path!));
      if (!fs.existsSync(pdfPath)) {
        missingPdf++;
        console.warn(`⚠️ Missing PDF on disk: ${pdfPath} (skipping)`);
        continue;
      }

      const pdfBytes = fs.readFileSync(pdfPath);

      const parsedPath = parsedMap.get(d.reg_doc_key.toLowerCase()) ?? null;
      let parsedText: string | null = null;
      if (parsedPath && fs.existsSync(parsedPath)) {
        parsedText = fs.readFileSync(parsedPath, "utf-8");
      } else {
        missingParsed++;
      }

      const facultyId = resolveFacultyIdForTree(d.tree, faculties, defaultFacultyId);
      if (facultyId !== null) facultyLinked++;

      await db.query(
        `
        INSERT INTO ReglementationDocument
          (title, tree, document_page_url, pdf_url, faculty_id, doc_pdf, doc_parsed, sha256, fetched_at, parsed_at, notes)
        VALUES
          ($1,   $2,   $3,               $4,      $5,        $6,      $7,         $8,     $9,        $10,      $11)
        ON CONFLICT (document_page_url)
        DO UPDATE SET
          title            = EXCLUDED.title,
          tree             = EXCLUDED.tree,
          pdf_url          = EXCLUDED.pdf_url,
          faculty_id       = EXCLUDED.faculty_id,
          doc_pdf          = EXCLUDED.doc_pdf,
          doc_parsed       = EXCLUDED.doc_parsed,
          sha256           = EXCLUDED.sha256,
          fetched_at       = EXCLUDED.fetched_at,
          parsed_at        = EXCLUDED.parsed_at,
          notes            = EXCLUDED.notes;
        `,
        [
          d.title,
          d.tree,
          d.document_page_url,
          d.pdf_url,
          facultyId,
          pdfBytes,
          parsedText,
          d.sha256,
          d.fetched_at ? new Date(d.fetched_at) : null,
          parsedText ? new Date() : null,
          d.notes ?? null,
        ]
      );

      imported++;
    }

    await db.query("COMMIT;");

    console.log(`✅ Imported ${imported} reglementation documents.`);
    console.log(`Faculty linked (tree contains "400 Fakultäten"): ${facultyLinked}`);
    if (missingPdf) console.log(`⚠️ Missing PDFs on disk: ${missingPdf} (skipped)`);
    if (missingParsed) console.log(`⚠️ Missing parsed text: ${missingParsed} (PDFs still imported)`);

  } catch (e) {
    await db.query("ROLLBACK;");
    throw e;
  } finally {
    client.release();
    await DataAccessController.pool.end();
  }
}

run().catch((e) => {
  console.error("❌ Import failed:", e);
  process.exit(1);
});