import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { query } from "../db.js";

const PlannerContextBody = z.object({
  program_id: z.number().int(),
  sem_id: z.string().min(1),

  include_types: z
    .array(z.enum(["Mandatory", "Elective"]))
    .default(["Mandatory", "Elective"]),
  include_flags: z
    .object({
      mobility: z.boolean().optional(),
      soft_skills: z.boolean().optional(),
      outside_domain: z.boolean().optional(),
      benefri: z.boolean().optional(),
      unipop: z.boolean().optional(),
    })
    .default({}),
});

type ProgramRow = {
  program_id: number;
  name: string | null;
  degree_level: string | null;
  total_ects: number | null;
  faculty_id: number | null;
  study_start: string | null;
};

type RequirementRow = {
  program_id: number;
  code: string;
  course_type: "Mandatory" | "Elective";
};

type CourseRow = {
  code: string;
  name: string | null;
  ects: number | null;
  faculty_id: number | null;
  domain_id: number | null;
  mobility: boolean | null;
  soft_skills: boolean | null;
  outside_domain: boolean | null;
  benefri: boolean | null;
  unipop: boolean | null;
};

type OfferingRow = {
  offering_id: number;
  code: string;
  sem_id: string;
  offering_type: string | null;
  day_time_info: string | null;
  link_course_catalogue: string | null;
};

type SessionRow = {
  offering_id: number;
  date: string;
  start_time: string | null;
  end_time: string | null;
  room_id: string | null;
  unit_type: string | null;
};

type OfferingProfessorRow = {
  prof_id: number;
  title: string | null;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
};

type OfferingLanguageRow = {
  description: string;
};

type OfferingDetailRow = {
  offering_id: number;
  sem_id: string;
  offering_type: string | null;
  day_time_info: string | null;
  link_course_catalogue: string | null;
  code: string;
  course_name: string | null;
  ects: number | null;
};

type OfferingSessionDetailRow = {
  session_id: number;
  offering_id: number;
  date: string;
  start_time: string | null;
  end_time: string | null;
  room_id: string | null;
  unit_type: string | null;
};

type PlannerProgramsQuery = {
  locale?: "de" | "en" | "fr";
  degree_level?: "Bachelor" | "Master" | "Doctorate";
  q?: string;
  limit?: string | number;
};

type PlannerCoursesQuery = {
  sem_id?: string;
  locale?: "de" | "en" | "fr";
  program_ids?: string | string[];
};

function toInt(value: unknown): number | null {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : null;
}

function parseProgramIds(value: string | string[] | undefined): number[] {
  if (!value) return [];

  const rawParts = Array.isArray(value) ? value : [value];

  const ids = rawParts
    .flatMap((part) => String(part).split(","))
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => Number(part))
    .filter((part) => Number.isInteger(part));

  return [...new Set(ids)];
}

function normalizeLocale(locale: unknown): "de" | "en" | "fr" {
  const normalized = String(locale ?? "de").toLowerCase();
  if (normalized === "en" || normalized === "fr") return normalized;
  return "de";
}

function localizedProgramNameSql(locale: "de" | "en" | "fr") {
  if (locale === "en") {
    return `COALESCE(NULLIF(p.name_en, ''), NULLIF(p.name_de, ''), NULLIF(p.name_fr, ''), p.name)`;
  }
  if (locale === "fr") {
    return `COALESCE(NULLIF(p.name_fr, ''), NULLIF(p.name_de, ''), NULLIF(p.name_en, ''), p.name)`;
  }
  return `COALESCE(NULLIF(p.name_de, ''), NULLIF(p.name_en, ''), NULLIF(p.name_fr, ''), p.name)`;
}

export async function plannerRoutes(app: FastifyInstance) {
  app.get(
    "/semesters",
    {
      schema: {
        tags: ["Planner"],
        summary: "List semesters for planner dropdown",
        response: {
          200: {
            type: "array",
            items: {
              type: "object",
              properties: {
                sem_id: { type: "string" },
                year: { type: "integer" },
                type: { type: "string" },
                label: { type: "string" },
              },
            },
          },
        },
      },
    },
    async () => {
      return query(
        `
        SELECT
          s.sem_id,
          s.year,
          s.type,
          CASE
            WHEN s.type = 'Autumn' THEN 'HS '
            WHEN s.type = 'Spring' THEN 'FS '
            ELSE ''
          END || s.year::text AS label
        FROM Semester s
        ORDER BY s.year DESC,
                 CASE WHEN s.type = 'Autumn' THEN 0 ELSE 1 END,
                 s.sem_id DESC
        `
      );
    }
  );

  app.get(
    "/programs",
    {
      schema: {
        tags: ["Planner"],
        summary: "List study programs for planner dropdown",
        querystring: {
          type: "object",
          properties: {
            locale: { type: "string", enum: ["de", "en", "fr"] },
            degree_level: {
              type: "string",
              enum: ["Bachelor", "Master", "Doctorate"],
            },
            q: { type: "string" },
            limit: { type: "integer", minimum: 1, maximum: 500 },
          },
        },
        response: {
          200: {
            type: "array",
            items: {
              type: "object",
              properties: {
                program_id: { type: "integer" },
                degree_level: { type: ["string", "null"] },
                total_ects: { type: ["number", "null"] },
                study_start: { type: ["string", "null"] },
                faculty_id: { type: ["integer", "null"] },
                faculty_name: { type: ["string", "null"] },
                display_name: { type: ["string", "null"] },
                name_de: { type: ["string", "null"] },
                name_en: { type: ["string", "null"] },
                name_fr: { type: ["string", "null"] },
                languages: {
                  type: "array",
                  items: { type: "string" },
                },
              },
            },
          },
        },
      },
    },
    async (req) => {
      const { locale, degree_level, q, limit } =
        (req.query as PlannerProgramsQuery) ?? {};
      const resolvedLocale = normalizeLocale(locale);
      const nameExpr = localizedProgramNameSql(resolvedLocale);

      return query(
        `
        SELECT
          p.program_id,
          p.degree_level,
          p.total_ects,
          p.study_start,
          p.faculty_id,
          f.name_en AS faculty_name,
          ${nameExpr} AS display_name,
          p.name_de,
          p.name_en,
          p.name_fr,
          COALESCE(
            ARRAY_AGG(DISTINCT l.description) FILTER (WHERE l.description IS NOT NULL),
            ARRAY[]::text[]
          ) AS languages
        FROM StudyProgram p
        LEFT JOIN Faculty f
          ON f.faculty_id = p.faculty_id
        LEFT JOIN has_lang hl
          ON hl.program_id = p.program_id
        LEFT JOIN Language l
          ON l.lang_id = hl.lang_id
        WHERE ($1::text IS NULL OR p.degree_level = $1)
          AND (
            $2::text IS NULL
            OR p.name ILIKE '%' || $2 || '%'
            OR p.name_de ILIKE '%' || $2 || '%'
            OR p.name_en ILIKE '%' || $2 || '%'
            OR p.name_fr ILIKE '%' || $2 || '%'
          )
        GROUP BY
          p.program_id,
          p.degree_level,
          p.total_ects,
          p.study_start,
          p.faculty_id,
          f.name_en,
          p.name,
          p.name_de,
          p.name_en,
          p.name_fr
        ORDER BY display_name ASC NULLS LAST, p.degree_level, p.total_ects, p.program_id
        LIMIT COALESCE($3::int, 200)
        `,
        [degree_level ?? null, q ?? null, toInt(limit) ?? 200]
      );
    }
  );

  app.get(
    "/courses",
    {
      schema: {
        tags: ["Planner"],
        summary:
          "List available course offerings for selected programs in one semester",
        querystring: {
          type: "object",
          required: ["sem_id", "program_ids"],
          properties: {
            sem_id: { type: "string" },
            locale: { type: "string", enum: ["de", "en", "fr"] },
            program_ids: {
              anyOf: [
                { type: "string", description: "Comma-separated ids, e.g. 1,2,3" },
                {
                  type: "array",
                  items: { type: "string" },
                  description: "Repeated query parameter, e.g. ?program_ids=1&program_ids=2",
                },
              ],
            },
          },
        },
        response: {
          200: {
            type: "object",
            properties: {
              semester: {
                type: "object",
                properties: {
                  sem_id: { type: "string" },
                  year: { type: "integer" },
                  type: { type: "string" },
                },
              },
              selected_programs: {
                type: "array",
                items: {
                  type: "object",
                  properties: {
                    program_id: { type: "integer" },
                    display_name: { type: ["string", "null"] },
                    degree_level: { type: ["string", "null"] },
                    total_ects: { type: ["number", "null"] },
                  },
                },
              },
              courses: {
                type: "array",
                items: {
                  type: "object",
                  properties: {
                    offering_id: { type: "integer" },
                    sem_id: { type: "string" },
                    offering_type: { type: ["string", "null"] },
                    day_time_info: { type: ["string", "null"] },
                    link_course_catalogue: { type: ["string", "null"] },
                    code: { type: "string" },
                    course_name: { type: ["string", "null"] },
                    ects: { type: ["number", "null"] },
                    teaching_languages: {
                      type: "array",
                      items: { type: "string" },
                    },
                    mandatory_for: {
                      type: "array",
                      items: {
                        type: "object",
                        properties: {
                          program_id: { type: "integer" },
                          program_name: { type: ["string", "null"] },
                        },
                      },
                    },
                    elective_for: {
                      type: "array",
                      items: {
                        type: "object",
                        properties: {
                          program_id: { type: "integer" },
                          program_name: { type: ["string", "null"] },
                        },
                      },
                    },
                    programs: {
                      type: "array",
                      items: {
                        type: "object",
                        properties: {
                          program_id: { type: "integer" },
                          program_name: { type: ["string", "null"] },
                          course_type: { type: ["string", "null"] },
                        },
                      },
                    },
                  },
                },
              },
            },
          },
          400: {
            type: "object",
            properties: { error: { type: "string" } },
          },
        },
      },
    },
    async (req, rep) => {
      const { sem_id, locale, program_ids } =
        (req.query as PlannerCoursesQuery) ?? {};
      const resolvedLocale = normalizeLocale(locale);
      const ids = parseProgramIds(program_ids);

      if (!sem_id) {
        return rep.code(400).send({
          error: "sem_id query parameter is required, e.g. /planner/courses?sem_id=HS-2026&program_ids=1,2",
        });
      }

      if (ids.length === 0) {
        return rep.code(400).send({
          error: "At least one program_id is required, e.g. /planner/courses?sem_id=HS-2026&program_ids=1,2",
        });
      }

      const nameExpr = localizedProgramNameSql(resolvedLocale);

      const semesterRows = await query<{
        sem_id: string;
        year: number;
        type: string;
      }>(
        `
        SELECT sem_id, year, type
        FROM Semester
        WHERE sem_id = $1
        `,
        [sem_id]
      );

      const selectedPrograms = await query<{
        program_id: number;
        display_name: string | null;
        degree_level: string | null;
        total_ects: number | null;
      }>(
        `
        SELECT
          p.program_id,
          ${nameExpr} AS display_name,
          p.degree_level,
          p.total_ects
        FROM StudyProgram p
        WHERE p.program_id = ANY($1::int[])
        ORDER BY display_name ASC NULLS LAST, p.program_id
        `,
        [ids]
      );

      const rows = await query<{
        offering_id: number;
        sem_id: string;
        offering_type: string | null;
        day_time_info: string | null;
        link_course_catalogue: string | null;
        code: string;
        course_name: string | null;
        ects: number | null;
        teaching_languages: string[] | null;
        mandatory_for: unknown;
        elective_for: unknown;
        programs: unknown;
      }>(
        `
        SELECT
          off.offering_id,
          off.sem_id,
          off.offering_type,
          off.day_time_info,
          off.link_course_catalogue,
          c.code,
          c.name AS course_name,
          c.ects,
          COALESCE(
            ARRAY_AGG(DISTINCT l.description) FILTER (WHERE l.description IS NOT NULL),
            ARRAY[]::text[]
          ) AS teaching_languages,
          COALESCE(
            JSONB_AGG(DISTINCT JSONB_BUILD_OBJECT(
              'program_id', p.program_id,
              'program_name', ${nameExpr}
            )) FILTER (WHERE co.course_type = 'Mandatory'),
            '[]'::jsonb
          ) AS mandatory_for,
          COALESCE(
            JSONB_AGG(DISTINCT JSONB_BUILD_OBJECT(
              'program_id', p.program_id,
              'program_name', ${nameExpr}
            )) FILTER (WHERE co.course_type = 'Elective'),
            '[]'::jsonb
          ) AS elective_for,
          COALESCE(
            JSONB_AGG(DISTINCT JSONB_BUILD_OBJECT(
              'program_id', p.program_id,
              'program_name', ${nameExpr},
              'course_type', co.course_type
            )),
            '[]'::jsonb
          ) AS programs
        FROM CourseOffering off
        JOIN Course c
          ON c.code = off.code
        JOIN consist_of co
          ON co.code = c.code
        JOIN StudyProgram p
          ON p.program_id = co.program_id
        LEFT JOIN is_taught_in iti
          ON iti.offering_id = off.offering_id
        LEFT JOIN Language l
          ON l.lang_id = iti.lang_id
        WHERE off.sem_id = $1
          AND p.program_id = ANY($2::int[])
        GROUP BY
          off.offering_id,
          off.sem_id,
          off.offering_type,
          off.day_time_info,
          off.link_course_catalogue,
          c.code,
          c.name,
          c.ects
        ORDER BY c.name ASC NULLS LAST, c.code, off.offering_id
        `,
        [sem_id, ids]
      );

      return {
        semester: semesterRows[0] ?? null,
        selected_programs: selectedPrograms,
        courses: rows,
      };
    }
  );

  app.get(
    "/offerings/:offeringId",
    {
      schema: {
        tags: ["Planner"],
        summary: "Get full offering details including sessions and professors",
        params: {
          type: "object",
          required: ["offeringId"],
          properties: {
            offeringId: { type: "integer" }
          }
        },
        response: {
          200: {
            type: "object",
            properties: {
              offering_id: { type: "integer" },
              sem_id: { type: "string" },
              offering_type: { type: ["string", "null"] },
              day_time_info: { type: ["string", "null"] },
              link_course_catalogue: { type: ["string", "null"] },
              code: { type: "string" },
              course_name: { type: ["string", "null"] },
              ects: { type: ["number", "null"] },
              teaching_languages: {
                type: "array",
                items: { type: "string" }
              },
              professors: {
                type: "array",
                items: {
                  type: "object",
                  properties: {
                    prof_id: { type: "integer" },
                    display_name: { type: "string" },
                    email: { type: ["string", "null"] }
                  }
                }
              },
              sessions: {
                type: "array",
                items: {
                  type: "object",
                  properties: {
                    session_id: { type: "integer" },
                    offering_id: { type: "integer" },
                    date: { type: "string" },
                    weekday: { type: "string" },
                    start_time: { type: ["string", "null"] },
                    end_time: { type: ["string", "null"] },
                    room_id: { type: ["string", "null"] },
                    unit_type: { type: ["string", "null"] }
                  }
                }
              }
            }
          },
          404: {
            type: "object",
            properties: {
              error: { type: "string" }
            }
          }
        }
      }
    },
    async (req, rep) => {
      const offeringId = Number((req.params as { offeringId: string }).offeringId);

      if (!Number.isInteger(offeringId)) {
        return rep.code(404).send({ error: "Offering not found" });
      }

      const offeringRows = await query<OfferingDetailRow>(
        `
        SELECT
          off.offering_id,
          off.sem_id,
          off.offering_type,
          off.day_time_info,
          off.link_course_catalogue,
          c.code,
          c.name AS course_name,
          c.ects
        FROM CourseOffering off
        JOIN Course c
          ON c.code = off.code
        WHERE off.offering_id = $1
        `,
        [offeringId]
      );

      const offering = offeringRows[0];
      if (!offering) {
        return rep.code(404).send({ error: "Offering not found" });
      }

      const languages = await query<OfferingLanguageRow>(
        `
        SELECT DISTINCT l.description
        FROM is_taught_in iti
        JOIN Language l
          ON l.lang_id = iti.lang_id
        WHERE iti.offering_id = $1
        ORDER BY l.description
        `,
        [offeringId]
      );

      const professors = await query<OfferingProfessorRow>(
        `
        SELECT DISTINCT
          p.prof_id,
          p.title,
          p.first_name,
          p.last_name,
          p.email
        FROM CourseOffering off
        JOIN teaches t
          ON t.code = off.code
        JOIN Professor p
          ON p.prof_id = t.prof_id
        WHERE off.offering_id = $1
        ORDER BY p.last_name, p.first_name
        `,
        [offeringId]
      );

      const sessions = await query<OfferingSessionDetailRow>(
        `
        SELECT
          s.session_id,
          s.offering_id,
          s.date::text AS date,
          s.start_time::text AS start_time,
          s.end_time::text AS end_time,
          s.room_id,
          s.unit_type
        FROM Session s
        WHERE s.offering_id = $1
        ORDER BY s.date, s.start_time, s.session_id
        `,
        [offeringId]
      );

      return {
        ...offering,
        teaching_languages: languages.map((row) => row.description),
        professors: professors.map((prof) => ({
          prof_id: prof.prof_id,
          display_name: [prof.title, prof.first_name, prof.last_name].filter(Boolean).join(" "),
          email: prof.email
        })),
        sessions: sessions.map((session) => ({
          ...session,
          weekday: new Date(session.date).toLocaleDateString("de-CH", { weekday: "long" })
        }))
      };
    }
  );

  app.post(
    "/context",
    {
      schema: {
        tags: ["Planner"],
        summary: "Get DB-backed context for semester planning",
        description:
          "Returns program info, program requirements, course master data, offerings for the given semester, and sessions (for block-time data).",
        body: {
          type: "object",
          required: ["program_id", "sem_id"],
          properties: {
            program_id: { type: "integer" },
            sem_id: { type: "string" },
            include_types: {
              type: "array",
              items: { type: "string", enum: ["Mandatory", "Elective"] },
              default: ["Mandatory", "Elective"],
            },
            include_flags: {
              type: "object",
              properties: {
                mobility: { type: "boolean" },
                soft_skills: { type: "boolean" },
                outside_domain: { type: "boolean" },
                benefri: { type: "boolean" },
                unipop: { type: "boolean" },
              },
              additionalProperties: false,
              default: {},
            },
          },
          additionalProperties: false,
        },
        response: {
          200: { type: "object" },
          404: {
            type: "object",
            properties: { error: { type: "string" } },
          },
        },
      },
    },
    async (req, rep) => {
      const b = PlannerContextBody.parse(req.body);

      const programRows = await query<ProgramRow>(
        `SELECT program_id, name, degree_level, total_ects, faculty_id, study_start
         FROM StudyProgram
         WHERE program_id = $1`,
        [b.program_id]
      );
      if (programRows.length === 0) {
        return rep.code(404).send({ error: "Program not found" });
      }
      const program = programRows[0];

      const reqs = await query<RequirementRow>(
        `SELECT program_id, code, course_type
         FROM consist_of
         WHERE program_id = $1
           AND course_type = ANY($2::text[])
         ORDER BY course_type, code`,
        [b.program_id, b.include_types]
      );

      const codes = reqs.map((r) => r.code);
      if (codes.length === 0) {
        return {
          program,
          semester: b.sem_id,
          requirements: [],
          courses: [],
          offerings: [],
          sessions: [],
        };
      }

      const f = b.include_flags;
      const courses = await query<CourseRow>(
        `
        SELECT code, name, ects, faculty_id, domain_id,
               mobility, soft_skills, outside_domain, benefri, unipop
        FROM Course
        WHERE code = ANY($1::text[])
          AND ($2::boolean IS NULL OR mobility = $2)
          AND ($3::boolean IS NULL OR soft_skills = $3)
          AND ($4::boolean IS NULL OR outside_domain = $4)
          AND ($5::boolean IS NULL OR benefri = $5)
          AND ($6::boolean IS NULL OR unipop = $6)
        ORDER BY code
        `,
        [
          codes,
          f.mobility ?? null,
          f.soft_skills ?? null,
          f.outside_domain ?? null,
          f.benefri ?? null,
          f.unipop ?? null,
        ]
      );

      const filteredCodes = courses.map((c) => c.code);

      const offerings = await query<OfferingRow>(
        `
        SELECT offering_id, code, sem_id, offering_type, day_time_info, link_course_catalogue
        FROM CourseOffering
        WHERE sem_id = $1
          AND code = ANY($2::text[])
        ORDER BY code, offering_id
        `,
        [b.sem_id, filteredCodes]
      );

      const offeringIds = offerings.map((o) => o.offering_id);

      const sessions = offeringIds.length
        ? await query<SessionRow>(
            `
            SELECT offering_id,
                   date::text AS date,
                   start_time::text AS start_time,
                   end_time::text AS end_time,
                   room_id,
                   unit_type
            FROM Session
            WHERE offering_id = ANY($1::int[])
            ORDER BY date, start_time
            `,
            [offeringIds]
          )
        : [];

      const filteredReqs = reqs.filter((r) => filteredCodes.includes(r.code));

      return {
        program,
        semester: b.sem_id,
        requirements: filteredReqs,
        courses,
        offerings,
        sessions,
      };
    }
  );
}
