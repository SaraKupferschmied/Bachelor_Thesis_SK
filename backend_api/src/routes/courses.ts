import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

type CoursesQuery = {
  mobility?: string | boolean;
  soft_skills?: string | boolean;
  ects?: string | number;
  faculty_id?: string | number;
  faculty_name?: string;
  domain_id?: string | number;
  domain_name?: string;
  language?: string;
  semester?: string;
  name_contains?: string;
  program_id?: string | number;
  program_name?: string;
  limit?: string | number;
};

function toBoolean(value: unknown): boolean | null {
  if (value === undefined || value === null || value === "") return null;
  if (typeof value === "boolean") return value;
  const normalized = String(value).trim().toLowerCase();
  if (["true", "1", "yes"].includes(normalized)) return true;
  if (["false", "0", "no"].includes(normalized)) return false;
  return null;
}

function toInt(value: unknown): number | null {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : null;
}

export async function coursesRoutes(app: FastifyInstance) {
  app.get("/", async (req) => {
    const {
      mobility,
      soft_skills,
      ects,
      faculty_id,
      faculty_name,
      domain_id,
      domain_name,
      language,
      semester,
      name_contains,
      program_id,
      program_name,
      limit,
    } = (req.query as CoursesQuery) ?? {};

    return query(
      `
      SELECT DISTINCT
        c.*,
        f.name_en AS faculty_name,
        d.name AS domain_name
      FROM Course c
      LEFT JOIN Faculty f
        ON f.faculty_id = c.faculty_id
      LEFT JOIN Domain d
        ON d.domain_id = c.domain_id
      LEFT JOIN consist_of co
        ON co.code = c.code
      LEFT JOIN StudyProgram p
        ON p.program_id = co.program_id
      WHERE ($1::boolean IS NULL OR c.mobility = $1)
        AND ($2::boolean IS NULL OR c.soft_skills = $2)
        AND ($3::float IS NULL OR c.ects = $3)
        AND ($4::int IS NULL OR c.faculty_id = $4)
        AND (
          $5::text IS NULL
          OR f.name_en ILIKE '%' || $5 || '%'
          OR f.name_de ILIKE '%' || $5 || '%'
          OR f.name_fr ILIKE '%' || $5 || '%'
        )
        AND ($6::int IS NULL OR c.domain_id = $6)
        AND ($7::text IS NULL OR d.name ILIKE '%' || $7 || '%')
        AND ($8::text IS NULL OR c.name ILIKE '%' || $8 || '%')
        AND ($9::int IS NULL OR p.program_id = $9)
        AND ($10::text IS NULL OR p.name ILIKE '%' || $10 || '%')
        AND (
          $11::text IS NULL
          OR EXISTS (
            SELECT 1
            FROM CourseOffering off
            LEFT JOIN Semester s
              ON s.sem_id = off.sem_id
            WHERE off.code = c.code
              AND (
                off.sem_id ILIKE $11
                OR s.sem_id ILIKE $11
                OR s.type ILIKE $11
              )
          )
        )
        AND (
          $12::text IS NULL
          OR EXISTS (
            SELECT 1
            FROM CourseOffering off
            JOIN is_taught_in iti
              ON iti.offering_id = off.offering_id
            JOIN Language l
              ON l.lang_id = iti.lang_id
            WHERE off.code = c.code
              AND l.description ILIKE $12
          )
        )
      ORDER BY c.code
      LIMIT COALESCE($13::int, 50)
      `,
      [
        toBoolean(mobility),
        toBoolean(soft_skills),
        ects !== undefined && ects !== null && ects !== "" ? Number(ects) : null,
        toInt(faculty_id),
        faculty_name ?? null,
        toInt(domain_id),
        domain_name ?? null,
        name_contains ?? null,
        toInt(program_id),
        program_name ?? null,
        semester ? `%${String(semester).trim()}%` : null,
        language ? `%${String(language).trim()}%` : null,
        toInt(limit) ?? 50,
      ]
    );
  });

  app.get("/:code", async (req) => {
    const { code } = req.params as { code: string };

    const rows = await query(
      `
      SELECT
        c.*,
        f.name_en AS faculty_name,
        d.name AS domain_name
      FROM Course c
      LEFT JOIN Faculty f
        ON f.faculty_id = c.faculty_id
      LEFT JOIN Domain d
        ON d.domain_id = c.domain_id
      WHERE c.code = $1
      `,
      [code]
    );

    return rows[0] ?? null;
  });

}