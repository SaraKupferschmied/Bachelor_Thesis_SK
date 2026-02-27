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
function inferCourseType(raw: {
  raw_text?: string | null;
  extracted_title?: string | null;
  section?: string | null;
}): CourseType {
  const t = normalizeText(
    `${raw.section ?? ""}\n${raw.extracted_title ?? ""}\n${raw.raw_text ?? ""}`
  );

  // Strong elective signals first
  const electiveRx: RegExp[] = [
    /\bwahlpflicht\b/, // DE
    /\bwahlbereich\b/, // DE
    /\bwahlmodul\b/, // DE
    /\bwahlf[aä]cher\b/, // DE
    /\bfrei(?:e|)\s*wahl\b/, // DE
    /\bwahl\b/, // DE (weak)

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

  const hasElective = electiveRx.some((rx) => rx.test(t));
  const hasMandatory = mandatoryRx.some((rx) => rx.test(t));

  if (hasElective && !hasMandatory) return "Elective";
  if (hasMandatory && !hasElective) return "Mandatory";

  // Tie-breakers:
  if (
    /\bnicht\s+obligatorisch\b/.test(t) ||
    /\bpas\s+obligatoire\b/.test(t) ||
    /\bnot\s+mandatory\b/.test(t)
  ) {
    return "Elective";
  }

  return "Mandatory";
}

async function run() {
  const args = parseArgs(process.argv);
  const dryRun = Boolean(args.dryRun);
  const limit = asInt(args.limit) ?? 50_000;
  const programId = asInt(args["program-id"] ?? args.program_id) ?? null;

  // Optional behavior switches:
  // - If true, staging codes may match Course.alternative_code; we then insert the canonical Course.code into consist_of.
  const matchAlternativeCode =
    String(args["match-alternative-code"] ?? "false").toLowerCase() === "true";

  // - If true AND programId is provided, only refresh that program's consist_of rows; else TRUNCATE all.
  const refreshOnlyProgram =
    String(args["refresh-only-program"] ?? "true").toLowerCase() !== "false";

  const client = await DataAccessController.pool.connect();

  const where: string[] = [];
  const params: any[] = [];

  if (programId != null) {
    params.push(programId);
    where.push(`s.program_id = $${params.length}`);
  }

  // Only rows with a non-empty code
  where.push(`s.extracted_code IS NOT NULL AND btrim(s.extracted_code) <> ''`);

  const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";

  try {
    await client.query("BEGIN;");

    // Clear consist_of:
    // - if program-id supplied and refreshOnlyProgram=true: delete only that program
    // - else: truncate all
    if (programId != null && refreshOnlyProgram) {
      if (dryRun) {
        console.log(
          `🧪 Dry-run: would DELETE FROM consist_of WHERE program_id = ${programId}`
        );
      } else {
        await client.query(`DELETE FROM consist_of WHERE program_id = $1;`, [
          programId,
        ]);
      }
    } else {
      if (dryRun) {
        console.log(`🧪 Dry-run: would TRUNCATE consist_of RESTART IDENTITY CASCADE`);
      } else {
        await client.query(`TRUNCATE TABLE consist_of RESTART IDENTITY CASCADE;`);
      }
    }

    // 1) Fetch newest distinct (program_id, code) from staging, but ONLY if code exists in Course.
    //    We normalize code using btrim(s.extracted_code).
    //    If matchAlternativeCode=true, allow match on Course.alternative_code and select canonical Course.code.
    const joinSql = matchAlternativeCode
      ? `JOIN Course c ON (c.code = r.code OR c.alternative_code = r.code)`
      : `JOIN Course c ON (c.code = r.code)`;

    const selectCodeSql = `c.code AS code`;

    const q = `
      WITH ranked AS (
        SELECT
          s.program_id,
          btrim(s.extracted_code) AS code,
          s.raw_text,
          s.extracted_title,
          s.section,
          s.inferred_type,
          ROW_NUMBER() OVER (
            PARTITION BY s.program_id, btrim(s.extracted_code)
            ORDER BY
              s.source_doc_id DESC NULLS LAST,
              s.page_no DESC NULLS LAST,
              s.created_at DESC NULLS LAST,
              s.staging_id DESC
          ) AS rn
        FROM programCourseStaging s
        ${whereSql}
      )
      SELECT
        r.program_id,
        c.code AS code,
        c.name AS course_name,   
        r.raw_text,
        r.extracted_title,
        r.section,
        r.inferred_type
      FROM ranked r
      JOIN Course c
        ON ${matchAlternativeCode
          ? `(c.code = r.code OR c.alternative_code = r.code)`
          : `c.code = r.code`}
      WHERE r.rn = 1
      LIMIT $${params.length + 1};
    `;

    const res = await client.query(q, [...params, limit]);

    const rows = res.rows as Array<{
      program_id: number;
      code: string; // canonical if matchAlternativeCode=true
      course_name: string | null;
      raw_text: string | null;
      extracted_title: string | null;
      section: string | null;
      inferred_type: string | null;
    }>;

    if (!rows.length) {
      console.log(
        "ℹ️ Nothing to import (no staging rows with codes that exist in Course)."
      );
      await client.query(dryRun ? "ROLLBACK;" : "COMMIT;");
      return;
    }

    // 2) Build payload for consist_of
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
          course_name: r.course_name ?? null,
        };
      })
      .filter((x) => x.code);

    if (!toInsert.length) {
      console.log("ℹ️ No rows to insert into consist_of after filtering.");
      await client.query(dryRun ? "ROLLBACK;" : "COMMIT;");
      return;
    }

    // 3) Upsert consist_of in chunks
    const chunkSize = 500;
    let upserted = 0;

    for (let i = 0; i < toInsert.length; i += chunkSize) {
      const chunk = toInsert.slice(i, i + chunkSize);

      const valuesSql = chunk
        .map((_, j) => {
          const base = j * 5; // changed from 4
          return `($${base + 1}, $${base + 2}, $${base + 3}, $${base + 4}, $${base + 5})`;
        })
        .join(",");

      const flatParams: any[] = [];
      for (const r of chunk) {
        flatParams.push(r.program_id, r.code, r.course_type, r.description, r.course_name);
      }

      if (dryRun) {
        upserted += chunk.length;
        continue;
      }

      const ins = await client.query(
        `
          INSERT INTO consist_of (program_id, code, course_type, description, course_name)
          VALUES ${valuesSql}
          ON CONFLICT (program_id, code)
          DO UPDATE SET
            course_type = EXCLUDED.course_type,
            description = EXCLUDED.description,
            course_name = EXCLUDED.course_name;
        `,
        flatParams
      );

      upserted += ins.rowCount ?? 0;
    }

    console.log(`✅ Prepared ${toInsert.length} rows for consist_of.`);
    console.log(
      dryRun ? `🧪 Dry-run: would upsert ~${upserted} rows.` : `✅ Upserted ${upserted} rows.`
    );

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