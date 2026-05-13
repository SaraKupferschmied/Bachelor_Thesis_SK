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
  description: string | null;
  learning_goals: string | null;
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

function normalizeCourseCode(value: unknown): string {
  return String(value ?? "")
    .toUpperCase()
    .replace(/^UE[-\s]?/, "")
    .replace(/[^A-Z0-9]/g, "");
}



type StudyProgramPlanQuery = {
  program_id?: string | number;
  semesters?: string | number;
  locale?: "de" | "en" | "fr";
  total_ects?: string | number;
  selected_elective_codes?: string;
};

type ProgramPlanCourseRow = {
  row_no: number;
  program_id: number;
  code: string;
  course_name: string | null;
  canonical_course_name: string | null;
  course_type: "Mandatory" | "Elective";
  program_course_description: string | null;
  ects: number | null;
  offered_semester_types: string[] | null;
  latest_sem_id: string | null;
  latest_day_time_info: string | null;
  teaching_languages: string[] | null;
};

type PlanCourse = {
  row_no: number;
  code: string;
  course_name: string | null;
  ects: number | null;
  course_type: "Mandatory" | "Elective";
  program_course_description: string | null;
  suggested_year: number | null;
  semester_types: string[];
  semester_type: string | null;
  sem_id: string | null;
  day_time_info: string | null;
  teaching_languages: string[];
};

type PlanGroup = {
  group_key: string;
  requires_choice: boolean;
  planned_ects: number;
  suggested_year: number | null;
  semester_types: string[];
  sequence: number;
  sequence_series_key: string;
  sequence_part: number | null;
  source_order: number;
  options: PlanCourse[];
};

function parseStudyYear(text: string | null): number | null {
  const t = String(text ?? "").toLowerCase();
  if (/\b(1\.|1st|first|erstes|premi[eè]re?)\s+(study\s+)?(year|studienjahr|jahr|ann[ée]e)/i.test(t)) return 1;
  if (/\b(2\.|2nd|second|zweites|deuxi[eè]me)\s+(study\s+)?(year|studienjahr|jahr|ann[ée]e)/i.test(t)) return 2;
  if (/\b(3\.|3rd|third|drittes|troisi[eè]me)\s+(study\s+)?(year|studienjahr|jahr|ann[ée]e)/i.test(t)) return 3;
  if (/\b1\.?\s*jahr\b|\b1\.?\s*studienjahr\b/.test(t)) return 1;
  if (/\b2\.?\s*jahr\b|\b2\.?\s*studienjahr\b/.test(t)) return 2;
  if (/\b3\.?\s*jahr\b|\b3\.?\s*studienjahr\b/.test(t)) return 3;
  return null;
}

function normalizeText(value: string | null): string {
  return String(value ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/\([^)]*\)/g, " ")
    .replace(/\b(f|d|e|fr|de|en)\b/g, " ")
    .replace(/[^a-z0-9ivx]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function canonicalCourseKey(name: string | null, code: string, ects: number | null): string {
  let n = normalizeText(name);
  const man = n.match(/\bman\s*0?(\d{1,2})\b/);
  if (man) return `man-${Number(man[1])}-${ects ?? ""}`;

  const replacements: Array<[RegExp, string]> = [
    [/\bwirtschaftsinformatik I\b|\binformatique de gestion I\b/g, "business informatics I"],
    [/\bwirtschaftsinformatik II\b|\binformatique de gestion II\b/g, "business informatics II"],
    [/\beinfuehrung in die statistik\b|\beinfuhrung in die statistik\b|\bintroduction a la statistique\b/g, "statistics"],
    [/\bvertiefungskurs statistik\b|\bstatistique approfondissement\b/g, "advanced statistics"],
    [/\bmathematik\b|\bmathematiques\b/g, "mathematics"],
    [/\beinfuehrung in die betriebswirtschaftslehre\b|\bintroduction a la gestion d entreprise\b/g, "management"],
    [/\bunternehmensrechnung\b|\bintroduction a la comptabilite\b/g, "accounting"],
    [/\brecht\b|\bdroit\b/g, "law"],
    [/\bmikrookonomie\b|\bmicroeconomie\b/g, "microeconomics"],
    [/\bmarketingforschung\b|\brecherche marketing\b/g, "marketing research"],
    [/\bbilanzierung\b|\bcomptabilite financiere\b/g, "financial accounting"],
    [/\bcontrolling\b|\bcomptabilite de gestion\b/g, "management accounting"],
    [/\bunternehmensfinanzierung\b|\bfinance d entreprise\b/g, "corporate finance"],
    [/\borganisation\b/g, "organisation"],
  ];
  for (const [rx, repl] of replacements) n = n.replace(rx, repl);
  n = n.replace(/\s+/g, " ").trim();
  return `${n || code}-${ects ?? ""}`;
}

function romanOrNumberToInt(value: string | null): number | null {
  const n = normalizeText(value);
  if (!n) return null;
  if (n === "1" || n === "i") return 1;
  if (n === "2" || n === "ii") return 2;
  if (n === "3" || n === "iii") return 3;
  if (n === "4" || n === "iv") return 4;
  if (n === "5" || n === "v") return 5;
  return null;
}

function titleAfterPrefix(name: string | null): string {
  const raw = String(name ?? "");
  const colonIndex = raw.indexOf(":");
  return colonIndex >= 0 ? raw.slice(colonIndex + 1) : raw;
}

function sequenceHint(name: string | null): number {
  const full = normalizeText(name);
  const title = normalizeText(titleAfterPrefix(name));

  // Prefer explicit part markers in the actual course title after prefixes such
  // as "Info II:" or "MAN04D:". This avoids reading the prefix as the course
  // progression number.
  const titleRoman = title.match(/\b(i|ii|iii|iv|v|1|2|3|4|5)\b/);
  const titleSeq = romanOrNumberToInt(titleRoman?.[1] ?? null);
  if (titleSeq !== null) return titleSeq;

  const fullRoman = full.match(/\b(i|ii|iii|iv|v|1|2|3|4|5)\b/);
  const fullSeq = romanOrNumberToInt(fullRoman?.[1] ?? null);
  return fullSeq ?? 50;
}

function removeSequenceMarkers(value: string): string {
  return normalizeText(value)
    .replace(/\b(i|ii|iii|iv|v|1|2|3|4|5)\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function courseSeriesKey(name: string | null): string {
  const title = translatedNameKey(titleAfterPrefix(name));
  return removeSequenceMarkers(title);
}

function uniqStrings(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  return [...new Set(values.map(String).filter(Boolean))];
}

function toPlanCourse(row: ProgramPlanCourseRow): PlanCourse {
  const types = uniqStrings(row.offered_semester_types);
  return {
    row_no: row.row_no,
    code: row.code,
    course_name: row.course_name ?? row.canonical_course_name ?? row.code,
    ects: row.ects,
    course_type: row.course_type,
    program_course_description: row.program_course_description,
    suggested_year: parseStudyYear(row.program_course_description),
    semester_types: types,
    semester_type: types.length === 1 ? (types[0] ?? null) : types.length ? types.join("/") : null,
    sem_id: row.latest_sem_id,
    day_time_info: row.latest_day_time_info,
    teaching_languages: uniqStrings(row.teaching_languages),
  };
}

function sameCourseTypeAndEcts(a: PlanCourse, b: PlanCourse): boolean {
  return a.course_type === b.course_type && Number(a.ects ?? 0) === Number(b.ects ?? 0);
}

function sameSection(a: PlanCourse, b: PlanCourse): boolean {
  return normalizeText(a.program_course_description) === normalizeText(b.program_course_description);
}

function languageSet(course: PlanCourse): Set<string> {
  return new Set(course.teaching_languages.map((x) => normalizeText(x)));
}

function hasGerman(course: PlanCourse): boolean {
  const langs = languageSet(course);
  return langs.has("deutsch") || langs.has("german") || langs.has("allemand");
}

function hasFrench(course: PlanCourse): boolean {
  const langs = languageSet(course);
  return langs.has("franzosisch") || langs.has("franzoesisch") || langs.has("french") || langs.has("francais") || langs.has("francais");
}

function hasDifferentGermanFrenchLanguages(a: PlanCourse, b: PlanCourse): boolean {
  return (hasGerman(a) && hasFrench(b)) || (hasFrench(a) && hasGerman(b));
}

function compatibleSemesterTypes(a: PlanCourse, b: PlanCourse): boolean {
  if (!a.semester_types.length || !b.semester_types.length) return true;
  return a.semester_types.some((type) => b.semester_types.includes(type));
}

function translatedNameKey(value: string | null): string {
  let n = normalizeText(value)
    .replace(/\bman\s*0?(\d{1,2})\s*[def]?\b/g, "man $1")
    .replace(/\bue\s*[a-z]+\s*\d+\b/g, " ");

  const replacements: Array<[RegExp, string]> = [
    [/\bwirtschaftsinformatik\b|\binformatique de gestion\b/g, "business informatics"],
    [/\binformation systems?\b/g, "business informatics"],
    [/\beinfuehrung\b|\beinfuhrung\b|\bintroduction\b|\bintroductory\b/g, "introduction"],
    [/\bstatistique\b|\bstatistik\b|\bstatistics?\b/g, "statistics"],
    [/\bvertiefungskurs\b|\bapprofondissement\b|\badvanced\b/g, "advanced"],
    [/\bmathematiques?\b|\bmathematik\b|\bmathematics?\b/g, "mathematics"],
    [/\bbetriebswirtschaftslehre\b|\bgestion d entreprise\b|\bbusiness administration\b/g, "business administration"],
    [/\bunternehmensrechnung\b|\bcomptabilite\b|\baccounting\b/g, "accounting"],
    [/\bbilanzierung\b|\bcomptabilite financiere\b|\bfinancial accounting\b/g, "financial accounting"],
    [/\brecht\b|\bdroit\b|\blaw\b/g, "law"],
    [/\bmikrookonomie\b|\bmicroeconomie\b|\bmicroeconomics?\b/g, "microeconomics"],
    [/\binvestissement\b|\binvestitions\b|\binvestition\b|\binvestment\b/g, "investment"],
    [/\bfinancement\b|\bfinanzierung\b|\bfinancing\b|\bfinance\b/g, "financing"],
    [/\bmarketingforschung\b|\brecherche marketing\b|\bmarketing research\b/g, "marketing research"],
    [/\bressources humaines\b|\bgestion des ressources humaines\b|\bhuman resource management\b/g, "human resource management"],
    [/\bcontrolling\b|\bcomptabilite de gestion\b|\bmanagement accounting\b/g, "management accounting"],
    [/\bunternehmensfinanzierung\b|\bfinance d entreprise\b|\bcorporate finance\b/g, "corporate finance"],
    [/\borganisation\b|\borganization\b/g, "organisation"],
    [/\bkompetenz\b|\bcompetences\b|\bcompetence\b/g, "competence"],
    [/\bdocumentaires?\b|\bdocumentary\b/g, "documentary"],
    [/\binformationskompetenz\b/g, "information competence"],
    [/\bwirtschaftswissenschaften\b|\beconomie\b|\beconomics?\b/g, "economics"],
  ];
  for (const [rx, repl] of replacements) n = n.replace(rx, repl);
  return n.replace(/\s+/g, " ").trim();
}

function tokenSet(value: string): Set<string> {
  const stop = new Set(["in", "de", "des", "der", "die", "das", "a", "the", "and", "et", "en", "of", "for", "to", "course", "cours"]);
  return new Set(value.split(/\s+/).filter((x) => x && !stop.has(x)));
}

function jaccard(a: Set<string>, b: Set<string>): number {
  if (!a.size && !b.size) return 1;
  let intersection = 0;
  for (const item of a) if (b.has(item)) intersection += 1;
  return intersection / (a.size + b.size - intersection);
}

function namesLookEquivalent(a: PlanCourse, b: PlanCourse): boolean {
  const aKey = canonicalCourseKey(a.course_name, a.code, a.ects);
  const bKey = canonicalCourseKey(b.course_name, b.code, b.ects);
  if (aKey === bKey) return true;

  const an = translatedNameKey(a.course_name);
  const bn = translatedNameKey(b.course_name);
  if (an && bn && an === bn) return true;

  const score = jaccard(tokenSet(an), tokenSet(bn));
  return score >= 0.72;
}

function sameDirectlyRepeatedZeroEctsName(a: PlanCourse, b: PlanCourse): boolean {
  return Number(a.ects ?? 0) === 0 && normalizeText(a.course_name) === normalizeText(b.course_name);
}

function shouldGroupAsLanguageEquivalent(a: PlanCourse, b: PlanCourse): boolean {
  if (!sameCourseTypeAndEcts(a, b)) return false;
  if (!compatibleSemesterTypes(a, b)) return false;

  // Strong case: same normalized/translated name. This catches bilingual course
  // names even when the rows are not adjacent after SQL sorting.
  if (namesLookEquivalent(a, b)) return true;

  // Safe import-order fallback: the source table stores language alternatives
  // directly next to each other. Only use adjacency with all hard guards enabled:
  // same course type, same ECTS, compatible semester, German/French split, and
  // either same section or a very similar translated title. This prevents false
  // pairs such as Microeconomics + Algorithmics.
  if (Math.abs(a.row_no - b.row_no) === 1 && hasDifferentGermanFrenchLanguages(a, b)) {
    if (sameSection(a, b) && jaccard(tokenSet(translatedNameKey(a.course_name)), tokenSet(translatedNameKey(b.course_name))) >= 0.45) {
      return true;
    }
  }

  // Several 0-ECTS information-literacy rows appear as repeated technical rows.
  return sameDirectlyRepeatedZeroEctsName(a, b);
}

function groupCoursesByEquivalentKey(courses: PlanCourse[]): Map<string, PlanCourse[]> {
  const ordered = [...courses].sort((a, b) => a.row_no - b.row_no);
  const parent = ordered.map((_, i) => i);

  const find = (i: number): number => {
    const currentParent = parent[i];
    if (currentParent === undefined || currentParent === i) return i;

    const root = find(currentParent);
    parent[i] = root;
    return root;
  };
  const unite = (a: number, b: number) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent[rb] = ra;
  };

  for (let i = 0; i < ordered.length; i += 1) {
    for (let j = i + 1; j < ordered.length; j += 1) {
      if (!sameCourseTypeAndEcts(ordered[i]!, ordered[j]!)) continue;
      if (!compatibleSemesterTypes(ordered[i]!, ordered[j]!)) continue;

      const adjacent = Math.abs(ordered[i]!.row_no - ordered[j]!.row_no) === 1;
      const sameCanonical = canonicalCourseKey(ordered[i]!.course_name, ordered[i]!.code, ordered[i]!.ects) ===
        canonicalCourseKey(ordered[j]!.course_name, ordered[j]!.code, ordered[j]!.ects);

      // Keep comparisons cheap and conservative: compare all exact canonical
      // matches, and otherwise only likely language alternatives.
      if (sameCanonical || adjacent || hasDifferentGermanFrenchLanguages(ordered[i]!, ordered[j]!)) {
        if (shouldGroupAsLanguageEquivalent(ordered[i]!, ordered[j]!)) unite(i, j);
      }
    }
  }

  const groupedByRoot = new Map<number, PlanCourse[]>();
  ordered.forEach((course, index) => {
    const root = find(index);
    groupedByRoot.set(root, [...(groupedByRoot.get(root) ?? []), course]);
  });

  const byKey = new Map<string, PlanCourse[]>();
  for (const [root, options] of groupedByRoot.entries()) {
    const first = ordered[root]!;
    const concept = translatedNameKey(first.course_name) || normalizeText(first.course_name) || first.code;
    const key = `${concept}-${first.course_type}-${first.ects ?? ""}-${options.map((o) => o.code).sort().join("_")}`;
    byKey.set(key, options.sort((a, b) => a.row_no - b.row_no));
  }

  return byKey;
}

function makeGroups(courses: PlanCourse[]): PlanGroup[] {
  const byKey = groupCoursesByEquivalentKey(courses);
  return [...byKey.entries()].map(([key, options]) => {
    const first = options[0] as PlanCourse;
    const allTypes = [...new Set(options.flatMap((o) => o.semester_types))];
    const years = options.map((o) => o.suggested_year).filter((y): y is number => y !== null);
    return {
      group_key: key,
      requires_choice: options.length > 1,
      planned_ects: Number(first.ects ?? 0),
      suggested_year: years.length ? Math.min(...years) : null,
      semester_types: allTypes,
      sequence: Math.min(...options.map((o) => sequenceHint(o.course_name))),
      sequence_series_key: courseSeriesKey(first.course_name),
      sequence_part: sequenceHint(first.course_name) === 50 ? null : sequenceHint(first.course_name),
      source_order: Math.min(...options.map((o) => o.row_no)),
      options,
    };
  });
}

function semesterTypeForNumber(n: number): "Autumn" | "Spring" {
  return n % 2 === 1 ? "Autumn" : "Spring";
}

function fitsSemester(group: PlanGroup, semesterType: "Autumn" | "Spring") {
  return group.semester_types.length === 0 || group.semester_types.includes(semesterType);
}

function buildSuggestedPlan(
  mandatoryGroups: PlanGroup[],
  electiveGroups: PlanGroup[],
  semesters: number,
  totalEcts: number | null,
  onlySelectedElectives = false
) {
  const target = (totalEcts && totalEcts > 0 ? totalEcts : 180) / semesters;
  const tolerance = 0.2;
  const lowerTarget = target * (1 - tolerance);
  const upperTarget = target * (1 + tolerance);

  const slots = Array.from({ length: semesters }, (_, i) => ({
    semester_number: i + 1,
    semester_type: semesterTypeForNumber(i + 1),
    target_ects: target,
    planned_ects: 0,
    mandatory: [] as PlanGroup[],
    electives: [] as PlanGroup[],
  }));

  const compareGroups = (a: PlanGroup, b: PlanGroup) =>
    (a.suggested_year ?? 99) - (b.suggested_year ?? 99) ||
    a.source_order - b.source_order ||
    a.sequence_series_key.localeCompare(b.sequence_series_key) ||
    (a.sequence_part ?? 999) - (b.sequence_part ?? 999) ||
    a.sequence - b.sequence ||
    a.group_key.localeCompare(b.group_key);

  const chooseSlotForMandatory = (candidates: typeof slots, group: PlanGroup) => {
    const ectsAfterAdding = (planned: number) => planned + group.planned_ects;

    const underLoadedAndFits = candidates
      .filter(
        (s) =>
          s.planned_ects < lowerTarget &&
          ectsAfterAdding(s.planned_ects) <= upperTarget
      )
      .sort((a, b) => a.semester_number - b.semester_number);

    if (underLoadedAndFits[0]) return underLoadedAndFits[0];

    const stillFitsUpperBand = candidates
      .filter((s) => ectsAfterAdding(s.planned_ects) <= upperTarget)
      .sort(
        (a, b) =>
          a.planned_ects - b.planned_ects ||
          a.semester_number - b.semester_number
      );

    if (stillFitsUpperBand[0]) return stillFitsUpperBand[0];

    return [...candidates].sort(
      (a, b) =>
        Math.max(0, ectsAfterAdding(a.planned_ects) - upperTarget) -
          Math.max(0, ectsAfterAdding(b.planned_ects) - upperTarget) ||
        a.planned_ects - b.planned_ects ||
        a.semester_number - b.semester_number
    )[0];
  };

  const sortedMandatory = [...mandatoryGroups].sort(compareGroups);

  const lastPlacedSemesterBySeries = new Map<string, number>();

  for (const group of sortedMandatory) {
    const baseMinSem = group.suggested_year
      ? Math.max(1, (group.suggested_year - 1) * 2 + 1)
      : 1;

    const previousSeriesSemester =
      group.sequence_part && group.sequence_part > 1
        ? lastPlacedSemesterBySeries.get(group.sequence_series_key)
        : undefined;

    const minSem =
      previousSeriesSemester !== undefined
        ? Math.max(baseMinSem, previousSeriesSemester + 1)
        : baseMinSem;

    let candidates = slots.filter(
      (s) =>
        s.semester_number >= minSem &&
        fitsSemester(group, s.semester_type)
    );

    if (!candidates.length) {
      candidates = slots.filter(
        (s) =>
          s.semester_number >= baseMinSem &&
          fitsSemester(group, s.semester_type)
      );
    }

    if (!candidates.length) {
      candidates = slots.filter((s) => fitsSemester(group, s.semester_type));
    }

    if (!candidates.length) candidates = slots;

    const selectedSlot = chooseSlotForMandatory(candidates, group);
    if (!selectedSlot) continue;

    selectedSlot.mandatory.push(group);
    selectedSlot.planned_ects += group.planned_ects;

    if (group.sequence_series_key && group.sequence_part !== null) {
      lastPlacedSemesterBySeries.set(
        group.sequence_series_key,
        selectedSlot.semester_number
      );
    }
  }

  const sortedElectives = [...electiveGroups].sort(
    (a, b) =>
      (a.suggested_year ?? 99) - (b.suggested_year ?? 99) ||
      a.sequence - b.sequence ||
      a.group_key.localeCompare(b.group_key)
  );

  const electiveStart = Math.max(
    1,
    Math.min(semesters, Math.ceil(semesters * 0.6))
  );

  for (const group of sortedElectives) {
    const candidates = slots
      .filter(
        (s) =>
          s.semester_number >= electiveStart &&
          fitsSemester(group, s.semester_type) &&
          s.planned_ects + group.planned_ects <= upperTarget
      )
      .sort(
        (a, b) =>
          a.planned_ects - b.planned_ects ||
          a.semester_number - b.semester_number
      );

    const slot = candidates[0];
    if (!slot) continue;

    if (
      onlySelectedElectives ||
      slot.planned_ects < lowerTarget ||
      slot.electives.length < 2
    ) {
      slot.electives.push(group);
      slot.planned_ects += group.planned_ects;
    }
  }

  return slots;
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
        [degree_level ?? null, q ?? null, toInt(limit) ?? 500]
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
        description: string | null;
        learning_goals: string | null;
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
          c.description,
          c.learning_goals,
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
          c.description,
          c.learning_goals,
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
    "/study-program-plan-proposal",
    {
      schema: {
        tags: ["Planner"],
        summary: "Build a whole-study-program plan proposal without changing semester-course planner routes",
        querystring: {
          type: "object",
          required: ["program_id"],
          properties: {
            program_id: { type: "integer" },
            semesters: { type: "integer", minimum: 1, maximum: 16, default: 8 },
            total_ects: { type: "number" },
            selected_elective_codes: { type: "string", description: "Comma-separated elective course codes selected by the student" },
            locale: { type: "string", enum: ["de", "en", "fr"] },
          },
        },
      },
    },
    async (req, rep) => {
      const { program_id, semesters, locale, total_ects, selected_elective_codes } =
        (req.query as StudyProgramPlanQuery) ?? {};
      const programId = toInt(program_id);
      const requestedSemesters = Math.max(1, Math.min(16, toInt(semesters) ?? 8));
      const resolvedLocale = normalizeLocale(locale);
      const nameExpr = localizedProgramNameSql(resolvedLocale);

      if (!programId) {
        return rep.code(400).send({ error: "program_id query parameter is required" });
      }

      const programRows = await query<{
        program_id: number;
        display_name: string | null;
        degree_level: string | null;
        total_ects: number | null;
        min_elective_ects: number | null;
        max_elective_ects: number | null;
      }>(
        `
        SELECT
          p.program_id,
          ${nameExpr} AS display_name,
          p.degree_level,
          p.total_ects,
          p.min_elective_ects,
          p.max_elective_ects
        FROM StudyProgram p
        WHERE p.program_id = $1
        `,
        [programId]
      );

      if (!programRows.length) {
        return rep.code(404).send({ error: `No study program found for id ${programId}` });
      }

      const rows = await query<ProgramPlanCourseRow>(
        `
        WITH latest_offering AS (
          SELECT DISTINCT ON (off.code)
            off.code,
            off.sem_id,
            off.day_time_info
          FROM CourseOffering off
          JOIN Semester s ON s.sem_id = off.sem_id
          ORDER BY off.code, s.year DESC, CASE WHEN s.type = 'Autumn' THEN 1 ELSE 0 END DESC
        ), offering_types AS (
          SELECT
            off.code,
            ARRAY_AGG(DISTINCT s.type ORDER BY s.type) AS offered_semester_types
          FROM CourseOffering off
          JOIN Semester s ON s.sem_id = off.sem_id
          GROUP BY off.code
        ), teaching_langs AS (
          SELECT
            off.code,
            ARRAY_AGG(DISTINCT l.description) FILTER (WHERE l.description IS NOT NULL) AS teaching_languages
          FROM CourseOffering off
          LEFT JOIN is_taught_in iti ON iti.offering_id = off.offering_id
          LEFT JOIN Language l ON l.lang_id = iti.lang_id
          GROUP BY off.code
        )
        SELECT
          ROW_NUMBER() OVER (ORDER BY co.course_type, co.description NULLS LAST, co.code) AS row_no,
          co.program_id,
          co.code,
          COALESCE(NULLIF(co.course_name, ''), c.name) AS course_name,
          c.name AS canonical_course_name,
          co.course_type,
          co.description AS program_course_description,
          c.ects,
          ot.offered_semester_types,
          lo.sem_id AS latest_sem_id,
          lo.day_time_info AS latest_day_time_info,
          tl.teaching_languages
        FROM consist_of co
        JOIN Course c ON c.code = co.code
        LEFT JOIN offering_types ot ON ot.code = co.code
        LEFT JOIN latest_offering lo ON lo.code = co.code
        LEFT JOIN teaching_langs tl ON tl.code = co.code
        WHERE co.program_id = $1
        ORDER BY co.course_type, co.description NULLS LAST, c.name NULLS LAST, co.code
        `,
        [programId]
      );

      const courses = rows.map(toPlanCourse);
      const selectedElectiveCodeSet = new Set(
        String(selected_elective_codes ?? "")
          .split(",")
          .map((x) => normalizeCourseCode(x))
          .filter(Boolean)
      );
      const mandatoryGroups = makeGroups(courses.filter((c) => c.course_type === "Mandatory"));
      const allElectiveGroups = makeGroups(courses.filter((c) => c.course_type === "Elective"));
      const electiveGroups = selectedElectiveCodeSet.size
        ? allElectiveGroups.filter((g) => g.options.some((o) => selectedElectiveCodeSet.has(normalizeCourseCode(o.code))))
        : [];
      const program = programRows[0]!;
      if (!program) {
        return rep.code(404).send({ error: `No study program found for id ${programId}` });
      }
      const programTotal = Number(total_ects ?? program.total_ects ?? 0) || null;
      const plan = buildSuggestedPlan(mandatoryGroups, electiveGroups, requestedSemesters, programTotal, selectedElectiveCodeSet.size > 0);
      const mandatoryEcts = mandatoryGroups.reduce((sum, g) => sum + g.planned_ects, 0);
      const electiveSuggestedEcts = plan.reduce((sum, s) => sum + s.electives.reduce((x, g) => x + g.planned_ects, 0), 0);

      return {
        program: program,
        requested_semesters: requestedSemesters,
        totals: {
          total_ects: programTotal ?? program.total_ects,
          target_ects_per_semester: (programTotal ?? program.total_ects ?? 180) / requestedSemesters,
          mandatory_ects_after_language_choices: mandatoryEcts,
          elective_ects_required: Math.max(0, Number(program.total_ects ?? programTotal ?? 180) - mandatoryEcts),
          suggested_elective_ects: electiveSuggestedEcts,
        },
        assumptions: [
          "This is a generated proposal from structured database rows, not a legally binding study plan.",
          "Odd planned semesters are treated as HS/Autumn; even planned semesters are treated as FS/Spring.",
          "Likely bilingual/equivalent alternatives are grouped and count only once toward ECTS.",
          "Courses suggested to take in year 1 are scheduled before others.",
        ],
        mandatory_choice_groups: mandatoryGroups.filter((g) => g.requires_choice),
        suggested_mandatory_semester_plan: plan,
        selected_elective_codes: [...selectedElectiveCodeSet],
        elective_courses: allElectiveGroups.flatMap((g) => g.options),
        raw_course_count: rows.length,
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
              description: { type: ["string", "null"] },
              learning_goals: { type: ["string", "null"] },
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
          c.description,
          c.learning_goals,
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
      const program = programRows[0]!;

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
