import "../environments/environment";
import { DataAccessController } from "../control/data_access_controller";
import fs from "fs";
import path from "path";

type FacultyItem = {
  key: string;
  lang: "de" | "fr" | "en";
  name: string;
  url: string;
  source_url: string;
};

type DB = { query: (text: string, params?: any[]) => Promise<any> };

// -----------------------------
// If you DON'T have faculty_key column, keep this mapping:
const KEY_TO_FACULTY_ID: Record<string, number> = {
  scimed: 1,
  interfaculty: 2,
  lettres: 3,
  ius: 9,
  eduform: 11,
  ses: 21,
  theo: 65,
};

// If you DO have Faculty.faculty_key, set this to true:
const USE_FACULTY_KEY_COLUMN = false;
// -----------------------------

function groupByKey(items: FacultyItem[]) {
  const grouped: Record<string, Partial<Record<"de" | "fr" | "en", FacultyItem>>> = {};
  for (const it of items) {
    if (!grouped[it.key]) grouped[it.key] = {};
    grouped[it.key][it.lang] = it;
  }
  return grouped;
}

async function upsertFacultyByKey(db: DB, args: {
  faculty_key: string;
  name_de: string | null;
  name_fr: string | null;
  name_en: string | null;
  url: string | null;
}) {
  const q = `
    INSERT INTO Faculty (faculty_key, name_de, name_fr, name_en, url)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (faculty_key) DO UPDATE SET
      name_de = EXCLUDED.name_de,
      name_fr = EXCLUDED.name_fr,
      name_en = EXCLUDED.name_en,
      url = EXCLUDED.url;
  `;
  await db.query(q, [args.faculty_key, args.name_de, args.name_fr, args.name_en, args.url]);
}

async function updateFacultyById(db: DB, args: {
  faculty_id: number;
  name_de: string | null;
  name_fr: string | null;
  name_en: string | null;
  url: string | null;
}) {
  const q = `
    UPDATE Faculty
    SET
      name_de = $1,
      name_fr = $2,
      name_en = $3,
      url = $4
    WHERE faculty_id = $5;
  `;
  await db.query(q, [args.name_de, args.name_fr, args.name_en, args.url, args.faculty_id]);
}

async function run() {
  const inputPath = process.argv[2] || path.resolve(process.cwd(), "faculties.json");
  if (!fs.existsSync(inputPath)) throw new Error(`Input file not found: ${inputPath}`);

  const raw = fs.readFileSync(inputPath, "utf-8");
  const items: FacultyItem[] = JSON.parse(raw);

  console.log(`Importing ${items.length} faculty language rows from ${inputPath} ...`);

  const grouped = groupByKey(items);
  const keys = Object.keys(grouped);

  // Use ONE pooled client so BEGIN/SAVEPOINT works
  const client = await DataAccessController.pool.connect();
  const db: DB = client;

  try {
    await db.query("BEGIN");

    for (const key of keys) {
      await db.query("SAVEPOINT sp_faculty");

      try {
        const row = grouped[key];

        const name_de = row.de?.name ?? null;
        const name_fr = row.fr?.name ?? null;
        const name_en = row.en?.name ?? null;

        // choose a canonical url (they're usually identical; interfaculty differs by lang)
        const url = row.de?.url ?? row.fr?.url ?? row.en?.url ?? null;

        if (USE_FACULTY_KEY_COLUMN) {
          await upsertFacultyByKey(db, { faculty_key: key, name_de, name_fr, name_en, url });
        } else {
          const faculty_id = KEY_TO_FACULTY_ID[key];
          if (!faculty_id) {
            console.warn(`⚠️ No mapping for key="${key}" — skipping`);
            await db.query("RELEASE SAVEPOINT sp_faculty");
            continue;
          }
          await updateFacultyById(db, { faculty_id, name_de, name_fr, name_en, url });
        }

        await db.query("RELEASE SAVEPOINT sp_faculty");
      } catch (e: any) {
        await db.query("ROLLBACK TO SAVEPOINT sp_faculty");
        await db.query("RELEASE SAVEPOINT sp_faculty");
        console.error(`❌ Failed faculty key="${key}":`, e?.message);
      }
    }

    await db.query("COMMIT");
    console.log(`✅ Faculty import done (${keys.length} faculties processed).`);
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
