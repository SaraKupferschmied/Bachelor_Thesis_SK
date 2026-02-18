import "../environments/environment";

import fs from "fs";
import path from "path";
import { DataAccessController } from "../control/data_access_controller";

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
  rows: {
    raw_text: string;
    extracted_code: string | null;
    extracted_title: string | null;
    inferred_type: "Mandatory" | "Elective" | null;
    page_no: number;
    section: string | null;
  }[];
};

type CurriculaLinkEntry = {
  // this file varies by faculty/crawler version; we handle best-effort
  program?: any;
  title?: string;
  name?: string;
  degree?: string;
  degree_level?: string;
  ects?: number | string | null;
  total_ects?: number | string | null;
  documents?: { url: string; label?: string }[];
  url?: string; // sometimes a single url field
};

type CanonicalProgramIdentity = {
  name: string;
  degree_level: "Bachelor" | "Master" | "Doctorate" | null;
  total_ects: number | null;
};

type UnresolvedItem = {
  key: string;
  faculty: string | null;
  category: string | null;
  program_name: string | null;
  source_url: string;
  doc_label: string | null;
  inferred_degree_level: string | null;
  inferred_target_ects: number | null;
  note: string;
};

type MappingFile = Record<
  string,
  {
    program_id: number;
    note?: string;
  }
>;

type DB = { query: (text: string, params?: any[]) => Promise<any> };

function readJsonIfExists<T>(p: string): T | null {
  if (!fs.existsSync(p)) return null;
  return JSON.parse(fs.readFileSync(p, "utf-8")) as T;
}

function normalizeUrl(u: string): string {
  // normalize minor differences like trailing slashes
  return (u ?? "").trim();
}

function guessDegreeLevelFromCategory(cat: string | null): "Bachelor" | "Master" | "Doctorate" | null {
  if (!cat) return null;
  const s = cat.toLowerCase();
  if (s.includes("bachelor") || s.includes("nebenfach") || s.includes("minor")) return "Bachelor";
  if (s.includes("master")) return "Master";
  if (s.includes("doktor") || s.includes("doctor") || s.includes("phd")) return "Doctorate";
  return null;
}

function inferTargetEcts(category: string | null, programName: string | null): number | null {
  const c = (category ?? "").toLowerCase();
  const n = (programName ?? "").toLowerCase();

  if (c.includes("nebenfach") || c.includes("minor") || n.includes("nebenfach") || n.includes("branche secondaire")) return 60;

  if (c.includes("bachelor")) return 180;
  if (c.includes("master")) {
    if (n.includes("passerelle")) return 120;
    return 90;
  }
  if (c.includes("doctor") || c.includes("doktor") || c.includes("phd")) return 30;

  return null;
}

function mapDocType(label: string | null): "study_plan" | "regulation" | "brochure" | "other" {
  const s = (label ?? "").toLowerCase();
  if (s.includes("studienplan") || s.includes("study plan") || s.includes("plan")) return "study_plan";
  if (s.includes("reglement") || s.includes("regulation") || s.includes("prüf") || s.includes("rrs")) return "regulation";
  if (s.includes("brosch") || s.includes("flyer") || s.includes("brochure")) return "brochure";
  return "other";
}

/**
 * Mapping key: source_url (stable) + optional faculty/category for safety.
 * This is MUCH better than names for your situation.
 */
function makeMappingKey(doc: ParsedDoc): string {
  return [
    (doc.faculty ?? "").trim().toLowerCase(),
    (doc.category ?? "").trim().toLowerCase(),
    normalizeUrl(doc.source_url).toLowerCase(),
  ].join("|");
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

function parseDegreeLevel(s: any): "Bachelor" | "Master" | "Doctorate" | null {
  const v = (s ?? "").toString().toLowerCase();
  if (v.includes("bachelor") || v === "ba" || v === "b") return "Bachelor";
  if (v.includes("master") || v === "ma" || v === "m") return "Master";
  if (v.includes("doctor") || v.includes("doktor") || v.includes("phd") || v === "d") return "Doctorate";
  return null;
}

function parseNumberMaybe(x: any): number | null {
  if (x === null || x === undefined) return null;
  const n = typeof x === "number" ? x : Number(String(x).replace(",", ".").trim());
  return Number.isFinite(n) ? n : null;
}

/**
 * Build URL -> canonical program identity index from curricula_links_level2_with_ects.json.
 * This is the key part that makes resolution work even when names are weird.
 */
function buildUrlToProgramIndex(entries: CurriculaLinkEntry[]): Map<string, CanonicalProgramIdentity> {
  const idx = new Map<string, CanonicalProgramIdentity>();

  for (const e of entries) {
    const programObj = e.program ?? null;
    const name =
      (programObj?.name_de ?? programObj?.name_en ?? programObj?.name_fr ?? programObj?.name ?? e.title ?? e.name ?? "")
        .toString()
        .trim();

    if (!name) continue;

    const degree_level =
      parseDegreeLevel(programObj?.degree_level ?? e.degree_level ?? e.degree) ?? null;

    const total_ects =
      parseNumberMaybe(programObj?.ects ?? e.total_ects ?? e.ects) ?? null;

    const ident: CanonicalProgramIdentity = { name, degree_level, total_ects };

    const urls: string[] = [];
    if (typeof e.url === "string") urls.push(e.url);
    for (const d of e.documents ?? []) {
      if (d?.url) urls.push(d.url);
    }

    for (const u of urls) {
      const nu = normalizeUrl(u);
      if (!nu) continue;
      // only set if not already present; first wins
      if (!idx.has(nu)) idx.set(nu, ident);
    }
  }

  return idx;
}

/**
 * Resolve program_id using canonical identity.
 * Prefer name+degree+ects when available; fall back sensibly.
 */
async function resolveProgramIdByCanonical(
  db: DB,
  ident: CanonicalProgramIdentity
): Promise<number | null> {
  const { name, degree_level, total_ects } = ident;

  // strongest
  if (degree_level && total_ects != null) {
    const r = await db.query(
      `SELECT program_id
       FROM StudyProgram
       WHERE LOWER(name)=LOWER($1) AND degree_level=$2 AND total_ects=$3
       LIMIT 1;`,
      [name, degree_level, total_ects]
    );
    if (r.rows[0]?.program_id) return r.rows[0].program_id;
  }

  // next
  if (degree_level) {
    const r = await db.query(
      `SELECT program_id
       FROM StudyProgram
       WHERE LOWER(name)=LOWER($1) AND degree_level=$2
       ORDER BY
         CASE WHEN $3::int IS NOT NULL AND total_ects=$3 THEN 0 ELSE 1 END,
         program_id ASC
       LIMIT 1;`,
      [name, degree_level, total_ects]
    );
    if (r.rows[0]?.program_id) return r.rows[0].program_id;
  }

  // fallback by name only
  const r = await db.query(
    `SELECT program_id
     FROM StudyProgram
     WHERE LOWER(name)=LOWER($1)
     ORDER BY
       CASE WHEN $2::int IS NOT NULL AND total_ects=$2 THEN 0 ELSE 1 END,
       program_id ASC
     LIMIT 1;`,
    [name, total_ects]
  );
  if (r.rows[0]?.program_id) return r.rows[0].program_id;

  return null;
}

async function run() {
  const root = path.resolve(__dirname, "../../..", "scrapy_crawler/outputs/faculty_downloads");
  console.log("Using faculty_downloads root:", root);

  const parsedPath = path.join(root, "_program_docs_parsed.json");
  if (!fs.existsSync(parsedPath)) throw new Error(`Missing parsed file: ${parsedPath}`);

  const parsed: ParsedDoc[] = JSON.parse(fs.readFileSync(parsedPath, "utf-8"));

  // Load curricula_links_level2_with_ects.json (this is what you used to import programs!)
  const curriculaPath = path.resolve(__dirname, "../../..", "scrapy_crawler/outputs/curricula_links_level2_with_ects.json");
  if (!fs.existsSync(curriculaPath)) {
    throw new Error(
      `Missing curricula links file: ${curriculaPath}\n` +
      `This importer uses it to resolve program_id by document URL.`
    );
  }
  const curriculaEntries: CurriculaLinkEntry[] = JSON.parse(fs.readFileSync(curriculaPath, "utf-8"));
  const urlToProgram = buildUrlToProgramIndex(curriculaEntries);

  // Mapping file lives next to parsed output by default:
  const mappingPath = path.join(root, "_program_mapping.json");
  const mapping: MappingFile = readJsonIfExists<MappingFile>(mappingPath) ?? {};

  const unresolved: UnresolvedItem[] = [];
  const mappingTemplate: MappingFile = {};

  const client = await DataAccessController.pool.connect();
  const db: DB = client;

  try {
    await db.query("BEGIN;");

    let docsUpserted = 0;
    let stagingInserted = 0;
    let facultyUpdates = 0;

    let resolvedByUrl = 0;
    let resolvedByMapping = 0;
    let resolvedFallback = 0;

    for (const doc of parsed) {
      const inferredDegree = guessDegreeLevelFromCategory(doc.category);
      const inferredEcts = inferTargetEcts(doc.category, doc.program_name);

      // 1) BEST: resolve by URL using curricula_links_level2_with_ects.json
      let programId: number | null = null;
      const canonical = urlToProgram.get(normalizeUrl(doc.source_url));
      if (canonical) {
        programId = await resolveProgramIdByCanonical(db, canonical);
        if (programId) resolvedByUrl++;
      }

      // 2) If URL did not resolve: mapping file (keyed by URL)
      const mapKey = makeMappingKey(doc);
      if (!programId) {
        const mapped = mapping[mapKey]?.program_id;
        if (typeof mapped === "number" && mapped > 0) {
          programId = mapped;
          resolvedByMapping++;
        }
      }

      // 3) Last fallback: try by whatever program_name we have (weak)
      if (!programId && doc.program_name) {
        // crude fallback using inferred degree/ects if DB has ambiguity
        const name = doc.program_name.trim();
        // try canonical-like query on that name
        programId = await resolveProgramIdByCanonical(db, {
          name,
          degree_level: inferredDegree,
          total_ects: inferredEcts,
        });
        if (programId) resolvedFallback++;
      }

      if (!programId) {
        unresolved.push({
          key: mapKey,
          faculty: doc.faculty,
          category: doc.category,
          program_name: doc.program_name,
          source_url: doc.source_url,
          doc_label: doc.doc_label,
          inferred_degree_level: inferredDegree,
          inferred_target_ects: inferredEcts,
          note:
            canonical
              ? `Found URL in curricula_links, but couldn't match StudyProgram by (name,degree,ects): ${canonical.name} / ${canonical.degree_level} / ${canonical.total_ects}`
              : "URL not found in curricula_links_level2_with_ects.json and no mapping exists",
        });

        if (!mappingTemplate[mapKey]) {
          mappingTemplate[mapKey] = { program_id: -1, note: "Fill with StudyProgram.program_id (keyed by faculty|category|source_url)" };
        }
        continue;
      }

      // Update faculty_id best-effort
      if (doc.faculty) {
        const facultyId = await resolveFacultyId(db, doc.faculty);
        if (facultyId) {
          const r = await db.query(`UPDATE StudyProgram SET faculty_id=$1 WHERE program_id=$2;`, [
            facultyId,
            programId,
          ]);
          facultyUpdates += r.rowCount ?? 0;
        }
      }

      // Upsert programDocument
      const docType = mapDocType(doc.doc_label);
      const insDoc = await db.query(
        `
        INSERT INTO programDocument (program_id, label, url, doc_type, fetched_at, parse_status, parse_notes)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (program_id, url, doc_type)
        DO UPDATE SET
          label = EXCLUDED.label,
          fetched_at = EXCLUDED.fetched_at,
          parse_status = EXCLUDED.parse_status,
          parse_notes = EXCLUDED.parse_notes
        RETURNING doc_id;
        `,
        [programId, doc.doc_label, doc.source_url, docType, doc.parsed_at, doc.parse_status, doc.parse_notes]
      );

      const docId = insDoc.rows[0]?.doc_id as number | undefined;
      if (docId) docsUpserted++;

      // Insert staging rows
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
            programId,
            row.raw_text,
            row.extracted_code,
            row.extracted_title,
            row.inferred_type,
            docId ?? null,
            row.page_no,
            row.section,
          ]
        );
        stagingInserted++;
      }
    }

    await db.query("COMMIT;");

    const unresolvedPath = path.join(root, "_unresolved_programs.json");
    fs.writeFileSync(unresolvedPath, JSON.stringify(unresolved, null, 2), "utf-8");

    const templatePath = path.join(root, "_program_mapping_template.json");
    fs.writeFileSync(templatePath, JSON.stringify(mappingTemplate, null, 2), "utf-8");

    console.log(`✅ programDocument upserts: ${docsUpserted}`);
    console.log(`✅ programCourseStaging inserts attempted: ${stagingInserted}`);
    console.log(`✅ StudyProgram faculty updates: ${facultyUpdates}`);
    console.log(`✅ Resolved by URL (curricula_links): ${resolvedByUrl}`);
    console.log(`✅ Resolved by mapping: ${resolvedByMapping}`);
    console.log(`✅ Resolved by fallback name: ${resolvedFallback}`);
    console.log(`⚠️ Unresolved programs: ${unresolved.length}`);
    console.log(`📝 Wrote unresolved list: ${unresolvedPath}`);
    console.log(`🧩 Wrote mapping template: ${templatePath}`);
    console.log(`ℹ️ If you create ${mappingPath} (based on the template), rerun to import everything.`);
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
