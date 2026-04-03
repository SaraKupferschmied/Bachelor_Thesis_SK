import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

type CoursesQuery = {
  code?: string;
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

function toFloat(value: unknown): number | null {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeCourseCode(value: unknown): string | null {
  if (value === undefined || value === null) return null;

  const raw = String(value).trim().toUpperCase();
  if (!raw) return null;

  return raw.startsWith("UE-") ? raw : `UE-${raw}`;
}

function normalizeCourseCodeLoose(value: unknown): string | null {
  if (value === undefined || value === null) return null;

  const raw = String(value).trim().toUpperCase();
  if (!raw) return null;

  return raw.startsWith("UE-") ? raw.slice(3) : raw;
}

export async function coursesRoutes(app: FastifyInstance) {
  // ============================
  // GET /courses
  // ============================
  app.get(
    "/",
    {
      schema: {
        summary: "Search courses",
        description:
          "Returns courses filtered by code, name, ects, faculty, domain, language, semester, program, and flags. Returns all courses if no filters are provided.",
        querystring: {
          type: "object",
          properties: {
            code: {
              type: "string",
              description:
                "Course code, with or without UE- prefix, e.g. UE-EIG.00037 or EIG.00037",
            },
            mobility: {
              type: "boolean",
              description: "Filter by mobility flag",
            },
            soft_skills: {
              type: "boolean",
              description: "Filter by soft skills flag",
            },
            ects: {
              type: "number",
              description: "Filter by exact course ECTS",
            },
            faculty_id: {
              type: "integer",
            },
            faculty_name: {
              type: "string",
              description: "Matches faculty name in EN/DE/FR",
            },
            domain_id: {
              type: "integer",
            },
            domain_name: {
              type: "string",
            },
            language: {
              type: "string",
              description:
                "Teaching language description, resolved through CourseOffering -> is_taught_in -> Language",
            },
            semester: {
              type: "string",
              description:
                "Semester id or type, e.g. FS-2026, Autumn, Spring",
            },
            name_contains: {
              type: "string",
              description: "Substring match on course name",
            },
            program_id: {
              type: "integer",
            },
            program_name: {
              type: "string",
              description: "Substring match on study program name",
            },
            limit: {
              type: "integer",
              minimum: 1,
              maximum: 500,
            },
          },
        },
      },
    },
    async (req) => {
      const {
        code,
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

      const normalizedCode = normalizeCourseCode(code);
      const looseCode = normalizeCourseCodeLoose(code);

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
        WHERE (
          $1::text IS NULL
          OR c.code = $1
          OR UPPER(REPLACE(c.code, 'UE-', '')) = $2
        )
          AND ($3::boolean IS NULL OR c.mobility = $3)
          AND ($4::boolean IS NULL OR c.soft_skills = $4)
          AND ($5::float IS NULL OR c.ects = $5)
          AND ($6::int IS NULL OR c.faculty_id = $6)
          AND (
            $7::text IS NULL
            OR f.name_en ILIKE '%' || $7 || '%'
            OR f.name_de ILIKE '%' || $7 || '%'
            OR f.name_fr ILIKE '%' || $7 || '%'
          )
          AND ($8::int IS NULL OR c.domain_id = $8)
          AND ($9::text IS NULL OR d.name ILIKE '%' || $9 || '%')
          AND ($10::text IS NULL OR c.name ILIKE '%' || $10 || '%')
          AND ($11::int IS NULL OR p.program_id = $11)
          AND ($12::text IS NULL OR p.name ILIKE '%' || $12 || '%')
          AND (
            $13::text IS NULL
            OR EXISTS (
              SELECT 1
              FROM CourseOffering off
              LEFT JOIN Semester s
                ON s.sem_id = off.sem_id
              WHERE off.code = c.code
                AND (
                  off.sem_id ILIKE $13
                  OR s.sem_id ILIKE $13
                  OR s.type ILIKE $13
                )
            )
          )
          AND (
            $14::text IS NULL
            OR EXISTS (
              SELECT 1
              FROM CourseOffering off
              JOIN is_taught_in iti
                ON iti.offering_id = off.offering_id
              JOIN Language l
                ON l.lang_id = iti.lang_id
              WHERE off.code = c.code
                AND l.description ILIKE $14
            )
          )
        ORDER BY c.code
        LIMIT COALESCE($15::int, 50)
        `,
        [
          normalizedCode,
          looseCode,
          toBoolean(mobility),
          toBoolean(soft_skills),
          toFloat(ects),
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
    }
  );

  // ============================
  // GET /courses/:code
  // exact course lookup, but tolerant to missing UE- prefix
  // ============================
  app.get(
    "/:code",
    {
      schema: {
        summary: "Get one course by code",
        description:
          "Returns one course by code. Accepts both UE-prefixed and raw codes, e.g. UE-EIG.00037 or EIG.00037.",
        params: {
          type: "object",
          required: ["code"],
          properties: {
            code: {
              type: "string",
              description:
                "Course code with or without UE- prefix, e.g. UE-EIG.00037 or EIG.00037",
            },
          },
        },
      },
    },
    async (req) => {
      const { code } = req.params as { code: string };

      const normalizedCode = normalizeCourseCode(code);
      const looseCode = normalizeCourseCodeLoose(code);

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
           OR UPPER(REPLACE(c.code, 'UE-', '')) = $2
        ORDER BY c.code
        LIMIT 1
        `,
        [normalizedCode, looseCode]
      );

      return rows[0] ?? null;
    }
  );
}