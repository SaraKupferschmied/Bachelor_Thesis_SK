import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

export async function programsRoutes(app: FastifyInstance) {

  // GET /programs
  app.get("/", async () => {
    return query(`
      SELECT program_id, name, degree_level, total_ects
      FROM studyprogram
      ORDER BY name
    `);
  });

  // GET /programs/:id
  app.get("/:id", async (req) => {
    const { id } = req.params as { id: string };

    const rows = await query(`
      SELECT *
      FROM studyprogram
      WHERE program_id = $1
    `, [id]);

    return rows[0];
  });

  // GET /programs/:id/courses
  app.get("/:id/courses", async (req) => {
    const { id } = req.params as { id: string };

    return query(`
      SELECT c.*
      FROM consist_of co
      JOIN Course c ON c.code = co.code
      WHERE co.program_id = $1
    `, [id]);
  });

}