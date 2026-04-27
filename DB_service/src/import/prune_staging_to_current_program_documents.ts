// DB_service/src/import/prune_staging_to_current_program_documents.ts
import "../environments/environment";
import { DataAccessController } from "../control/data_access_controller";

type ProgramDocRow = {
  doc_id: number;
  program_id: number;
  label: string | null;
  url: string;
  fetched_at: string | null;
};

function parseArgs(argv: string[]) {
  const out: Record<string, string | boolean> = {};
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
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

function filenameFromUrl(url: string): string {
  try {
    const u = new URL(url);
    const parts = u.pathname.split("/").filter(Boolean);
    return decodeURIComponent(parts[parts.length - 1] ?? "").toLowerCase();
  } catch {
    const clean = (url ?? "").split("?")[0].split("#")[0];
    const parts = clean.split("/").filter(Boolean);
    return decodeURIComponent(parts[parts.length - 1] ?? "").toLowerCase();
  }
}

function yearFromUrl(url: string): number {
  const m = (url ?? "").match(/\/(20\d{2})\//);
  return m ? Number(m[1]) : 0;
}

function currentRank(row: ProgramDocRow): number {
  const url = row.url ?? "";

  // Highest priority: studies/plans/current/... links.
  if (/\/current\//i.test(url)) return 1_000_000;

  // Otherwise prefer the newest year visible in the URL path.
  const year = yearFromUrl(url);
  if (year) return year;

  // Last fallback: doc_id usually grows over imports.
  return 0;
}

function compareDocs(a: ProgramDocRow, b: ProgramDocRow): number {
  const rankDiff = currentRank(b) - currentRank(a);
  if (rankDiff !== 0) return rankDiff;

  const aTime = a.fetched_at ? new Date(a.fetched_at).getTime() : 0;
  const bTime = b.fetched_at ? new Date(b.fetched_at).getTime() : 0;
  if (bTime !== aTime) return bTime - aTime;

  return b.doc_id - a.doc_id;
}

async function run() {
  const args = parseArgs(process.argv);
  const dryRun = Boolean(args.dryRun);

  const client = await DataAccessController.pool.connect();

  try {
    await client.query("BEGIN;");

    const res = await client.query<ProgramDocRow>(`
      SELECT doc_id, program_id, label, url, fetched_at::text AS fetched_at
      FROM programDocument
      WHERE url IS NOT NULL AND btrim(url) <> ''
      ORDER BY program_id, doc_id;
    `);

    const groups = new Map<string, ProgramDocRow[]>();

    for (const row of res.rows) {
      const filename = filenameFromUrl(row.url);
      if (!filename) continue;

      const key = `${row.program_id}::${filename}`;
      const arr = groups.get(key) ?? [];
      arr.push(row);
      groups.set(key, arr);
    }

    const keepIds = new Set<number>();
    const obsoleteIds: number[] = [];
    const decisions: Array<{
      program_id: number;
      filename: string;
      keep_doc_id: number;
      keep_url: string;
      obsolete_doc_ids: number[];
    }> = [];

    for (const [key, docs] of groups.entries()) {
      if (docs.length === 1) {
        keepIds.add(docs[0].doc_id);
        continue;
      }

      const sorted = [...docs].sort(compareDocs);
      const keep = sorted[0];
      const obsolete = sorted.slice(1);

      keepIds.add(keep.doc_id);
      obsoleteIds.push(...obsolete.map((d) => d.doc_id));

      const [, filename] = key.split("::");
      decisions.push({
        program_id: keep.program_id,
        filename,
        keep_doc_id: keep.doc_id,
        keep_url: keep.url,
        obsolete_doc_ids: obsolete.map((d) => d.doc_id),
      });
    }

    if (!obsoleteIds.length) {
      console.log("✅ No historical duplicate program documents found.");
      await client.query(dryRun ? "ROLLBACK;" : "COMMIT;");
      return;
    }

    const countBefore = await client.query(
      `
      SELECT count(*)::int AS n
      FROM programCourseStaging
      WHERE source_doc_id = ANY($1::int[]);
      `,
      [obsoleteIds]
    );

    const rowsToDelete = Number(countBefore.rows[0]?.n ?? 0);

    console.log(JSON.stringify({
      dry_run: dryRun,
      duplicate_doc_groups: decisions.length,
      obsolete_doc_ids: obsoleteIds.length,
      staging_rows_to_delete: rowsToDelete,
      examples: decisions.slice(0, 20),
    }, null, 2));

    if (!dryRun) {
      const del = await client.query(
        `
        DELETE FROM programCourseStaging
        WHERE source_doc_id = ANY($1::int[]);
        `,
        [obsoleteIds]
      );

      console.log(`✅ Deleted ${del.rowCount ?? 0} staging rows from historical duplicate documents.`);
    } else {
      console.log("🧪 Dry-run only: no rows deleted.");
    }

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
  console.error("❌ prune_staging_to_current_program_documents failed:", e);
  process.exit(1);
});
