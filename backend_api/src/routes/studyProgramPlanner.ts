import type { FastifyInstance } from "fastify";
import { query } from "../db.js";

type StudyProgramPlanQuery = {
  program_id?: string | number;
  semesters?: string | number;
  locale?: "de" | "en" | "fr";
};

type ProgramPlanRow = {
  program_id: number;
  display_name: string | null;
  degree_level: string | null;
  total_ects: number | null;
  study_start: string | null;
  faculty_name: string | null;
};

type PlanCourseRow = {
  code: string;
  course_name: string | null;
  ects: number | null;
  course_type: "Mandatory" | "Elective";
  domain_name: string | null;
  mobility: boolean | null;
  soft_skills: boolean | null;
  outside_domain: boolean | null;
  benefri: boolean | null;
  unipop: boolean | null;
  offering_id: number | null;
  sem_id: string | null;
  semester_type: string | null;
  semester_year: number | null;
  day_time_info: string | null;
  link_course_catalogue: string | null;
  teaching_languages: string[] | null;
};

function toInt(value: unknown): number | null {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : null;
}

function normalizeLocale(locale: unknown): "de" | "en" | "fr" {
  const normalized = String(locale ?? "en").toLowerCase();
  if (normalized === "de" || normalized === "fr") return normalized;
  return "en";
}

function localizedProgramNameSql(locale: "de" | "en" | "fr") {
  if (locale === "de") {
    return `COALESCE(NULLIF(p.name_de, ''), NULLIF(p.name_en, ''), NULLIF(p.name_fr, ''), p.name)`;
  }
  if (locale === "fr") {
    return `COALESCE(NULLIF(p.name_fr, ''), NULLIF(p.name_en, ''), NULLIF(p.name_de, ''), p.name)`;
  }
  return `COALESCE(NULLIF(p.name_en, ''), NULLIF(p.name_de, ''), NULLIF(p.name_fr, ''), p.name)`;
}

function normalizeForEquivalence(value: string | null): string {
  return String(value ?? "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/\b(deutsch|german|allemand|francais|franzoesisch|french|englisch|english|anglais)\b/g, "")
    .replace(/\b(de|fr|en)\b/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function languageBucket(languages: string[] | null): string {
  return (languages ?? []).map((x) => x.toLowerCase()).sort().join("+");
}

function equivalenceKey(row: PlanCourseRow): string {
  // Primary heuristic: bilingual duplicates usually share the same cleaned title and ECTS.
  // Fallback to code to avoid accidentally merging unrelated courses.
  const normalizedName = normalizeForEquivalence(row.course_name);
  if (normalizedName.length >= 8) return `${normalizedName}|${row.ects ?? ""}`;
  return `${row.code}|${row.ects ?? ""}|${languageBucket(row.teaching_languages)}`;
}

function buildChoiceGroups(courses: PlanCourseRow[]) {
  const groups = new Map<string, PlanCourseRow[]>();
  for (const course of courses) {
    const key = equivalenceKey(course);
    groups.set(key, [...(groups.get(key) ?? []), course]);
  }

  return [...groups.entries()].map(([key, options]) => ({
    key,
    required_ects: options[0]?.ects ?? null,
    requires_choice: options.length > 1,
    reason: options.length > 1 ? "Possible language/equivalent-course choice" : null,
    options,
  }));
}

function semesterTypeForSlot(index: number, studyStart: string | null): "Autumn" | "Spring" {
  const startsInSpring = String(studyStart ?? "").toLowerCase() === "spring";
  if (startsInSpring) return index % 2 === 0 ? "Spring" : "Autumn";
  return index % 2 === 0 ? "Autumn" : "Spring";
}

function planMandatoryGroups(groups: ReturnType<typeof buildChoiceGroups>, semesters: number, studyStart: string | null, totalEcts: number | null) {
  const slots = Array.from({ length: semesters }, (_, i) => ({
    semester_number: i + 1,
    semester_type: semesterTypeForSlot(i, studyStart),
    target_ects: totalEcts && semesters > 0 ? Math.round((totalEcts / semesters) * 10) / 10 : null,
    planned_ects: 0,
    mandatory: [] as unknown[],
  }));

  const sorted = [...groups].sort((a, b) => Number(b.required_ects ?? 0) - Number(a.required_ects ?? 0));

  for (const group of sorted) {
    const offeredTypes = new Set(group.options.map((o) => o.semester_type).filter(Boolean));
    const eligible = slots.filter((slot) => offeredTypes.size === 0 || offeredTypes.has(slot.semester_type));
    const targetSlots = eligible.length ? eligible : slots;
    const chosen = targetSlots.sort((a, b) => a.planned_ects - b.planned_ects)[0];

    if (!chosen) {
      continue;
    }
    
    chosen.mandatory.push(group);
    chosen.planned_ects += Number(group.required_ects ?? 0);
  }

  return slots;
}

export async function studyProgramPlannerRoutes(app: FastifyInstance) {
  app.get(
    "/study-program-plan",
    {
      schema: {
        tags: ["Planner"],
        summary: "Build a whole-study-program planning context",
        description: "Returns program totals, mandatory/elective split, language-choice groups, latest known offerings by semester type, and a rough mandatory distribution over the requested number of semesters.",
        querystring: {
          type: "object",
          required: ["program_id", "semesters"],
          properties: {
            program_id: { type: "integer" },
            semesters: { type: "integer", minimum: 1, maximum: 16 },
            locale: { type: "string", enum: ["de", "en", "fr"] },
          },
        },
        response: {
          200: { type: "object" },
          400: { type: "object", properties: { error: { type: "string" } } },
          404: { type: "object", properties: { error: { type: "string" } } },
        },
      },
    },
    async (req, rep) => {
      const { program_id, semesters, locale } = (req.query as StudyProgramPlanQuery) ?? {};
      const programId = toInt(program_id);
      const semesterCount = toInt(semesters);
      const resolvedLocale = normalizeLocale(locale);
      const nameExpr = localizedProgramNameSql(resolvedLocale);

      if (!programId || !semesterCount) {
        return rep.code(400).send({ error: "program_id and semesters are required" });
      }

      const programRows = await query<ProgramPlanRow>(
        `
        SELECT
          p.program_id,
          ${nameExpr} AS display_name,
          p.degree_level,
          p.total_ects,
          p.study_start,
          f.name_en AS faculty_name
        FROM StudyProgram p
        LEFT JOIN Faculty f ON f.faculty_id = p.faculty_id
        WHERE p.program_id = $1
        `,
        [programId]
      );

      const program = programRows[0];
      if (!program) return rep.code(404).send({ error: "Program not found" });

      const courses = await query<PlanCourseRow>(
        `
        WITH latest_offering AS (
          SELECT DISTINCT ON (off.code, s.type)
            off.offering_id,
            off.code,
            off.sem_id,
            s.type AS semester_type,
            s.year AS semester_year,
            off.day_time_info,
            off.link_course_catalogue
          FROM CourseOffering off
          LEFT JOIN Semester s ON s.sem_id = off.sem_id
          ORDER BY off.code, s.type, s.year DESC NULLS LAST, off.sem_id DESC, off.offering_id DESC
        )
        SELECT
          c.code,
          c.name AS course_name,
          c.ects,
          co.course_type,
          d.name AS domain_name,
          c.mobility,
          c.soft_skills,
          c.outside_domain,
          c.benefri,
          c.unipop,
          lo.offering_id,
          lo.sem_id,
          lo.semester_type,
          lo.semester_year,
          lo.day_time_info,
          lo.link_course_catalogue,
          COALESCE(
            ARRAY_AGG(DISTINCT l.description) FILTER (WHERE l.description IS NOT NULL),
            ARRAY[]::text[]
          ) AS teaching_languages
        FROM consist_of co
        JOIN Course c ON c.code = co.code
        LEFT JOIN Domain d ON d.domain_id = c.domain_id
        LEFT JOIN latest_offering lo ON lo.code = c.code
        LEFT JOIN is_taught_in iti ON iti.offering_id = lo.offering_id
        LEFT JOIN Language l ON l.lang_id = iti.lang_id
        WHERE co.program_id = $1
        GROUP BY
          c.code, c.name, c.ects, co.course_type, d.name,
          c.mobility, c.soft_skills, c.outside_domain, c.benefri, c.unipop,
          lo.offering_id, lo.sem_id, lo.semester_type, lo.semester_year,
          lo.day_time_info, lo.link_course_catalogue
        ORDER BY co.course_type, c.name ASC NULLS LAST, c.code, lo.semester_type
        `,
        [programId]
      );

      const mandatory = courses.filter((c) => c.course_type === "Mandatory");
      const electives = courses.filter((c) => c.course_type === "Elective");
      const mandatoryGroups = buildChoiceGroups(mandatory);
      const mandatoryChoiceEcts = mandatoryGroups.reduce((sum, g) => sum + Number(g.required_ects ?? 0), 0);
      const mandatoryNaiveEcts = mandatory.reduce((sum, c) => sum + Number(c.ects ?? 0), 0);
      const totalEcts = program.total_ects === null ? null : Number(program.total_ects);
      const electiveEctsRequired = totalEcts === null ? null : Math.max(0, totalEcts - mandatoryChoiceEcts);

      return {
        program,
        requested_semesters: semesterCount,
        assumptions: [
          "Future offering dates are not known. For each course, the latest known offering per semester type is reused as a planning proxy.",
          "Courses with very similar names, equal ECTS, and different teaching languages are grouped as choices; the student should choose one option.",
          "The generated semester distribution is a first draft and should be checked against official prerequisites and timetable changes.",
        ],
        totals: {
          total_ects: totalEcts,
          mandatory_ects_counting_all_rows: mandatoryNaiveEcts,
          mandatory_ects_after_language_choices: mandatoryChoiceEcts,
          elective_ects_required: electiveEctsRequired,
          elective_ects_available: electives.reduce((sum, c) => sum + Number(c.ects ?? 0), 0),
        },
        mandatory_choice_groups: mandatoryGroups,
        elective_courses: electives,
        suggested_mandatory_semester_plan: planMandatoryGroups(mandatoryGroups, semesterCount, program.study_start, totalEcts),
      };
    }
  );
}
