import "../environments/environment";
import { DataAccessController } from "../control/data_access_controller";
import fs from "fs";
import path from "path";

type AnyObj = Record<string, any>;
type DB = { query: (text: string, params?: any[]) => Promise<any> };

// -----------------------------
// Helpers
// -----------------------------
function parseBoolJaNein(v: any): boolean | null {
  if (v == null) return null;
  const s = String(v).trim().toLowerCase();
  if (s === "ja") return true;
  if (s === "nein") return false;
  return null;
}

function splitLanguages(raw: string | null | undefined): string[] {
  if (!raw) return [];
  const s = raw.replace(/\s+/g, " ").trim();

  if (/zweisprachig/i.test(s)) {
    if (/f\/d|f\s*\/\s*d/i.test(s)) return ["Französisch", "Deutsch"];
    if (/d\/f|d\s*\/\s*f/i.test(s)) return ["Deutsch", "Französisch"];
    return ["Zweisprachig"];
  }

  return s
    .split(/[,/]\s*|\s{2,}|\s*,\s*/g)
    .map((x) => x.trim())
    .filter(Boolean);
}

function parseSemester(
  semId: string | null | undefined
): { sem_id: string; year: number; type: "Spring" | "Autumn" } | null {
  if (!semId) return null;
  const m = String(semId).trim().match(/^(FS|HS)-(\d{4})$/);
  if (!m) return null;
  const [, t, y] = m;
  return { sem_id: `${t}-${y}`, year: Number(y), type: t === "FS" ? "Spring" : "Autumn" };
}

function ddmmyyyyToIso(d: string): string | null {
  const m = String(d).trim().match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
  if (!m) return null;
  const [, dd, mm, yyyy] = m;
  return `${yyyy}-${mm}-${dd}`;
}

function parseTimeRange(t: string): { start: string | null; end: string | null } {
  const m = String(t).match(/(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})/);
  if (!m) return { start: null, end: null };
  return { start: m[1], end: m[2] };
}

function guessOfferingType(schedule: AnyObj, singleDates: AnyObj[]): "Weekly" | "Block" {
  const vt = String(schedule?.["Vorlesungszeiten"] ?? "").toLowerCase();
  if (vt.includes("wöchentlich") || vt.includes("weekly")) return "Weekly";
  if (vt.includes("blockkurs") || vt.includes("bloc") || vt.includes("block")) return "Block";
  if (singleDates && singleDates.length > 0) return "Block";
  return "Weekly";
}

function buildDayTimeInfo(
  offering_type: "Weekly" | "Block",
  schedule: AnyObj
): string | null {
  const vt = String(schedule?.["Vorlesungszeiten"] ?? "").trim();
  const struktur = String(schedule?.["Strukturpläne"] ?? "").trim();
  const kontakt = String(schedule?.["Kontaktstunden"] ?? "").trim();

  if (offering_type === "Weekly") {
    if (!vt) return null;

    // Extract all weekday + time ranges
    const weekdayMap: Record<string, string> = {
      Montag: "Monday",
      Dienstag: "Tuesday",
      Mittwoch: "Wednesday",
      Donnerstag: "Thursday",
      Freitag: "Friday",
    };

    const slotRegex =
      /(Montag|Dienstag|Mittwoch|Donnerstag|Freitag)\s+(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})/g;

    const slots: string[] = [];
    let match: RegExpExecArray | null;

    while ((match = slotRegex.exec(vt)) !== null) {
      const [, dayDe, start, end] = match;
      slots.push(`${weekdayMap[dayDe]} ${start} - ${end}`);
    }

    if (slots.length > 0) {
      return Array.from(new Set(slots)).join("; ");
    }

    return vt; // fallback to raw text
  }

  // ---- BLOCK COURSES ----
  if (struktur || kontakt) {
    const parts = [];
    if (struktur) parts.push(struktur);

    if (kontakt) {
      const hours = kontakt.match(/\d+/)?.[0];
      if (hours) parts.push(`${hours} hours total`);
    }

    return parts.join(", ");
  }

  if (vt) {
    // fallback if only Vorlesungszeiten exists
    if (vt.toLowerCase().includes("unregelmässig") || vt.toLowerCase().includes("irrégulier")) {
      return "Irregular schedule";
    }
    return "Block course";
  }

  return null;
}

// -----------------------------
// Faculty lookup (NO INSERTS)
// -----------------------------

// Timetable "Fakultät" strings sometimes differ from your canonical Faculty.name_de
const FACULTY_NAME_ALIASES: Record<string, string> = {
  // timetable -> canonical Faculty.name_de from your faculties.json
  "Math.-Nat. und Med. Fakultät":
    "Mathematisch-Naturwissenschaftliche und Medizinische Fakultät",
  "Interfakultär": "Interfakultär",
  "Philosophische Fakultät": "Philosophische Fakultät",
  "Rechtswissenschaftliche Fakultät": "Rechtswissenschaftliche Fakultät",
  "Theologische Fakultät": "Theologische Fakultät",
  "Wirtschafts- und Sozialwissenschaftliche Fakultät":
    "Wirtschafts- und Sozialwissenschaftliche Fakultät",
  "Fakultät für Erziehungs- und Bildungswissenschaften":
    "Fakultät für Erziehungs- und Bildungswissenschaften",
};

function canonicalizeFacultyName(raw: any): string | null {
  if (!raw) return null;
  const s = String(raw).trim();
  if (!s) return null;
  return FACULTY_NAME_ALIASES[s] ?? s;
}

async function getFacultyIdByName(db: DB, rawFacultyName: any): Promise<number | null> {
  const name = canonicalizeFacultyName(rawFacultyName);
  if (!name) return null;

  // exact match in any language column
  const r = await db.query(
    `
    SELECT faculty_id
    FROM Faculty
    WHERE name_de = $1 OR name_fr = $1 OR name_en = $1
    LIMIT 1;
    `,
    [name]
  );

  return r.rows[0]?.faculty_id ?? null;
}

// -----------------------------
// DB upserts (IMPORTANT: use db.query, not pool.query)
// -----------------------------
async function upsertDomain(
  db: DB,
  name: string | null | undefined,
  facultyId: number | null
): Promise<number | null> {
  if (!name || !facultyId) return null;
  const q = `
    INSERT INTO Domain (name, faculty_id)
    VALUES ($1, $2)
    ON CONFLICT (name, faculty_id) DO UPDATE SET name = EXCLUDED.name
    RETURNING domain_id;
  `;
  const r = await db.query(q, [name.trim(), facultyId]);
  return r.rows[0]?.domain_id ?? null;
}

async function upsertLanguage(db: DB, desc: string): Promise<number> {
  const q = `
    INSERT INTO Language (description)
    VALUES ($1)
    ON CONFLICT (description) DO UPDATE SET description = EXCLUDED.description
    RETURNING lang_id;
  `;
  const r = await db.query(q, [desc.trim()]);
  return r.rows[0].lang_id;
}

async function upsertRoom(db: DB, roomId: string | null | undefined): Promise<string | null> {
  if (!roomId) return null;
  const id = roomId.trim();
  if (!id) return null;
  const q = `INSERT INTO Room (room_id) VALUES ($1) ON CONFLICT (room_id) DO NOTHING;`;
  await db.query(q, [id]);
  return id;
}

async function upsertProfessor(db: DB, fullName: string): Promise<number> {
  const name = fullName.trim().replace(/\s+/g, " ");
  const parts = name.split(" ");

  const first_name = parts.length >= 2 ? parts[parts.length - 1] : null;
  const last_name = parts.length >= 2 ? parts.slice(0, -1).join(" ") : name;

  const lockKey = `${(first_name ?? "").toLowerCase()}||${last_name.toLowerCase()}`;
  await db.query(`SELECT pg_advisory_xact_lock(hashtext($1));`, [lockKey]);

  const found = await db.query(
    `
    SELECT prof_id
    FROM Professor
    WHERE first_name IS NOT DISTINCT FROM $1
      AND last_name = $2
    LIMIT 1;
    `,
    [first_name, last_name]
  );

  if (found.rows.length > 0) return found.rows[0].prof_id;

  const inserted = await db.query(
    `
    INSERT INTO Professor (first_name, last_name)
    VALUES ($1, $2)
    RETURNING prof_id;
    `,
    [first_name, last_name]
  );

  return inserted.rows[0].prof_id;
}

async function upsertSemester(
  db: DB,
  sem: { sem_id: string; year: number; type: "Spring" | "Autumn" }
): Promise<void> {
  const q = `
    INSERT INTO Semester (sem_id, year, type)
    VALUES ($1, $2, $3)
    ON CONFLICT (sem_id) DO UPDATE SET year = EXCLUDED.year, type = EXCLUDED.type;
  `;
  await db.query(q, [sem.sem_id, sem.year, sem.type]);
}

async function upsertCourse(
  db: DB,
  args: {
    code: string;
    name?: string | null;
    ects?: number | null;
    description?: string | null;
    learning_goals?: string | null;
    remarks?: string | null;
    soft_skills?: boolean | null;
    outside_domain?: boolean | null;
    benefri?: boolean | null;
    mobility?: boolean | null;
    unipop?: boolean | null;
    faculty_id?: number | null;
    domain_id?: number | null;
  }
): Promise<void> {
  const q = `
    INSERT INTO Course (
      code, alternative_code, name, ects,
      description, learning_goals, admission_conditions, remarks,
      soft_skills, outside_domain, benefri, mobility, unipop,
      faculty_id, domain_id
    )
    VALUES (
      $1, NULL, $2, $3,
      $4, $5, NULL, $6,
      $7, $8, $9, $10, $11,
      $12, $13
    )
    ON CONFLICT (code) DO UPDATE SET
      name = EXCLUDED.name,
      ects = EXCLUDED.ects,
      description = EXCLUDED.description,
      learning_goals = EXCLUDED.learning_goals,
      remarks = EXCLUDED.remarks,
      soft_skills = EXCLUDED.soft_skills,
      outside_domain = EXCLUDED.outside_domain,
      benefri = EXCLUDED.benefri,
      mobility = EXCLUDED.mobility,
      unipop = EXCLUDED.unipop,
      faculty_id = EXCLUDED.faculty_id,
      domain_id = EXCLUDED.domain_id;
  `;

  await db.query(q, [
    args.code,
    args.name ?? null,
    args.ects ?? null,
    args.description ?? null,
    args.learning_goals ?? null,
    args.remarks ?? null,
    args.soft_skills ?? null,
    args.outside_domain ?? null,
    args.benefri ?? null,
    args.mobility ?? null,
    args.unipop ?? null,
    args.faculty_id ?? null,
    args.domain_id ?? null,
  ]);
}

async function upsertCourseOffering(
  db: DB,
  code: string,
  sem_id: string,
  offering_type: "Weekly" | "Block",
  link: string | null,
  day_time_info: string | null
): Promise<number> {
  const q = `
    INSERT INTO CourseOffering (code, sem_id, offering_type, link_course_catalogue, day_time_info)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (code, sem_id, offering_type)
    DO UPDATE SET
      link_course_catalogue = EXCLUDED.link_course_catalogue,
      day_time_info = EXCLUDED.day_time_info
    RETURNING offering_id;
  `;
  const r = await db.query(q, [code, sem_id, offering_type, link, day_time_info]);
  return r.rows[0].offering_id;
}

async function insertSessions(db: DB, offering_id: number, sessions: AnyObj[]): Promise<void> {
  for (const s of sessions || []) {
    const iso = ddmmyyyyToIso(s.date);
    if (!iso) continue;

    const { start, end } = parseTimeRange(s.time ?? "");
    const room_id = await upsertRoom(db, s.location);

    const q = `
      INSERT INTO Session (offering_id, date, start_time, end_time, room_id, unit_type)
      VALUES ($1, $2, $3, $4, $5, $6)
      ON CONFLICT DO NOTHING;
    `;
    await db.query(q, [offering_id, iso, start, end, room_id, s.unit_type ?? null]);
  }
}

async function insertEvaluations(db: DB, offering_id: number, evals: AnyObj[]): Promise<void> {
  for (const e of evals || []) {
    const title = e.title ?? null;
    const scheme = e.kv?.["Bewertungsmodus"] ?? null;
    const desc = e.kv?.["Beschreibung"] ?? null;

    const q = `
      INSERT INTO Evaluation (offering_id, date, start_time, end_time, description, requirements, evaluation_scheme, remarks)
      VALUES ($1, NULL, NULL, NULL, $2, NULL, $3, $4);
    `;
    await db.query(q, [offering_id, title, scheme, desc]);
  }
}

async function linkOfferingLanguages(db: DB, offering_id: number, langs: string[]): Promise<void> {
  for (const l of langs) {
    const lang_id = await upsertLanguage(db, l);
    const q = `
      INSERT INTO is_taught_in (offering_id, lang_id)
      VALUES ($1, $2)
      ON CONFLICT (offering_id, lang_id) DO NOTHING;
    `;
    await db.query(q, [offering_id, lang_id]);
  }
}

async function linkCourseProfessors(db: DB, code: string, profNames: string[]): Promise<void> {
  for (const n of profNames || []) {
    const prof_id = await upsertProfessor(db, n);
    const q = `
      INSERT INTO teaches (code, prof_id)
      VALUES ($1, $2)
      ON CONFLICT (code, prof_id) DO NOTHING;
    `;
    await db.query(q, [code, prof_id]);
  }
}

// -----------------------------
// Main runner (resilient import)
// -----------------------------
async function run() {
  // adjust default to your output file if needed
  const inputPath = process.argv[2] || path.resolve(process.cwd(), "scrapy_crawler/scrapy_crawler/spider_outputs/courses.json");
  if (!fs.existsSync(inputPath)) throw new Error(`Input file not found: ${inputPath}`);

  const raw = fs.readFileSync(inputPath, "utf-8");
  const items: AnyObj[] = JSON.parse(raw);

  console.log(`Importing ${items.length} items from ${inputPath} ...`);

  const failures: any[] = [];

  const client = await DataAccessController.pool.connect();
  const db: DB = client;

  try {
    await db.query("BEGIN");

    await db.query(`
      TRUNCATE TABLE
        examined_in,
        evaluation,
        session,
        is_taught_in,
        teaches,
        courseoffering,
        course,
        domain,
        semester,
        professor,
        language,
        room
      RESTART IDENTITY CASCADE;
    `);

    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      await db.query("SAVEPOINT sp_item");

      try {
        const course = item.course ?? {};
        const details = item.details ?? {};
        const schedule = item.schedule ?? {};
        const teaching = item.teaching ?? {};
        const singleDates = item.einzeltermine_raeume ?? [];
        const evals = item.leistungskontrolle ?? [];

        const code = String(course.code || details.Code || "").trim();
        if (!code) {
          await db.query("RELEASE SAVEPOINT sp_item");
          continue;
        }

        // ✅ faculty lookup (NO INSERTS)
        const facultyName = details["Fakultät"] ?? null;
        const faculty_id = await getFacultyIdByName(db, facultyName);
        if (!faculty_id) {
          throw new Error(
            `Unknown faculty "${facultyName}" (canonical="${canonicalizeFacultyName(facultyName)}")`
          );
        }

        const domainName = details["Bereich"] ?? details["Domaine"] ?? null;
        const domain_id = await upsertDomain(db, domainName, faculty_id);

        const soft_skills = parseBoolJaNein(teaching["Soft Skills"]);
        const outside_domain = parseBoolJaNein(teaching["ausserhalb des Bereichs"]);
        const benefri = parseBoolJaNein(teaching["BeNeFri"]);
        const mobility = parseBoolJaNein(teaching["Mobilität"]);
        const unipop = parseBoolJaNein(teaching["UniPop"]);

        await upsertCourse(db, {
          code,
          name: course.name ?? details["Name"] ?? null,
          ects: typeof course.ects === "number" ? course.ects : null,
          description: teaching["Beschreibung"] ?? null,
          learning_goals: teaching["Lernziele"] ?? null,
          remarks: teaching["Bemerkungen"] ?? null,
          soft_skills,
          outside_domain,
          benefri,
          mobility,
          unipop,
          faculty_id,
          domain_id,
        });

        const sem = parseSemester(course.semester ?? details["Semester"]);
        if (!sem) {
          await db.query("RELEASE SAVEPOINT sp_item");
          continue;
        }
        await upsertSemester(db, sem);

        const offering_type = guessOfferingType(schedule, singleDates);
          const dayTimeInfo = buildDayTimeInfo(offering_type, schedule);

          const offering_id = await upsertCourseOffering(
            db,
            code,
            sem.sem_id,
            offering_type,
            item.source?.detail_page_url ?? null,
            dayTimeInfo
          );

        const langs = splitLanguages(details["Sprachen"]);
        await linkOfferingLanguages(db, offering_id, langs);

        const profsRaw = [
          ...(Array.isArray(teaching["Dozenten-innen"]) ? teaching["Dozenten-innen"] : []),
          ...(Array.isArray(teaching["Verantwortliche"]) ? teaching["Verantwortliche"] : []),
        ]
          .filter((x) => typeof x === "string")
          .map((x) => x.trim().replace(/\s+/g, " "))
          .filter(Boolean);

        const profs = Array.from(new Set(profsRaw));
        await linkCourseProfessors(db, code, profs);

        // store all occurrences, for Weekly *and* Block
        await insertSessions(db, offering_id, singleDates);

        await insertEvaluations(db, offering_id, evals);

        await db.query("RELEASE SAVEPOINT sp_item");
      } catch (e: any) {
        await db.query("ROLLBACK TO SAVEPOINT sp_item");
        await db.query("RELEASE SAVEPOINT sp_item");

        const code = item?.course?.code ?? item?.details?.Code ?? "UNKNOWN";
        const url = item?.source?.detail_page_url ?? null;

        failures.push({
          index: i,
          code,
          url,
          error: { message: e?.message, code: e?.code },
        });

        console.error(`❌ Failed item #${i} (${code}) continuing...`, e?.message);
      }
    }

    await db.query("COMMIT");

    if (failures.length) {
      fs.writeFileSync("import_failures.json", JSON.stringify(failures, null, 2), "utf-8");
      console.log(`⚠️ Import finished with ${failures.length} failures. See import_failures.json`);
    } else {
      console.log("✅ Import finished with 0 failures");
    }
  } catch (fatal) {
    await client.query("ROLLBACK");
    throw fatal;
  } finally {
    client.release();
    await DataAccessController.pool.end();
  }
}

run().catch((e) => {
  console.error("❌ Import failed:", e);
  process.exit(1);
});
