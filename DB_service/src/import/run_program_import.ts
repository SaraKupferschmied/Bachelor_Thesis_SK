import "../environments/environment";

/**
 * One-shot ETL for UniFR programs + their documents.
 *
 * Input: a single JSON array like `program_links_with_ects_and_docs_enriched.json`
 *        (each item contains programme fields + a `documents` array of URLs).
 *
 * What it does:
 *   1) Upsert StudyProgram records (name + degree + ects)
 *   2) Upsert programDocument records (label/url/type)
 *   3) Download PDFs (and best-effort Calaméo read links → PDF)
 *   4) Parse PDFs for course-like lines and write to programCourseStaging
 *   5) Update programDocument fetched_at/parse_status/parse_notes
 *
 * Usage:
 *   ts-node 04_program_docs_etl.ts \
 *     --input ./program_links_with_ects_and_docs_enriched.json \
 *     --out ./scrapy_crawler/outputs/program_docs_etl
 *
 * Optional:
 *   --concurrency 6
 *   --defaultFacultyId 1
 *   --skipDownload   (still upserts and parses whatever is already on disk)
 */

import fs from "fs";
import path from "path";
import crypto from "crypto";
import axios from "axios";
import * as pdfjsLib from "pdfjs-dist/legacy/build/pdf.mjs";

import { DataAccessController } from "../control/data_access_controller";

// -----------------------------
// Types
// -----------------------------

type InputDoc = {
  url: string;
  label?: string;
  source_type?: string;
};

type InputProgram = {
  programme_name_en?: string;
  programme_name_de?: string;
  programme_name_fr?: string;
  programme?: string;
  level?: "B" | "M" | "D" | string;
  ects_points?: number | null;
  programme_url?: string;
  programme_url_en?: string;
  programme_url_de?: string;
  programme_url_fr?: string;
  curriculum_de_url?: string | null;
  curriculum_fr_url?: string | null;
  curriculum_en_url?: string | null;
  curriculum_unspecified_url?: string | null;
  faculty?: string | null;
  faculties?: string[];
  documents?: InputDoc[];
  // passthrough fields
  [k: string]: any;
};

type DegreeLevel = "Bachelor" | "Master" | "Doctorate";

type ProgramIdentity = {
  name: string;
  degree_level: DegreeLevel;
  total_ects: number | null;
  source_faculty_key: string | null;
  source_last_page_url: string | null;
  source_hints: any;
};

type ProgramDocRow = {
  raw_text: string;
  extracted_code: string | null;
  extracted_title: string | null;
  inferred_type: "Mandatory" | "Elective" | null;
  page_no: number;
  section: string | null;
};

type EtlDoc = {
  program_id: number;
  doc_id: number;
  label: string | null;
  url: string;
  doc_type: "study_plan" | "regulation" | "brochure" | "other";
  local_path: string | null;
  sha256: string | null;
  fetched_at: string | null;
  download_status:
    | "downloaded"
    | "already_present"
    | "skipped_non_pdf"
    | "calameo_no_direct_pdf"
    | "failed";
  download_notes: string | null;
  parse_status: "ok" | "failed" | "skipped";
  parse_notes: string | null;
  rows_count: number;
};

type DB = { query: (text: string, params?: any[]) => Promise<any> };

// -----------------------------
// CLI
// -----------------------------

function getArg(name: string): string | null {
  const i = process.argv.indexOf(`--${name}`);
  if (i < 0) return null;
  return process.argv[i + 1] ?? null;
}

function hasFlag(name: string): boolean {
  return process.argv.includes(`--${name}`);
}

function parseIntArg(name: string, fallback: number): number {
  const v = getArg(name);
  if (!v) return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

// -----------------------------
// Helpers
// -----------------------------

function ensureDir(p: string) {
  fs.mkdirSync(p, { recursive: true });
}

function safeFileName(s: string) {
  return s.replace(/[^a-z0-9._-]+/gi, "_").slice(0, 180);
}

function normalizeUrl(u: string): string {
  return (u ?? "").trim();
}

function sha256File(filePath: string): string {
  const hash = crypto.createHash("sha256");
  hash.update(fs.readFileSync(filePath));
  return hash.digest("hex");
}

const LEVEL_MAP: Record<string, DegreeLevel> = {
  B: "Bachelor",
  M: "Master",
  D: "Doctorate",
};

function pickProgramName(p: InputProgram): string | null {
  const name =
    (p.programme_name_en ?? p.programme_name_de ?? p.programme_name_fr ?? p.programme ?? "")
      .toString()
      .trim();
  return name || null;
}

function pickDegreeLevel(p: InputProgram): DegreeLevel | null {
  const lvl = (p.level ?? "").toString().trim().toUpperCase();
  return LEVEL_MAP[lvl] ?? null;
}

function pickFacultyKey(p: InputProgram): string | null {
  const f = (p.faculty ?? p.faculties?.[0] ?? null);
  return (f ?? "").toString().trim() || null;
}

function pickSourceLastPageUrl(p: InputProgram): string | null {
  return (
    p.programme_url ?? p.programme_url_en ?? p.programme_url_de ?? p.programme_url_fr ?? null
  )?.toString().trim() || null;
}

function mapDocType(label: string | null): "study_plan" | "regulation" | "brochure" | "other" {
  const s = (label ?? "").toLowerCase();
  if (s.includes("studienplan") || s.includes("study plan") || s.includes("plan")) return "study_plan";
  if (s.includes("reglement") || s.includes("règlement") || s.includes("regulation") || s.includes("prüf"))
    return "regulation";
  if (s.includes("brosch") || s.includes("flyer") || s.includes("brochure")) return "brochure";
  return "other";
}

function detectSourceType(url: string): "pdf" | "calameo" | "unknown" {
  const u = url.toLowerCase();
  if (u.includes("calameo.com/read/")) return "calameo";
  if (u.endsWith(".pdf") || u.includes(".pdf?")) return "pdf";
  return "unknown";
}

async function tableHasColumn(db: DB, table: string, column: string): Promise<boolean> {
  const r = await db.query(
    `
    SELECT 1
    FROM information_schema.columns
    WHERE table_name = $1 AND column_name = $2
    LIMIT 1;
    `,
    [table.toLowerCase(), column.toLowerCase()]
  );
  return r.rows.length > 0;
}

async function resolveFacultyId(db: DB, facultyKeyOrName: string): Promise<number | null> {
  const hasKey = await tableHasColumn(db, "faculty", "faculty_key");
  if (hasKey) {
    const r = await db.query(
      `SELECT faculty_id FROM Faculty WHERE LOWER(faculty_key)=LOWER($1) LIMIT 1;`,
      [facultyKeyOrName]
    );
    if (r.rows[0]?.faculty_id) return r.rows[0].faculty_id;
  }

  const hasName = await tableHasColumn(db, "faculty", "name");
  if (hasName) {
    const r = await db.query(
      `SELECT faculty_id FROM Faculty WHERE LOWER(name)=LOWER($1) LIMIT 1;`,
      [facultyKeyOrName]
    );
    if (r.rows[0]?.faculty_id) return r.rows[0].faculty_id;
  }

  for (const col of ["name_de", "name_fr", "name_en"]) {
    if (!(await tableHasColumn(db, "faculty", col))) continue;
    const r = await db.query(
      `SELECT faculty_id FROM Faculty WHERE LOWER(${col})=LOWER($1) LIMIT 1;`,
      [facultyKeyOrName]
    );
    if (r.rows[0]?.faculty_id) return r.rows[0].faculty_id;
  }

  return null;
}

async function downloadToFile(url: string, outPath: string): Promise<void> {
  const resp = await axios.get(url, { responseType: "arraybuffer", maxRedirects: 5, timeout: 60_000 });
  fs.writeFileSync(outPath, Buffer.from(resp.data));
}

/**
 * Best-effort: try to find a direct PDF URL from a Calaméo read page.
 * This may fail when the publisher disabled downloads.
 */
async function tryGetCalameoDirectPdfUrl(readUrl: string): Promise<string | null> {
  const html = (await axios.get(readUrl, { responseType: "text", timeout: 60_000 })).data as string;

  const pdfMatch =
    html.match(/https?:\/\/[^"' ]+\.pdf(\?[^"' ]*)?/i) ??
    html.match(/"downloadUrl"\s*:\s*"([^"]+)"/i);

  if (!pdfMatch) return null;
  const candidate = (pdfMatch[1] ?? pdfMatch[0])
    .replace(/\\u002F/g, "/")
    .replace(/\\\//g, "/");
  if (!candidate.toLowerCase().includes(".pdf")) return null;
  return candidate;
}

// -----------------------------
// Parsing (PDF → staging rows)
// -----------------------------

const COURSE_CODE_REGEXES: RegExp[] = [
  /\b[A-Z]{2,6}\.\d{4}\b/g, // e.g. ABCD.1234
  /\b[A-Z]{2,6}-\d{4}\b/g, // e.g. ABCD-1234
  /\b[A-Z]{2,6}\s?\d{3,4}\b/g, // e.g. ABCD 123 or ABCD1234
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

async function parsePdfToStagingRows(localPath: string): Promise<ProgramDocRow[]> {
  const pagesText = await pdfToPagesText(localPath);

  const rows: ProgramDocRow[] = [];
  for (let i = 0; i < pagesText.length; i++) {
    const page_no = i + 1;
    const text = pagesText[i].replace(/\s+/g, " ").trim();
    const lines = text
      .split(/(?<=[.;:])\s+|\n+/g)
      .map((x) => x.trim())
      .filter(Boolean);

    for (const line of lines) {
      const code = extractFirstCode(line);
      const title = tryExtractTitle(line, code);
      const inferred = inferType(line);
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
  return rows;
}

// -----------------------------
// Core ETL
// -----------------------------

async function upsertStudyProgram(db: DB, ident: ProgramIdentity, fallbackFacultyId: number): Promise<number> {
  const facultyId = ident.source_faculty_key
    ? (await resolveFacultyId(db, ident.source_faculty_key))
    : null;
  const effectiveFacultyId = facultyId ?? fallbackFacultyId;

  const r = await db.query(
    `
    INSERT INTO StudyProgram
      (name, degree_level, total_ects, faculty_id, source_hints, source_faculty_key, source_last_page_url)
    VALUES
      ($1, $2, $3, $4, $5, $6, $7)
    ON CONFLICT (name, degree_level, total_ects)
    DO UPDATE SET
      faculty_id = EXCLUDED.faculty_id,
      source_hints = COALESCE(StudyProgram.source_hints, '{}'::jsonb) || EXCLUDED.source_hints,
      source_faculty_key = COALESCE(EXCLUDED.source_faculty_key, StudyProgram.source_faculty_key),
      source_last_page_url = COALESCE(EXCLUDED.source_last_page_url, StudyProgram.source_last_page_url)
    RETURNING program_id;
    `,
    [
      ident.name,
      ident.degree_level,
      ident.total_ects,
      effectiveFacultyId,
      JSON.stringify(ident.source_hints ?? {}),
      ident.source_faculty_key,
      ident.source_last_page_url,
    ]
  );
  const programId = r.rows?.[0]?.program_id as number | undefined;
  if (!programId) throw new Error(`Failed to upsert StudyProgram: ${ident.name}`);
  return programId;
}

async function upsertProgramDocument(
  db: DB,
  programId: number,
  url: string,
  label: string | null,
  docType: "study_plan" | "regulation" | "brochure" | "other"
): Promise<number> {
  const r = await db.query(
    `
    INSERT INTO programDocument
      (program_id, label, url, doc_type, fetched_at, parse_status, parse_notes)
    VALUES
      ($1,$2,$3,$4,NULL,NULL,NULL)
    ON CONFLICT (program_id, url, doc_type)
    DO UPDATE SET
      label = COALESCE(EXCLUDED.label, programDocument.label)
    RETURNING doc_id;
    `,
    [programId, label, url, docType]
  );
  const docId = r.rows?.[0]?.doc_id as number | undefined;
  if (!docId) throw new Error(`Failed to upsert programDocument url=${url}`);
  return docId;
}

async function updateProgramDocumentStatus(
  db: DB,
  docId: number,
  fetchedAt: string | null,
  parseStatus: string | null,
  parseNotes: string | null
) {
  await db.query(
    `
    UPDATE programDocument
    SET fetched_at = $2,
        parse_status = $3,
        parse_notes = $4
    WHERE doc_id = $1;
    `,
    [docId, fetchedAt, parseStatus, parseNotes]
  );
}

async function insertStagingRows(db: DB, programId: number, docId: number, rows: ProgramDocRow[]) {
  for (const row of rows) {
    await db.query(
      `
      INSERT INTO programCourseStaging
        (program_id, raw_text, extracted_code, extracted_title, inferred_type, source_doc_id, page_no, section)
      VALUES
        ($1,$2,$3,$4,$5,$6,$7,$8)
      ON CONFLICT (program_id, extracted_code, source_doc_id, page_no)
      DO NOTHING;
      `,
      [
        programId,
        row.raw_text,
        row.extracted_code,
        row.extracted_title,
        row.inferred_type,
        docId,
        row.page_no,
        row.section,
      ]
    );
  }
}

// Simple concurrency pool
async function mapPool<T, R>(items: T[], concurrency: number, fn: (item: T, idx: number) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let next = 0;

  async function worker() {
    while (true) {
      const i = next++;
      if (i >= items.length) return;
      out[i] = await fn(items[i], i);
    }
  }

  const workers = Array.from({ length: Math.max(1, concurrency) }, () => worker());
  await Promise.all(workers);
  return out;
}

async function run() {
  const input = getArg("input") ?? path.resolve(process.cwd(), "program_links_with_ects_and_docs_enriched.json");
  const outDir = getArg("out") ?? path.resolve(process.cwd(), "./scrapy_crawler/outputs/program_docs_etl");
  const concurrency = parseIntArg("concurrency", 6);
  const defaultFacultyId = parseIntArg("defaultFacultyId", 1);
  const skipDownload = hasFlag("skipDownload");

  if (!fs.existsSync(input)) {
    throw new Error(`Input JSON not found: ${input}`);
  }

  ensureDir(outDir);
  const pdfDir = path.join(outDir, "pdfs");
  ensureDir(pdfDir);

  const programs: InputProgram[] = JSON.parse(fs.readFileSync(input, "utf-8"));
  if (!Array.isArray(programs)) throw new Error("Input must be a JSON array");

  const client = await DataAccessController.pool.connect();
  const db: DB = client;

  const etlDocs: EtlDoc[] = [];
  let programsUpserted = 0;
  let documentsUpserted = 0;
  let docsParsed = 0;
  let stagingInserted = 0;

  // 1) Upsert StudyProgram + programDocument rows (DB transaction)
  try {
    await db.query("BEGIN;");

    for (const p of programs) {
      const name = pickProgramName(p);
      const degree_level = pickDegreeLevel(p);
      if (!name || !degree_level) continue;

      const total_ects =
        typeof p.ects_points === "number" && Number.isFinite(p.ects_points) ? p.ects_points : null;

      const ident: ProgramIdentity = {
        name,
        degree_level,
        total_ects,
        source_faculty_key: pickFacultyKey(p),
        source_last_page_url: pickSourceLastPageUrl(p),
        source_hints: {
          programme_url_en: p.programme_url_en ?? null,
          programme_url_de: p.programme_url_de ?? null,
          programme_url_fr: p.programme_url_fr ?? null,
          programme_url: p.programme_url ?? null,
          curriculum_de_url: p.curriculum_de_url ?? null,
          curriculum_fr_url: p.curriculum_fr_url ?? null,
          curriculum_en_url: p.curriculum_en_url ?? null,
          curriculum_unspecified_url: p.curriculum_unspecified_url ?? null,
          faculties: p.faculties ?? [],
        },
      };

      const programId = await upsertStudyProgram(db, ident, defaultFacultyId);
      programsUpserted++;

      for (const d of p.documents ?? []) {
        if (!d?.url) continue;
        const url = normalizeUrl(d.url);
        if (!url) continue;
        const label = (d.label ?? null)?.toString().trim() || null;
        const docType = mapDocType(label);
        const docId = await upsertProgramDocument(db, programId, url, label, docType);
        documentsUpserted++;

        // placeholder ETL row; we fill download/parse later
        etlDocs.push({
          program_id: programId,
          doc_id: docId,
          label,
          url,
          doc_type: docType,
          local_path: null,
          sha256: null,
          fetched_at: null,
          download_status: "skipped_non_pdf",
          download_notes: null,
          parse_status: "skipped",
          parse_notes: null,
          rows_count: 0,
        });
      }
    }

    await db.query("COMMIT;");
  } catch (e) {
    await db.query("ROLLBACK;");
    throw e;
  } finally {
    client.release();
  }

  // 2) Download + Parse + Import staging rows (new DB connection)
  const client2 = await DataAccessController.pool.connect();
  const db2: DB = client2;
  try {
    const fetchedAt = new Date().toISOString();

    const processed = await mapPool(etlDocs, concurrency, async (doc): Promise<EtlDoc> => {
      const sourceType = detectSourceType(doc.url);
      if (sourceType === "unknown") {
        return { ...doc, download_status: "skipped_non_pdf", download_notes: "not a pdf and not a calameo read link" };
      }

      // Output file is deterministic by URL hash (keeps reruns stable)
      const urlHash = crypto.createHash("sha1").update(doc.url).digest("hex");
      const baseName = safeFileName(`${doc.program_id}_${doc.doc_type}_${doc.label ?? "doc"}_${urlHash}.pdf`);
      const outPath = path.join(pdfDir, baseName);

      // Download
      try {
        if (fs.existsSync(outPath)) {
          const sha = sha256File(outPath);
          return {
            ...doc,
            local_path: outPath,
            sha256: sha,
            fetched_at: fetchedAt,
            download_status: "already_present",
            download_notes: "file already existed on disk",
          };
        }

        if (skipDownload) {
          return {
            ...doc,
            local_path: null,
            sha256: null,
            fetched_at: null,
            download_status: "failed",
            download_notes: "--skipDownload enabled and file not on disk",
          };
        }

        if (sourceType === "pdf") {
          await downloadToFile(doc.url, outPath);
          const sha = sha256File(outPath);
          return { ...doc, local_path: outPath, sha256: sha, fetched_at: fetchedAt, download_status: "downloaded", download_notes: null };
        }

        // calaméo
        const direct = await tryGetCalameoDirectPdfUrl(doc.url);
        if (!direct) {
          return {
            ...doc,
            local_path: null,
            sha256: null,
            fetched_at: fetchedAt,
            download_status: "calameo_no_direct_pdf",
            download_notes: "could not find direct PDF link on Calaméo read page",
          };
        }
        await downloadToFile(direct, outPath);
        const sha = sha256File(outPath);
        return {
          ...doc,
          local_path: outPath,
          sha256: sha,
          fetched_at: fetchedAt,
          download_status: "downloaded",
          download_notes: `downloaded via Calaméo direct pdf: ${direct}`,
        };
      } catch (e: any) {
        return { ...doc, local_path: null, sha256: null, fetched_at: fetchedAt, download_status: "failed", download_notes: e?.message ?? String(e) };
      }
    });

    // Persist manifest
    const manifestPath = path.join(outDir, "_program_docs_etl_manifest.json");
    fs.writeFileSync(manifestPath, JSON.stringify(processed, null, 2), "utf-8");

    // Parse + import (DB transaction)
    await db2.query("BEGIN;");

    for (const doc of processed) {
      // update fetched_at even if download failed (helps with tracking)
      if (doc.fetched_at) {
        await updateProgramDocumentStatus(db2, doc.doc_id, doc.fetched_at, null, doc.download_notes);
      }

      if (!doc.local_path || (doc.download_status !== "downloaded" && doc.download_status !== "already_present")) {
        // still mark parse status if we tried
        const note = doc.download_notes ?? "no local pdf";
        await updateProgramDocumentStatus(db2, doc.doc_id, doc.fetched_at, "skipped", note);
        continue;
      }

      try {
        const rows = await parsePdfToStagingRows(doc.local_path);
        await insertStagingRows(db2, doc.program_id, doc.doc_id, rows);
        stagingInserted += rows.length;
        docsParsed++;

        await updateProgramDocumentStatus(db2, doc.doc_id, doc.fetched_at, "ok", null);
      } catch (e: any) {
        await updateProgramDocumentStatus(db2, doc.doc_id, doc.fetched_at, "failed", e?.message ?? String(e));
      }
    }

    await db2.query("COMMIT;");

    console.log("✅ ETL done");
    console.log(`StudyProgram upserts attempted: ${programsUpserted}`);
    console.log(`programDocument upserts attempted: ${documentsUpserted}`);
    console.log(`Docs parsed: ${docsParsed}`);
    console.log(`programCourseStaging inserts attempted: ${stagingInserted}`);
    console.log(`Manifest written: ${manifestPath}`);
  } catch (e) {
    await db2.query("ROLLBACK;");
    throw e;
  } finally {
    client2.release();
    await DataAccessController.pool.end();
  }
}

run().catch((e) => {
  console.error("❌ ETL failed:", e);
  process.exit(1);
});
