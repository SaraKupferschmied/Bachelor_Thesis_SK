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

function parseFirstWeeklySlot(vorlesungszeiten: string): {
  weekday: string | null;
  start: string | null;
  end: string | null;
  room: string | null;
} {
  const s = (vorlesungszeiten || "").replace(/\s+/g, " ").trim();

  const weekdayMap: Record<string, string> = {
    montag: "Monday",
    dienstag: "Tuesday",
    mittwoch: "Wednesday",
    donnerstag: "Thursday",
    freitag: "Friday",
  };

  const w = Object.keys(weekdayMap).find((k) => s.toLowerCase().startsWith(k));
  const weekday = w ? weekdayMap[w] : null;

  const { start, end } = parseTimeRange(s);

  const parts = s.split(",");
  let room: string | null = null;
  if (parts.length >= 3) {
    room = parts
      .slice(2)
      .join(",")
      .replace(/\(.*?\)/g, "")
      .trim();
    if (!room) room = null;
  }
  return { weekday, start, end, room };
}

// -----------------------------
// DB upserts (IMPORTANT: use db.query, not pool.query)
// -----------------------------
async function upsertFaculty(db: DB, name: string | null | undefined): Promise<number | null> {
  if (!name) return null;
  const q = `
    INSERT INTO Faculty (name)
    VALUES ($1)
    ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
    RETURNING faculty_id;
  `;
  const r = await db.query(q, [name.trim()]);
  return r.rows[0]?.faculty_id ?? null;
}

async function upsertDomain(db: DB, name: string | null | undefined, facultyId: number | null): Promise<number | null> {
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

  const last_name = parts.length >= 2 ? parts[parts.length - 1] : name;
  const first_name = parts.length >= 2 ? parts.slice(0, -1).join(" ") : null;

  // Prevent race conditions / duplicates even in parallel runs:
  const lockKey = `${(first_name ?? "").toLowerCase()}||${last_name.toLowerCase()}`;
  await db.query(`SELECT pg_advisory_xact_lock(hashtext($1));`, [lockKey]);

  // 1) try find existing
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

  if (found.rows.length > 0) {
    return found.rows[0].prof_id;
  }

  // 2) otherwise insert new
  const inserted = await db.query(
    `
    INSERT INTO Professor (title, first_name, last_name, email, office)
    VALUES (NULL, $1, $2, NULL, NULL)
    RETURNING prof_id;
    `,
    [first_name, last_name]
  );

  return inserted.rows[0].prof_id;
}



async function upsertSemester(db: DB, sem: { sem_id: string; year: number; type: "Spring" | "Autumn" }): Promise<void> {
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
      soft_skills, outside_domain, mobility, unipop,
      faculty_id, domain_id
    )
    VALUES (
      $1, NULL, $2, $3,
      $4, $5, NULL, $6,
      $7, $8, $9, $10,
      $11, $12
    )
    ON CONFLICT (code) DO UPDATE SET
      name = EXCLUDED.name,
      ects = EXCLUDED.ects,
      description = EXCLUDED.description,
      learning_goals = EXCLUDED.learning_goals,
      remarks = EXCLUDED.remarks,
      soft_skills = EXCLUDED.soft_skills,
      outside_domain = EXCLUDED.outside_domain,
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
  link: string | null
): Promise<number> {
  const q = `
    INSERT INTO CourseOffering (code, sem_id, offering_type, link_course_catalogue)
    VALUES ($1, $2, $3, $4)
    ON CONFLICT (code, sem_id, offering_type) DO UPDATE SET link_course_catalogue = EXCLUDED.link_course_catalogue
    RETURNING offering_id;
  `;
  const r = await db.query(q, [code, sem_id, offering_type, link]);
  return r.rows[0].offering_id;
}

async function upsertWeeklySlot(
  db: DB,
  offering_id: number,
  slot: { weekday: string | null; start: string | null; end: string | null; room: string | null }
): Promise<void> {
  const room_id = await upsertRoom(db, slot.room);
  const q = `
    INSERT INTO SemesterCourse (offering_id, weekday, start_time, end_time, course_format, room_id)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT (offering_id) DO UPDATE SET
      weekday = EXCLUDED.weekday,
      start_time = EXCLUDED.start_time,
      end_time = EXCLUDED.end_time,
      room_id = EXCLUDED.room_id;
  `;
  await db.query(q, [offering_id, slot.weekday, slot.start, slot.end, "Lecture", room_id]);
}

async function ensureBlocCourse(db: DB, offering_id: number): Promise<void> {
  const q = `INSERT INTO BlocCourse (offering_id) VALUES ($1) ON CONFLICT (offering_id) DO NOTHING;`;
  await db.query(q, [offering_id]);
}

async function insertSessions(db: DB, offering_id: number, sessions: AnyObj[]): Promise<void> {
  for (const s of sessions || []) {
    const iso = ddmmyyyyToIso(s.date);
    if (!iso) continue;

    const { start, end } = parseTimeRange(s.time ?? "");
    const room_id = await upsertRoom(db, s.location);

    const q = `
      INSERT INTO Session (offering_id, date, start_time, end_time, room_id)
      VALUES ($1, $2, $3, $4, $5)
      ON CONFLICT DO NOTHING;
    `;
    await db.query(q, [offering_id, iso, start, end, room_id]);
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
  const inputPath = process.argv[2] || path.resolve(process.cwd(), "../out.json");
  if (!fs.existsSync(inputPath)) {
    throw new Error(`Input file not found: ${inputPath}`);
  }

  const raw = fs.readFileSync(inputPath, "utf-8");
  const items: AnyObj[] = JSON.parse(raw);

  console.log(`Importing ${items.length} items from ${inputPath} ...`);

  const who = await DataAccessController.pool.query(`
    SELECT
      current_database() as db,
      inet_server_addr()::text as server_ip,
      inet_server_port() as server_port,
      current_schema() as schema,
      current_setting('search_path') as search_path
  `);
  console.log("DB connection info:", who.rows[0]);

  const exists = await DataAccessController.pool.query(`
    SELECT to_regclass('public.faculty') as faculty_table
  `);
  console.log("public.faculty:", exists.rows[0]);


  const failures: any[] = [];

  // ✅ CRITICAL: use ONE connection from the pool, otherwise BEGIN/SAVEPOINT breaks
  const client = await DataAccessController.pool.connect();
  const db: DB = client;

  try {
    await db.query("BEGIN");

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

        const facultyName = details["Fakultät"] ?? null;
        const domainName = details["Bereich"] ?? details["Domaine"] ?? null;

        const faculty_id = await upsertFaculty(db, facultyName);
        const domain_id = await upsertDomain(db, domainName, faculty_id);

        const soft_skills = parseBoolJaNein(teaching["Soft Skills"]);
        const outside_domain = parseBoolJaNein(teaching["ausserhalb des Bereichs"]);
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
        const offering_id = await upsertCourseOffering(db, code, sem.sem_id, offering_type, item.source?.detail_page_url ?? null);

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

        if (offering_type === "Block") {
          await ensureBlocCourse(db, offering_id);
          await insertSessions(db, offering_id, singleDates);
        } else {
          const vt = String(schedule?.["Vorlesungszeiten"] ?? "");
          if (vt) {
            const slot = parseFirstWeeklySlot(vt);
            await upsertWeeklySlot(db, offering_id, slot);
          }
        }

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
