import "../environments/environment";

import fs from "fs";
import path from "path";
import { DataAccessController } from "../control/data_access_controller";

type EnrichedItem = {
  programme?: string; // legacy
  programme_name_en?: string | null;
  programme_name_de?: string | null;
  programme_name_fr?: string | null;

  level?: "B" | "M" | "D" | string;
  ects_points?: number | null;

  // from your enriched JSON
  faculty?: string | null;      // e.g. "SCIMED", "SES", "INTERFACULTY"
  faculties?: string[] | null;  // sometimes []
};

const LEVEL_MAP: Record<string, "Bachelor" | "Master" | "Doctorate"> = {
  B: "Bachelor",
  M: "Master",
  D: "Doctorate",
};

const DEFAULT_FACULTY_ID = 100;

function makeKey(nameEn: string, degree: string, ects: number | null) {
  return `${nameEn.toLowerCase()}|||${degree}|||${ects ?? "null"}`;
}

function pickFacultyKey(it: EnrichedItem): string | null {
  // prefer the single field
  const primary = (it.faculty ?? "").trim();
  if (primary) return primary;

  // fallback: first entry in faculties[]
  const arr = it.faculties ?? [];
  if (Array.isArray(arr) && arr.length > 0) {
    const first = (arr[0] ?? "").trim();
    return first || null;
  }

  return null;
}

async function run() {
  console.log("RUNNING FILE:", __filename);
  console.log("CWD:", process.cwd());
  console.log("DIRNAME:", __dirname);
  const jsonPath = path.resolve(
    process.cwd(),
    "scrapy_crawler",
    "scrapy_crawler",
    "spider_outputs",
    "program_links_with_ects_and_docs_enriched.json"
  );
  console.log("JSONPATH COMPUTED:", jsonPath);

  if (!fs.existsSync(jsonPath)) {
    throw new Error(`JSON not found at: ${jsonPath}`);
  }

  const items: EnrichedItem[] = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));
  const client = await DataAccessController.pool.connect();

  // cache: faculty_key (lower) -> faculty_id | null
  const facultyIdCache = new Map<string, number | null>();

  async function resolveFacultyId(facultyKey: string | null): Promise<number> {
    if (!facultyKey) return DEFAULT_FACULTY_ID;

    const keyLower = facultyKey.toLowerCase();
    if (facultyIdCache.has(keyLower)) {
      return facultyIdCache.get(keyLower) ?? DEFAULT_FACULTY_ID;
    }

    // ✅ match against Faculty.faculty_key
    const r = await client.query(
      `SELECT faculty_id
         FROM Faculty
        WHERE LOWER(faculty_key) = $1
        LIMIT 1;`,
      [keyLower]
    );

    const foundId = r.rows.length ? (r.rows[0].faculty_id as number) : null;
    facultyIdCache.set(keyLower, foundId);

    return foundId ?? DEFAULT_FACULTY_ID;
  }

  try {
    await client.query("BEGIN;");

    // sanity check that fallback faculty exists
    const fallbackCheck = await client.query(
      `SELECT faculty_id FROM Faculty WHERE faculty_id = $1;`,
      [DEFAULT_FACULTY_ID]
    );
    if (!fallbackCheck.rows.length) {
      throw new Error(
        `Fallback faculty with faculty_id=${DEFAULT_FACULTY_ID} does not exist.`
      );
    }

    // wipe StudyProgram (and dependent tables)
    await client.query(`TRUNCATE TABLE StudyProgram RESTART IDENTITY CASCADE;`);

    const seen = new Set<string>();
    let inserted = 0;
    let skipped = 0;

    let fallbackUsed = 0;
    let specifiedButMissing = 0;

    for (const it of items) {
      const lvl = (it.level ?? "").trim();
      const degree = LEVEL_MAP[lvl];

      const nameEn = (it.programme_name_en ?? it.programme ?? "").trim();
      const nameDe = (it.programme_name_de ?? "").trim() || null;
      const nameFr = (it.programme_name_fr ?? "").trim() || null;

      if (!nameEn || !degree) {
        skipped++;
        continue;
      }

      const ects =
        typeof it.ects_points === "number" && Number.isFinite(it.ects_points)
          ? it.ects_points
          : null;

      const key = makeKey(nameEn, degree, ects);
      if (seen.has(key)) continue;
      seen.add(key);

      const facultyKey = pickFacultyKey(it); // e.g. "SCIMED"
      const facultyId = await resolveFacultyId(facultyKey);

      if (facultyId === DEFAULT_FACULTY_ID) {
        fallbackUsed++;
        if (facultyKey) specifiedButMissing++;
      }

      await client.query(
        `INSERT INTO StudyProgram
           (name, name_en, name_de, name_fr, degree_level, total_ects, faculty_id)
         VALUES
           ($1,  $2,      $3,      $4,      $5,          $6,         $7);`,
        [nameEn, nameEn, nameDe, nameFr, degree, ects, facultyId]
      );

      inserted++;
    }

    await client.query("COMMIT;");

    console.log(`✅ Imported StudyPrograms: ${inserted}`);
    if (skipped) console.log(`⚠️ Skipped rows (missing name_en/level): ${skipped}`);
    console.log(`ℹ️ total_ects filled from ects_points`);
    console.log(`ℹ️ fallback faculty_id=${DEFAULT_FACULTY_ID} used: ${fallbackUsed}`);
    console.log(
      `ℹ️ ...of which had faculty specified but not found in DB: ${specifiedButMissing}`
    );
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