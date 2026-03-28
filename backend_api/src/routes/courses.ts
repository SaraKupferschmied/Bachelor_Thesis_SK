import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

export async function coursesRoutes(app: FastifyInstance) {

  app.get("/", async (req) => {
    const { mobility, soft_skills, limit } = req.query as any;

    return query(`
      SELECT *
      FROM Course
      WHERE ($1::boolean IS NULL OR mobility = $1)
        AND ($2::boolean IS NULL OR soft_skills = $2)
      ORDER BY code
      LIMIT COALESCE($3::int, 50)
    `, [
      mobility ?? null,
      soft_skills ?? null,
      limit ?? 50
    ]);
  });

  app.get("/:code", async (req) => {
    const { code } = req.params as { code: string };

    const rows = await query(`
      SELECT *
      FROM Course
      WHERE code = $1
    `, [code]);

    return rows[0];
  });

}