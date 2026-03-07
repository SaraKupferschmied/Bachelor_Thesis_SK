//500 error

import type { FastifyInstance } from "fastify";
import { z } from "zod";
import { query } from "../db.js";

const PlannerContextBody = z.object({
  program_id: z.number().int(),
  sem_id: z.string().min(1),

  // optional filters (server-side filtering is still helpful)
  include_types: z.array(z.enum(["Mandatory", "Elective"])).default(["Mandatory", "Elective"]),
  include_flags: z.object({
    mobility: z.boolean().optional(),
    soft_skills: z.boolean().optional(),
    outside_domain: z.boolean().optional(),
    benefri: z.boolean().optional(),
    unipop: z.boolean().optional(),
  }).default({}),
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

export async function plannerRoutes(app: FastifyInstance) {
  // POST /planner/context
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

      // 1) program
      const programRows = await query<ProgramRow>(
        `SELECT program_id, name, degree_level, total_ects, faculty_id, study_start
         FROM StudyProgram
         WHERE program_id = $1`,
        [b.program_id]
      );
      if (programRows.length === 0) return rep.code(404).send({ error: "Program not found" });
      const program = programRows[0];

      // 2) requirements: consist_of
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

      // 3) courses (master data) with optional flag filters
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

      // 4) offerings in this semester for those codes
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

      // 5) sessions (mostly for block offerings)
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