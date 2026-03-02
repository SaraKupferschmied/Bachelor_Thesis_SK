import { Pool, type QueryResultRow } from "pg";

const databaseUrl =
  process.env.DATABASE_URL ??
  `postgres://${encodeURIComponent(process.env.POSTGRES_USER ?? "")}:${encodeURIComponent(
    process.env.POSTGRES_PASSWORD ?? ""
  )}@${process.env.POSTGRES_HOST ?? "db"}:${process.env.POSTGRES_PORT ?? "5432"}/${
    process.env.POSTGRES_DB ?? ""
  }`;

export const pool = new Pool({ connectionString: databaseUrl });

export async function query<T extends QueryResultRow>(
  text: string,
  params: unknown[] = []
): Promise<T[]> {
  const res = await pool.query<T>(text, params);
  return res.rows;
}