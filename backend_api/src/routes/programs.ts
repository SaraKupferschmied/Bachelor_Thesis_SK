import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

type ProgramsListQuery = {
  name?: string;
  name_en?: string;
  name_de?: string;
  name_fr?: string;
  degree_level?: "Bachelor" | "Master" | "Doctorate";
  faculty_id?: string;
  faculty_name?: string;
  study_start?: "Autumn" | "Spring" | "Both";
  total_ects?: string;
  total_ects_operator?: "eq" | "lt" | "lte" | "gt" | "gte";
  program_type?: "minor" | "major" | "mono";
};

type ProgramCoursesQuery = {
  q?: string;
  program_en?: string;
  program_de?: string;
  program_fr?: string;
  degree_level?: "Bachelor" | "Master" | "Doctorate";
  faculty_id?: string;
  faculty_name?: string;
  study_start?: "Autumn" | "Spring" | "Both";
  total_ects?: string;
  total_ects_operator?: "eq" | "lt" | "lte" | "gt" | "gte";
  program_type?: "minor" | "major" | "mono";

  course_type?: "Mandatory" | "Elective";
  semester_type?: "Autumn" | "Spring";
  ects?: string;
  ects_operator?: "eq" | "lt" | "lte" | "gt" | "gte";
  domain_id?: string;
  domain_name?: string;
  language?: string;
  semester?: string;
  name_contains?: string;
  mobility?: string;
  soft_skills?: string;
  section_contains?: string;
  limit?: string;
};

type NumericOperator = "eq" | "lt" | "lte" | "gt" | "gte";

function parseNumericFilter(
  value: unknown,
  explicitOperator?: unknown
): { value: number | null; operator: NumericOperator } {
  const explicit = String(explicitOperator ?? "").trim().toLowerCase();
  const operatorMap: Record<string, NumericOperator> = {
    "=": "eq",
    eq: "eq",
    exact: "eq",
    "<": "lt",
    lt: "lt",
    "<=": "lte",
    lte: "lte",
    ">": "gt",
    gt: "gt",
    ">=": "gte",
    gte: "gte",
  };

  let operator = operatorMap[explicit] ?? "eq";

  if (value === undefined || value === null || value === "") {
    return { value: null, operator };
  }

  const raw = String(value).trim();
  const match = raw.match(/^(<=|>=|<|>|=)\s*(.+)$/);
  const symbol = match?.[1];
  const numericPart = match?.[2] ?? raw;
  if (symbol) operator = operatorMap[symbol] ?? operator;

  const parsed = Number(numericPart);
  return { value: Number.isFinite(parsed) ? parsed : null, operator };
}

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

async function getProgramDocuments(programIds: number[]) {
  const uniqueIds = [...new Set(programIds.filter((id) => Number.isInteger(id)))];
  if (uniqueIds.length === 0) return [];

  return query(
    `
    SELECT
      pd.program_id,
      COALESCE(jsonb_agg(jsonb_build_object(
        'doc_id', pd.doc_id,
        'label', pd.label,
        'url', pd.url,
        'doc_type', pd.doc_type,
        'fetched_at', pd.fetched_at
      ) ORDER BY pd.fetched_at DESC NULLS LAST, pd.doc_id DESC), '[]'::jsonb) AS program_documents
    FROM programDocument pd
    WHERE pd.program_id = ANY($1::int[])
    GROUP BY pd.program_id
    ORDER BY pd.program_id
    `,
    [uniqueIds]
  );
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
            name: { type: "string", description: "Generic name search across EN/DE/FR/default program names" },
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
            total_ects: {
              type: "string",
              description:
                "Filter by total program ECTS. Supports exact values and comparisons like 60, >90, >=90, <90, <=90.",
            },
            total_ects_operator: {
              type: "string",
              enum: ["eq", "lt", "lte", "gt", "gte"],
              description:
                "Optional operator for total_ects when the total_ects value is passed without a symbol.",
            },
            program_type: {
              type: "string",
              enum: ["minor", "major", "mono"],
              description:
                "Derived classification from degree_level and total_ects: minor <90 ECTS; master major =90; bachelor major 90-150; master mono =120; bachelor mono =180.",
            },
          },
        },
      },
    },
    async (req) => {
      const {
        name,
        name_en,
        name_de,
        name_fr,
        degree_level,
        faculty_id,
        faculty_name,
        study_start,
        total_ects,
        total_ects_operator,
        program_type,
      } = (req.query as ProgramsListQuery) ?? {};

      const totalEctsFilter = parseNumericFilter(total_ects, total_ects_operator);

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
          CASE
            WHEN p.total_ects < 90 THEN 'minor'
            WHEN p.degree_level = 'Master' AND p.total_ects = 90 THEN 'major'
            WHEN p.degree_level = 'Bachelor' AND p.total_ects >= 90 AND p.total_ects < 180 THEN 'major'
            WHEN p.degree_level = 'Master' AND p.total_ects = 120 THEN 'mono'
            WHEN p.degree_level = 'Bachelor' AND p.total_ects = 180 THEN 'mono'
            ELSE NULL
          END AS program_type,
          p.study_start,
          p.faculty_id,
          f.name_en AS faculty_name,
          COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
              'doc_id', d.doc_id,
              'label', d.label,
              'url', d.url,
              'doc_type', d.doc_type,
              'fetched_at', d.fetched_at
            ) ORDER BY d.fetched_at DESC NULLS LAST, d.doc_id DESC)
            FROM programDocument d
            WHERE d.program_id = p.program_id
          ), '[]'::jsonb) AS program_documents
        FROM StudyProgram p
        LEFT JOIN Faculty f
          ON f.faculty_id = p.faculty_id
        WHERE ($1::text IS NULL OR p.name_en ILIKE '%' || $1 || '%' OR p.name ILIKE '%' || $1 || '%')
          AND ($2::text IS NULL OR p.name_de ILIKE '%' || $2 || '%' OR p.name ILIKE '%' || $2 || '%')
          AND ($3::text IS NULL OR p.name_fr ILIKE '%' || $3 || '%' OR p.name ILIKE '%' || $3 || '%')
          AND (
            $11::text IS NULL
            OR p.name ILIKE '%' || $11 || '%'
            OR p.name_en ILIKE '%' || $11 || '%'
            OR p.name_de ILIKE '%' || $11 || '%'
            OR p.name_fr ILIKE '%' || $11 || '%'
          )
          AND ($4::text IS NULL OR p.degree_level = $4)
          AND ($5::int IS NULL OR p.faculty_id = $5)
          AND (
            $6::text IS NULL
            OR f.name_en ILIKE '%' || $6 || '%'
            OR f.name_de ILIKE '%' || $6 || '%'
            OR f.name_fr ILIKE '%' || $6 || '%'
          )
          AND ($7::text IS NULL OR p.study_start = $7)
          AND (
            $8::float IS NULL
            OR CASE $9::text
              WHEN 'lt' THEN p.total_ects < $8
              WHEN 'lte' THEN p.total_ects <= $8
              WHEN 'gt' THEN p.total_ects > $8
              WHEN 'gte' THEN p.total_ects >= $8
              ELSE p.total_ects = $8
            END
          )
          AND (
            $10::text IS NULL
            OR CASE
              WHEN p.total_ects < 90 THEN 'minor'
              WHEN p.degree_level = 'Master' AND p.total_ects = 90 THEN 'major'
              WHEN p.degree_level = 'Bachelor' AND p.total_ects >= 90 AND p.total_ects < 180 THEN 'major'
              WHEN p.degree_level = 'Master' AND p.total_ects = 120 THEN 'mono'
              WHEN p.degree_level = 'Bachelor' AND p.total_ects = 180 THEN 'mono'
              ELSE NULL
            END = $10
          )
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
          totalEctsFilter.value,
          totalEctsFilter.operator,
          program_type ?? null,
          name ?? null,
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
            total_ects: {
              type: "string",
              description:
                "Filter by total program ECTS. Supports exact values and comparisons like 60, >90, >=90, <90, <=90.",
            },
            total_ects_operator: {
              type: "string",
              enum: ["eq", "lt", "lte", "gt", "gte"],
            },
            program_type: {
              type: "string",
              enum: ["minor", "major", "mono"],
            },

            course_type: {
              type: "string",
              enum: ["Mandatory", "Elective"],
            },
            semester_type: {
              type: "string",
              enum: ["Autumn", "Spring"],
            },
            ects: {
              type: "string",
              description:
                "Filter by course ECTS. Supports exact values and comparisons like 6, >6, >=6, <6, <=6.",
            },
            ects_operator: {
              type: "string",
              enum: ["eq", "lt", "lte", "gt", "gte"],
            },
            domain_id: { type: "integer" },
            domain_name: { type: "string" },
            language: { type: "string" },
            semester: { type: "string" },
            name_contains: { type: "string" },
            q: {
              type: "string",
              description: "Broad text search across study program names, course title, course description, program-course title/description, and domain",
            },
            mobility: { type: "boolean" },
            soft_skills: { type: "boolean" },
            section_contains: {
              type: "string",
              description: "Substring match on consist_of.description, useful for study-year or section labels such as first year.",
            },
            limit: { type: "integer", minimum: 1, maximum: 500 },
          },
        },
      },
    },
    async (req) => {
      const {
        q,
        program_en,
        program_de,
        program_fr,
        degree_level,
        faculty_id,
        faculty_name,
        study_start,
        total_ects,
        total_ects_operator,
        program_type,
        course_type,
        semester_type,
        ects,
        ects_operator,
        domain_id,
        domain_name,
        language,
        semester,
        name_contains,
        mobility,
        soft_skills,
        section_contains,
        limit,
      } = (req.query as ProgramCoursesQuery & { section_contains?: string }) ?? {};

      const totalEctsFilter = parseNumericFilter(total_ects, total_ects_operator);
      const courseEctsFilter = parseNumericFilter(ects, ects_operator);

      const rows = await query(
        `
        SELECT DISTINCT
          c.*,
          f.name_en AS faculty_name,
          d.name AS domain_name,
          co.course_type,
          co.description AS program_course_description,
          p.program_id,
          p.name_en AS program_name_en,
          p.name_de AS program_name_de,
          p.name_fr AS program_name_fr,
          p.degree_level,
          p.total_ects,
          CASE
            WHEN p.total_ects < 90 THEN 'minor'
            WHEN p.degree_level = 'Master' AND p.total_ects = 90 THEN 'major'
            WHEN p.degree_level = 'Bachelor' AND p.total_ects >= 90 AND p.total_ects < 180 THEN 'major'
            WHEN p.degree_level = 'Master' AND p.total_ects = 120 THEN 'mono'
            WHEN p.degree_level = 'Bachelor' AND p.total_ects = 180 THEN 'mono'
            ELSE NULL
          END AS program_type,
          p.study_start,
          COALESCE(p.name_en, p.name) AS program_sort_name
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
        WHERE ($1::text IS NULL OR p.name_en ILIKE '%' || $1 || '%' OR p.name ILIKE '%' || $1 || '%')
          AND ($2::text IS NULL OR p.name_de ILIKE '%' || $2 || '%' OR p.name ILIKE '%' || $2 || '%')
          AND ($3::text IS NULL OR p.name_fr ILIKE '%' || $3 || '%' OR p.name ILIKE '%' || $3 || '%')
          AND ($4::text IS NULL OR p.degree_level = $4)
          AND ($5::int IS NULL OR p.faculty_id = $5)
          AND (
            $6::text IS NULL
            OR pf.name_en ILIKE '%' || $6 || '%'
            OR pf.name_de ILIKE '%' || $6 || '%'
            OR pf.name_fr ILIKE '%' || $6 || '%'
          )
          AND ($7::text IS NULL OR p.study_start = $7)
          AND (
            $8::float IS NULL
            OR CASE $9::text
              WHEN 'lt' THEN p.total_ects < $8
              WHEN 'lte' THEN p.total_ects <= $8
              WHEN 'gt' THEN p.total_ects > $8
              WHEN 'gte' THEN p.total_ects >= $8
              ELSE p.total_ects = $8
            END
          )
          AND (
            $10::text IS NULL
            OR CASE
              WHEN p.total_ects < 90 THEN 'minor'
              WHEN p.degree_level = 'Master' AND p.total_ects = 90 THEN 'major'
              WHEN p.degree_level = 'Bachelor' AND p.total_ects >= 90 AND p.total_ects < 180 THEN 'major'
              WHEN p.degree_level = 'Master' AND p.total_ects = 120 THEN 'mono'
              WHEN p.degree_level = 'Bachelor' AND p.total_ects = 180 THEN 'mono'
              ELSE NULL
            END = $10
          )

          AND ($11::text IS NULL OR co.course_type = $11)
          AND (
            $12::text IS NULL
            OR EXISTS (
              SELECT 1
              FROM CourseOffering off
              LEFT JOIN Semester s
                ON s.sem_id = off.sem_id
              WHERE off.code = c.code
                AND s.type = $12
            )
          )
          AND (
            $13::float IS NULL
            OR CASE $14::text
              WHEN 'lt' THEN c.ects < $13
              WHEN 'lte' THEN c.ects <= $13
              WHEN 'gt' THEN c.ects > $13
              WHEN 'gte' THEN c.ects >= $13
              ELSE c.ects = $13
            END
          )
          AND ($15::int IS NULL OR c.domain_id = $15)
          AND ($16::text IS NULL OR d.name ILIKE '%' || $16 || '%')
          AND ($17::text IS NULL OR c.name ILIKE '%' || $17 || '%')
          AND ($18::boolean IS NULL OR c.mobility = $18)
          AND ($19::boolean IS NULL OR c.soft_skills = $19)
          AND (
            $20::text IS NULL
            OR EXISTS (
              SELECT 1
              FROM CourseOffering off
              LEFT JOIN Semester s
                ON s.sem_id = off.sem_id
              WHERE off.code = c.code
                AND (
                  off.sem_id ILIKE $20
                  OR s.sem_id ILIKE $20
                  OR s.type ILIKE $20
                )
            )
          )
          AND (
            $21::text IS NULL
            OR EXISTS (
              SELECT 1
              FROM CourseOffering off
              JOIN is_taught_in iti
                ON iti.offering_id = off.offering_id
              JOIN Language l
                ON l.lang_id = iti.lang_id
              WHERE off.code = c.code
                AND l.description ILIKE $21
            )
          )
          AND ($22::text IS NULL OR co.description ILIKE $22)
          AND (
            $24::text IS NULL
            OR p.name ILIKE $24
            OR p.name_en ILIKE $24
            OR p.name_de ILIKE $24
            OR p.name_fr ILIKE $24
            OR c.name ILIKE $24
            OR c.description ILIKE $24
            OR c.learning_goals ILIKE $24
            OR co.course_name ILIKE $24
            OR co.description ILIKE $24
            OR d.name ILIKE $24
          )
        ORDER BY program_sort_name, p.degree_level, p.total_ects, co.description NULLS LAST, c.code
        LIMIT COALESCE($23::int, 100)
        `,
        [
          program_en ?? null,
          program_de ?? null,
          program_fr ?? null,
          degree_level ?? null,
          toInt(faculty_id),
          faculty_name ?? null,
          study_start ?? null,
          totalEctsFilter.value,
          totalEctsFilter.operator,
          program_type ?? null,

          course_type ?? null,
          semester_type ?? null,
          courseEctsFilter.value,
          courseEctsFilter.operator,
          toInt(domain_id),
          domain_name ?? null,
          name_contains ?? null,
          toBoolean(mobility),
          toBoolean(soft_skills),
          semester ? `%${String(semester).trim()}%` : null,
          language ? `%${String(language).trim()}%` : null,
          section_contains ? `%${String(section_contains).trim()}%` : null,
          toInt(limit) ?? 100,
          q ? `%${String(q).trim()}%` : null,
        ]
      );

      const program_documents = await getProgramDocuments(
        rows.map((row: any) => Number(row.program_id))
      );

      return {
        program_documents,
        courses: rows,
      };
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
            ects: {
              type: "string",
              description:
                "Filter by course ECTS. Supports exact values and comparisons like 6, >6, >=6, <6, <=6.",
            },
            ects_operator: {
              type: "string",
              enum: ["eq", "lt", "lte", "gt", "gte"],
            },
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
        ects_operator,
        faculty_id,
        faculty_name,
        domain_id,
        domain_name,
        language,
        semester,
        name_contains,
        mobility,
        soft_skills,
        section_contains,
        limit,
      } = (req.query as ProgramCoursesQuery) ?? {};

      const courseEctsFilter = parseNumericFilter(ects, ects_operator);

      const rows = await query(
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
          AND (
            $4::float IS NULL
            OR CASE $16::text
              WHEN 'lt' THEN c.ects < $4
              WHEN 'lte' THEN c.ects <= $4
              WHEN 'gt' THEN c.ects > $4
              WHEN 'gte' THEN c.ects >= $4
              ELSE c.ects = $4
            END
          )
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
          AND ($14::text IS NULL OR co.description ILIKE $14)
        ORDER BY c.code
        LIMIT COALESCE($15::int, 100)
        `,
        [
          toInt(id),
          course_type ?? null,
          semester_type ?? null,
          courseEctsFilter.value,
          toInt(faculty_id),
          faculty_name ?? null,
          toInt(domain_id),
          domain_name ?? null,
          name_contains ?? null,
          toBoolean(mobility),
          toBoolean(soft_skills),
          semester ? `%${String(semester).trim()}%` : null,
          language ? `%${String(language).trim()}%` : null,
          section_contains ? `%${String(section_contains).trim()}%` : null,
          toInt(limit) ?? 100,
          courseEctsFilter.operator,
        ]
      );

      const program_documents = await getProgramDocuments([toInt(id) ?? -1]);

      return {
        program_documents: program_documents[0]?.program_documents ?? [],
        courses: rows,
      };
    }
  );


  // ============================
  // GET /programs/:id/course-sections
  // ============================
  app.get(
    "/:id/course-sections",
    {
      schema: {
        summary: "Get course-section descriptions for courses in a program",
        description:
          "Returns consist_of.description for each program-course row, exposed as section_heading for chatbot compatibility. This metadata can contain useful study-year hints, but may also contain noisy source headings.",
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
            code: { type: "string" },
            course_type: {
              type: "string",
              enum: ["Mandatory", "Elective"],
            },
            section_contains: {
              type: "string",
              description: "Substring match on consist_of.description / returned section_heading.",
            },
            non_empty_only: {
              type: "boolean",
              description: "When true, only rows with a non-empty consist_of.description value are returned.",
            },
            limit: { type: "integer", minimum: 1, maximum: 500 },
          },
        },
      },
    },
    async (req) => {
      const { id } = req.params as { id: string };
      const { code, course_type, section_contains, non_empty_only, limit } =
        (req.query as {
          code?: string;
          course_type?: "Mandatory" | "Elective";
          section_contains?: string;
          non_empty_only?: string | boolean;
          limit?: string;
        }) ?? {};

      const columnRows = await query<{ column_name: string }>(
        `
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'consist_of'
          AND column_name = ANY($1::text[])
        `,
        [["description"]]
      );

      const sectionColumn = ["description"].find((candidate) =>
        columnRows.some((row) => row.column_name === candidate)
      );

      if (!sectionColumn) {
        return [];
      }

      const sectionExpression = `co.${sectionColumn}`;

      const rows = await query(
        `
        SELECT
          co.program_id,
          co.code,
          COALESCE(co.course_name, c.name) AS course_name,
          co.course_type,
          ${sectionExpression} AS section_heading,
          c.ects,
          c.name AS canonical_course_name
        FROM consist_of co
        LEFT JOIN Course c
          ON c.code = co.code
        WHERE co.program_id = $1
          AND ($2::text IS NULL OR co.code = $2)
          AND ($3::text IS NULL OR co.course_type = $3)
          AND (
            $4::boolean IS NULL
            OR $4 = false
            OR NULLIF(BTRIM(${sectionExpression}), '') IS NOT NULL
          )
          AND ($5::text IS NULL OR ${sectionExpression} ILIKE $5)
        ORDER BY ${sectionExpression} NULLS LAST, co.course_type, co.code
        LIMIT COALESCE($6::int, 100)
        `,
        [
          toInt(id),
          code ?? null,
          course_type ?? null,
          toBoolean(non_empty_only),
          section_contains ? `%${String(section_contains).trim()}%` : null,
          toInt(limit) ?? 100,
        ]
      );

      const program_documents = await getProgramDocuments([toInt(id) ?? -1]);

      return {
        program_documents: program_documents[0]?.program_documents ?? [],
        course_sections: rows,
      };
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
          f.name_en AS faculty_name,
          COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
              'doc_id', d.doc_id,
              'label', d.label,
              'url', d.url,
              'doc_type', d.doc_type,
              'fetched_at', d.fetched_at
            ) ORDER BY d.fetched_at DESC NULLS LAST, d.doc_id DESC)
            FROM programDocument d
            WHERE d.program_id = p.program_id
          ), '[]'::jsonb) AS program_documents
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