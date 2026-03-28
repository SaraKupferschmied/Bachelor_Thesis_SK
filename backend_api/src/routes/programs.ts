import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

type ProgramsListQuery = {
  name?: string;
  degree_level?: "Bachelor" | "Master" | "Doctorate";
  faculty_id?: string;
  faculty_name?: string;
  study_start?: "Autumn" | "Spring" | "Both";
};

type ProgramCoursesQuery = {
  program_name?: string;
  course_type?: "Mandatory" | "Elective";
  semester_type?: "Autumn" | "Spring";
};

export async function programsRoutes(app: FastifyInstance) {
  // ============================
  // GET /programs
  // ============================
  app.get("/", async (req) => {
    const {
      name,
      degree_level,
      faculty_id,
      faculty_name,
      study_start,
    } = (req.query as ProgramsListQuery) ?? {};

    return query(
      `
      SELECT 
        p.program_id,
        p.name,
        p.degree_level,
        p.total_ects,
        p.study_start,
        p.faculty_id,
        f.name AS faculty_name
      FROM StudyProgram p
      LEFT JOIN Faculty f
        ON f.faculty_id = p.faculty_id
      WHERE ($1::text IS NULL OR p.name ILIKE '%' || $1 || '%')
        AND ($2::text IS NULL OR p.degree_level = $2)
        AND ($3::int  IS NULL OR p.faculty_id = $3)
        AND ($4::text IS NULL OR f.name ILIKE '%' || $4 || '%')
        AND ($5::text IS NULL OR p.study_start = $5)
      ORDER BY p.name
      `,
      [
        name ?? null,
        degree_level ?? null,
        faculty_id ? Number(faculty_id) : null,
        faculty_name ?? null,
        study_start ?? null,
      ]
    );
  });

  // ============================
  // GET /programs/:id
  // ============================
  app.get("/:id", async (req) => {
    const { id } = req.params as { id: string };

    const rows = await query(
      `
      SELECT 
        p.*,
        f.name AS faculty_name
      FROM StudyProgram p
      LEFT JOIN Faculty f
        ON f.faculty_id = p.faculty_id
      WHERE p.program_id = $1
      `,
      [id]
    );

    return rows[0] ?? null;
  });

  // ============================
  // GET /programs/courses
  // (search by program name instead of id)
  // ============================
  app.get("/courses", async (req) => {
    const { program_name, course_type, semester_type } =
      (req.query as ProgramCoursesQuery) ?? {};

    return query(
      `
      SELECT DISTINCT
        c.*,
        co.course_type,
        p.name AS program_name
      FROM StudyProgram p
      JOIN consist_of co
        ON co.program_id = p.program_id
      JOIN Course c
        ON c.code = co.code
      LEFT JOIN CourseOffering off
        ON off.code = c.code
      LEFT JOIN Semester s
        ON s.sem_id = off.sem_id
      WHERE ($1::text IS NULL OR p.name ILIKE '%' || $1 || '%')
        AND ($2::text IS NULL OR co.course_type = $2)
        AND ($3::text IS NULL OR s.type = $3)
      ORDER BY p.name, c.code
      `,
      [
        program_name ?? null,
        course_type ?? null,
        semester_type ?? null,
      ]
    );
  });

  // ============================
  // GET /programs/:id/courses
  // ============================
  app.get("/:id/courses", async (req) => {
    const { id } = req.params as { id: string };
    const { course_type, semester_type } =
      (req.query as ProgramCoursesQuery) ?? {};

    return query(
      `
      SELECT DISTINCT
        c.*,
        co.course_type
      FROM consist_of co
      JOIN Course c
        ON c.code = co.code
      LEFT JOIN CourseOffering off
        ON off.code = c.code
      LEFT JOIN Semester s
        ON s.sem_id = off.sem_id
      WHERE co.program_id = $1
        AND ($2::text IS NULL OR co.course_type = $2)
        AND ($3::text IS NULL OR s.type = $3)
      ORDER BY c.code
      `,
      [id, course_type ?? null, semester_type ?? null]
    );
  });

  // ============================
  // GET /programs/:id/docs
  // ============================
  app.get("/:id/docs", async (req) => {
    const { id } = req.params as { id: string };
    const { doc_type } = (req.query as { doc_type?: string }) ?? {};

    return query(
      `
      SELECT *
      FROM programDocument
      WHERE program_id = $1
        AND ($2::text IS NULL OR doc_type = $2)
      ORDER BY fetched_at DESC NULLS LAST
      `,
      [id, doc_type ?? null]
    );
  });
}