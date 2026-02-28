import fs from "fs";
import path from "path";
import readline from "readline";

import { DataAccessController } from "../control/data_access_controller";

type DB = {
  query: (sql: string, params?: any[]) => Promise<{ rowCount?: number; rows?: any[] }>;
};

type PeopleRow = {
  input_name: string;
  email?: string | null;
  title?: string | null;
  office?: string | null;
  status: "ok" | "not_found" | string;
};

function parseLastFirst(fullName: string): { first: string; last: string } {
  const clean = String(fullName || "").trim().replace(/\s+/g, " ");
  if (!clean) return { first: "", last: "" };

  // "Lastname Firstname ..."
  const parts = clean.split(" ");
  if (parts.length === 1) return { first: "", last: parts[0] };
  return { last: parts[0], first: parts.slice(1).join(" ") };
}

function normalizeOffice(office: string | null | undefined): string | null {
  if (!office) return null;
  const clean = String(office).trim().replace(/\s+/g, " ");
  if (!clean) return null;
  return clean.length > 20 ? clean.slice(0, 20) : clean;
}

async function run() {
  const inputPath =
    process.argv[2] ||
    path.resolve(
      process.cwd(),
      "scrapy_crawler/scrapy_crawler/spider_outputs/unifr_people.jsonl"
    );

  if (!fs.existsSync(inputPath)) throw new Error(`Input file not found: ${inputPath}`);

  const client = await DataAccessController.pool.connect();
  const db: DB = client as any;

  const rl = readline.createInterface({
    input: fs.createReadStream(inputPath, { encoding: "utf-8" }),
    crlfDelay: Infinity,
  });

  let total = 0;
  let updated = 0;
  let skipped = 0;
  let notInDb = 0;

  try {
    await db.query("BEGIN");

    for await (const line of rl) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      total++;

      let row: PeopleRow;
      try {
        row = JSON.parse(trimmed);
      } catch {
        skipped++;
        continue;
      }

      if (!row || row.status !== "ok" || !row.input_name) {
        skipped++;
        continue;
      }

      const { first, last } = parseLastFirst(row.input_name);
      if (!last) {
        skipped++;
        continue;
      }

      const email = row.email ?? null;
      const title = row.title ?? null;
      const office = normalizeOffice(row.office);

      // If we have literally nothing new, skip
      if (email === null && title === null && office === null) {
        skipped++;
        continue;
      }

      await db.query("SAVEPOINT sp_prof_update");
      try {
        const res = await db.query(
          `
          UPDATE professor
             SET email  = COALESCE($3, professor.email),
                 title  = COALESCE($4, professor.title),
                 office = COALESCE($5, professor.office)
           WHERE COALESCE(first_name, '') = $1
             AND last_name = $2
          `,
          [first || "", last, email, title, office]
        );

        // rowCount === 0 => professor not found in DB => skip
        if ((res.rowCount ?? 0) === 0) notInDb++;
        else updated++;

        await db.query("RELEASE SAVEPOINT sp_prof_update");
      } catch (e) {
        await db.query("ROLLBACK TO SAVEPOINT sp_prof_update");
        skipped++;
        console.warn(`Failed to update professor for "${row.input_name}":`, (e as any)?.message ?? e);
      }

      if (total % 500 === 0) {
        console.log(`Processed ${total} (updated ${updated}, notInDb ${notInDb}, skipped ${skipped})...`);
      }
    }

    await db.query("COMMIT");
    console.log(`Done. Processed ${total} (updated ${updated}, notInDb ${notInDb}, skipped ${skipped}).`);
  } catch (err) {
    try {
      await db.query("ROLLBACK");
    } catch {}
    throw err;
  } finally {
    client.release();
  }
}

run().catch((e) => {
  console.error(e);
  process.exit(1);
});