// DB_service/src/import/import_consist_of.ts
import "../environments/environment";

import { DataAccessController } from "../control/data_access_controller";

type CourseType = "Mandatory" | "Elective";

function parseArgs(argv: string[]) {
  const out: Record<string, string | boolean> = {};
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (!a) continue;

    if (a === "--dry-run" || a === "-n") {
      out.dryRun = true;
      continue;
    }

    const m = a.match(/^--([^=]+)=(.*)$/);
    if (m) {
      out[m[1]] = m[2];
      continue;
    }

    const m2 = a.match(/^--(.+)$/);
    if (m2) {
      const key = m2[1];
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

function asInt(v: unknown): number | null {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : null;
}

function stripNullBytes(s: string): string {
  return (s ?? "").replace(/\u0000/g, "");
}

function normalizeText(s: string): string {
  return stripNullBytes(s ?? "")
    .replace(/[’']/g, "'")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Heuristic course-type inference.
 *
 * Default: Mandatory.
 *
 * Elective hints (DE/FR/EN):
 *  - wahl / wahlpflicht / wahlbereich / wahlmodul / wahlfach / frei(wahl)
 *  - option / optionnel / au choix / à choix / choix / à option
 *  - elective / optional / choose
 *
 * Mandatory hints:
 *  - obligatorisch / pflicht / verpflichtend
 *  - obligatoire
 *  - mandatory / required / core
 */
function inferCourseType(raw: { raw_text?: string | null; extracted_title?: string | null; section?: string | null }): CourseType {
  const t = normalizeText(`${raw.section ?? ""}\n${raw.extracted_title ?? ""}\n${raw.raw_text ?? ""}`);

  // Strong elective signals first
  const electiveRx: RegExp[] = [
    /\bwahlpflicht\b/, // DE
    /\bwahlbereich\b/, // DE
    /\bwahlmodul\b/, // DE
    /\bwahlf[aä]cher\b/, // DE
    /\bfrei(?:e|)\s*wahl\b/, // DE
    /\bwahl\b/, // DE (weak, but in curricula sections it's informative)

    /\b(optionnel|optionnelle|options?)\b/, // FR
    /\b(au|a)\s+choix\b/, // FR
    /\bchoix\b/, // FR (weak)

    /\belective\b/, // EN
    /\boptional\b/, // EN
    /\bchoose\b/, // EN
  ];

  // Strong mandatory signals
  const mandatoryRx: RegExp[] = [
    /\bobligatorisch\b/, // DE
    /\bpflicht\b/, // DE
    /\bverpflichtend\b/, // DE
    /\bobligatoire\b/, // FR
    /\bmandatory\b/, // EN
    /\brequired\b/, // EN
    /\bcore\b/, // EN
  ];

  // If we see both, prefer explicit over broad.
  const hasElective = electiveRx.some((rx) => rx.test(t));
  const hasMandatory = mandatoryRx.some((rx) => rx.test(t));

  if (hasElective && !hasMandatory) return "Elective";
  if (hasMandatory && !hasElective) return "Mandatory";

  // Tie-breakers:
  // If the text contains explicit negation like "nicht obligatorisch" -> elective.
  if (/\bnicht\s+obligatorisch\b/.test(t) || /\bpas\s+obligatoire\b/.test(t) || /\bnot\s+mandatory\b/.test(t)) {
    return "Elective";
  }

  // Default
  return "Mandatory";
}

async function run() {
  const args = parseArgs(process.argv);
  const dryRun = Boolean(args.dryRun);
  const limit = asInt(args.limit) ?? 50_000;
  const programId = asInt(args["program-id"] ?? args.program_id) ?? null;
  const upsertMissingCourses = String(args["upsert-missing-courses"] ?? "true").toLowerCase() !== "false";

  const client = await DataAccessController.pool.connect();

  const where: string[] = [];
  const params: any[] = [];

  if (programId != null) {
    params.push(programId);
    where.push(`s.program_id = $${params.length}`);
  }

  // Only rows with a code
  where.push(`s.extracted_code IS NOT NULL AND btrim(s.extracted_code) <> ''`);

  const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

  try {
    await client.query("BEGIN;");

    // 1) Fetch distinct (program_id, extracted_code) not yet in consist_of
    //    - Prefer newest staging row (by created_at if available); else highest staging_id.
    //    NOTE: If your staging table has different column names, adjust ORDER BY.
    const q = `
      WITH ranked AS (
        SELECT
          s.program_id,
          s.extracted_code AS code,
          s.raw_text,
          s.extracted_title,
          s.section,
          s.inferred_type,
          ROW_NUMBER() OVER (
            PARTITION BY s.program_id, s.extracted_code
            ORDER BY
              s.source_doc_id DESC,
              s.page_no DESC
          ) AS rn
        FROM programCourseStaging s
        ${whereSql}
      )
      SELECT r.program_id, r.code, r.raw_text, r.extracted_title, r.section, r.inferred_type
      FROM ranked r
      LEFT JOIN consist_of c
        ON c.program_id = r.program_id AND c.code = r.code
      WHERE r.rn = 1 AND c.program_id IS NULL
      LIMIT $${params.length + 1};
    `;

    const res = await client.query(q, [...params, limit]);

    const rows = res.rows as Array<{
      program_id: number;
      code: string;
      raw_text: string | null;
      extracted_title: string | null;
      section: string | null;
      inferred_type: string | null;
    }>;

    if (!rows.length) {
      console.log("ℹ️ Nothing to import (no new staging rows for consist_of).");
      await client.query(dryRun ? "ROLLBACK;" : "COMMIT;");
      return;
    }

    // 2) Ensure Course rows exist (FK). Optionally insert minimal rows.
    //    We'll bulk-check then bulk-insert missing.
    const distinctCodes = Array.from(new Set(rows.map((r) => String(r.code).trim()))).filter(Boolean);

    const existing = await client.query(
      `SELECT code FROM Course WHERE code = ANY($1::varchar[]);`,
      [distinctCodes]
    );

    const existingSet = new Set<string>((existing.rows ?? []).map((r: any) => String(r.code)));
    const missing = distinctCodes.filter((c) => !existingSet.has(c));

    if (missing.length) {
      if (!upsertMissingCourses) {
        console.warn(`⚠️ ${missing.length} course codes missing in Course; they will be skipped (FK).`);
      } else {
        console.log(`🧩 Inserting ${missing.length} missing Course rows (minimal stub: code only).`);

        // Insert in chunks to avoid huge parameter lists.
        const chunkSize = 500;
        for (let i = 0; i < missing.length; i += chunkSize) {
          const chunk = missing.slice(i, i + chunkSize);
          // Build VALUES list: ($1), ($2), ...
          const valuesSql = chunk.map((_, j) => `($${j + 1})`).join(",");
          await client.query(
            `INSERT INTO Course (code) VALUES ${valuesSql} ON CONFLICT (code) DO NOTHING;`,
            chunk
          );
        }
      }
    }

    // 3) Build insert payload for consist_of
    const toInsert = rows
      .map((r) => {
        const code = String(r.code).trim();
        const program_id = Number(r.program_id);
        const inferred = (r.inferred_type ?? "").toString().trim();

        let course_type: CourseType;
        if (inferred === "Mandatory" || inferred === "Elective") {
          course_type = inferred as CourseType;
        } else {
          course_type = inferCourseType({
            raw_text: r.raw_text,
            extracted_title: r.extracted_title,
            section: r.section,
          });
        }

        return {
          program_id,
          code,
          course_type,
          description: null as string | null,
        };
      })
      .filter((x) => x.code);

    // If we didn't upsert missing courses, drop entries that would violate FK.
    let finalInsert = toInsert;
    if (missing.length && !upsertMissingCourses) {
      const missingSet = new Set(missing);
      finalInsert = toInsert.filter((x) => !missingSet.has(x.code));
    }

    if (!finalInsert.length) {
      console.log("ℹ️ No rows to insert into consist_of after FK checks.");
      await client.query(dryRun ? "ROLLBACK;" : "COMMIT;");
      return;
    }

    // 4) Insert into consist_of in chunks
    const chunkSize = 500;
    let inserted = 0;

    for (let i = 0; i < finalInsert.length; i += chunkSize) {
      const chunk = finalInsert.slice(i, i + chunkSize);

      // program_id, code, course_type, description
      const valuesSql = chunk
        .map((_, j) => {
          const base = j * 4;
          return `($${base + 1}, $${base + 2}, $${base + 3}, $${base + 4})`;
        })
        .join(",");

      const flatParams: any[] = [];
      for (const r of chunk) {
        flatParams.push(r.program_id, r.code, r.course_type, r.description);
      }

      if (dryRun) {
        inserted += chunk.length;
        continue;
      }

      const ins = await client.query(
        `
          INSERT INTO consist_of (program_id, code, course_type, description)
          VALUES ${valuesSql}
          ON CONFLICT (program_id, code)
          DO UPDATE SET
            course_type = EXCLUDED.course_type;
        `,
        flatParams
      );

      inserted += ins.rowCount ?? 0;
    }

    console.log(`✅ Prepared ${finalInsert.length} rows for consist_of.`);
    console.log(dryRun ? `🧪 Dry-run: would upsert ~${inserted} rows.` : `✅ Upserted ${inserted} rows.`);

    await client.query(dryRun ? "ROLLBACK;" : "COMMIT;");
  } catch (e) {
    await client.query("ROLLBACK;");
    throw e;
  } finally {
    client.release();
    await DataAccessController.pool.end();
  }
}

run().catch((e) => {
  console.error("❌ import_consist_of failed:", e);
  process.exit(1);
});
