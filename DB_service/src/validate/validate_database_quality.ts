// DB_service/src/import/validate_database_quality.ts
//
// Read-only database validation for the final thesis evaluation layer.
//
// Put this file into:
//   DB_service/src/import/validate_database_quality.ts
//
// Run from project root while Docker/Postgres is running:
//   npx ts-node DB_service\src\import\validate_database_quality.ts --out scrapy_crawler\scrapy_crawler\validation\metrics\database\database_quality.json
//
// Uses the same connection pattern as your schema/import files:
//   import "../environments/environment";
//   import { DataAccessController } from "../control/data_access_controller";
//
// This version matches your actual schema:
//   Faculty, Room, Language, Professor, Domain, StudyProgram, Course,
//   has_lang, teaches, consist_of, Semester, CourseOffering, Session,
//   is_taught_in, Evaluation, examined_in, programDocument, programCourseStaging.

import "../environments/environment";
import * as fs from "fs";
import * as path from "path";
import { DataAccessController } from "../control/data_access_controller";

type Severity = "info" | "warning" | "error";

type CheckResult = {
  name: string;
  category: string;
  severity: Severity;
  passed: boolean;
  value?: number | string | boolean | null;
  total?: number;
  rate?: number | null;
  details?: any;
  sql?: string;
};

const EXPECTED_TABLES = [
  "faculty",
  "room",
  "language",
  "professor",
  "domain",
  "studyprogram",
  "course",
  "has_lang",
  "teaches",
  "consist_of",
  "semester",
  "courseoffering",
  "session",
  "is_taught_in",
  "evaluation",
  "examined_in",
  "programdocument",
  "programcoursestaging",
];

const REQUIRED_COLUMNS: Record<string, string[]> = {
  faculty: ["faculty_id", "name_de", "name_fr", "name_en", "url", "faculty_key"],
  room: ["room_id"],
  language: ["lang_id", "description"],
  professor: ["prof_id", "title", "first_name", "last_name", "email", "office"],
  domain: ["domain_id", "name", "faculty_id"],
  studyprogram: [
    "program_id",
    "name",
    "degree_level",
    "total_ects",
    "study_start",
    "faculty_id",
    "director",
    "source_last_page_url",
    "name_en",
    "name_de",
    "name_fr",
  ],
  course: [
    "code",
    "alternative_code",
    "name",
    "ects",
    "description",
    "learning_goals",
    "faculty_id",
    "domain_id",
  ],
  has_lang: ["program_id", "lang_id"],
  teaches: ["code", "prof_id"],
  consist_of: ["program_id", "code", "course_name", "course_type", "description"],
  semester: ["sem_id", "year", "type"],
  courseoffering: ["offering_id", "code", "sem_id", "offering_type", "day_time_info", "link_course_catalogue"],
  session: ["session_id", "offering_id", "date", "start_time", "end_time", "room_id", "unit_type"],
  is_taught_in: ["offering_id", "lang_id"],
  evaluation: ["eval_id", "offering_id", "date", "start_time", "end_time", "description", "requirements", "evaluation_scheme", "remarks"],
  examined_in: ["room_id", "eval_id"],
  programdocument: ["doc_id", "program_id", "label", "url", "doc_type", "fetched_at", "parse_status", "parse_notes"],
  programcoursestaging: ["staging_id", "program_id", "raw_text", "extracted_code", "extracted_title", "inferred_type", "source_doc_id", "page_no", "section", "created_at"],
};

const PRIMARY_KEYS: Record<string, string[]> = {
  faculty: ["faculty_id"],
  room: ["room_id"],
  language: ["lang_id"],
  professor: ["prof_id"],
  domain: ["domain_id"],
  studyprogram: ["program_id"],
  course: ["code"],
  has_lang: ["program_id", "lang_id"],
  teaches: ["code", "prof_id"],
  consist_of: ["program_id", "code"],
  semester: ["sem_id"],
  courseoffering: ["offering_id"],
  session: ["session_id"],
  is_taught_in: ["offering_id", "lang_id"],
  evaluation: ["eval_id"],
  examined_in: ["eval_id", "room_id"],
  programdocument: ["doc_id"],
  programcoursestaging: ["staging_id"],
};

const SCORE_WEIGHTS: Record<string, number> = {
  schema_completeness: 0.10,
  relational_integrity: 0.20,
  studyprogram_quality: 0.20,
  document_quality: 0.15,
  parser_import_quality: 0.15,
  final_program_course_quality: 0.15,
  course_catalogue_quality: 0.05,
};

function parseArgs(argv: string[]) {
  const out: Record<string, string | boolean> = {};
  for (let i = 2; i < argv.length; i++) {
    const arg = argv[i];
    if (!arg) continue;

    if (arg === "--verbose") {
      out.verbose = true;
      continue;
    }

    const eq = arg.match(/^--([^=]+)=(.*)$/);
    if (eq) {
      out[eq[1]] = eq[2];
      continue;
    }

    const m = arg.match(/^--(.+)$/);
    if (m) {
      const key = m[1];
      const next = argv[i + 1];
      if (next && !next.startsWith("--")) {
        out[key] = next;
        i++;
      } else {
        out[key] = true;
      }
    }
  }
  return out;
}

function ensureParentDir(filePath: string) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function safeRate(good: number, total: number): number | null {
  if (!total) return null;
  return good / total;
}

function clamp01(value: number | null | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(1, value));
}

function check(
  name: string,
  category: string,
  severity: Severity,
  passed: boolean,
  extra: Partial<CheckResult> = {}
): CheckResult {
  return { name, category, severity, passed, ...extra };
}

async function q(client: any, sql: string, params: any[] = []) {
  return client.query(sql, params);
}

async function tableExists(client: any, table: string): Promise<boolean> {
  const res = await q(client, "SELECT to_regclass($1) AS table_name;", [table]);
  return Boolean(res.rows[0]?.table_name);
}

async function columnExists(client: any, table: string, column: string): Promise<boolean> {
  const res = await q(
    client,
    `
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND lower(table_name) = lower($1)
      AND lower(column_name) = lower($2)
    LIMIT 1;
    `,
    [table, column]
  );
  return Boolean(res.rows[0]);
}

async function getExistingTables(client: any): Promise<Set<string>> {
  const res = await q(
    client,
    `
    SELECT lower(table_name) AS table_name
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_type = 'BASE TABLE';
    `
  );
  return new Set(res.rows.map((r: any) => String(r.table_name).toLowerCase()));
}

async function getCount(client: any, table: string): Promise<number> {
  const res = await q(client, `SELECT COUNT(*)::int AS n FROM ${table};`);
  return Number(res.rows[0]?.n ?? 0);
}

async function missingTextCount(client: any, table: string, column: string): Promise<number> {
  const res = await q(
    client,
    `SELECT COUNT(*)::int AS n FROM ${table} WHERE ${column} IS NULL OR btrim(${column}::text) = '';`
  );
  return Number(res.rows[0]?.n ?? 0);
}

async function nullCount(client: any, table: string, column: string): Promise<number> {
  const res = await q(client, `SELECT COUNT(*)::int AS n FROM ${table} WHERE ${column} IS NULL;`);
  return Number(res.rows[0]?.n ?? 0);
}

async function primaryKeyChecks(client: any, table: string, pk: string[]): Promise<CheckResult[]> {
  if (!(await tableExists(client, table))) return [];

  const out: CheckResult[] = [];
  const total = await getCount(client, table);
  const keyExpr = pk.map((c) => `COALESCE(${c}::text, '<NULL>')`).join(` || '|' || `);

  const distinctRes = await q(client, `SELECT COUNT(DISTINCT ${keyExpr})::int AS n FROM ${table};`);
  const distinct = Number(distinctRes.rows[0]?.n ?? 0);
  const duplicates = Math.max(0, total - distinct);

  out.push(check(`${table}: primary key uniqueness`, "primary_keys", "error", duplicates === 0, {
    value: duplicates,
    total,
    rate: safeRate(total - duplicates, total),
    details: { pk },
  }));

  for (const col of pk) {
    const missing = await nullCount(client, table, col);
    out.push(check(`${table}.${col}: primary key not null`, "primary_keys", "error", missing === 0, {
      value: missing,
      total,
      rate: safeRate(total - missing, total),
    }));
  }

  return out;
}

async function orphanCheck(
  client: any,
  name: string,
  fromTable: string,
  fromCol: string,
  toTable: string,
  toCol: string
): Promise<CheckResult> {
  if (!(await tableExists(client, fromTable)) || !(await tableExists(client, toTable))) {
    return check(name, "foreign_keys", "warning", false, {
      details: { skipped: "missing_table", fromTable, toTable },
    });
  }

  const sql = `
    SELECT COUNT(*)::int AS n
    FROM ${fromTable} f
    LEFT JOIN ${toTable} t ON f.${fromCol} = t.${toCol}
    WHERE f.${fromCol} IS NOT NULL
      AND t.${toCol} IS NULL;
  `;
  const totalSql = `
    SELECT COUNT(*)::int AS n
    FROM ${fromTable}
    WHERE ${fromCol} IS NOT NULL;
  `;

  const orphanCount = Number((await q(client, sql)).rows[0]?.n ?? 0);
  const totalRefs = Number((await q(client, totalSql)).rows[0]?.n ?? 0);

  return check(name, "foreign_keys", "error", orphanCount === 0, {
    value: orphanCount,
    total: totalRefs,
    rate: safeRate(totalRefs - orphanCount, totalRefs),
    sql,
  });
}

async function schemaChecks(client: any) {
  const checks: CheckResult[] = [];
  const counts: Record<string, number> = {};
  const tables = await getExistingTables(client);

  for (const table of EXPECTED_TABLES) {
    const exists = tables.has(table);
    checks.push(check(`${table}: table exists`, "schema", "error", exists, { value: exists }));
    if (exists) counts[table] = await getCount(client, table);
  }

  for (const [table, columns] of Object.entries(REQUIRED_COLUMNS)) {
    if (!tables.has(table)) continue;
    for (const column of columns) {
      const exists = await columnExists(client, table, column);
      checks.push(check(`${table}.${column}: column exists`, "schema", "error", exists, { value: exists }));
    }
  }

  const schemaChecksOnly = checks.filter((c) => c.category === "schema");
  const schemaCompleteness =
    schemaChecksOnly.filter((c) => c.passed).length / Math.max(1, schemaChecksOnly.length);

  return {
    checks,
    counts,
    components: { schema_completeness: schemaCompleteness },
  };
}

async function relationalChecks(client: any) {
  const checks: CheckResult[] = [];

  for (const [table, pk] of Object.entries(PRIMARY_KEYS)) {
    checks.push(...await primaryKeyChecks(client, table, pk));
  }

  checks.push(...await Promise.all([
    orphanCheck(client, "Domain.faculty_id -> Faculty", "domain", "faculty_id", "faculty", "faculty_id"),
    orphanCheck(client, "StudyProgram.faculty_id -> Faculty", "studyprogram", "faculty_id", "faculty", "faculty_id"),
    orphanCheck(client, "StudyProgram.director -> Professor", "studyprogram", "director", "professor", "prof_id"),
    orphanCheck(client, "Course.faculty_id -> Faculty", "course", "faculty_id", "faculty", "faculty_id"),
    orphanCheck(client, "Course.domain_id -> Domain", "course", "domain_id", "domain", "domain_id"),
    orphanCheck(client, "has_lang.program_id -> StudyProgram", "has_lang", "program_id", "studyprogram", "program_id"),
    orphanCheck(client, "has_lang.lang_id -> Language", "has_lang", "lang_id", "language", "lang_id"),
    orphanCheck(client, "teaches.code -> Course", "teaches", "code", "course", "code"),
    orphanCheck(client, "teaches.prof_id -> Professor", "teaches", "prof_id", "professor", "prof_id"),
    orphanCheck(client, "consist_of.program_id -> StudyProgram", "consist_of", "program_id", "studyprogram", "program_id"),
    orphanCheck(client, "consist_of.code -> Course", "consist_of", "code", "course", "code"),
    orphanCheck(client, "CourseOffering.code -> Course", "courseoffering", "code", "course", "code"),
    orphanCheck(client, "CourseOffering.sem_id -> Semester", "courseoffering", "sem_id", "semester", "sem_id"),
    orphanCheck(client, "Session.offering_id -> CourseOffering", "session", "offering_id", "courseoffering", "offering_id"),
    orphanCheck(client, "Session.room_id -> Room", "session", "room_id", "room", "room_id"),
    orphanCheck(client, "is_taught_in.offering_id -> CourseOffering", "is_taught_in", "offering_id", "courseoffering", "offering_id"),
    orphanCheck(client, "is_taught_in.lang_id -> Language", "is_taught_in", "lang_id", "language", "lang_id"),
    orphanCheck(client, "Evaluation.offering_id -> CourseOffering", "evaluation", "offering_id", "courseoffering", "offering_id"),
    orphanCheck(client, "examined_in.room_id -> Room", "examined_in", "room_id", "room", "room_id"),
    orphanCheck(client, "examined_in.eval_id -> Evaluation", "examined_in", "eval_id", "evaluation", "eval_id"),
    orphanCheck(client, "programDocument.program_id -> StudyProgram", "programdocument", "program_id", "studyprogram", "program_id"),
    orphanCheck(client, "programCourseStaging.program_id -> StudyProgram", "programcoursestaging", "program_id", "studyprogram", "program_id"),
    orphanCheck(client, "programCourseStaging.source_doc_id -> programDocument", "programcoursestaging", "source_doc_id", "programdocument", "doc_id"),
  ]));

  const relevant = checks.filter((c) => c.category === "primary_keys" || c.category === "foreign_keys");
  const relationalIntegrity = relevant.filter((c) => c.passed).length / Math.max(1, relevant.length);

  return {
    checks,
    components: { relational_integrity: relationalIntegrity },
  };
}

async function studyProgramChecks(client: any) {
  const checks: CheckResult[] = [];
  const samples: Record<string, any[]> = {};

  if (!(await tableExists(client, "studyprogram"))) {
    return { checks, samples, components: { studyprogram_quality: 0 } };
  }

  const total = await getCount(client, "studyprogram");

  const requiredFields = ["name", "degree_level", "total_ects", "faculty_id"];
  const completenessRates: number[] = [];

  for (const col of requiredFields) {
    const missing = col === "total_ects" || col === "faculty_id"
      ? await nullCount(client, "studyprogram", col)
      : await missingTextCount(client, "studyprogram", col);

    completenessRates.push(safeRate(total - missing, total) ?? 0);
    checks.push(check(`StudyProgram.${col}: completeness`, "studyprogram", "error", missing === 0, {
      value: missing,
      total,
      rate: safeRate(total - missing, total),
    }));
  }

  const multilingualNamesSql = `
    SELECT COUNT(*)::int AS n
    FROM studyprogram
    WHERE name_en IS NOT NULL OR name_de IS NOT NULL OR name_fr IS NOT NULL;
  `;
  const multilingualNames = Number((await q(client, multilingualNamesSql)).rows[0]?.n ?? 0);
  checks.push(check("StudyProgram: at least one multilingual name field present", "studyprogram", "info", multilingualNames === total, {
    value: multilingualNames,
    total,
    rate: safeRate(multilingualNames, total),
    sql: multilingualNamesSql,
  }));

  const invalidDegreeSql = `
    SELECT COUNT(*)::int AS n
    FROM studyprogram
    WHERE degree_level NOT IN ('Bachelor','Master','Doctorate');
  `;
  const invalidDegree = Number((await q(client, invalidDegreeSql)).rows[0]?.n ?? 0);
  checks.push(check("StudyProgram.degree_level: valid values", "studyprogram", "error", invalidDegree === 0, {
    value: invalidDegree,
    total,
    rate: safeRate(total - invalidDegree, total),
    sql: invalidDegreeSql,
  }));

  const invalidEctsSql = `
    SELECT COUNT(*)::int AS n
    FROM studyprogram
    WHERE total_ects IS NULL OR total_ects <= 0 OR total_ects > 300;
  `;
  const invalidEcts = Number((await q(client, invalidEctsSql)).rows[0]?.n ?? 0);
  checks.push(check("StudyProgram.total_ects: plausible range", "studyprogram", "error", invalidEcts === 0, {
    value: invalidEcts,
    total,
    rate: safeRate(total - invalidEcts, total),
    sql: invalidEctsSql,
  }));

  const defaultFacultySql = `
    SELECT COUNT(*)::int AS n
    FROM studyprogram
    WHERE faculty_id = 100;
  `;
  const defaultFacultyCount = Number((await q(client, defaultFacultySql)).rows[0]?.n ?? 0);
  checks.push(check("StudyProgram: not assigned to default faculty", "studyprogram", "warning", defaultFacultyCount === 0, {
    value: defaultFacultyCount,
    total,
    rate: safeRate(total - defaultFacultyCount, total),
    sql: defaultFacultySql,
  }));

  const withDocsSql = `
    SELECT COUNT(DISTINCT sp.program_id)::int AS n
    FROM studyprogram sp
    JOIN programdocument pd ON pd.program_id = sp.program_id;
  `;
  const withDocs = Number((await q(client, withDocsSql)).rows[0]?.n ?? 0);
  checks.push(check("StudyProgram: programmes with at least one ProgramDocument", "studyprogram", "warning", withDocs === total, {
    value: withDocs,
    total,
    rate: safeRate(withDocs, total),
    sql: withDocsSql,
  }));

  const withCourseLinksSql = `
    SELECT COUNT(DISTINCT sp.program_id)::int AS n
    FROM studyprogram sp
    JOIN consist_of co ON co.program_id = sp.program_id;
  `;
  const withCourseLinks = Number((await q(client, withCourseLinksSql)).rows[0]?.n ?? 0);
  checks.push(check("StudyProgram: programmes with at least one final course link", "studyprogram", "warning", withCourseLinks === total, {
    value: withCourseLinks,
    total,
    rate: safeRate(withCourseLinks, total),
    sql: withCourseLinksSql,
  }));

  samples["studyprogram_default_faculty_first_25"] = (await q(client, `
    SELECT program_id, name, degree_level, total_ects, source_last_page_url
    FROM studyprogram
    WHERE faculty_id = 100
    ORDER BY name
    LIMIT 25;
  `)).rows;

  samples["studyprogram_without_course_links_first_25"] = (await q(client, `
    SELECT sp.program_id, sp.name, sp.degree_level, sp.total_ects, f.name_en AS faculty
    FROM studyprogram sp
    LEFT JOIN faculty f ON f.faculty_id = sp.faculty_id
    LEFT JOIN consist_of co ON co.program_id = sp.program_id
    WHERE co.program_id IS NULL
    ORDER BY sp.name
    LIMIT 25;
  `)).rows;

  samples["studyprogram_duplicate_name_degree_ects_first_25"] = (await q(client, `
    SELECT lower(name) AS name_norm, degree_level, total_ects, COUNT(*)::int AS n
    FROM studyprogram
    GROUP BY lower(name), degree_level, total_ects
    HAVING COUNT(*) > 1
    ORDER BY n DESC, name_norm
    LIMIT 25;
  `)).rows;

  const duplicatePenalty = samples["studyprogram_duplicate_name_degree_ects_first_25"].length === 0 ? 1 : 0.8;

  const component =
    0.35 * (completenessRates.reduce((a, b) => a + b, 0) / Math.max(1, completenessRates.length))
    + 0.15 * (safeRate(total - invalidDegree, total) ?? 0)
    + 0.15 * (safeRate(total - invalidEcts, total) ?? 0)
    + 0.10 * (safeRate(total - defaultFacultyCount, total) ?? 0)
    + 0.15 * (safeRate(withDocs, total) ?? 0)
    + 0.10 * (safeRate(withCourseLinks, total) ?? 0);

  return {
    checks,
    samples,
    components: {
      studyprogram_quality: clamp01(component * duplicatePenalty),
    },
  };
}

async function documentChecks(client: any) {
  const checks: CheckResult[] = [];
  const samples: Record<string, any[]> = {};

  if (!(await tableExists(client, "programdocument"))) {
    return { checks, samples, components: { document_quality: 0 } };
  }

  const total = await getCount(client, "programdocument");

  const requiredFields = ["program_id", "url", "doc_type"];
  const completenessRates: number[] = [];

  for (const col of requiredFields) {
    const missing = col === "program_id"
      ? await nullCount(client, "programdocument", col)
      : await missingTextCount(client, "programdocument", col);

    completenessRates.push(safeRate(total - missing, total) ?? 0);
    checks.push(check(`programDocument.${col}: completeness`, "documents", "error", missing === 0, {
      value: missing,
      total,
      rate: safeRate(total - missing, total),
    }));
  }

  const validDocTypeSql = `
    SELECT COUNT(*)::int AS n
    FROM programdocument
    WHERE doc_type IN ('study_plan','regulation','brochure','other');
  `;
  const validDocTypes = Number((await q(client, validDocTypeSql)).rows[0]?.n ?? 0);
  checks.push(check("programDocument.doc_type: valid values", "documents", "error", validDocTypes === total, {
    value: validDocTypes,
    total,
    rate: safeRate(validDocTypes, total),
    sql: validDocTypeSql,
  }));

  const parsedDocsSql = `
    SELECT COUNT(*)::int AS n
    FROM programdocument
    WHERE parse_status IS NULL
       OR lower(parse_status) IN ('ok','parsed','downloaded','already_present','success');
  `;
  const parsedDocs = Number((await q(client, parsedDocsSql)).rows[0]?.n ?? 0);
  checks.push(check("programDocument.parse_status: ok-like or NULL", "documents", "warning", parsedDocs === total, {
    value: parsedDocs,
    total,
    rate: safeRate(parsedDocs, total),
    sql: parsedDocsSql,
  }));

  const withLabelSql = `
    SELECT COUNT(*)::int AS n
    FROM programdocument
    WHERE label IS NOT NULL AND btrim(label) <> '';
  `;
  const withLabel = Number((await q(client, withLabelSql)).rows[0]?.n ?? 0);
  checks.push(check("programDocument.label: completeness", "documents", "info", withLabel === total, {
    value: withLabel,
    total,
    rate: safeRate(withLabel, total),
    sql: withLabelSql,
  }));

  const uniqueUrlSql = `
    SELECT COUNT(DISTINCT url)::int AS n
    FROM programdocument
    WHERE url IS NOT NULL AND btrim(url) <> '';
  `;
  const uniqueUrls = Number((await q(client, uniqueUrlSql)).rows[0]?.n ?? 0);

  const totalPrograms = await getCount(client, "studyprogram");
  const programsWithDocsSql = `
    SELECT COUNT(DISTINCT program_id)::int AS n
    FROM programdocument;
  `;
  const programsWithDocs = Number((await q(client, programsWithDocsSql)).rows[0]?.n ?? 0);

  samples["programdocument_parse_status_distribution"] = (await q(client, `
    SELECT COALESCE(parse_status, '<NULL>') AS parse_status, COUNT(*)::int AS n
    FROM programdocument
    GROUP BY COALESCE(parse_status, '<NULL>')
    ORDER BY n DESC;
  `)).rows;

  samples["programdocument_doc_type_distribution"] = (await q(client, `
    SELECT doc_type, COUNT(*)::int AS n
    FROM programdocument
    GROUP BY doc_type
    ORDER BY n DESC;
  `)).rows;

  samples["programdocument_shared_urls_top_25"] = (await q(client, `
    SELECT url, COUNT(*)::int AS refs, COUNT(DISTINCT program_id)::int AS programmes
    FROM programdocument
    GROUP BY url
    HAVING COUNT(*) > 1
    ORDER BY refs DESC
    LIMIT 25;
  `)).rows;

  const duplicateProgramUrlTypeRows = (await q(client, `
    SELECT program_id, url, doc_type, COUNT(*)::int AS n
    FROM programdocument
    GROUP BY program_id, url, doc_type
    HAVING COUNT(*) > 1
    ORDER BY n DESC
    LIMIT 25;
  `)).rows;

  checks.push(check("programDocument: unique per program/url/doc_type", "documents", "error", duplicateProgramUrlTypeRows.length === 0, {
    value: duplicateProgramUrlTypeRows.length,
    details: { first_25: duplicateProgramUrlTypeRows },
  }));

  const component =
    0.30 * (completenessRates.reduce((a, b) => a + b, 0) / Math.max(1, completenessRates.length))
    + 0.15 * (safeRate(validDocTypes, total) ?? 0)
    + 0.20 * (safeRate(parsedDocs, total) ?? 0)
    + 0.15 * (safeRate(programsWithDocs, totalPrograms) ?? 0)
    + 0.10 * (safeRate(uniqueUrls, total) ?? 0)
    + 0.10 * (duplicateProgramUrlTypeRows.length === 0 ? 1 : 0);

  return {
    checks,
    samples,
    components: {
      document_quality: clamp01(component),
    },
  };
}

async function stagingChecks(client: any) {
  const checks: CheckResult[] = [];
  const samples: Record<string, any[]> = {};

  if (!(await tableExists(client, "programcoursestaging"))) {
    return { checks, samples, components: { parser_import_quality: 0 } };
  }

  const total = await getCount(client, "programcoursestaging");

  const rawTextPresentSql = `
    SELECT COUNT(*)::int AS n
    FROM programcoursestaging
    WHERE raw_text IS NOT NULL AND btrim(raw_text) <> '';
  `;
  const rawTextPresent = Number((await q(client, rawTextPresentSql)).rows[0]?.n ?? 0);

  const sourceDocPresentSql = `
    SELECT COUNT(*)::int AS n
    FROM programcoursestaging
    WHERE source_doc_id IS NOT NULL;
  `;
  const sourceDocPresent = Number((await q(client, sourceDocPresentSql)).rows[0]?.n ?? 0);

  const codePresentSql = `
    SELECT COUNT(*)::int AS n
    FROM programcoursestaging
    WHERE extracted_code IS NOT NULL AND btrim(extracted_code) <> '';
  `;
  const codePresent = Number((await q(client, codePresentSql)).rows[0]?.n ?? 0);

  const codeValidSql = `
    SELECT COUNT(*)::int AS n
    FROM programcoursestaging s
    JOIN course c ON c.code = btrim(s.extracted_code)
    WHERE s.extracted_code IS NOT NULL AND btrim(s.extracted_code) <> '';
  `;
  const codeValid = Number((await q(client, codeValidSql)).rows[0]?.n ?? 0);

  const titlePresentSql = `
    SELECT COUNT(*)::int AS n
    FROM programcoursestaging
    WHERE extracted_title IS NOT NULL AND btrim(extracted_title) <> '';
  `;
  const titlePresent = Number((await q(client, titlePresentSql)).rows[0]?.n ?? 0);

  const inferredTypePresentSql = `
    SELECT COUNT(*)::int AS n
    FROM programcoursestaging
    WHERE inferred_type IN ('Mandatory','Elective');
  `;
  const inferredTypePresent = Number((await q(client, inferredTypePresentSql)).rows[0]?.n ?? 0);

  checks.push(check("programCourseStaging.raw_text: present", "staging", "error", rawTextPresent === total, {
    value: rawTextPresent,
    total,
    rate: safeRate(rawTextPresent, total),
    sql: rawTextPresentSql,
  }));
  checks.push(check("programCourseStaging.source_doc_id: present", "staging", "warning", sourceDocPresent === total, {
    value: sourceDocPresent,
    total,
    rate: safeRate(sourceDocPresent, total),
    sql: sourceDocPresentSql,
  }));
  checks.push(check("programCourseStaging.extracted_code: present", "staging", "warning", codePresent === total, {
    value: codePresent,
    total,
    rate: safeRate(codePresent, total),
    sql: codePresentSql,
  }));
  checks.push(check("programCourseStaging.extracted_code: exists in Course", "staging", "warning", codeValid === codePresent, {
    value: codeValid,
    total: codePresent,
    rate: safeRate(codeValid, codePresent),
    sql: codeValidSql,
  }));
  checks.push(check("programCourseStaging.extracted_title: present", "staging", "info", titlePresent === total, {
    value: titlePresent,
    total,
    rate: safeRate(titlePresent, total),
    sql: titlePresentSql,
  }));
  checks.push(check("programCourseStaging.inferred_type: present", "staging", "info", inferredTypePresent === total, {
    value: inferredTypePresent,
    total,
    rate: safeRate(inferredTypePresent, total),
    sql: inferredTypePresentSql,
  }));

  samples["staging_invalid_course_codes_first_25"] = (await q(client, `
    SELECT s.program_id, s.extracted_code, s.extracted_title, s.inferred_type, s.section, LEFT(s.raw_text, 300) AS raw_text_sample
    FROM programcoursestaging s
    LEFT JOIN course c ON c.code = btrim(s.extracted_code)
    WHERE s.extracted_code IS NOT NULL
      AND btrim(s.extracted_code) <> ''
      AND c.code IS NULL
    LIMIT 25;
  `)).rows;

  samples["staging_rows_per_program_top_25"] = (await q(client, `
    SELECT sp.program_id, sp.name, COUNT(*)::int AS staging_rows, COUNT(DISTINCT btrim(s.extracted_code))::int AS distinct_codes
    FROM programcoursestaging s
    JOIN studyprogram sp ON sp.program_id = s.program_id
    GROUP BY sp.program_id, sp.name
    ORDER BY staging_rows DESC
    LIMIT 25;
  `)).rows;

  const component =
    0.20 * (safeRate(rawTextPresent, total) ?? 0)
    + 0.20 * (safeRate(sourceDocPresent, total) ?? 0)
    + 0.25 * (safeRate(codePresent, total) ?? 0)
    + 0.20 * (safeRate(codeValid, codePresent) ?? 0)
    + 0.10 * (safeRate(titlePresent, total) ?? 0)
    + 0.05 * (safeRate(inferredTypePresent, total) ?? 0);

  return {
    checks,
    samples,
    components: {
      parser_import_quality: clamp01(component),
    },
  };
}

async function finalProgramCourseChecks(client: any) {
  const checks: CheckResult[] = [];
  const samples: Record<string, any[]> = {};

  if (!(await tableExists(client, "consist_of"))) {
    return { checks, samples, components: { final_program_course_quality: 0 } };
  }

  const total = await getCount(client, "consist_of");
  const totalPrograms = await getCount(client, "studyprogram");

  const validCourseTypesSql = `
    SELECT COUNT(*)::int AS n
    FROM consist_of
    WHERE course_type IN ('Mandatory','Elective');
  `;
  const validCourseTypes = Number((await q(client, validCourseTypesSql)).rows[0]?.n ?? 0);

  const courseNamePresentSql = `
    SELECT COUNT(*)::int AS n
    FROM consist_of
    WHERE course_name IS NOT NULL AND btrim(course_name) <> '';
  `;
  const courseNamePresent = Number((await q(client, courseNamePresentSql)).rows[0]?.n ?? 0);

  const programsWithFinalLinksSql = `
    SELECT COUNT(DISTINCT program_id)::int AS n
    FROM consist_of;
  `;
  const programsWithFinalLinks = Number((await q(client, programsWithFinalLinksSql)).rows[0]?.n ?? 0);

  const duplicateLinks = (await q(client, `
    SELECT program_id, code, COUNT(*)::int AS n
    FROM consist_of
    GROUP BY program_id, code
    HAVING COUNT(*) > 1
    ORDER BY n DESC
    LIMIT 25;
  `)).rows;

  checks.push(check("consist_of.course_type: valid", "consist_of", "warning", validCourseTypes === total, {
    value: validCourseTypes,
    total,
    rate: safeRate(validCourseTypes, total),
    sql: validCourseTypesSql,
  }));
  checks.push(check("consist_of.course_name: present", "consist_of", "info", courseNamePresent === total, {
    value: courseNamePresent,
    total,
    rate: safeRate(courseNamePresent, total),
    sql: courseNamePresentSql,
  }));
  checks.push(check("StudyProgram: programmes with final consist_of links", "consist_of", "warning", programsWithFinalLinks === totalPrograms, {
    value: programsWithFinalLinks,
    total: totalPrograms,
    rate: safeRate(programsWithFinalLinks, totalPrograms),
    sql: programsWithFinalLinksSql,
  }));
  checks.push(check("consist_of: no duplicate program/course links", "consist_of", "error", duplicateLinks.length === 0, {
    value: duplicateLinks.length,
    details: { first_25: duplicateLinks },
  }));

  samples["programs_with_fewest_final_course_links_first_30"] = (await q(client, `
    SELECT sp.program_id, sp.name, sp.degree_level, sp.total_ects, COUNT(co.code)::int AS course_count
    FROM studyprogram sp
    LEFT JOIN consist_of co ON co.program_id = sp.program_id
    GROUP BY sp.program_id, sp.name, sp.degree_level, sp.total_ects
    ORDER BY course_count ASC, sp.name
    LIMIT 30;
  `)).rows;

  samples["consist_of_course_type_distribution"] = (await q(client, `
    SELECT COALESCE(course_type, '<NULL>') AS course_type, COUNT(*)::int AS n
    FROM consist_of
    GROUP BY COALESCE(course_type, '<NULL>')
    ORDER BY n DESC;
  `)).rows;

  const component =
    0.25 * (safeRate(validCourseTypes, total) ?? 0)
    + 0.15 * (safeRate(courseNamePresent, total) ?? 0)
    + 0.40 * (safeRate(programsWithFinalLinks, totalPrograms) ?? 0)
    + 0.20 * (duplicateLinks.length === 0 ? 1 : 0);

  return {
    checks,
    samples,
    components: {
      final_program_course_quality: clamp01(component),
    },
  };
}

async function courseCatalogueChecks(client: any) {
  const checks: CheckResult[] = [];
  const samples: Record<string, any[]> = {};

  if (!(await tableExists(client, "course"))) {
    return { checks, samples, components: { course_catalogue_quality: 0 } };
  }

  const total = await getCount(client, "course");

  const namePresentSql = `
    SELECT COUNT(*)::int AS n
    FROM course
    WHERE name IS NOT NULL AND btrim(name) <> '';
  `;
  const namePresent = Number((await q(client, namePresentSql)).rows[0]?.n ?? 0);

  const ectsPlausibleSql = `
    SELECT COUNT(*)::int AS n
    FROM course
    WHERE ects IS NOT NULL AND ects >= 0 AND ects <= 60;
  `;
  const ectsPlausible = Number((await q(client, ectsPlausibleSql)).rows[0]?.n ?? 0);

  const withOfferingSql = `
    SELECT COUNT(DISTINCT c.code)::int AS n
    FROM course c
    JOIN courseoffering o ON o.code = c.code;
  `;
  const withOffering = Number((await q(client, withOfferingSql)).rows[0]?.n ?? 0);

  const withTeacherSql = `
    SELECT COUNT(DISTINCT c.code)::int AS n
    FROM course c
    JOIN teaches t ON t.code = c.code;
  `;
  const withTeacher = Number((await q(client, withTeacherSql)).rows[0]?.n ?? 0);

  checks.push(check("Course.name: present", "courses", "error", namePresent === total, {
    value: namePresent,
    total,
    rate: safeRate(namePresent, total),
    sql: namePresentSql,
  }));
  checks.push(check("Course.ects: plausible", "courses", "warning", ectsPlausible === total, {
    value: ectsPlausible,
    total,
    rate: safeRate(ectsPlausible, total),
    sql: ectsPlausibleSql,
  }));
  checks.push(check("Course: has CourseOffering", "courses", "info", withOffering === total, {
    value: withOffering,
    total,
    rate: safeRate(withOffering, total),
    sql: withOfferingSql,
  }));
  checks.push(check("Course: has teacher relation", "courses", "info", withTeacher === total, {
    value: withTeacher,
    total,
    rate: safeRate(withTeacher, total),
    sql: withTeacherSql,
  }));

  samples["courses_without_offering_first_25"] = (await q(client, `
    SELECT c.code, c.name, c.ects
    FROM course c
    LEFT JOIN courseoffering o ON o.code = c.code
    WHERE o.code IS NULL
    ORDER BY c.code
    LIMIT 25;
  `)).rows;

  const component =
    0.35 * (safeRate(namePresent, total) ?? 0)
    + 0.25 * (safeRate(ectsPlausible, total) ?? 0)
    + 0.25 * (safeRate(withOffering, total) ?? 0)
    + 0.15 * (safeRate(withTeacher, total) ?? 0);

  return {
    checks,
    samples,
    components: {
      course_catalogue_quality: clamp01(component),
    },
  };
}

function summarizeChecks(checks: CheckResult[]) {
  const byCategory: Record<string, any> = {};
  const bySeverity: Record<string, any> = {};

  for (const c of checks) {
    if (!byCategory[c.category]) byCategory[c.category] = { total: 0, passed: 0, failed: 0 };
    if (!bySeverity[c.severity]) bySeverity[c.severity] = { total: 0, passed: 0, failed: 0 };

    byCategory[c.category].total++;
    bySeverity[c.severity].total++;

    if (c.passed) {
      byCategory[c.category].passed++;
      bySeverity[c.severity].passed++;
    } else {
      byCategory[c.category].failed++;
      bySeverity[c.severity].failed++;
    }
  }

  return {
    total_checks: checks.length,
    passed: checks.filter((c) => c.passed).length,
    failed: checks.filter((c) => !c.passed).length,
    failed_errors: checks.filter((c) => !c.passed && c.severity === "error").length,
    failed_warnings: checks.filter((c) => !c.passed && c.severity === "warning").length,
    failed_info: checks.filter((c) => !c.passed && c.severity === "info").length,
    by_category: byCategory,
    by_severity: bySeverity,
  };
}

function computeScore(components: Record<string, number>) {
  let score = 0;
  let weightSum = 0;
  const details: Record<string, any> = {};

  for (const [key, weight] of Object.entries(SCORE_WEIGHTS)) {
    const value = clamp01(components[key]);
    const contribution = value * weight;
    score += contribution;
    weightSum += weight;
    details[key] = {
      value: Number(value.toFixed(6)),
      weight,
      contribution: Number(contribution.toFixed(6)),
    };
  }

  return {
    score: Number(score.toFixed(6)),
    normalized_score: Number((score / Math.max(weightSum, 1e-9)).toFixed(6)),
    weights: SCORE_WEIGHTS,
    details,
  };
}

async function run() {
  const args = parseArgs(process.argv);
  const outPath = String(args.out ?? "scrapy_crawler/scrapy_crawler/validation/metrics/database/database_quality.json");

  const client = await DataAccessController.pool.connect();

  try {
    const checks: CheckResult[] = [];
    const samples: Record<string, any[]> = {};
    const components: Record<string, number> = {};

    const schema = await schemaChecks(client);
    checks.push(...schema.checks);
    Object.assign(components, schema.components);

    const relational = await relationalChecks(client);
    checks.push(...relational.checks);
    Object.assign(components, relational.components);

    const studyPrograms = await studyProgramChecks(client);
    checks.push(...studyPrograms.checks);
    Object.assign(samples, studyPrograms.samples);
    Object.assign(components, studyPrograms.components);

    const documents = await documentChecks(client);
    checks.push(...documents.checks);
    Object.assign(samples, documents.samples);
    Object.assign(components, documents.components);

    const staging = await stagingChecks(client);
    checks.push(...staging.checks);
    Object.assign(samples, staging.samples);
    Object.assign(components, staging.components);

    const finalLinks = await finalProgramCourseChecks(client);
    checks.push(...finalLinks.checks);
    Object.assign(samples, finalLinks.samples);
    Object.assign(components, finalLinks.components);

    const courses = await courseCatalogueChecks(client);
    checks.push(...courses.checks);
    Object.assign(samples, courses.samples);
    Object.assign(components, courses.components);

    const summary = summarizeChecks(checks);
    const score = computeScore(components);

    const result = {
      generated_at: new Date().toISOString(),
      metric_scope: "database_quality",
      description: [
        "Final database validation layer for the thesis pipeline.",
        "Runs read-only SQL checks against the Docker/Postgres database using the same DataAccessController as the schema/import scripts.",
        "The score estimates database readiness for reliable chatbot/backend usage, not full manual semantic correctness.",
      ],
      table_counts: schema.counts,
      score,
      components,
      summary,
      checks,
      samples,
      thesis_interpretation: [
        "Database quality is evaluated after JSON acquisition, document acquisition, document parsing, and import.",
        "This layer verifies that the final relational state is complete, connected, and usable by backend APIs.",
        "Strong signals are StudyProgram completeness, programme-document coverage, parsed/staged course extraction quality, and final consist_of programme-course links.",
        "Warnings should be discussed as data limitations or extraction/import limitations rather than application runtime failures.",
      ],
    };

    ensureParentDir(outPath);
    fs.writeFileSync(outPath, JSON.stringify(result, null, 2), "utf8");

    console.log("=== DATABASE QUALITY VALIDATION ===");
    console.log(`Output: ${outPath}`);
    console.log(`Score: ${score.score}`);
    console.log(`Checks passed: ${summary.passed}/${summary.total_checks}`);
    console.log(`Failed errors: ${summary.failed_errors}`);
    console.log(`Failed warnings: ${summary.failed_warnings}`);
    console.log("");
    console.log("Components:");
    for (const [key, detail] of Object.entries(score.details)) {
      console.log(`- ${key}: ${(detail as any).value}`);
    }
  } finally {
    client.release();
    await DataAccessController.pool.end();
  }
}

run().catch((err) => {
  console.error("❌ Database validation failed");
  console.error(err);
  process.exit(1);
});
