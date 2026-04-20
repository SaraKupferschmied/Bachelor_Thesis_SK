import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

type ProgramsListQuery = {
  name_en?: string;
  name_de?: string;
  name_fr?: string;
  degree_level?: "Bachelor" | "Master" | "Doctorate";
  faculty_id?: string;
  faculty_name?: string;
  study_start?: "Autumn" | "Spring" | "Both";
  total_ects?: string;
};

type ProgramCoursesQuery = {
  program_en?: string;
  program_de?: string;
  program_fr?: string;
  degree_level?: "Bachelor" | "Master" | "Doctorate";
  faculty_id?: string;
  faculty_name?: string;
  study_start?: "Autumn" | "Spring" | "Both";
  total_ects?: string;

  course_type?: "Mandatory" | "Elective";
  semester_type?: "Autumn" | "Spring";
  ects?: string;
  domain_id?: string;
  domain_name?: string;
  language?: string;
  semester?: string;
  name_contains?: string;
  mobility?: string;
  soft_skills?: string;
  limit?: string;
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

export async function programsRoutes(app: FastifyInstance) {
  // ============================
  // GET /programs
  // ============================
  app.get(
    "/",
    {
      schema: {
        summary: "Get study programs",
        description:
          "Returns all programs if no filters are provided. Optional filters can narrow the result.",
        querystring: {
          type: "object",
          properties: {
            name_en: { type: "string" },
            name_de: { type: "string" },
            name_fr: { type: "string" },
            degree_level: {
              type: "string",
              enum: ["Bachelor", "Master", "Doctorate"],
            },
            faculty_id: { type: "integer" },
            faculty_name: { type: "string" },
            study_start: {
              type: "string",
              enum: ["Autumn", "Spring", "Both"],
            },
            total_ects: { type: "number" },
          },
        },
      },
    },
    async (req) => {
      const {
        name_en,
        name_de,
        name_fr,
        degree_level,
        faculty_id,
        faculty_name,
        study_start,
        total_ects,
      } = (req.query as ProgramsListQuery) ?? {};

      return query(
        `
        SELECT 
          p.program_id,
          p.name,
          p.name_en,
          p.name_de,
          p.name_fr,
          p.degree_level,
          p.total_ects,
          p.study_start,
          p.faculty_id,
          f.name_en AS faculty_name
        FROM StudyProgram p
        LEFT JOIN Faculty f
          ON f.faculty_id = p.faculty_id
        WHERE ($1::text IS NULL OR p.name_en ILIKE '%' || $1 || '%')
          AND ($2::text IS NULL OR p.name_de ILIKE '%' || $2 || '%')
          AND ($3::text IS NULL OR p.name_fr ILIKE '%' || $3 || '%')
          AND ($4::text IS NULL OR p.degree_level = $4)
          AND ($5::int IS NULL OR p.faculty_id = $5)
          AND (
            $6::text IS NULL
            OR f.name_en ILIKE '%' || $6 || '%'
            OR f.name_de ILIKE '%' || $6 || '%'
            OR f.name_fr ILIKE '%' || $6 || '%'
          )
          AND ($7::text IS NULL OR p.study_start = $7)
          AND ($8::float IS NULL OR p.total_ects = $8)
        ORDER BY COALESCE(p.name_en, p.name), p.degree_level, p.total_ects
        `,
        [
          name_en ?? null,
          name_de ?? null,
          name_fr ?? null,
          degree_level ?? null,
          toInt(faculty_id),
          faculty_name ?? null,
          study_start ?? null,
          toFloat(total_ects),
        ]
      );
    }
  );

  // ============================
  // GET /programs/courses
  // (search by program attributes instead of id)
  // ============================
  app.get(
    "/courses",
    {
      schema: {
        summary: "Get program courses by program metadata",
        description:
          "Returns the same kind of result as GET /programs/:id/courses, but filters the target program(s) by name and optional program metadata. Use degree_level and total_ects to disambiguate non-unique names.",
        querystring: {
          type: "object",
          properties: {
            program_en: { type: "string" },
            program_de: { type: "string" },
            program_fr: { type: "string" },
            degree_level: {
              type: "string",
              enum: ["Bachelor", "Master", "Doctorate"],
            },
            faculty_id: { type: "integer" },
            faculty_name: { type: "string" },
            study_start: {
              type: "string",
              enum: ["Autumn", "Spring", "Both"],
            },
            total_ects: { type: "number" },

            course_type: {
              type: "string",
              enum: ["Mandatory", "Elective"],
            },
            semester_type: {
              type: "string",
              enum: ["Autumn", "Spring"],
            },
            ects: { type: "number" },
            domain_id: { type: "integer" },
            domain_name: { type: "string" },
            language: { type: "string" },
            semester: { type: "string" },
            name_contains: { type: "string" },
            mobility: { type: "boolean" },
            soft_skills: { type: "boolean" },
            limit: { type: "integer", minimum: 1, maximum: 500 },
          },
        },
      },
    },
    async (req) => {
      const {
        program_en,
        program_de,
        program_fr,
        degree_level,
        faculty_id,
        faculty_name,
        study_start,
        total_ects,
        course_type,
        semester_type,
        ects,
        domain_id,
        domain_name,
        language,
        semester,
        name_contains,
        mobility,
        soft_skills,
        limit,
      } = (req.query as ProgramCoursesQuery) ?? {};

      return query(
        `
        SELECT DISTINCT
          c.*,
          f.name_en AS faculty_name,
          d.name AS domain_name,
          co.course_type,
          p.program_id,
          p.name_en AS program_name_en,
          p.name_de AS program_name_de,
          p.name_fr AS program_name_fr,
          p.degree_level,
          p.total_ects,
          p.study_start
        FROM StudyProgram p
        JOIN consist_of co
          ON co.program_id = p.program_id
        JOIN Course c
          ON c.code = co.code
        LEFT JOIN Faculty f
          ON f.faculty_id = c.faculty_id
        LEFT JOIN Domain d
          ON d.domain_id = c.domain_id
        LEFT JOIN Faculty pf
          ON pf.faculty_id = p.faculty_id
        WHERE ($1::text IS NULL OR p.name_en ILIKE '%' || $1 || '%')
          AND ($2::text IS NULL OR p.name_de ILIKE '%' || $2 || '%')
          AND ($3::text IS NULL OR p.name_fr ILIKE '%' || $3 || '%')
          AND ($4::text IS NULL OR p.degree_level = $4)
          AND ($5::int IS NULL OR p.faculty_id = $5)
          AND (
            $6::text IS NULL
            OR pf.name_en ILIKE '%' || $6 || '%'
            OR pf.name_de ILIKE '%' || $6 || '%'
            OR pf.name_fr ILIKE '%' || $6 || '%'
          )
          AND ($7::text IS NULL OR p.study_start = $7)
          AND ($8::float IS NULL OR p.total_ects = $8)

          AND ($9::text IS NULL OR co.course_type = $9)
          AND (
            $10::text IS NULL
            OR EXISTS (
              SELECT 1
              FROM CourseOffering off
              LEFT JOIN Semester s
                ON s.sem_id = off.sem_id
              WHERE off.code = c.code
                AND s.type = $10
            )
          )
          AND ($11::float IS NULL OR c.ects = $11)
          AND ($12::int IS NULL OR c.domain_id = $12)
          AND ($13::text IS NULL OR d.name ILIKE '%' || $13 || '%')
          AND ($14::text IS NULL OR c.name ILIKE '%' || $14 || '%')
          AND ($15::boolean IS NULL OR c.mobility = $15)
          AND ($16::boolean IS NULL OR c.soft_skills = $16)
          AND (
            $17::text IS NULL
            OR EXISTS (
              SELECT 1
              FROM CourseOffering off
              LEFT JOIN Semester s
                ON s.sem_id = off.sem_id
              WHERE off.code = c.code
                AND (
                  off.sem_id ILIKE $17
                  OR s.sem_id ILIKE $17
                  OR s.type ILIKE $17
                )
            )
          )
          AND (
            $18::text IS NULL
            OR EXISTS (
              SELECT 1
              FROM CourseOffering off
              JOIN is_taught_in iti
                ON iti.offering_id = off.offering_id
              JOIN Language l
                ON l.lang_id = iti.lang_id
              WHERE off.code = c.code
                AND l.description ILIKE $18
            )
          )
        ORDER BY COALESCE(p.name_en, p.name), p.degree_level, p.total_ects, c.code
        LIMIT COALESCE($19::int, 100)
        `,
        [
          program_en ?? null,
          program_de ?? null,
          program_fr ?? null,
          degree_level ?? null,
          toInt(faculty_id),
          faculty_name ?? null,
          study_start ?? null,
          toFloat(total_ects),

          course_type ?? null,
          semester_type ?? null,
          toFloat(ects),
          toInt(domain_id),
          domain_name ?? null,
          name_contains ?? null,
          toBoolean(mobility),
          toBoolean(soft_skills),
          semester ? `%${String(semester).trim()}%` : null,
          language ? `%${String(language).trim()}%` : null,
          toInt(limit) ?? 100,
        ]
      );
    }
  );

  // ============================
  // GET /programs/:id/courses
  // ============================
  app.get(
    "/:id/courses",
    {
      schema: {
        summary: "Get courses for a specific program id",
        params: {
          type: "object",
          required: ["id"],
          properties: {
            id: { type: "integer" },
          },
        },
        querystring: {
          type: "object",
          properties: {
            course_type: {
              type: "string",
              enum: ["Mandatory", "Elective"],
            },
            semester_type: {
              type: "string",
              enum: ["Autumn", "Spring"],
            },
            ects: { type: "number" },
            faculty_id: { type: "integer" },
            faculty_name: { type: "string" },
            domain_id: { type: "integer" },
            domain_name: { type: "string" },
            language: { type: "string" },
            semester: { type: "string" },
            name_contains: { type: "string" },
            mobility: { type: "boolean" },
            soft_skills: { type: "boolean" },
            limit: { type: "integer", minimum: 1, maximum: 500 },
          },
        },
      },
    },
    async (req) => {
      const { id } = req.params as { id: string };
      const {
        course_type,
        semester_type,
        ects,
        faculty_id,
        faculty_name,
        domain_id,
        domain_name,
        language,
        semester,
        name_contains,
        mobility,
        soft_skills,
        limit,
      } = (req.query as ProgramCoursesQuery) ?? {};

      return query(
        `
        SELECT DISTINCT
          c.*,
          f.name_en AS faculty_name,
          d.name AS domain_name,
          co.course_type
        FROM consist_of co
        JOIN Course c
          ON c.code = co.code
        LEFT JOIN Faculty f
          ON f.faculty_id = c.faculty_id
        LEFT JOIN Domain d
          ON d.domain_id = c.domain_id
        WHERE co.program_id = $1
          AND ($2::text IS NULL OR co.course_type = $2)
          AND (
            $3::text IS NULL
            OR EXISTS (
              SELECT 1
              FROM CourseOffering off
              LEFT JOIN Semester s
                ON s.sem_id = off.sem_id
              WHERE off.code = c.code
                AND s.type = $3
            )
          )
          AND ($4::float IS NULL OR c.ects = $4)
          AND ($5::int IS NULL OR c.faculty_id = $5)
          AND (
            $6::text IS NULL
            OR f.name_en ILIKE '%' || $6 || '%'
            OR f.name_de ILIKE '%' || $6 || '%'
            OR f.name_fr ILIKE '%' || $6 || '%'
          )
          AND ($7::int IS NULL OR c.domain_id = $7)
          AND ($8::text IS NULL OR d.name ILIKE '%' || $8 || '%')
          AND ($9::text IS NULL OR c.name ILIKE '%' || $9 || '%')
          AND ($10::boolean IS NULL OR c.mobility = $10)
          AND ($11::boolean IS NULL OR c.soft_skills = $11)
          AND (
            $12::text IS NULL
            OR EXISTS (
              SELECT 1
              FROM CourseOffering off
              LEFT JOIN Semester s
                ON s.sem_id = off.sem_id
              WHERE off.code = c.code
                AND (
                  off.sem_id ILIKE $12
                  OR s.sem_id ILIKE $12
                  OR s.type ILIKE $12
                )
            )
          )
          AND (
            $13::text IS NULL
            OR EXISTS (
              SELECT 1
              FROM CourseOffering off
              JOIN is_taught_in iti
                ON iti.offering_id = off.offering_id
              JOIN Language l
                ON l.lang_id = iti.lang_id
              WHERE off.code = c.code
                AND l.description ILIKE $13
            )
          )
        ORDER BY c.code
        LIMIT COALESCE($14::int, 100)
        `,
        [
          toInt(id),
          course_type ?? null,
          semester_type ?? null,
          toFloat(ects),
          toInt(faculty_id),
          faculty_name ?? null,
          toInt(domain_id),
          domain_name ?? null,
          name_contains ?? null,
          toBoolean(mobility),
          toBoolean(soft_skills),
          semester ? `%${String(semester).trim()}%` : null,
          language ? `%${String(language).trim()}%` : null,
          toInt(limit) ?? 100,
        ]
      );
    }
  );

  // ============================
  // GET /programs/:id/docs
  // ============================
  app.get(
    "/:id/docs",
    {
      schema: {
        summary: "Get documents for a specific program id",
        params: {
          type: "object",
          required: ["id"],
          properties: {
            id: { type: "integer" },
          },
        },
        querystring: {
          type: "object",
          properties: {
            doc_type: {
              type: "string",
              enum: ["study_plan", "regulation", "brochure", "other"],
            },
          },
        },
      },
    },
    async (req) => {
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
        [toInt(id), doc_type ?? null]
      );
    }
  );

  // ============================
  // GET /programs/:id
  // ============================
  app.get(
    "/:id",
    {
      schema: {
        summary: "Get one program by id",
        params: {
          type: "object",
          required: ["id"],
          properties: {
            id: { type: "integer" },
          },
        },
      },
    },
    async (req) => {
      const { id } = req.params as { id: string };

      const rows = await query(
        `
        SELECT 
          p.*,
          f.name_en AS faculty_name
        FROM StudyProgram p
        LEFT JOIN Faculty f
          ON f.faculty_id = p.faculty_id
        WHERE p.program_id = $1
        `,
        [toInt(id)]
      );

      return rows[0] ?? null;
    }
  );
}