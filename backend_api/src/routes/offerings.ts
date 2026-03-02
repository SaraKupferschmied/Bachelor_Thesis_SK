import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

export async function offeringsRoutes(app: FastifyInstance) {

  // GET /offerings?sem_id=HS2025
  app.get("/", async (req) => {
    const { sem_id } = req.query as { sem_id: string };

    return query(`
      SELECT off.*, c.name
      FROM CourseOffering off
      JOIN Course c ON c.code = off.code
      WHERE off.sem_id = $1
    `, [sem_id]);
  });

}