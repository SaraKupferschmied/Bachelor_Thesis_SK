import "../environments/environment";
import { DataAccessController } from "../control/data_access_controller";
import fs from "fs";
import path from "path";

type FacultyRow = {
  key: string;
  name_en?: string | null;
  name_de: string;
  name_fr?: string | null;
  url_en?: string | null;
  url_de?: string | null;
  url_fr?: string | null;
};

type DB = { query: (text: string, params?: any[]) => Promise<any> };

async function upsertFacultyByKey(
  db: DB,
  args: {
    faculty_key: string;
    name_de: string;
    name_fr: string | null;
    name_en: string | null;
    url: string | null;
  }
) {
  const q = `
    INSERT INTO Faculty (faculty_key, name_de, name_fr, name_en, url)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (faculty_key) DO UPDATE SET
      name_de = EXCLUDED.name_de,
      name_fr = EXCLUDED.name_fr,
      name_en = EXCLUDED.name_en,
      url = EXCLUDED.url;
  `;
  await db.query(q, [
    args.faculty_key,
    args.name_de,
    args.name_fr,
    args.name_en,
    args.url,
  ]);
}

async function run() {
  const inputPath =
    process.argv[2] ||
    path.resolve(
      __dirname,
      "../../../scrapy_crawler/scrapy_crawler/spider_outputs/faculties.json"
    );

  if (!fs.existsSync(inputPath)) throw new Error(`Input file not found: ${inputPath}`);

  const raw = fs.readFileSync(inputPath, "utf-8");
  const rows: FacultyRow[] = JSON.parse(raw);

  console.log(`Importing ${rows.length} faculties from ${inputPath} ...`);

  const client = await DataAccessController.pool.connect();
  const db: DB = client;

  try {
    await db.query("BEGIN");

    for (const r of rows) {
      await db.query("SAVEPOINT sp_faculty");
      try {
        const faculty_key = r.key?.trim();
        if (!faculty_key) throw new Error("Missing key");

        const name_de = r.name_de?.trim();
        if (!name_de) throw new Error(`Missing name_de for key="${faculty_key}"`);

        const name_fr = (r.name_fr ?? null)?.toString().trim() || null;
        const name_en = (r.name_en ?? null)?.toString().trim() || null;

        const url =
          (r.url_de ?? null)?.toString().trim() ||
          (r.url_fr ?? null)?.toString().trim() ||
          (r.url_en ?? null)?.toString().trim() ||
          null;

        await upsertFacultyByKey(db, { faculty_key, name_de, name_fr, name_en, url });

        await db.query("RELEASE SAVEPOINT sp_faculty");
      } catch (e: any) {
        await db.query("ROLLBACK TO SAVEPOINT sp_faculty");
        await db.query("RELEASE SAVEPOINT sp_faculty");
        console.error(`❌ Failed faculty key="${r.key}":`, e?.message);
      }
    }

    await db.query("COMMIT");
    console.log(`✅ Faculty import done (${rows.length} faculties processed).`);
  } catch (fatal) {
    await client.query("ROLLBACK");
    throw fatal;
  } finally {
    client.release();
    await DataAccessController.pool.end();
  }
}

run().catch((e) => {
  console.error("❌ Import failed:", e);
  process.exit(1);
});
