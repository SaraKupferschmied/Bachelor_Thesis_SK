import "../environments/environment";

import fs from "fs";
import path from "path";
import { DataAccessController } from "../control/data_access_controller";

type Level2Item = {
  programme?: string;
  level?: "B" | "M" | "D" | string;
  ects_points?: number | null;
};

const LEVEL_MAP: Record<string, "Bachelor" | "Master" | "Doctorate"> = {
  B: "Bachelor",
  M: "Master",
  D: "Doctorate",
};

function makeKey(name: string, degree: string, ects: number | null) {
  return `${name.toLowerCase()}|||${degree}|||${ects ?? "null"}`;
}

async function run() {
  const jsonPath = path.resolve(
    __dirname,
    "../../../scrapy_crawler/outputs/curricula_links_level2_with_ects.json"
  );

  if (!fs.existsSync(jsonPath)) {
    throw new Error(`JSON not found at: ${jsonPath}`);
  }

  const items: Level2Item[] = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));

  const client = await DataAccessController.pool.connect();

  try {
    await client.query("BEGIN;");

    // sanity check that faculty_id=1 exists
    const facultyCheck = await client.query(
      `SELECT faculty_id FROM Faculty WHERE faculty_id = 1;`
    );
    if (!facultyCheck.rows.length) {
      throw new Error("Faculty with faculty_id=1 does not exist.");
    }

    // wipe StudyProgram (and dependent tables)
    await client.query(`TRUNCATE TABLE StudyProgram RESTART IDENTITY CASCADE;`);

    // prevent accidental duplicates
    const seen = new Set<string>();

    let inserted = 0;
    let skipped = 0;

    for (const it of items) {
      const name = (it.programme ?? "").trim();
      const lvl = (it.level ?? "").trim();
      const degree = LEVEL_MAP[lvl];

      if (!name || !degree) {
        skipped++;
        continue;
      }

      const ects =
        typeof it.ects_points === "number" && Number.isFinite(it.ects_points)
          ? it.ects_points
          : null;

      const key = makeKey(name, degree, ects);
      if (seen.has(key)) continue;
      seen.add(key);

      await client.query(
        `INSERT INTO StudyProgram (name, degree_level, total_ects, faculty_id)
         VALUES ($1, $2, $3, 1);`,
        [name, degree, ects]
      );

      inserted++;
    }

    await client.query("COMMIT;");

    console.log(`✅ Imported StudyPrograms: ${inserted}`);
    if (skipped) console.log(`⚠️ Skipped rows (missing programme/level): ${skipped}`);
    console.log(`ℹ️ faculty_id forced to 1`);
    console.log(`ℹ️ total_ects filled from ects_points`);
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
