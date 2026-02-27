// DB_service/src/import/new_program_import.ts
import "../environments/environment";

import fs from "fs";
import path from "path";
import { DataAccessController } from "../control/data_access_controller";

type EnrichedProgram = {
  programme_name_en?: string | null;
  programme_name_de?: string | null;
  programme_name_fr?: string | null;
  programme?: string | null; // legacy
  level?: "B" | "M" | "D" | string;
  ects_points?: number | null;
  faculty?: string | null;
  faculties?: string[] | null;
  documents?: { url: string; label?: string | null }[];
};

type ParsedIndexRow = {
  doc_key: string;
  program_key: string;
  source_url: string;
  local_path: string;
  output_path: string;
  title: string | null;
  pages: number;
  sha256: string | null;
  parsed_at: string;
  parse_status: "ok" | "failed";
  parse_notes: string | null;
};

type ParsedTxt = {
  meta: any;
  pages: string[]; // 0-indexed
};

type CourseType = "Mandatory" | "Elective";
type CodeHit = { code: string; idx: number; len: number };

const LEVEL_MAP: Record<string, "Bachelor" | "Master" | "Doctorate"> = {
  B: "Bachelor",
  M: "Master",
  D: "Doctorate",
};

function normalizeUrl(u: string): string {
  return (u ?? "").trim().replace(/\/+$/, "");
}

function toTokens(s: string): string[] {
  return (s ?? "")
    .toLowerCase()
    .replace(/[’']/g, "'")
    .replace(/[^a-z0-9äöüàâçéèêëîïôûùüÿñæœ]+/gi, " ")
    .split(/\s+/)
    .map((x) => x.trim())
    .filter((x) => x.length >= 3);
}

function tokenOverlapScore(a: string, b: string): number {
  const A = new Set(toTokens(a));
  const B = new Set(toTokens(b));
  if (!A.size || !B.size) return 0;

  let inter = 0;
  for (const t of A) if (B.has(t)) inter++;

  const denom = Math.max(6, Math.min(A.size, B.size));
  return Math.round((10 * inter) / denom);
}

function pickFacultyKey(p: EnrichedProgram): string | null {
  const f = (p.faculty ?? "").trim();
  if (f) return f;
  const arr = p.faculties ?? [];
  if (Array.isArray(arr) && arr.length) return (arr[0] ?? "").trim() || null;
  return null;
}

function mapDocType(labelOrTitle: string | null): "study_plan" | "regulation" | "brochure" | "other" {
  const s = (labelOrTitle ?? "").toLowerCase();
  if (s.includes("studienplan") || s.includes("study plan") || s.includes("plan d’études") || s.includes("plan d'etudes"))
    return "study_plan";
  if (s.includes("reglement") || s.includes("règlement") || s.includes("regulation") || s.includes("prüf") || s.includes("rrs"))
    return "regulation";
  if (s.includes("brosch") || s.includes("flyer") || s.includes("brochure")) return "brochure";
  return "other";
}

function readJsonl<T>(p: string): T[] {
  const lines = fs.readFileSync(p, "utf-8").split(/\r?\n/).filter(Boolean);
  return lines.map((l) => JSON.parse(l));
}

function parseParsedFulltextFile(txtPath: string): ParsedTxt {
  const raw = fs.readFileSync(txtPath, "utf-8");

  const metaStart = raw.indexOf("---METADATA_JSON---");
  const metaEnd = raw.indexOf("---/METADATA_JSON---");

  let meta: any = {};
  if (metaStart >= 0 && metaEnd > metaStart) {
    const jsonStr = raw.slice(metaStart + "---METADATA_JSON---".length, metaEnd).trim();
    try {
      meta = JSON.parse(jsonStr);
    } catch {
      meta = {};
    }
  }

  const pages: string[] = [];
  const pageRx = /---PAGE\s+(\d+)---\s*\n/g;
  let m: RegExpExecArray | null;

  const indices: { pageNo: number; idx: number }[] = [];
  while ((m = pageRx.exec(raw)) !== null) {
    indices.push({ pageNo: Number(m[1]), idx: m.index + m[0].length });
  }

  for (let i = 0; i < indices.length; i++) {
    const start = indices[i].idx;
    const end = i + 1 < indices.length ? indices[i + 1].idx - 1 : raw.length;
    const content = raw.slice(start, end).trim();
    pages.push(content);
  }

  return { meta, pages };
}

function inferStudyStart(text: string): "Autumn" | "Spring" | "Both" | null {
  const t = (text ?? "").toLowerCase();

  if (/(studienbeginn|début des études)[^.\n]{0,80}(nur|seulement)[^.\n]{0,40}(herbst|automne)/i.test(t)) return "Autumn";
  if (/(studienbeginn|début des études)[^.\n]{0,80}(nur|seulement)[^.\n]{0,40}(frühling|printemps)/i.test(t)) return "Spring";

  if (/(studienbeginn|début des études)[^.\n]{0,120}(herbst|automne)[^.\n]{0,40}(oder|ou)[^.\n]{0,40}(frühling|printemps)/i.test(t))
    return "Both";

  return null;
}

function normalizeDegree(s: any): "Bachelor" | "Master" | "Doctorate" | null {
  if (!s) return null;
  const v = String(s).trim().toLowerCase();
  if (v === "bachelor") return "Bachelor";
  if (v === "master") return "Master";
  if (v === "doctorate") return "Doctorate";
  if (v.startsWith("bachel")) return "Bachelor";
  if (v.startsWith("mast")) return "Master";
  if (v.startsWith("doc")) return "Doctorate";
  return null;
}

function normalizeFaculty(s: any): string | null {
  const v = (s ?? "").toString().trim();
  return v ? v.toLowerCase() : null;
}

function normalizeNumber(n: any): number | null {
  if (n == null) return null;
  const x = Number(n);
  return Number.isFinite(x) ? x : null;
}

function stripNullBytes(s: string): string {
  // Postgres UTF-8 error 0x00 comes from NUL bytes in text
  return (s ?? "").replace(/\u0000/g, "");
}

/**
 * STRICT final validation (no spaces) for what we store.
 * Accepts:
 *  - UE-XXX.00000
 *  - XXX.00000
 * Requires at least one LETTER in the prefix => blocks dates like 08.2025
 * Enforces EXACTLY 5 DIGITS after the dot.
 */
const COURSE_CODE_STRICT_RX =
  /^(?:UE-)?(?=[A-Z0-9]{2,4}\.[0-9]{5}$)[A-Z0-9]*[A-Z][A-Z0-9]*\.[0-9]{5}$/;

/**
 * LOOSE matcher allowing OCR/PDF spacing issues.
 * Matches EXACTLY 5 digits after the dot (with optional spaces between digits).
 *
 * Examples matched:
 *  - ESE.00051
 *  - ESE.00 0 51
 *  - UE-ESE.000512   -> match will be UE-ESE.00051 (extra digit ignored)
 *  - UE - DDR . 0 0 1 7 4  -> UE-DDR.00174
 */
const COURSE_CODE_LOOSE_RX =
  /\b(?:UE\s*-\s*)?(?=[A-Z0-9]{2,4}\s*\.)[A-Z0-9]*[A-Z][A-Z0-9]*\s*\.\s*(?:[0-9]\s*){5}/g;

function normalizeExtractedCode(raw: string): string {
  let s = (raw ?? "").toUpperCase();
  s = s.replace(/\bUE\s*-\s*/g, "UE-"); // UE - => UE-
  s = s.replace(/\s*\.\s*/g, "."); // spaces around dot
  s = s.replace(/\s+/g, ""); // remove all remaining spaces
  return s;
}

function extractCourseCodeHits(text: string): CodeHit[] {
  if (!text) return [];
  const out: CodeHit[] = [];
  const seen = new Set<string>();

  const rx = new RegExp(COURSE_CODE_LOOSE_RX.source, COURSE_CODE_LOOSE_RX.flags);
  let m: RegExpExecArray | null;

  while ((m = rx.exec(text)) !== null) {
    const raw = m[0];
    const normalized = normalizeExtractedCode(raw);
    if (!COURSE_CODE_STRICT_RX.test(normalized)) continue;
    if (seen.has(normalized)) continue;

    seen.add(normalized);
    out.push({ code: normalized, idx: m.index, len: raw.length });
  }

  return out;
}

/**
 * Course title is usually directly after the code.
 * We take same-line + possibly next line (wrap) and cut before typical meta fields.
 */
function extractCourseTitleAfterCode(fullChunk: string, hit: CodeHit): string | null {
  const after = (fullChunk ?? "").slice(hit.idx + hit.len);
  const lines = after
    .replace(/\r/g, "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

  if (!lines.length) return null;

  // allow title wrap
  let s = `${lines[0]} ${lines[1] ?? ""}`.trim();

  // remove separators at start
  s = s.replace(/^[:\-\–\—•·\|]+\s*/, "");

  // cut at typical meta fields (multi-language)
  const stopRx =
    /\b(ects|credits?|kp|cr|sws|semester|sem\.?|language|sprache|langue|niveau|level|typ|type|responsible|verantwortlich|dozent|lecturer|professor|assessment|prüf|prüfung|pruef|learning outcomes|inhalt|content|ziele|objectifs)\b/i;

  const stopIdx = s.search(stopRx);
  if (stopIdx >= 0) s = s.slice(0, stopIdx);

  // also cut on common separators
  s = s.split(/\s{2,}|\s\|\s| \u00b7 | \u2022 | \s\/\s/)[0];

  s = s.replace(/^[\s:–—-]+/, "").replace(/[\s:–—-]+$/, "").trim();

  if (s.length < 4) return null;
  if (!/[A-Za-zÄÖÜäöüÀ-ÿ]/.test(s)) return null;

  if (s.length > 140) s = s.slice(0, 140).trim();

  return s || null;
}

function extractContextAroundHit(fullChunk: string, hit: CodeHit, before = 160, after = 80): string {
  const start = Math.max(0, hit.idx - before);
  const end = Math.min(fullChunk.length, hit.idx + hit.len + after);
  return fullChunk
    .slice(start, end)
    .replace(/\s+/g, " ")
    .trim();
}

function inferCourseType(text: string): CourseType | null {
  const t = (text ?? "").toLowerCase();

  // Elective signals (DE/FR/EN)
  // - "Wahlkurse" appears in your example study plan
  const electiveRx =
    /\b(wahlkurs(?:e|en)?|wahlfach|wahlbereich|wahlmodul|wahlpflicht|wahlpflichtbereich|elective|electives|optional|optionnel|optionnels|cours?\s+(?:à|a)\s+choix|module\s+(?:à|a)\s+choix|frei\s*wählbar|à\s*choix)\b/i;

  // Mandatory/core signals (DE/FR/EN)
  const mandatoryRx =
    /\b(pflicht(?:modul)?|pflichtkurse?|pflichtveranstaltungen?|obligatorisch|obligatoire|mandatory|compulsory|required|core|tronc\s+commun|cours?\s+obligatoires?|compulsory\s+courses)\b/i;

  const isElective = electiveRx.test(t);
  const isMandatory = mandatoryRx.test(t);

  if (isElective && !isMandatory) return "Elective";
  if (isMandatory && !isElective) return "Mandatory";

  if (isElective && isMandatory) {
    if (/\bwahlpflicht\b/i.test(t)) return "Elective";
    return "Mandatory";
  }

  return null;
}

function makeStagingChunks(pages: unknown): {
  raw_text: string;
  page_no: number;
  section: string | null;
  extracted_title: string | null;
  extracted_code: string | null;
  inferred_type: CourseType | null;
}[] {
  const out: {
    raw_text: string;
    page_no: number;
    section: string | null;
    extracted_title: string | null;
    extracted_code: string | null;
    inferred_type: CourseType | null;
  }[] = [];

  const safePages: string[] = Array.isArray(pages)
    ? pages.map((p: unknown) => (typeof p === "string" ? p : String(p ?? "")))
    : [];

  const splitRx = /\n+|(?=\bModule\s+\d+\b)|(?=\b\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\b)/g;
  const sectionRx = /\b\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\b/;

  // snippet for storage (your original)
  const AFTER_CHARS = 50;

  for (let i = 0; i < safePages.length; i++) {
    const pageNo = i + 1;
    const pageTrimmed = safePages[i].trim();
    if (!pageTrimmed) continue;

    // Carry type within this page (tables are usually contained per page, but this already helps a lot)
    let carryType: CourseType | null = null;

    const parts = pageTrimmed
      .split(splitRx)
      .filter((x): x is string => typeof x === "string")
      .map((x) => x.trim())
      .filter((x) => x.length >= 20);

    for (const part of parts) {
      const section =
        part.match(/\bModule\s+\d+\b/i)?.[0] ??
        part.match(sectionRx)?.[0] ??
        null;

      // 1) Update carryType if this part looks like a header/label chunk
      // This catches “Elective courses” / “Compulsory courses” lines that come before codes.
      const headerType = inferCourseType(part);
      if (headerType) carryType = headerType;

      const hits = extractCourseCodeHits(part);
      if (!hits.length) continue;

      for (const hit of hits) {
        const code = hit.code.startsWith("UE-") ? hit.code : `UE-${hit.code}`;

        // Store snippet (same as you do)
        const start = Math.max(0, hit.idx);
        const end = Math.min(part.length, hit.idx + hit.len + AFTER_CHARS);
        const rawSnippet = part
          .slice(start, end)
          .replace(/\s+/g, " ")
          .trim();

        const extracted_title = extractCourseTitleAfterCode(part, hit) ?? section;

        // 2) First try: infer from a bigger *around-hit* context (includes text before the code)
        const around = extractContextAroundHit(part, hit, 200, 120);
        let inferred_type = inferCourseType(around);

        // 3) Fallback: use carryType from the last seen header in this page/table
        if (!inferred_type && carryType) inferred_type = carryType;

        // 4) Keep “Mandatory as default”: if we still don’t know, store null (treat downstream as mandatory)
        out.push({
          raw_text: rawSnippet,
          page_no: pageNo,
          section,
          extracted_code: code,
          extracted_title,
          inferred_type: inferred_type ?? null,
        });
      }
    }
  }

  return out;
}

function scoreProgramVsDoc(p: EnrichedProgram, parsed: ParsedTxt, verifyText: string): { score: number; hardReject: boolean } {
  const expectedDegree = LEVEL_MAP[(p.level ?? "").trim()] ?? null;
  const expectedEcts = typeof p.ects_points === "number" ? p.ects_points : null;
  const expectedFaculty = pickFacultyKey(p)?.toLowerCase() ?? null;

  const metaDegree = normalizeDegree(parsed.meta?.degree_level);
  const metaEcts = normalizeNumber(parsed.meta?.total_ects);
  const metaFaculty = normalizeFaculty(parsed.meta?.faculty);

  // HARD rejects only if meta exists
  if (expectedDegree && metaDegree && expectedDegree !== metaDegree) return { score: 0, hardReject: true };
  if (expectedEcts != null && metaEcts != null && Number(expectedEcts) !== Number(metaEcts)) return { score: 0, hardReject: true };

  let score = 0;

  if (expectedFaculty && metaFaculty && expectedFaculty === metaFaculty) score += 6;
  if (expectedDegree && metaDegree && expectedDegree === metaDegree) score += 6;
  if (expectedEcts != null && metaEcts != null && Number(expectedEcts) === Number(metaEcts)) score += 6;

  const nameEn = (p.programme_name_en ?? p.programme ?? "").trim();
  const nameDe = (p.programme_name_de ?? "").trim();
  const nameFr = (p.programme_name_fr ?? "").trim();

  score += Math.max(
    nameEn ? tokenOverlapScore(verifyText, nameEn) : 0,
    nameDe ? tokenOverlapScore(verifyText, nameDe) : 0,
    nameFr ? tokenOverlapScore(verifyText, nameFr) : 0
  );

  return { score, hardReject: false };
}

async function run() {
  const cwd = process.cwd();

  const programsJsonPath = path.resolve(
    cwd,
    "scrapy_crawler",
    "scrapy_crawler",
    "spider_outputs",
    "program_links_with_ects_and_docs_enriched.json"
  );

  const parsedDir = path.resolve(cwd, "scrapy_crawler", "outputs", "parsed_fulltext");
  const indexPath = path.join(parsedDir, "_index.jsonl");

  if (!fs.existsSync(programsJsonPath)) throw new Error(`Missing programs JSON: ${programsJsonPath}`);
  if (!fs.existsSync(indexPath)) throw new Error(`Missing parsed index: ${indexPath}`);

  const programs: EnrichedProgram[] = JSON.parse(fs.readFileSync(programsJsonPath, "utf-8"));
  const idxRows = readJsonl<ParsedIndexRow>(indexPath).filter((r) => r.parse_status === "ok");

  // Eligibility: ONLY docs present in enriched JSON can attach
  const urlToPrograms = new Map<string, EnrichedProgram[]>();
  for (const p of programs) {
    for (const d of p.documents ?? []) {
      const u = normalizeUrl(d.url);
      if (!u) continue;
      const arr = urlToPrograms.get(u) ?? [];
      arr.push(p);
      urlToPrograms.set(u, arr);
    }
  }

  const client = await DataAccessController.pool.connect();

  const skippedNotEligible: any[] = [];
  const skippedMissingTxt: any[] = [];
  const skippedLowScore: any[] = [];
  const matched: any[] = [];

  try {
    await client.query("BEGIN;");

    // 🔥 FULL RESET BEFORE IMPORT
    await client.query(`
      TRUNCATE TABLE programCourseStaging RESTART IDENTITY CASCADE;
    `);

    await client.query(`
      TRUNCATE TABLE programDocument RESTART IDENTITY CASCADE;
    `);

    let docsUpserted = 0;
    let stagingAttempted = 0;
    let stagingInserted = 0;
    let programUpdated = 0;

    for (const row of idxRows) {
      const sourceUrl = normalizeUrl(row.source_url);
      const candidates = urlToPrograms.get(sourceUrl) ?? [];

      if (!candidates.length) {
        skippedNotEligible.push({
          source_url: row.source_url,
          output_path: row.output_path,
          title: row.title,
          reason: "URL not present in program_links_with_ects_and_docs_enriched.json",
        });
        continue;
      }

      const txtPath = path.isAbsolute(row.output_path) ? row.output_path : path.resolve(cwd, row.output_path);
      if (!fs.existsSync(txtPath)) {
        skippedMissingTxt.push({ source_url: row.source_url, output_path: txtPath, reason: "parsed txt missing" });
        continue;
      }

      const parsed = parseParsedFulltextFile(txtPath);
      const docTitle = stripNullBytes((row.title ?? parsed.meta?.title ?? "").toString());
      const page1 = stripNullBytes((parsed.pages[0] ?? "").toString());
      const verifyText = `${docTitle}\n${page1}`.trim();

      let best: { p: EnrichedProgram; score: number } | null = null;

      for (const p of candidates) {
        const { score, hardReject } = scoreProgramVsDoc(p, parsed, verifyText);
        if (hardReject) continue;
        if (!best || score > best.score) best = { p, score };
      }

      if (!best || best.score < 12) {
        skippedLowScore.push({
          source_url: row.source_url,
          output_path: row.output_path,
          title: docTitle,
          best_score: best?.score ?? null,
          candidate_names: candidates.map((p) => p.programme_name_en ?? p.programme),
          doc_meta: {
            degree_level: parsed.meta?.degree_level ?? null,
            total_ects: parsed.meta?.total_ects ?? null,
            faculty: parsed.meta?.faculty ?? null,
          },
        });
        continue;
      }

      const p = best.p;
      const degree = LEVEL_MAP[(p.level ?? "").trim()];
      const ects = typeof p.ects_points === "number" ? p.ects_points : null;

      const nameEn = (p.programme_name_en ?? p.programme ?? "").trim();
      if (!nameEn || !degree) continue;

      let programId: number | null = null;

      if (ects != null) {
        const r = await client.query(
          `SELECT program_id FROM StudyProgram
           WHERE LOWER(name)=LOWER($1) AND degree_level=$2 AND total_ects=$3
           LIMIT 1;`,
          [nameEn, degree, ects]
        );
        programId = r.rows[0]?.program_id ?? null;
      }

      if (!programId) {
        const r = await client.query(
          `SELECT program_id FROM StudyProgram
           WHERE LOWER(name)=LOWER($1) AND degree_level=$2
           ORDER BY program_id ASC
           LIMIT 1;`,
          [nameEn, degree]
        );
        programId = r.rows[0]?.program_id ?? null;
      }

      if (!programId) {
        skippedLowScore.push({
          source_url: row.source_url,
          reason: "could not resolve StudyProgram by (name,degree,ects)",
          nameEn,
          degree,
          ects,
        });
        continue;
      }

      const studyStart = inferStudyStart(`${parsed.pages[0] ?? ""}\n${parsed.pages[1] ?? ""}`);
      if (studyStart) {
        const upd = await client.query(
          `UPDATE StudyProgram
             SET study_start = COALESCE(study_start, $1)
           WHERE program_id=$2;`,
          [studyStart, programId]
        );
        programUpdated += upd.rowCount ?? 0;
      }

      const docLabelFromJson = (p.documents ?? []).find((d) => normalizeUrl(d.url) === sourceUrl)?.label ?? null;
      const docType = mapDocType(docLabelFromJson ?? docTitle);

      const insDoc = await client.query(
        `
        INSERT INTO programDocument (program_id, label, url, doc_type, fetched_at, parse_status, parse_notes)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        ON CONFLICT (program_id, url, doc_type)
        DO UPDATE SET
          label = EXCLUDED.label,
          fetched_at = EXCLUDED.fetched_at,
          parse_status = EXCLUDED.parse_status,
          parse_notes = EXCLUDED.parse_notes
        RETURNING doc_id;
        `,
        [
          programId,
          docLabelFromJson,
          row.source_url,
          docType,
          parsed.meta?.fetched_at ?? row.parsed_at ?? null,
          row.parse_status,
          row.parse_notes,
        ]
      );

      const docId: number | null = insDoc.rows[0]?.doc_id ?? null;
      if (!docId) continue;
      docsUpserted++;

      // ✅ Insert staging rows ONLY for chunks with a course code
      const chunks = makeStagingChunks(parsed.pages);

      // local dedupe (avoid tons of conflict attempts)
      const seen = new Set<string>();

      for (const ch of chunks) {
        const k = `${programId}|${docId}|${ch.page_no}|${ch.extracted_code}`;
        if (seen.has(k)) continue;
        seen.add(k);

        const rawTextClean = stripNullBytes(ch.raw_text);
        const titleClean = ch.extracted_title ? stripNullBytes(ch.extracted_title) : null;
        const sectionClean = ch.section ? stripNullBytes(ch.section) : null;
        const inferredType: CourseType | null = ch.inferred_type;

        stagingAttempted++;

        const res = await client.query(
          `
          INSERT INTO programCourseStaging
            (program_id, raw_text, extracted_code, extracted_title, inferred_type, source_doc_id, page_no, section)
          VALUES
            ($1,$2,$3,$4,$5,$6,$7,$8)
          ON CONFLICT (program_id, extracted_code, source_doc_id, page_no)
          DO NOTHING;
          `,
          [programId, rawTextClean, ch.extracted_code, titleClean, inferredType, docId, ch.page_no, sectionClean]
        );

        stagingInserted += res.rowCount ?? 0;
      }

      matched.push({
        source_url: row.source_url,
        program: nameEn,
        degree,
        ects,
        score: best.score,
        doc_type: docType,
        doc_meta: {
          degree_level: parsed.meta?.degree_level ?? null,
          total_ects: parsed.meta?.total_ects ?? null,
          faculty: parsed.meta?.faculty ?? null,
        },
      });
    }

    await client.query("COMMIT;");

    const outDir = path.resolve(cwd, "scrapy_crawler", "outputs");
    fs.writeFileSync(path.join(outDir, "_docs_skipped_not_eligible.json"), JSON.stringify(skippedNotEligible, null, 2), "utf-8");
    fs.writeFileSync(path.join(outDir, "_docs_skipped_missing_txt.json"), JSON.stringify(skippedMissingTxt, null, 2), "utf-8");
    fs.writeFileSync(path.join(outDir, "_docs_skipped_low_score.json"), JSON.stringify(skippedLowScore, null, 2), "utf-8");
    fs.writeFileSync(path.join(outDir, "_docs_matched.json"), JSON.stringify(matched, null, 2), "utf-8");

    console.log(`✅ programDocument upserts: ${docsUpserted}`);
    console.log(`✅ programCourseStaging attempted: ${stagingAttempted}`);
    console.log(`✅ programCourseStaging inserted: ${stagingInserted}`);
    console.log(`✅ StudyProgram updates (study_start best-effort): ${programUpdated}`);
    console.log(`⚠️ skipped (not eligible): ${skippedNotEligible.length}`);
    console.log(`⚠️ skipped (parsed txt missing): ${skippedMissingTxt.length}`);
    console.log(`⚠️ skipped (low score / mismatch): ${skippedLowScore.length}`);
    console.log(`📝 logs written to scrapy_crawler/outputs/_docs_*.json`);
  } catch (e) {
    await client.query("ROLLBACK;");
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