import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

type DocsQuery = {
  doc_type?: "study_plan" | "regulation" | "brochure" | "other";
  degree_level?: "Bachelor" | "Master" | "Doctorate";
  faculty_id?: string;
};

export async function docsRoutes(app: FastifyInstance) {
  // GET /docs-api/program/:id
  // Optional: ?doc_type=study_plan
  app.get("/program/:id", async (req) => {
    const { id } = req.params as { id: string };
    const { doc_type } = (req.query as DocsQuery) ?? {};

    return query(
      `
      SELECT *
      FROM programDocument
      WHERE program_id = $1
        AND ($2::text IS NULL OR doc_type = $2)
      ORDER BY fetched_at DESC NULLS LAST, doc_id DESC
      `,
      [id, doc_type ?? null]
    );
  });

  // GET /docs-api/studyplans
  // Optional:
  //   ?degree_level=Bachelor
  //   ?faculty_id=3
  app.get("/studyplans", async (req) => {
    const { degree_level, faculty_id } = (req.query as DocsQuery) ?? {};

    return query(
      `
      SELECT
        d.doc_id,
        d.program_id,
        p.name AS program_name,
        p.degree_level,
        p.faculty_id,
        d.label,
        d.url,
        d.doc_type,
        d.fetched_at,
        d.parse_status,
        d.parse_notes
      FROM programDocument d
      JOIN StudyProgram p
        ON p.program_id = d.program_id
      WHERE d.doc_type = 'study_plan'
        AND ($1::text IS NULL OR p.degree_level = $1)
        AND ($2::int  IS NULL OR p.faculty_id = $2)
      ORDER BY p.name, d.fetched_at DESC NULLS LAST
      `,
      [degree_level ?? null, faculty_id ? Number(faculty_id) : null]
    );
  });
}