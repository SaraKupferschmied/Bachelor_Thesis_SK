import "../environments/environment";

import fs from "fs";
import path from "path";
import { DataAccessController } from "../control/data_access_controller";

type DB = { query: (text: string, params?: any[]) => Promise<any> };

type HintDoc = { url: string; label?: string | null; source_type?: string | null };

type HintEntry = {
  faculty?: string;               // e.g. "EDUFORM", "Law", "Philosophy"
  category?: string | null;       // e.g. "bachelor", "master", ...
  level?: string | null;          // some crawlers use "level"
  lang?: string | null;
  year?: number | null;
  ects?: number | null;

  // SCIMED shape:
  program?: any;

  // EDUFORM / others:
  program_raw?: string | null;
  program_name?: string | null;
  title?: string | null;

  page_url?: string | null;

  documents?: HintDoc[];
  file_urls?: string[];
};

type FacultyRow = {
  faculty_id: number;
  faculty_key: string; // e.g. "eduform", "scimed", "ius", "lettres", ...
  url: string | null;
};

type StudyProgramRow = {
  program_id: number;
  name: string;
  degree_level: "Bachelor" | "Master" | "Doctorate";
  total_ects: number | null;
  faculty_id: number | null;
};

function readJson<T>(p: string): T {
  return JSON.parse(fs.readFileSync(p, "utf-8")) as T;
}

function listHintFiles(inputPath: string): string[] {
  const p = path.resolve(inputPath);
  if (!fs.existsSync(p)) throw new Error(`Input not found: ${p}`);

  const stat = fs.statSync(p);
  if (stat.isFile()) return [p];

  const files = fs
    .readdirSync(p)
    .filter((f) => f.endsWith("_studyplans.json"))
    .map((f) => path.join(p, f));

  if (files.length === 0) {
    throw new Error(`No *_studyplans.json files found in: ${p}`);
  }
  return files;
}

function normalize(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/\s+/g, " ");
}

function basenameNoExt(url: string): string {
  try {
    const u = new URL(url);
    const base = path.basename(u.pathname);
    return base.replace(/\.[a-z0-9]+$/i, "");
  } catch {
    const base = path.basename(url);
    return base.replace(/\.[a-z0-9]+$/i, "");
  }
}

function collectDocUrls(h: HintEntry): string[] {
  const urls = new Set<string>();
  for (const d of h.documents ?? []) if (d?.url) urls.add(d.url);
  for (const u of h.file_urls ?? []) if (u) urls.add(u);
  return [...urls];
}

function getAllTextCandidates(h: HintEntry): string[] {
  const out: string[] = [];
  const push = (x?: string | null) => {
    if (typeof x === "string" && x.trim()) out.push(x.trim());
  };

  push(h.program_name);
  push(h.program_raw);
  push(h.title);

  // program object / string
  if (h.program && typeof h.program === "object") {
    push(h.program.name_en);
    push(h.program.name_de);
    push(h.program.name_fr);
    push(h.program.program_name);
    push(h.program.title);
    push(h.program.page_url_de);
    push(h.program.page_url_fr);
    push(h.program.page_url_en);
    push(h.program.page_url);
    push(h.program.studienplan_url);
  } else if (typeof h.program === "string") {
    push(h.program);
  }

  push(h.page_url ?? null);

  // doc labels + filenames
  for (const d of h.documents ?? []) {
    push(d.label ?? null);
    push(d.url ?? null);
    if (d.url) push(basenameNoExt(d.url));
  }
  for (const u of h.file_urls ?? []) {
    push(u);
    push(basenameNoExt(u));
  }

  return [...new Set(out)];
}

function extractEctsFromText(texts: string[]): number[] {
  const ects = new Set<number>();
  const add = (n: number) => {
    if (Number.isFinite(n) && n > 0 && n < 500) ects.add(n);
  };

  for (const t of texts) {
    const s = t;

    // "(60 ECTS)" / "60 ECTS"
    for (const m of s.matchAll(/(\d{1,3})\s*ECTS/gi)) add(parseInt(m[1], 10));

    // EDUFORM style: "90+30"
    for (const m of s.matchAll(/(\d{1,3})\s*\+\s*(\d{1,3})/g)) {
      const a = parseInt(m[1], 10);
      const b = parseInt(m[2], 10);
      if (a > 0 && b > 0) add(a + b);
    }

    // filenames: "..._60_2021", "BSc_XXX_120", etc.
    for (const m of s.matchAll(/[_-](\d{1,3})(?:[_-]|$)/g)) add(parseInt(m[1], 10));
  }

  return [...ects].sort((a, b) => a - b);
}

function deriveDegreeFromText(catOrLevel?: string | null, texts: string[] = []): "Bachelor" | "Master" | "Doctorate" | null {
  const c = (catOrLevel ?? "").toLowerCase();
  if (c.includes("bachelor") || c.includes("minor") || c.includes("major") || c.includes("nebenfach")) return "Bachelor";
  if (c.includes("master")) return "Master";
  if (c.includes("doktor") || c.includes("doctor") || c.includes("phd")) return "Doctorate";

  const joined = texts.join(" ").toLowerCase();
  if (/\b(bsc|ba|bachelor)\b/.test(joined)) return "Bachelor";
  if (/\b(msc|ma|master)\b/.test(joined)) return "Master";
  if (/\b(phd|doktor|doctorate)\b/.test(joined)) return "Doctorate";

  return null;
}

function deriveFacultyKeyFromHint(h: HintEntry): string | null {
  const raw = (h.faculty ?? "").trim().toLowerCase();
  if (!raw) return null;

  // IMPORTANT: your hints use things like "Law" / "Philosophy"
  const map: Record<string, string> = {
    scimed: "scimed",
    eduform: "eduform",
    ses: "ses",
    ius: "ius",
    interfaculty: "interfaculty",
    theology: "theo",
    theo: "theo",

    // crawler values:
    law: "ius",
    philosophy: "lettres",
    humanities: "lettres",
    letters: "lettres",
  };

  return map[raw] ?? raw; // if crawler already gives "lettres", keep it
}

function resolveFacultyByUrlsOrKey(faculties: FacultyRow[], facultyKey: string | null, urls: string[]): FacultyRow | null {
  const key = (facultyKey ?? "").trim().toLowerCase();
  if (key) {
    const direct = faculties.find((f) => f.faculty_key.toLowerCase() === key);
    if (direct) return direct;
  }

  // fallback: match by URL prefix
  for (const u of urls) {
    for (const f of faculties) {
      if (!f.url) continue;
      if (u.startsWith(f.url)) return f;
    }
  }

  // fallback: contains "/<faculty_key>/" or hostname patterns
  for (const u of urls) {
    for (const f of faculties) {
      const fk = f.faculty_key.toLowerCase();
      if (u.toLowerCase().includes(`/${fk}`) || u.toLowerCase().includes(`.${fk}.`)) return f;
    }
  }

  return null;
}

function getProgramNameCandidate(h: HintEntry): string | null {
  if (h.program && typeof h.program === "object") {
    const n = h.program.name_en ?? h.program.name_de ?? h.program.name_fr ?? null;
    if (typeof n === "string" && n.trim()) return n.trim();
  }
  if (typeof h.program === "string" && h.program.trim()) return h.program.trim();

  const pn = (h.program_name ?? h.program_raw ?? h.title ?? "").trim();
  return pn ? pn : null;
}

async function loadPrograms(db: DB): Promise<StudyProgramRow[]> {
  const r = await db.query(
    `SELECT program_id, name, degree_level, total_ects, faculty_id
     FROM StudyProgram;`
  );
  return r.rows as StudyProgramRow[];
}

async function loadFaculties(db: DB): Promise<FacultyRow[]> {
  const r = await db.query(
    `SELECT faculty_id, faculty_key, url
     FROM Faculty
     WHERE faculty_key IS NOT NULL;`
  );
  return r.rows as FacultyRow[];
}

function pickBestCandidates(
  programs: StudyProgramRow[],
  nameCandidate: string | null,
  degree: "Bachelor" | "Master" | "Doctorate" | null,
  ectsCandidates: number[]
): StudyProgramRow[] {
  let candidates = programs;

  if (degree) candidates = candidates.filter((p) => p.degree_level === degree);

  // If we have ECTS candidates, try strongest match first (exact total_ects)
  if (ectsCandidates.length > 0) {
    const exact = candidates.filter((p) => p.total_ects != null && ectsCandidates.includes(p.total_ects));
    if (exact.length > 0) candidates = exact;
  }

  if (!nameCandidate) return candidates;

  const tn = normalize(nameCandidate);
  const contains = candidates.filter((p) => normalize(p.name).includes(tn) || tn.includes(normalize(p.name)));
  if (contains.length > 0) return contains;

  // token overlap
  const tokens = tn.split(" ").filter((t) => t.length >= 3);
  const scored = candidates
    .map((p) => {
      const pn = normalize(p.name);
      let score = 0;
      for (const t of tokens) if (pn.includes(t)) score++;
      return { p, score };
    })
    .sort((a, b) => b.score - a.score);

  if (scored.length === 0) return [];
  const bestScore = scored[0].score;
  if (bestScore < 2) return []; // too weak
  return scored.filter((x) => x.score === bestScore).map((x) => x.p);
}

async function appendSourceHints(db: DB, programId: number, hintsToAdd: any[]): Promise<number> {
  const r = await db.query(`SELECT source_hints FROM StudyProgram WHERE program_id=$1;`, [programId]);
  const existing: any[] = r.rows[0]?.source_hints ?? [];

  const key = (h: any) => {
    const page = (h.page_url ?? "").toString();
    const docs = Array.isArray(h.documents) ? h.documents.map((d: any) => d?.url).filter(Boolean).sort().join("|") : "";
    const fac = (h.faculty_key ?? "").toString();
    return `${fac}::${page}::${docs}`;
  };

  const seen = new Set(existing.map(key));
  let added = 0;

  for (const h of hintsToAdd) {
    const k = key(h);
    if (seen.has(k)) continue;
    existing.push(h);
    seen.add(k);
    added++;
  }

  if (added > 0) {
    await db.query(`UPDATE StudyProgram SET source_hints=$2 WHERE program_id=$1;`, [programId, JSON.stringify(existing)]);
  }

  return added;
}

async function run() {
  const input = process.argv[2] ?? "scrapy_crawler/outputs/faculty_downloads";
  const inputAbs = path.resolve(input);

  const files = listHintFiles(inputAbs);

  const allHints: HintEntry[] = [];
  for (const f of files) {
    const arr = readJson<any[]>(f);
    for (const x of arr) allHints.push(x as HintEntry);
  }

  console.log(`Loaded hints: ${allHints.length} from ${files.length} file(s)`);

  const client = await DataAccessController.pool.connect();
  const db: DB = client;

  try {
    await db.query("BEGIN;");

    const programs = await loadPrograms(db);
    const faculties = await loadFaculties(db);

    let updatedRows = 0;
    let hintsAdded = 0;
    let updatedFaculty = 0;

    const unmatched: any[] = [];

    for (const h of allHints) {
      const urls = collectDocUrls(h);
      const texts = getAllTextCandidates(h);

      const derivedFacultyKey = deriveFacultyKeyFromHint(h);
      const resolvedFaculty = resolveFacultyByUrlsOrKey(faculties, derivedFacultyKey, [
        ...(h.page_url ? [h.page_url] : []),
        ...urls,
        ...texts.filter((t) => t.startsWith("http")),
      ]);

      const degree = deriveDegreeFromText(h.category ?? h.level ?? null, texts);
      const ectsFromHint =
        typeof h.ects === "number"
          ? [h.ects]
          : h.program && typeof h.program === "object" && typeof h.program.ects === "number"
          ? [h.program.ects]
          : [];
      const ectsCandidates = [...new Set([...ectsFromHint, ...extractEctsFromText(texts)])];

      // Match per-document if we can (better for entries that include multiple PDFs)
      const baseProgramName = getProgramNameCandidate(h);
      const matchCandidates = pickBestCandidates(programs, baseProgramName, degree, ectsCandidates);

      if (matchCandidates.length === 0) {
        unmatched.push({
          ...h,
          derived_faculty_key: derivedFacultyKey,
          derived_faculty_id: resolvedFaculty?.faculty_id ?? null,
          derived_degree: degree,
          derived_ects: ectsCandidates.length ? ectsCandidates : null,
          derived_program_name: baseProgramName,
          name_candidates: texts.slice(0, 12),
          doc_urls_sample: urls.slice(0, 6),
          resolved_faculty: resolvedFaculty
            ? { faculty_id: resolvedFaculty.faculty_id, faculty_key: resolvedFaculty.faculty_key, url: resolvedFaculty.url }
            : null,
        });
        continue;
      }

      // Apply updates to all best matches (usually 1; sometimes multiple if duplicates exist)
      for (const match of matchCandidates) {
        if (resolvedFaculty?.faculty_id) {
          const r = await db.query(
            `UPDATE StudyProgram
             SET faculty_id=$2
             WHERE program_id=$1
               AND (faculty_id IS NULL OR faculty_id <> $2);`,
            [match.program_id, resolvedFaculty.faculty_id]
          );
          updatedFaculty += r.rowCount ?? 0;
        }

        const hintPayload = {
          faculty_raw: (h.faculty ?? null),
          faculty_key: resolvedFaculty?.faculty_key ?? derivedFacultyKey ?? null,
          category: h.category ?? h.level ?? null,
          lang: h.lang ?? null,
          year: h.year ?? null,
          program_name: baseProgramName,
          degree: degree,
          ects_candidates: ectsCandidates,
          page_url:
            h.page_url ??
            (h.program && typeof h.program === "object"
              ? (h.program.page_url_de ?? h.program.page_url_en ?? h.program.page_url_fr ?? h.program.page_url ?? h.program.studienplan_url ?? null)
              : null),
          documents: (h.documents ?? []).map((d) => ({ url: d.url, label: d.label ?? null, source_type: d.source_type ?? null })),
        };

        const added = await appendSourceHints(db, match.program_id, [hintPayload]);
        if (added > 0) {
          hintsAdded += added;
          updatedRows += 1;
        }
      }
    }

    await db.query("COMMIT;");

    const unmatchedPath = path.join(inputAbs, "_unmatched_source_hints.json");
    fs.writeFileSync(unmatchedPath, JSON.stringify(unmatched, null, 2), "utf-8");

    console.log(`✅ StudyProgram rows updated (hints merged): ${updatedRows}`);
    console.log(`✅ Hints appended (deduped): ${hintsAdded}`);
    console.log(`✅ StudyProgram faculty_id updated: ${updatedFaculty}`);
    console.log(`⚠️ Unmatched hints written: ${unmatchedPath}`);
    console.log(`ℹ️ Next: update doc-import to resolve by URLs inside StudyProgram.source_hints.`);
  } catch (e) {
    await db.query("ROLLBACK;");
    throw e;
  } finally {
    client.release();
    await DataAccessController.pool.end();
  }
}

run().catch((e) => {
  console.error("❌ Import failed:", e);
  process.exit(1);
});
