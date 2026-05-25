import fs from "fs";
import path from "path";
import "../environments/environment";
import { PoolClient } from "pg";
import { DataAccessController } from "../control/data_access_controller";

const INPUT_PATH = path.resolve(
  process.cwd(),
  "scrapy_crawler",
  "scrapy_crawler",
  "spider_outputs",
  "programmes_with_curricula_enriched.json"
);

type SpiderItem = {
  programme_name_en?: string;
  programme_name_de?: string;
  programme_name_fr?: string;
  programme?: string;
  level?: string;
  ects_points?: number | null;
  min_semesters?: number | null;
  studyplan_metadata?: Record<string, string>;
  faculty?: string | null;
  study_director?: string | null;
  contact_mail?: string | null;
  programme_url?: string | null;
  programme_url_en?: string | null;
  programme_url_de?: string | null;
  programme_url_fr?: string | null;
};

function mapLevel(level: string | undefined): string {
  if (level === "B") return "Bachelor";
  if (level === "M") return "Master";
  if (level === "D") return "Doctorate";
  return level || "Bachelor";
}

function inferStudyStart(text?: string): string | null {
  if (!text) return null;
  const t = text.toLowerCase();
  if (t.includes("autumn") && t.includes("spring")) return "Both";
  if (t.includes("autumn")) return "Autumn";
  if (t.includes("spring")) return "Spring";
  return null;
}

function extractLanguages(text?: string): string[] {
  if (!text) return [];
  const t = text.toLowerCase();
  const langs: string[] = [];

  if (t.includes("english")) langs.push("English");
  if (t.includes("french")) langs.push("French");
  if (t.includes("german")) langs.push("German");

  return [...new Set(langs)];
}

function cleanText(value?: string | null): string | null {
  if (!value) return null;
  const cleaned = value.replace(/\s+/g, " ").trim();
  return cleaned || null;
}

function splitDirectorName(raw?: string | null): {
  title: string | null;
  firstName: string | null;
  lastName: string | null;
} {
  const name = cleanText(raw);
  if (!name) {
    return { title: null, firstName: null, lastName: null };
  }

  // detect titles at the beginning
  const titleMatch = name.match(/^((?:prof\.?|dr\.?)\s+)+/i);

  let title: string | null = null;
  let remaining = name;

  if (titleMatch) {
    title = titleMatch[1].replace(/\.$/, ""); // normalize "Prof." -> "Prof"
    remaining = name.slice(titleMatch[0].length).trim();
  }

  // office / authority fallback
  const authorityHints = [
    "office",
    "dean",
    "secretariat",
    "service",
    "bureau",
    "rectorat",
    "decanat",
    "doyen",
    "study advisor",
  ];

  const lowered = remaining.toLowerCase();
  if (authorityHints.some((hint) => lowered.includes(hint))) {
    return { title: null, firstName: null, lastName: name };
  }

  const parts = remaining.split(/\s+/).filter(Boolean);

  if (parts.length === 1) {
    return { title, firstName: null, lastName: parts[0] };
  }

  return {
    title,
    firstName: parts.slice(0, -1).join(" "),
    lastName: parts[parts.length - 1],
  };
}

function normalizeText(value?: string | null): string {
  return (value ?? "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[–—]/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

const FACULTY_ALIAS_TO_KEY: Record<string, string> = {
  // direct faculty names
  "faculty of theology": "theo",
  "faculty of law": "ius",
  "faculty of management, economics and social sciences": "ses",
  "faculty of humanities": "lettres",
  "faculty of education": "eduform",
  "faculty of science and medicine": "scimed",
  "interfaculty": "interfaculty",

  // known institutes / centres from the spider output
  "swiss centre for islam and society": "interfaculty",
  "institute for family research and counseling": "interfaculty",
  "institute for family research and counselling": "interfaculty",
  "environmental sciences and humanities institute - unifr-esh": "interfaculty",
  "environmental sciences and humanities institute": "interfaculty",
  "unifr-esh": "interfaculty",
  "adolphe merkle institute": "scimed",
};

async function getFacultyId(
  client: PoolClient,
  facultyName?: string | null
): Promise<number> {
  const faculty = cleanText(facultyName);
  if (!faculty) {
    return 100; // default fallback
  }

  // 1) exact / case-insensitive name match against Faculty table
  const exact = await client.query(
    `SELECT faculty_id
       FROM Faculty
      WHERE lower(name_en) = lower($1)
         OR lower(name_de) = lower($1)
         OR lower(name_fr) = lower($1)
      LIMIT 1`,
    [faculty]
  );

  if (exact.rowCount) {
    return exact.rows[0].faculty_id;
  }

  // 2) alias map for institutes / centres / alternate labels
  const normalized = normalizeText(faculty);

  for (const [alias, facultyKey] of Object.entries(FACULTY_ALIAS_TO_KEY)) {
    if (normalized === alias || normalized.includes(alias)) {
      const byKey = await client.query(
        `SELECT faculty_id
           FROM Faculty
          WHERE lower(faculty_key) = lower($1)
          LIMIT 1`,
        [facultyKey]
      );
      if (byKey.rowCount) {
        return byKey.rows[0].faculty_id;
      }
    }
  }

  // 3) generic heuristic: many centres/institutes should go to interfaculty
  if (
    normalized.includes("interfaculty") ||
    normalized.includes("centre") ||
    normalized.includes("center") ||
    normalized.includes("institute") ||
    normalized.includes("institut") ||
    normalized.includes("society")
  ) {
    const interfaculty = await client.query(
      `SELECT faculty_id
         FROM Faculty
        WHERE lower(faculty_key) = 'interfaculty'
        LIMIT 1`
    );
    if (interfaculty.rowCount) {
      return interfaculty.rows[0].faculty_id;
    }
  }

  console.warn("Faculty not found, using default fallback:", faculty);
  return 100;
}

async function getOrCreateProfessorId(
  client: PoolClient,
  directorRaw?: string | null,
  emailRaw?: string | null
): Promise<number | null> {
  const director = cleanText(directorRaw);
  const email = cleanText(emailRaw)?.toLowerCase() || null;

  if (!director && !email) {
    return null;
  }

  if (email) {
    const byEmail = await client.query(
      `SELECT prof_id FROM Professor WHERE lower(email) = lower($1) LIMIT 1`,
      [email]
    );
    if (byEmail.rowCount) {
      const profId = byEmail.rows[0].prof_id;
      if (director) {
        const { title, firstName, lastName } = splitDirectorName(director);
        await client.query(
          `UPDATE Professor
              SET title = COALESCE(title, $2),
              first_name = COALESCE(first_name, $3),
              last_name = COALESCE(last_name, $4)
            WHERE prof_id = $1`,
          [profId, title, firstName, lastName]
        );
      }
      return profId;
    }
  }

  if (director) {
    const { title,firstName, lastName } = splitDirectorName(director);
    const byName = await client.query(
      `SELECT prof_id, email
         FROM Professor
        WHERE COALESCE(first_name, '') = COALESCE($1, '')
          AND last_name = $2
        LIMIT 1`,
      [firstName, lastName]
    );

    if (byName.rowCount) {
      const profId = byName.rows[0].prof_id;
      if (email && !byName.rows[0].email) {
        await client.query(
          `UPDATE Professor SET email = $2 WHERE prof_id = $1`,
          [profId, email]
        );
      }
      return profId;
    }

    const inserted = await client.query(
      `INSERT INTO Professor (title, first_name, last_name, email)
       VALUES ($1, $2, $3, $4)
       RETURNING prof_id`,
      [title, firstName, lastName, email]
    );

    return inserted.rows[0].prof_id;
  }

  // fallback: only email exists
  const inserted = await client.query(
    `INSERT INTO Professor (first_name, last_name, email)
     VALUES (NULL, $1, $2)
     RETURNING prof_id`,
    [email, email]
  );

  return inserted.rows[0].prof_id;
}

async function getOrCreateLanguageId(client: PoolClient, description: string): Promise<number> {
  const existing = await client.query(
    `SELECT lang_id FROM Language WHERE description = $1 LIMIT 1`,
    [description]
  );
  if (existing.rowCount) {
    return existing.rows[0].lang_id;
  }

  const inserted = await client.query(
    `INSERT INTO Language(description)
     VALUES ($1)
     RETURNING lang_id`,
    [description]
  );
  return inserted.rows[0].lang_id;
}

async function main() {
  const client = await DataAccessController.pool.connect();

  try {
    const raw = fs.readFileSync(INPUT_PATH, "utf-8");
    const data: SpiderItem[] = JSON.parse(raw);

    await client.query("BEGIN");
    await client.query(`TRUNCATE TABLE StudyProgram RESTART IDENTITY CASCADE;`);

    for (const item of data) {
      const degreeLevel = mapLevel(item.level);
      const studyStart = inferStudyStart(item.studyplan_metadata?.commencement_of_studies);
      const facultyId = await getFacultyId(client, item.faculty);

      const directorId = await getOrCreateProfessorId(
        client,
        item.study_director,
        item.contact_mail
      );

      const insertRes = await client.query(
        `INSERT INTO StudyProgram (
          name,
          degree_level,
          semesters,
          total_ects,
          study_start,
          faculty_id,
          director,
          source_last_page_url,
          name_en,
          name_de,
          name_fr
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        ON CONFLICT (name, degree_level, total_ects)
        DO UPDATE SET
          semesters = EXCLUDED.semesters,
          study_start = EXCLUDED.study_start,
          faculty_id = EXCLUDED.faculty_id,
          director = COALESCE(EXCLUDED.director, StudyProgram.director),
          source_last_page_url = EXCLUDED.source_last_page_url,
          name_en = EXCLUDED.name_en,
          name_de = EXCLUDED.name_de,
          name_fr = EXCLUDED.name_fr
        RETURNING program_id`,
        [
          item.programme_name_en || item.programme,
          degreeLevel,
          item.min_semesters ?? null,
          item.ects_points ?? null,
          studyStart,
          facultyId,
          directorId,
          item.programme_url || item.programme_url_en || null,
          item.programme_name_en || item.programme || null,
          item.programme_name_de || null,
          item.programme_name_fr || null,
        ]
      );

      const programId = insertRes.rows[0].program_id;
      const langs = extractLanguages(item.studyplan_metadata?.languages_of_study);

      for (const lang of langs) {
        const langId = await getOrCreateLanguageId(client, lang);
        await client.query(
          `INSERT INTO has_lang(program_id, lang_id)
           VALUES ($1,$2)
           ON CONFLICT DO NOTHING`,
          [programId, langId]
        );
      }
    }

    await client.query("COMMIT");
    console.log("Base program data import completed.");
  } catch (err) {
    await client.query("ROLLBACK");
    console.error(err);
    process.exitCode = 1;
  } finally {
    client.release();
    await DataAccessController.pool.end();
  }
}

main();
