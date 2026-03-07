import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

type OfferingsQuery = {
  sem_id: string;
};

type ErrorResponse = {
  error: string;
};

export async function offeringsRoutes(app: FastifyInstance) {
  // GET /offerings?sem_id=FS-2026
  app.get(
    "/",
    {
      schema: {
        description: "List course offerings for a given semester id",
        tags: ["offerings"],
        querystring: {
          type: "object",
          required: ["sem_id"],
          properties: {
            sem_id: {
              type: "string",
              description: "Semester id (e.g. FS-2026, HS-2025)",
              examples: ["FS-2026"],
            },
          },
        },
        response: {
          200: {
            type: "array",
            items: {
              type: "object",
              properties: {
                offering_id: { type: "integer" },
                code: { type: "string" },
                sem_id: { type: "string" },
                offering_type: { type: "string" },
                day_time_info: { type: ["string", "null"] },
                link_course_catalogue: { type: ["string", "null"] },
                name: { type: ["string", "null"] }, // course.name is text and can be null in your schema
              },
            },
          },
          400: {
            type: "object",
            properties: {
              error: { type: "string" },
            },
          },
        },
      },
    },
    async (req, reply) => {
      const { sem_id } = req.query as OfferingsQuery;

      if (!sem_id) {
        reply.code(400);
        return {
          error: "sem_id query parameter is required, e.g. /offerings?sem_id=FS-2026",
        } satisfies ErrorResponse;
      }

      // If you only want offerings that have a matching course row, keep JOIN.
      // If you want offerings even when a course is missing, use LEFT JOIN.
      const rows = await query(
        `
        SELECT off.*, c.name
        FROM courseoffering off
        JOIN course c ON c.code = off.code
        WHERE off.sem_id = $1
        ORDER BY c.name NULLS LAST, off.code, off.offering_id;
        `,
        [sem_id]
      );

      return rows;
    }
  );
}