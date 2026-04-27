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

type StagingCourse = {
  raw_text: string;
  extracted_code: string;
  extracted_title: string | null;
  inferred_type: CourseType | null;
  page_no: number;
  section: string | null;
};

function normalize(s: string | null | undefined): string {
  return (s ?? "").toLowerCase().replace(/\s+/g, " ").trim();
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
  const mandatory = /\b(pflicht(?:modul)?|pflichtkurse?|pflichtveranstaltungen?|obligatorisch|obligatoire|mandatory|compulsory|required|core|tronc\s+commun|cours?\s+obligatoires?)\b/i.test(t);

  if (elective && !mandatory) return "Elective";
  if (mandatory && !elective) return "Mandatory";
  if (elective && mandatory) return /wahlpflicht/i.test(t) ? "Elective" : "Mandatory";
  return null;
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
          cells.find((c) => rowType === "Elective" ? /wahl|elective|choix|option/i.test(c) : /pflicht|obligatoire|mandatory|compulsory|required/i.test(c))
          ?? cells.find((c) => c.trim().length > 0)
          ?? rowText;
        section = headingCandidate.trim();
        carryType = rowType;
        continue;
      }

      if (!codes.length) continue;

      for (const code of codes) {
        results.push({
          raw_text: tableLine,
          extracted_code: code,
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
      const key = `${c.page_no}|${c.extracted_code}|${c.raw_text}`;
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

async function resolveProgramIds(client: any, meta: any): Promise<ResolvedProgram[]> {
  const programName = meta.program_name ?? meta.programme_name_en ?? meta.programme ?? meta.name ?? null;
  const degree = normalizeDegree(meta.degree_level ?? meta.level);
  const ectsValues = normalizeNumbers(meta.total_ects ?? meta.ects_points ?? meta.ects);

  if (!programName || !degree) return [];

  const out: ResolvedProgram[] = [];
  const seen = new Set<number>();

  // total_ects can be an array, e.g. [30, 60]. In that case the same
  // parsed document belongs to multiple StudyProgram rows.
  for (const ects of ectsValues) {
    const r = await client.query(
      `SELECT program_id, total_ects
       FROM StudyProgram
       WHERE lower(name) = lower($1)
         AND degree_level = $2
         AND total_ects = $3
       LIMIT 1;`,
      [programName, degree, ects]
    );

    const row = r.rows[0];
    if (!row?.program_id || seen.has(row.program_id)) continue;
    seen.add(row.program_id);
    out.push({
      program_id: row.program_id,
      total_ects: normalizeNumber(row.total_ects),
    });
  }

  // Fallback for older metadata that does not contain ECTS. Do not use this
  // fallback when ECTS were provided, otherwise a multi-ECTS document could
  // silently be attached to only the first matching program.
  if (out.length === 0) {
    const r = await client.query(
      `SELECT program_id, total_ects
       FROM StudyProgram
       WHERE lower(name) = lower($1)
         AND degree_level = $2
       ORDER BY program_id ASC
       LIMIT 1;`,
      [programName, degree]
    );

    const row = r.rows[0];
    if (row?.program_id) {
      out.push({
        program_id: row.program_id,
        total_ects: normalizeNumber(row.total_ects),
      });
    }
  }

  return out;
}

async function run() {
  const CRAWLER_ROOT = process.env.CRAWLER_ROOT ?? "/scrapy_crawler";
  const parsedDir = process.env.DOCLING_PARSED_DIR
    ?? path.posix.join(CRAWLER_ROOT, "outputs", "parsed_fulltext_docling_new");

  const files = readTxtFiles(parsedDir);
  const client = await DataAccessController.pool.connect();

  let docsUpserted = 0;
  let stagingAttempted = 0;
  let stagingInserted = 0;
  const skipped: any[] = [];

  try {
    await client.query("BEGIN;");

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
          const key = `${programId}|${docId}|${c.page_no}|${c.extracted_code}`;
          if (seen.has(key)) continue;
          seen.add(key);

          stagingAttempted++;
          const res = await client.query(
            `INSERT INTO programCourseStaging
               (program_id, raw_text, extracted_code, extracted_title, inferred_type, source_doc_id, page_no, section)
             VALUES
               ($1, $2, $3, $4, $5, $6, $7, $8)
             ON CONFLICT (program_id, extracted_code, source_doc_id, page_no)
             DO UPDATE SET
               raw_text = EXCLUDED.raw_text,
               extracted_title = COALESCE(EXCLUDED.extracted_title, programCourseStaging.extracted_title),
               inferred_type = COALESCE(EXCLUDED.inferred_type, programCourseStaging.inferred_type),
               section = COALESCE(EXCLUDED.section, programCourseStaging.section);`,
            [
              programId,
              stripNullBytes(c.raw_text),
              c.extracted_code,
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
