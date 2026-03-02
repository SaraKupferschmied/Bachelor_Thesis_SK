import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

export async function docsRoutes(app: FastifyInstance) {

  app.get("/program/:id", async (req) => {
    const { id } = req.params as { id: string };

    return query(`
      SELECT *
      FROM programDocument
      WHERE program_id = $1
      ORDER BY fetched_at DESC
    `, [id]);
  });

}