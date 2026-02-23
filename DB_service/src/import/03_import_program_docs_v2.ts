import "../environments/environment";

import fs from "fs";
import path from "path";
import { DataAccessController } from "../control/data_access_controller";

/**
 * Input (default):
 *  - <out>/parsed/_program_docs_parsed.json   (preferred)
 *  - <out>/parsed/_program_docs_review.json   (preferred)
 *
 * Fallback:
 *  - <out>/_program_docs_parsed.json
 *  - <out>/_program_docs_review.json
 *
 * Override (strongly recommended if your filenames differ):
 *  - --parsed <path-to-parsed-json>
 *  - --review <path-to-review-json>
 *
 * Output:
 *  - Upserts StudyProgram
 *  - Upserts programDocument
 *  - Inserts programCourseStaging
 *  - Writes <out>/_program_docs_import_match_report.json (what was skipped & why)
 *
 * Usage:
 *   npx ts-node DB_service\src\import\03_import_program_docs_v2.ts --out scrapy_crawler\scrapy_crawler\spider_outputs\program_docs_v2
 *
 * If your file names/paths differ:
 *   npx ts-node ... --out <out> --parsed <fullpath.json> --review <fullpath.json>
 */

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

  rows: {
    raw_text: string;
    extracted_code: string | null;
    extracted_title: string | null;
    inferred_type: "Mandatory" | "Elective" | null;
    page_no: number;
    section: string | null;
  }[];
};

type ReviewItem = {
  doc_key: string;
  include: boolean;
  override_program_key?: string | null;
  note?: string | null;
  force_import?: boolean | null; // optional
};

type DB = { query: (text: string, params?: any[]) => Promise<any> };

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

function readJson<T>(p: string): T {
  return JSON.parse(fs.readFileSync(p, "utf-8")) as T;
}

function writeJson(p: string, v: any) {
  fs.writeFileSync(p, JSON.stringify(v, null, 2), "utf-8");
}

function mapDocType(label: string | null): "study_plan" | "regulation" | "brochure" | "other" {
  const s = (label ?? "").toLowerCase();
  if (s.includes("studienplan") || s.includes("study plan") || s.includes("plan")) return "study_plan";
  if (s.includes("reglement") || s.includes("regulation") || s.includes("prüf") || s.includes("rrs")) return "regulation";
  if (s.includes("brosch") || s.includes("flyer") || s.includes("brochure")) return "brochure";
  return "other";
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
  if (!facultyKeyOrName) return null;

  const hasKey = await tableHasColumn(db, "faculty", "faculty_key");
  if (hasKey) {
    const r = await db.query(`SELECT faculty_id FROM Faculty WHERE LOWER(faculty_key)=LOWER($1) LIMIT 1;`, [
      facultyKeyOrName,
    ]);
    if (r.rows[0]?.faculty_id) return r.rows[0].faculty_id;
  }

  const hasName = await tableHasColumn(db, "faculty", "name");
  if (hasName) {
    const r = await db.query(`SELECT faculty_id FROM Faculty WHERE LOWER(name)=LOWER($1) LIMIT 1;`, [facultyKeyOrName]);
    if (r.rows[0]?.faculty_id) return r.rows[0].faculty_id;
  }

  for (const col of ["name_de", "name_fr", "name_en"]) {
    if (!(await tableHasColumn(db, "faculty", col))) continue;
    const r = await db.query(`SELECT faculty_id FROM Faculty WHERE LOWER(${col})=LOWER($1) LIMIT 1;`, [facultyKeyOrName]);
    if (r.rows[0]?.faculty_id) return r.rows[0].faculty_id;
  }

  return null;
}

function parseProgramKey(program_key: string): {
  faculty_key: string | null;
  degree_level: "Bachelor" | "Master" | "Doctorate" | null;
  total_ects: number | null;
  name: string | null;
} {
  const parts = program_key.split("|");
  const faculty_key = (parts[0] ?? "").trim() || null;
  const deg = (parts[1] ?? "").trim().toLowerCase();
  const ectsRaw = (parts[2] ?? "").trim();
  const name = (parts.slice(3).join("|") ?? "").trim() || null;

  const degree_level =
    deg === "bachelor" ? "Bachelor" : deg === "master" ? "Master" : deg === "doctorate" ? "Doctorate" : null;

  const ects = ectsRaw ? Number(ectsRaw) : NaN;
  const total_ects = Number.isFinite(ects) ? ects : null;

  return { faculty_key, degree_level, total_ects, name };
}

/** --- Matching helpers (title ↔ program) --- */

function stripDiacritics(s: string): string {
  return s.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

function normalizeText(s: string): string {
  const t = stripDiacritics(s)
    .toLowerCase()
    .replace(/[_/\\\-]+/g, " ")
    .replace(/[^\p{L}\p{N}\s]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
  return t;
}

const STOPWORDS = new Set([
  "of","in","and","for","the","a","an","to",
  "program","programme","programmes","study","studies","plan","studienplan","curriculum","curricula",
  "minor","major","module","modules","track","tracks",
  "university","universite","universität","fribourg","freiburg","unifr",
  "science","sciences",
  "master","bachelor","doctorate","phd","msc","bsc","ma","ba","dr",
  "credits","credit","ects","cr","cp","kreditpunkte","kreditpunkt","punkte","punkte",
]);

function tokens(s: string): string[] {
  const t = normalizeText(s);
  if (!t) return [];
  return t
    .split(" ")
    .map((x) => x.trim())
    .filter((x) => x.length >= 3)
    .filter((x) => !STOPWORDS.has(x));
}

function jaccard(a: string[], b: string[]): number {
  const A = new Set(a);
  const B = new Set(b);
  if (A.size === 0 || B.size === 0) return 0;
  let inter = 0;
  for (const x of A) if (B.has(x)) inter++;
  const union = A.size + B.size - inter;
  return union === 0 ? 0 : inter / union;
}

function extractTitleCandidates(doc: ParsedDoc): string[] {
  const out: string[] = [];
  if (doc.doc_label) out.push(doc.doc_label);

  try {
    const base = path.basename(doc.local_path || "");
    if (base) out.push(base);
  } catch {
    // ignore
  }

  if (doc.source_url) out.push(doc.source_url);

  const seen = new Set<string>();
  const uniq: string[] = [];
  for (const t of out) {
    const k = normalizeText(t);
    if (!k) continue;
    if (seen.has(k)) continue;
    seen.add(k);
    uniq.push(t);
  }
  return uniq;
}

function detectDegreeInText(t: string): "Bachelor" | "Master" | "Doctorate" | null {
  const s = normalizeText(t);

  if (/\b(phd|doctorate|doctoral|doktorat|doctorat)\b/.test(s)) return "Doctorate";

  if (/\b(master|msc|m sc|m\.sc|ma|m a|m\.a)\b/.test(s)) return "Master";
  if (/\b(masters)\b/.test(s)) return "Master";

  if (/\b(bachelor|bsc|b sc|b\.sc|ba|b a|b\.a)\b/.test(s)) return "Bachelor";

  return null;
}

function extractEctsInText(t: string): number | null {
  const s = normalizeText(t);

  const m = s.match(/\b(\d{1,3})\s*(ects|credits|credit|cr|cp|kreditpunkte|kreditpunkt|points|point|crdits|credits)\b/);
  if (m?.[1]) {
    const n = Number(m[1]);
    if (Number.isFinite(n)) return n;
  }

  return null;
}

function degreeMatches(expected: "Bachelor" | "Master" | "Doctorate" | null, titleCandidates: string[]): boolean {
  if (!expected) return true;
  for (const t of titleCandidates) {
    const got = detectDegreeInText(t);
    if (got && got === expected) return true;
  }
  return false;
}

function ectsMatches(expected: number | null, titleCandidates: string[]): { ok: boolean; found: number | null } {
  if (!expected) return { ok: true, found: null };
  for (const t of titleCandidates) {
    const got = extractEctsInText(t);
    if (got !== null) return { ok: got === expected, found: got };
  }
  return { ok: false, found: null };
}

function nameMatches(
  expectedName: string | null,
  titleCandidates: string[],
  minJaccard: number
): { ok: boolean; best: number } {
  if (!expectedName) return { ok: true, best: 0 };
  const expTok = tokens(expectedName);
  let best = 0;

  for (const t of titleCandidates) {
    const gotTok = tokens(t);
    const score = jaccard(expTok, gotTok);
    if (score > best) best = score;

    const expSet = new Set(expTok);
    const gotSet = new Set(gotTok);
    let inter = 0;
    for (const x of expSet) if (gotSet.has(x)) inter++;
    if (inter >= 2 && score >= Math.min(minJaccard, 0.35)) return { ok: true, best };
  }

  return { ok: best >= minJaccard, best };
}

function checkDocMatchesProgram(
  doc: ParsedDoc,
  programKeyToUse: string,
  opts: {
    requireDegreeMatch: boolean;
    requireEctsMatch: boolean;
    requireNameMatch: boolean;
    minNameJaccard: number;
  }
) {
  const keyParsed = parseProgramKey(programKeyToUse);

  const expectedDegree = doc.degree_level ?? keyParsed.degree_level ?? null;
  const expectedEcts = doc.total_ects ?? keyParsed.total_ects ?? null;
  const expectedName = (doc.program_name ?? keyParsed.name ?? null)?.trim() || null;

  const titleCandidates = extractTitleCandidates(doc);

  const reasons: string[] = [];

  const degOk = degreeMatches(expectedDegree, titleCandidates);
  if (opts.requireDegreeMatch && !degOk) reasons.push(`degree mismatch (expected ${expectedDegree ?? "?"})`);

  const ects = ectsMatches(expectedEcts, titleCandidates);
  if (opts.requireEctsMatch && !ects.ok) {
    reasons.push(`ECTS mismatch (expected ${expectedEcts ?? "?"}, found ${ects.found ?? "none in title"})`);
  }

  const nm = nameMatches(expectedName, titleCandidates, opts.minNameJaccard);
  if (opts.requireNameMatch && !nm.ok) {
    reasons.push(`name mismatch (best jaccard=${nm.best.toFixed(2)} vs min=${opts.minNameJaccard})`);
  }

  const ok =
    (!opts.requireDegreeMatch || degOk) &&
    (!opts.requireEctsMatch || ects.ok) &&
    (!opts.requireNameMatch || nm.ok);

  return {
    ok,
    expected: { degree: expectedDegree, ects: expectedEcts, name: expectedName },
    observed: { ectsFound: ects.found, nameBestJaccard: nm.best },
    titleCandidates,
    reasons,
  };
}

/** --- DB ops --- */

async function upsertStudyProgram(db: DB, doc: ParsedDoc, programKeyToUse: string, defaultFacultyId: number): Promise<number> {
  const parsed = parseProgramKey(programKeyToUse);

  const facultyKey = parsed.faculty_key ?? doc.faculty ?? null;
  const faculty_id = (facultyKey ? await resolveFacultyId(db, facultyKey) : null) ?? defaultFacultyId;

  const name = (doc.program_name ?? parsed.name ?? "").trim();
  if (!name) throw new Error(`Missing program name for program_key=${programKeyToUse}`);

  const degree_level = doc.degree_level ?? parsed.degree_level;
  if (!degree_level) throw new Error(`Missing degree_level for program=${name} (program_key=${programKeyToUse})`);

  const total_ects = doc.total_ects ?? parsed.total_ects ?? null;

  const source_hints = {
    programme_url: doc.programme_url ?? null,
    curriculum_url: doc.curriculum_url ?? null,
    doc_key: doc.doc_key,
  };

  const r = await db.query(
    `
    INSERT INTO StudyProgram (name, degree_level, total_ects, faculty_id, source_hints, source_faculty_key, source_last_page_url)
    VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
    ON CONFLICT (name, degree_level, total_ects)
    DO UPDATE SET
      faculty_id = EXCLUDED.faculty_id,
      source_hints = COALESCE(StudyProgram.source_hints, '{}'::jsonb) || COALESCE(EXCLUDED.source_hints, '{}'::jsonb),
      source_faculty_key = COALESCE(EXCLUDED.source_faculty_key, StudyProgram.source_faculty_key),
      source_last_page_url = COALESCE(EXCLUDED.source_last_page_url, StudyProgram.source_last_page_url)
    RETURNING program_id;
    `,
    [
      name,
      degree_level,
      total_ects,
      faculty_id,
      JSON.stringify(source_hints),
      facultyKey,
      doc.programme_url ?? null,
    ]
  );

  return r.rows[0].program_id as number;
}

async function upsertProgramDocument(db: DB, program_id: number, doc: ParsedDoc): Promise<number> {
  const doc_type = mapDocType(doc.doc_label);

  const r = await db.query(
    `
    INSERT INTO programDocument (program_id, label, url, doc_type, fetched_at, parse_status, parse_notes)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
    ON CONFLICT (program_id, url, doc_type)
    DO UPDATE SET
      label = COALESCE(EXCLUDED.label, programDocument.label),
      fetched_at = COALESCE(EXCLUDED.fetched_at, programDocument.fetched_at),
      parse_status = COALESCE(EXCLUDED.parse_status, programDocument.parse_status),
      parse_notes = COALESCE(EXCLUDED.parse_notes, programDocument.parse_notes)
    RETURNING doc_id;
    `,
    [
      program_id,
      doc.doc_label,
      doc.source_url,
      doc_type,
      doc.parsed_at ? new Date(doc.parsed_at) : null,
      doc.parse_status,
      doc.parse_notes,
    ]
  );

  return r.rows[0].doc_id as number;
}

async function insertStagingRows(db: DB, program_id: number, source_doc_id: number, doc: ParsedDoc) {
  for (const row of doc.rows) {
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
        program_id,
        row.raw_text,
        row.extracted_code,
        row.extracted_title,
        row.inferred_type,
        source_doc_id,
        row.page_no,
        row.section,
      ]
    );
  }
}

/** --- Input resolution helpers --- */

function resolveInputPaths(outRoot: string): {
  inputRoot: string;
  parsedPath: string;
  reviewPath: string;
  reportPath: string;
} {
  const parsedFileArg = getArg("--parsed");
  const reviewFileArg = getArg("--review");

  // Determine where we expect inputs by default: <out>/parsed, else <out>
  const parsedDirCandidate = path.join(outRoot, "parsed");
  const inputRoot = fs.existsSync(parsedDirCandidate) ? parsedDirCandidate : outRoot;

  const parsedPath = path.resolve(process.cwd(), parsedFileArg ?? path.join(inputRoot, "_program_docs_parsed.json"));
  const reviewPath = path.resolve(process.cwd(), reviewFileArg ?? path.join(inputRoot, "_program_docs_review.json"));
  const reportPath = path.join(outRoot, "_program_docs_import_match_report.json");

  return { inputRoot, parsedPath, reviewPath, reportPath };
}

async function run() {
  const defaultOut = path.resolve(process.cwd(), "./scrapy_crawler/outputs/program_docs_v2");
  const outRoot = path.resolve(process.cwd(), getArg("--out") ?? defaultOut);

  const { inputRoot, parsedPath, reviewPath, reportPath } = resolveInputPaths(outRoot);

  // Debug prints (helps you immediately see what it tried)
  console.log("outRoot   =", outRoot);
  console.log("inputRoot =", inputRoot);
  console.log("parsedPath=", parsedPath);
  console.log("reviewPath=", reviewPath);

  if (!fs.existsSync(parsedPath)) {
    try {
      console.log("Files in inputRoot:", fs.readdirSync(inputRoot));
    } catch (e) {
      console.log("Could not list inputRoot:", e);
    }
    throw new Error(`Missing parsed file: ${parsedPath}`);
  }
  if (!fs.existsSync(reviewPath)) {
    try {
      console.log("Files in inputRoot:", fs.readdirSync(inputRoot));
    } catch (e) {
      console.log("Could not list inputRoot:", e);
    }
    throw new Error(`Missing review file: ${reviewPath} (create it by running the parse step)`);
  }

  const parsed = readJson<ParsedDoc[]>(parsedPath);
  const review = readJson<ReviewItem[]>(reviewPath);
  const reviewByKey = new Map(review.map((r) => [r.doc_key, r]));

  const selected = parsed.filter((d) => {
    const r = reviewByKey.get(d.doc_key);
    if (!r) return false;
    return !!r.include;
  });

  console.log(`Selected docs for import: ${selected.length}/${parsed.length} (based on review file).`);

  const defaultFacultyId = Number(getArg("--defaultFacultyId") ?? "1");
  const db = DataAccessController.pool as unknown as DB;

  // Matching config
  const requireDegreeMatch = !hasFlag("--noRequireDegreeMatch");
  const requireEctsMatch = !hasFlag("--noRequireEctsMatch");
  const requireNameMatch = !hasFlag("--noRequireNameMatch");
  const minNameJaccard = Number(getArg("--minNameJaccard") ?? "0.45");

  const matchOpts = { requireDegreeMatch, requireEctsMatch, requireNameMatch, minNameJaccard };

  if (hasFlag("--truncateStaging")) {
    console.log("⚠️ Truncating programCourseStaging ...");
    await db.query(`TRUNCATE TABLE programCourseStaging RESTART IDENTITY;`);
  }

  let okDocs = 0;
  let failDocs = 0;
  let skippedDocs = 0;

  const report: any[] = [];

  for (const doc of selected) {
    const r = reviewByKey.get(doc.doc_key)!;
    const programKeyToUse = (r.override_program_key ?? "").trim() || doc.program_key;

    const match = checkDocMatchesProgram(doc, programKeyToUse, matchOpts);

    if (!match.ok && !r.force_import) {
      skippedDocs++;
      report.push({
        doc_key: doc.doc_key,
        action: "skipped",
        reason: "title_does_not_match_program",
        match,
        review_note: r.note ?? null,
        override_program_key: r.override_program_key ?? null,
        force_import: r.force_import ?? false,
      });
      console.warn(`⏭️  Skipping doc_key=${doc.doc_key} (${doc.doc_label ?? "no label"}) -> ${match.reasons.join("; ")}`);
      continue;
    }

    try {
      const program_id = await upsertStudyProgram(db, doc, programKeyToUse, defaultFacultyId);
      const doc_id = await upsertProgramDocument(db, program_id, doc);

      if (doc.parse_status === "ok") {
        await insertStagingRows(db, program_id, doc_id, doc);
      }

      okDocs++;
      report.push({
        doc_key: doc.doc_key,
        action: "imported",
        program_id,
        doc_id,
        rows: doc.rows.length,
        match,
        override_program_key: r.override_program_key ?? null,
        force_import: r.force_import ?? false,
      });
      console.log(`✅ Imported doc_key=${doc.doc_key} program_id=${program_id} doc_id=${doc_id} rows=${doc.rows.length}`);
    } catch (e: any) {
      failDocs++;
      report.push({
        doc_key: doc.doc_key,
        action: "failed",
        error: e?.message ?? String(e),
        match,
        override_program_key: r.override_program_key ?? null,
        force_import: r.force_import ?? false,
      });
      console.warn(`❌ Failed importing doc_key=${doc.doc_key}: ${e?.message ?? e}`);
    }
  }

  writeJson(reportPath, {
    generated_at: new Date().toISOString(),
    outRoot,
    inputRoot,
    parsedPath,
    reviewPath,
    matchOpts,
    counts: { selected: selected.length, imported: okDocs, skipped: skippedDocs, failed: failDocs },
    items: report,
  });

  console.log(`Done. imported=${okDocs} skipped=${skippedDocs} failed=${failDocs}`);
  console.log(`Match report: ${reportPath}`);
}

run().catch((e) => {
  console.error("❌ Import failed:", e);
  process.exit(1);
});