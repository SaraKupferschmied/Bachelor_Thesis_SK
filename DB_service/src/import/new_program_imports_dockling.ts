// DB_service/src/import/new_program_import_docling_fixed.ts

import "../environments/environment";
import * as fs from "fs";
import * as path from "path";
import { DataAccessController } from "../control/data_access_controller";

type CourseType = "Mandatory" | "Elective";
type DocType = "study_plan" | "regulation" | "brochure" | "other";

type ParsedDocling = {
  meta: any;
  body: string;
  pages: string[];
  sourceUrl: string | null;
};

type ReferenceType = "course" | "module";

type StagingCourse = {
  raw_text: string;
  reference_type: ReferenceType;
  extracted_code: string | null;
  extracted_module: string | null;
  extracted_title: string | null;
  inferred_type: CourseType | null;
  page_no: number;
  section: string | null;
};

function normalize(s: string | null | undefined): string {
  return (s ?? "").toLowerCase().replace(/\s+/g, " ").trim();
}

function normalizeComparable(s: string | null | undefined): string {
  return (s ?? "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/&/g, " and ")
    .replace(/[^a-zA-Z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function uniqueNonEmpty(values: Array<string | null | undefined>): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const cleaned = String(value ?? "").replace(/_/g, " ").replace(/\s+/g, " ").trim();
    if (!cleaned) continue;
    const key = normalizeComparable(cleaned);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(cleaned);
  }
  return out;
}

function stripNullBytes(s: string | null | undefined): string {
  return (s ?? "").replace(/\u0000/g, "");
}

function normalizeUrl(u: string | null | undefined): string {
  return (u ?? "").trim().replace(/\/+$/, "");
}

function readTxtFiles(dir: string): string[] {
  if (!fs.existsSync(dir)) throw new Error(`Missing parsed Docling directory: ${dir}`);
  return fs.readdirSync(dir)
    .filter((f) => f.endsWith(".txt"))
    .map((f) => path.join(dir, f));
}

function parseMetadata(raw: string): { meta: any; bodyStart: number } {
  const startMarker = "---METADATA_JSON---";
  const endMarker = "---/METADATA_JSON---";
  const start = raw.indexOf(startMarker);
  const end = raw.indexOf(endMarker);

  if (start >= 0 && end > start) {
    const jsonStr = raw.slice(start + startMarker.length, end).trim();
    return { meta: JSON.parse(jsonStr), bodyStart: end + endMarker.length };
  }

  // Fallback for files that have only ---METADATA_JSON--- followed by a JSON object.
  const m = raw.match(/---METADATA_JSON---\s*(\{[\s\S]*?\})\s*/);
  if (!m || m.index == null) return { meta: {}, bodyStart: 0 };
  return { meta: JSON.parse(m[1]), bodyStart: m.index + m[0].length };
}

function splitPages(body: string): string[] {
  const pageRx = /---PAGE\s+(\d+)---\s*\n/g;
  const markers: { idx: number; contentStart: number }[] = [];
  let m: RegExpExecArray | null;

  while ((m = pageRx.exec(body)) !== null) {
    markers.push({ idx: m.index, contentStart: m.index + m[0].length });
  }

  if (!markers.length) return [body.trim()].filter(Boolean);

  const pages: string[] = [];
  for (let i = 0; i < markers.length; i++) {
    const start = markers[i].contentStart;
    const end = i + 1 < markers.length ? markers[i + 1].idx : body.length;
    pages.push(body.slice(start, end).trim());
  }
  return pages;
}

function parseDocling(file: string): ParsedDocling {
  const raw = fs.readFileSync(file, "utf8");
  const { meta, bodyStart } = parseMetadata(raw);
  const body = raw.slice(bodyStart).trim();
  const pages = splitPages(body);

  const sourceUrl = normalizeUrl(
    meta.source_url ?? meta.url ?? meta.document_url ?? meta.pdf_url ?? null
  ) || null;

  return { meta, body, pages, sourceUrl };
}

function isTableLine(line: string): boolean {
  const trimmed = line.trim();
  return trimmed.startsWith("|") && trimmed.includes("|");
}

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.replace(/\s+/g, " ").trim());
}

function isSeparatorRow(cells: string[]): boolean {
  return cells.length > 0 && cells.every((c) => /^:?-{2,}:?$/.test(c) || c === "");
}

const COURSE_CODE_LOOSE_RX = /\b(?:UE\s*-\s*)?(?=[A-Z0-9]{2,4}\s*\.)[A-Z0-9]*[A-Z][A-Z0-9]*\s*\.\s*(?:[0-9]\s*){5}\b/g;
const COURSE_CODE_STRICT_RX = /^(?:UE-)?(?=[A-Z0-9]{2,4}\.[0-9]{5}$)[A-Z0-9]*[A-Z][A-Z0-9]*\.[0-9]{5}$/;

function normalizeCourseCode(raw: string): string | null {
  let s = raw.toUpperCase();
  s = s.replace(/\bUE\s*-\s*/g, "UE-");
  s = s.replace(/\s*\.\s*/g, ".");
  s = s.replace(/\s+/g, "");
  if (!COURSE_CODE_STRICT_RX.test(s)) return null;
  return s.startsWith("UE-") ? s : `UE-${s}`;
}

function extractCodes(text: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const rx = new RegExp(COURSE_CODE_LOOSE_RX.source, COURSE_CODE_LOOSE_RX.flags);
  let m: RegExpExecArray | null;

  while ((m = rx.exec(text)) !== null) {
    const code = normalizeCourseCode(m[0]);
    if (!code || seen.has(code)) continue;
    seen.add(code);
    out.push(code);
  }
  return out;
}

function inferType(section: string | null, row: string): CourseType | null {
  const t = normalize(`${section ?? ""} ${row}`);

  const elective = /\b(wahlkurs(?:e|en)?|wahlfach|wahlbereich|wahlmodul|wahlpflicht|elective|electives|optional|optionnel|optionnels|cours?\s+(?:à|a)\s+choix|module\s+(?:à|a)\s+choix|choix)\b/i.test(t);
  const mandatory = /\b(pflicht(?:modul)?|pflichtkurse?|pflichtveranstaltungen?|obligatorisch|obligatoire|obligatoires|mandatory|compulsory|required|core|tronc\s+commun|cours?\s+obligatoires?)\b/i.test(t);

  if (elective && !mandatory) return "Elective";
  if (mandatory && !elective) return "Mandatory";
  if (elective && mandatory) return /wahlpflicht/i.test(t) ? "Elective" : "Mandatory";
  return null;
}

function hasElectiveSignal(text: string): boolean {
  return /\b(wahl|wahlkurs|wahlkurse|wahlbereich|wahlmodul|wahlpflicht|optionnel|optionnelle|options?|cours?\s+(?:à|a)\s+choix|ue\s+(?:à|a)\s+choix|module\s+(?:à|a)\s+choix|choix|elective|optional)\b/i.test(text);
}

function extractElectiveEctsText(pages: string[], totalEcts: number | null): string | null {
  const candidates: string[] = [];
  const seen = new Set<string>();

  for (const page of pages) {
    for (const rawLine of page.split(/\r?\n/)) {
      const line = rawLine
        .replace(/^#{1,6}\s+/, "")
        .replace(/\|/g, " ")
        .replace(/\s+/g, " ")
        .trim();

      if (!line || !/\bECTS(?:-Kreditpunkte)?\b/i.test(line)) continue;
      if (!hasElectiveSignal(line)) continue;

      const nums = [...line.matchAll(/\b\d+(?:[.,]\d+)?\b/g)]
        .map((m) => Number(m[0].replace(",", ".")))
        .filter(Number.isFinite);

      // Avoid storing broad programme totals like 180 ECTS as elective requirements.
      if (totalEcts != null && nums.length === 1 && nums[0] === totalEcts) continue;
      if (/\bpour\s+un\s+total\s+de\b/i.test(line) && nums.some((n) => totalEcts != null && n === totalEcts)) continue;

      const key = normalizeComparable(line);
      if (seen.has(key)) continue;
      seen.add(key);
      candidates.push(line.slice(0, 240));
    }
  }

  return candidates.length ? candidates.join(" | ") : null;
}

function extractModuleReference(text: string): string | null {
  const cleaned = text
    .replace(/\s+/g, " ")
    .replace(/^[\s:–—-]+|[\s:–—-]+$/g, "")
    .trim();

  if (!cleaned) return null;

  // Capture module-only references such as:
  // - Modul 1 (15 ECTS) Grundlagen...
  // - Module 4 (21 ECTS) Sonderpädagogische Unterrichtspraxis
  // - Environmental Humanities Module / Geosciences Module
  // Keep this deliberately broad for staging; consist_of will still only import real Course matches.
  const hasModuleWord = /\b(module|modul|module\s+(?:à|a)\s+choix)\b/i.test(cleaned);
  const hasEctsOrNumber = /\b\d+(?:[.,]\d+)?\s*(?:ects|ects-kreditpunkte)\b/i.test(cleaned) || /\b(module|modul)\s*\d+/i.test(cleaned);

  if (!hasModuleWord || !hasEctsOrNumber) return null;
  if (extractCodes(cleaned).length > 0) return null;

  return cleaned.slice(0, 240);
}

function titleFromCells(cells: string[], code: string): string | null {
  const idx = cells.findIndex((c) => extractCodes(c).includes(code));
  if (idx < 0) return null;

  const candidates = [cells[idx + 1], cells[idx - 1]].filter(Boolean) as string[];
  for (const candidate of candidates) {
    const cleaned = candidate
      .replace(COURSE_CODE_LOOSE_RX, "")
      .replace(/^[\s:–—-]+|[\s:–—-]+$/g, "")
      .trim();
    if (cleaned.length >= 4 && /[A-Za-zÄÖÜäöüÀ-ÿ]/.test(cleaned)) {
      return cleaned.slice(0, 180);
    }
  }
  return null;
}

function extractCoursesFromPage(page: string, pageNo: number): StagingCourse[] {
  const lines = page.split(/\r?\n/);
  const results: StagingCourse[] = [];
  let section: string | null = null;
  let carryType: CourseType | null = null;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;

    const heading = line.match(/^#{1,6}\s+(.+)/);
    if (heading) {
      section = heading[1].trim();
      carryType = inferType(section, "") ?? carryType;

      const headingModule = extractModuleReference(section);
      if (headingModule) {
        results.push({
          raw_text: line,
          reference_type: "module",
          extracted_code: null,
          extracted_module: headingModule,
          extracted_title: headingModule,
          inferred_type: carryType,
          page_no: pageNo,
          section,
        });
      }

      continue;
    }

    const lineType = inferType(section, line);
    if (lineType) carryType = lineType;

    if (!isTableLine(line)) continue;

    const table: string[] = [];
    while (i < lines.length && isTableLine(lines[i])) {
      table.push(lines[i]);
      i++;
    }
    i--;

    for (const tableLine of table) {
      const cells = splitRow(tableLine);
      if (isSeparatorRow(cells)) continue;

      // Docling can embed logical headings inside a markdown table, e.g.
      // | Wahlkurse min. 14 ECTS | Wahlkurse min. 14 ECTS | ... |
      // Those rows must update the active section/type even though they are
      // not course rows themselves. Otherwise following rows keep the previous
      // heading, such as "Pflichtkurse".
      const rowText = cells.filter(Boolean).join(" ");
      const rowType = inferType(null, rowText);
      const codes = extractCodes(tableLine);

      if (!codes.length && rowType) {
        const headingCandidate =
          cells.find((c) => rowType === "Elective" ? /wahl|elective|choix|option/i.test(c) : /pflicht|obligatoire|obligatoires|mandatory|compulsory|required/i.test(c))
          ?? cells.find((c) => c.trim().length > 0)
          ?? rowText;
        section = headingCandidate.trim();
        carryType = rowType;

        const moduleRef = extractModuleReference(rowText);
        if (moduleRef) {
          results.push({
            raw_text: tableLine,
            reference_type: "module",
            extracted_code: null,
            extracted_module: moduleRef,
            extracted_title: moduleRef,
            inferred_type: rowType ?? carryType,
            page_no: pageNo,
            section,
          });
        }
        continue;
      }

      if (!codes.length) {
        const moduleRef = extractModuleReference(rowText);
        if (moduleRef) {
          results.push({
            raw_text: tableLine,
            reference_type: "module",
            extracted_code: null,
            extracted_module: moduleRef,
            extracted_title: moduleRef,
            inferred_type: rowType ?? carryType,
            page_no: pageNo,
            section,
          });
        }
        continue;
      }

      for (const code of codes) {
        results.push({
          raw_text: tableLine,
          reference_type: "course",
          extracted_code: code,
          extracted_module: null,
          extracted_title: titleFromCells(cells, code),
          inferred_type: rowType ?? carryType,
          page_no: pageNo,
          section,
        });
      }
    }
  }

  return results;
}

function extractCourses(pages: string[]): StagingCourse[] {
  const out: StagingCourse[] = [];
  const seen = new Set<string>();

  for (let i = 0; i < pages.length; i++) {
    for (const c of extractCoursesFromPage(pages[i], i + 1)) {
      const key = `${c.page_no}|${c.reference_type}|${c.extracted_code ?? ''}|${c.extracted_module ?? ''}|${c.raw_text}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(c);
    }
  }
  return out;
}

function normalizeDegree(s: any): "Bachelor" | "Master" | "Doctorate" | null {
  const v = String(s ?? "").trim().toLowerCase();
  if (v === "b" || v.startsWith("bachel")) return "Bachelor";
  if (v === "m" || v.startsWith("mast")) return "Master";
  if (v === "d" || v.startsWith("doc")) return "Doctorate";
  return null;
}

function normalizeNumber(n: any): number | null {
  if (n == null || n === "") return null;
  const x = Number(n);
  return Number.isFinite(x) ? x : null;
}

function normalizeNumbers(value: any): number[] {
  const rawValues = Array.isArray(value) ? value : [value];
  const out: number[] = [];
  const seen = new Set<number>();

  for (const raw of rawValues) {
    const n = normalizeNumber(raw);
    if (n == null || seen.has(n)) continue;
    seen.add(n);
    out.push(n);
  }

  return out;
}

function mapDocType(labelOrTitle: string | null | undefined): DocType {
  const t = normalize(labelOrTitle);
  if (/study plan|studienplan|plan d['’]?études|plan d etudes/.test(t)) return "study_plan";
  if (/regulation|reglement|règlement|reglementation|ordnung/.test(t)) return "regulation";
  if (/brochure|flyer/.test(t)) return "brochure";
  return "other";
}

type ResolvedProgram = {
  program_id: number;
  total_ects: number | null;
};

function metadataProgramCandidates(meta: any): {
  names: string[];
  degree: "Bachelor" | "Master" | "Doctorate" | null;
  ectsValues: number[];
} {
  const keyParts = String(meta.program_key ?? "")
    .split("|")
    .map((x) => x.trim())
    .filter(Boolean);

  const degreeFromKey = keyParts.length >= 2 ? normalizeDegree(keyParts[1]) : null;
  const ectsFromKey = keyParts.length >= 3 ? normalizeNumber(keyParts[2]) : null;
  const nameFromKey = keyParts.length >= 4 ? keyParts.slice(3).join(" ") : null;

  const names = uniqueNonEmpty([
    meta.program_name,
    meta.programme_name_en,
    meta.programme,
    meta.name,
    nameFromKey,
  ]);

  const degree = normalizeDegree(meta.degree_level ?? meta.level) ?? degreeFromKey;
  const ectsValues = normalizeNumbers(meta.total_ects ?? meta.ects_points ?? meta.ects);
  if (ectsFromKey != null && !ectsValues.includes(ectsFromKey)) ectsValues.push(ectsFromKey);

  return { names, degree, ectsValues };
}

async function resolveProgramIds(client: any, meta: any): Promise<ResolvedProgram[]> {
  const { names, degree, ectsValues } = metadataProgramCandidates(meta);
  if (!names.length || !degree) return [];

  const out: ResolvedProgram[] = [];
  const seen = new Set<number>();

  const addRows = (rows: any[]) => {
    for (const row of rows) {
      if (!row?.program_id || seen.has(row.program_id)) continue;
      seen.add(row.program_id);
      out.push({
        program_id: row.program_id,
        total_ects: normalizeNumber(row.total_ects),
      });
    }
  };

  // 1) Exact match against all stored program name columns.
  for (const name of names) {
    for (const ects of ectsValues) {
      const r = await client.query(
        `SELECT program_id, total_ects
         FROM StudyProgram
         WHERE degree_level = $2
           AND total_ects = $3
           AND (
             lower(name) = lower($1)
             OR lower(COALESCE(name_en, '')) = lower($1)
             OR lower(COALESCE(name_de, '')) = lower($1)
             OR lower(COALESCE(name_fr, '')) = lower($1)
           )
         ORDER BY program_id ASC;`,
        [name, degree, ects]
      );
      addRows(r.rows);
    }
  }

  if (out.length > 0) return out;

  // 2) Fuzzy fallback for Docling metadata generated from filenames, e.g.
  //    program_key = "faculty of education|bachelor|180|special education".
  //    Keep the degree/ECTS filters strict to avoid cross-program leakage.
  const params: any[] = [degree];
  let ectsSql = "";
  if (ectsValues.length > 0) {
    params.push(ectsValues);
    ectsSql = `AND total_ects = ANY($${params.length}::float8[])`;
  }

  const candidates = await client.query(
    `SELECT program_id, total_ects, name, name_en, name_de, name_fr
     FROM StudyProgram
     WHERE degree_level = $1
       ${ectsSql}
     ORDER BY program_id ASC;`,
    params
  );

  const wanted = names.map(normalizeComparable).filter(Boolean);
  const fuzzyRows = candidates.rows.filter((row: any) => {
    const stored = [row.name, row.name_en, row.name_de, row.name_fr]
      .map(normalizeComparable)
      .filter(Boolean);

    return wanted.some((w) =>
      stored.some((s) => s === w || s.includes(w) || w.includes(s))
    );
  });

  addRows(fuzzyRows);
  return out;
}

async function run() {
  const CRAWLER_ROOT = process.env.CRAWLER_ROOT ?? "/scrapy_crawler";
  const parsedDir = process.env.DOCLING_PARSED_DIR
    ?? path.posix.join(CRAWLER_ROOT, "outputs", "parsed_fulltext_docling_new2");

  const files = readTxtFiles(parsedDir);
  const client = await DataAccessController.pool.connect();

  let docsUpserted = 0;
  let stagingAttempted = 0;
  let stagingInserted = 0;
  const skipped: any[] = [];

  try {
    await client.query("BEGIN;");
    await client.query("TRUNCATE TABLE programCourseStaging RESTART IDENTITY;");

    for (const file of files) {
      const parsed = parseDocling(file);
      const programs = await resolveProgramIds(client, parsed.meta);

      if (!programs.length) {
        skipped.push({ file, reason: "Could not resolve any StudyProgram from Docling metadata", meta: parsed.meta });
        continue;
      }

      const docUrl = parsed.sourceUrl ?? `file://${path.basename(file)}`;
      const docLabel = parsed.meta.title ?? parsed.meta.label ?? path.basename(file);
      const docType = mapDocType(docLabel);
      const courses = extractCourses(parsed.pages);

      for (const program of programs) {
        const programId = program.program_id;
        const electiveEcts = extractElectiveEctsText(parsed.pages, program.total_ects);

        if (electiveEcts) {
          await client.query(
            `UPDATE StudyProgram
             SET elective_ects = $2
             WHERE program_id = $1
               AND (elective_ects IS NULL OR elective_ects = '' OR elective_ects <> $2);`,
            [programId, stripNullBytes(electiveEcts)]
          );
        }

        const docRes = await client.query(
          `INSERT INTO programDocument (program_id, label, url, doc_type, fetched_at, parse_status, parse_notes)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           ON CONFLICT (program_id, url, doc_type)
           DO UPDATE SET
             label = EXCLUDED.label,
             fetched_at = EXCLUDED.fetched_at,
             parse_status = EXCLUDED.parse_status,
             parse_notes = EXCLUDED.parse_notes
           RETURNING doc_id;`,
          [
            programId,
            stripNullBytes(docLabel),
            docUrl,
            docType,
            parsed.meta.fetched_at ?? parsed.meta.parsed_at ?? null,
            parsed.meta.parse_status ?? "ok",
            parsed.meta.parse_notes ?? null,
          ]
        );

        const docId = docRes.rows[0]?.doc_id;
        if (!docId) continue;
        docsUpserted++;

        const seen = new Set<string>();

        for (const c of courses) {
          const key = `${programId}|${docId}|${c.page_no}|${c.reference_type}|${c.extracted_code ?? ''}|${c.extracted_module ?? ''}|${c.raw_text}`;
          if (seen.has(key)) continue;
          seen.add(key);

          stagingAttempted++;
          const res = await client.query(
            `INSERT INTO programCourseStaging
               (program_id, raw_text, reference_type, extracted_code, extracted_module, extracted_title, inferred_type, source_doc_id, page_no, section)
             VALUES
               ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
             ON CONFLICT DO NOTHING;`,
            [
              programId,
              stripNullBytes(c.raw_text),
              c.reference_type,
              c.extracted_code,
              c.extracted_module ? stripNullBytes(c.extracted_module) : null,
              c.extracted_title ? stripNullBytes(c.extracted_title) : null,
              c.inferred_type,
              docId,
              c.page_no,
              c.section ? stripNullBytes(c.section) : null,
            ]
          );
          stagingInserted += res.rowCount ?? 0;
        }
      }
    }

    await client.query("COMMIT;");

    const outDir = path.posix.dirname(parsedDir);
    fs.writeFileSync(path.posix.join(outDir, "_docling_import_skipped.json"), JSON.stringify(skipped, null, 2), "utf8");

    console.log(`✅ programDocument upserts: ${docsUpserted}`);
    console.log(`✅ programCourseStaging attempted: ${stagingAttempted}`);
    console.log(`✅ programCourseStaging inserted/updated: ${stagingInserted}`);
    console.log(`⚠️ skipped docs: ${skipped.length}`);
  } catch (e) {
    await client.query("ROLLBACK;");
    throw e;
  } finally {
    client.release();
    await DataAccessController.pool.end();
  }
}

run().catch((e) => {
  console.error("❌ Docling staging import failed:", e);
  process.exit(1);
});
