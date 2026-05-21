//import "../environments/environment";

import fs from "fs";
import path from "path";
import crypto from "crypto";
import axios from "axios";
import { CookieJar } from "tough-cookie";
import { wrapper } from "axios-cookiejar-support";

/**
 * Downloads faculty/programme documents from the new spider outputs.
 *
 * Supports two input shapes:
 *  1) Matched programme file:
 *     programmes_with_faculty_documents_patched.json
 *     -> programme entries with nested `documents[]`
 *  2) Unmatched faculty docs file:
 *     unmatched_faculty_documents_remaining.json
 *     -> flat document entries
 *
 * Output:
 *  - PDFs in <out>/pdfs/
 *  - Manifest at <out>/_faculty_docs_manifest.json
 *
 * Usage:
 *   ts-node 01_download_faculty_docs_v3.ts \
 *     --matched-input ./scrapy_crawler/scrapy_crawler/spider_outputs/programmes_with_faculty_documents_patched.json \
 *     --unmatched-input ./scrapy_crawler/scrapy_crawler/spider_outputs/unmatched_faculty_documents_remaining.json \
 *     --out ./scrapy_crawler/outputs/faculty_docs_v3
 *
 * Backwards compatible shortcut:
 *   ts-node 01_download_faculty_docs_v3.ts --input ./programmes_with_faculty_documents_patched.json
 */

type DegreeLevel = "Bachelor" | "Master" | "Doctorate" | null;
type ManifestStatus =
  | "downloaded"
  | "already_present"
  | "skipped_non_pdf"
  | "calameo_no_direct_pdf"
  | "failed";
type SourceType = "pdf" | "calameo" | "unknown";
type MatchScope = "matched_programme_document" | "unmatched_faculty_document";

type FacultyDocument = {
  url?: string | null;
  label?: string | null;
  source_type?: string | null;

  faculty?: string | null;
  language?: string | null;
  category?: string | null;
  level?: string | null;
  year?: number | string | null;
  ects?: number | string | null;
  ects_values?: Array<number | string> | null;

  page_url?: string | null;
  document_url?: string | null;
  document_label?: string | null;
  file_url?: string | null;

  path?: string | null;
  checksum?: string | null;
  file_status?: string | null;
  source_file?: string | null;
  source_index?: number | string | null;

  match_score?: number | string | null;
  match_reasons?: string[] | null;

  program_name?: string | null;
  program_name_variants?: string[] | null;
};

type ProgramEntry = {
  programme_name_en?: string | null;
  programme_name_de?: string | null;
  programme_name_fr?: string | null;
  programme?: string | null;

  level?: string | null; // "B" | "M" | "D"
  ects_points?: number | string | null;
  min_semesters?: number | string | null;
  studyplan_metadata?: Record<string, unknown> | null;

  faculty?: string | null;
  faculties?: string[] | null;
  department?: string | null;
  study_director?: string | null;
  contact_mail?: string | null;

  programme_url?: string | null;
  programme_url_en?: string | null;
  programme_url_de?: string | null;
  programme_url_fr?: string | null;

  curriculum_de_url?: string | null;
  curriculum_fr_url?: string | null;
  curriculum_en_url?: string | null;
  curriculum_unspecified_url?: string | null;

  document_match_count?: number | null;
  matched_sources?: string[] | null;
  documents?: FacultyDocument[] | null;
};

type ProgramDocManifestItem = {
  match_scope: MatchScope;
  program_key: string | null;
  doc_key: string;

  // Programme metadata. Filled for matched documents, null for unmatched ones.
  program_metadata: {
    faculty: string | null;
    degree_level: DegreeLevel;
    raw_level: string | null;
    total_ects: number | null;
    program_name: string | null;
    programme_name_en: string | null;
    programme_name_de: string | null;
    programme_name_fr: string | null;
    min_semesters: number | null;
    department: string | null;
    study_director: string | null;
    contact_mail: string | null;
    programme_url: string | null;
    programme_url_en: string | null;
    programme_url_de: string | null;
    programme_url_fr: string | null;
    curriculum_url: string | null;
    curriculum_de_url: string | null;
    curriculum_fr_url: string | null;
    curriculum_en_url: string | null;
    curriculum_unspecified_url: string | null;
    studyplan_metadata: Record<string, unknown> | null;
  } | null;

  // Faculty-document metadata. This is intentionally richer than the old manifest.
  document_metadata: {
    faculty: string | null;
    language: string | null;
    category: string | null;
    raw_level: string | null;
    degree_level: DegreeLevel;
    year: number | null;
    ects: number | null;
    ects_values: number[];
    program_name: string | null;
    program_name_variants: string[];
    doc_label: string | null;
    source_type_raw: string | null;
    page_url: string | null;
    document_url: string | null;
    file_url: string | null;
    existing_spider_path: string | null;
    existing_spider_checksum: string | null;
    existing_spider_file_status: string | null;
    source_file: string | null;
    source_index: number | null;
    match_score: number | null;
    match_reasons: string[];
  };

  source_url: string;
  source_type: SourceType;

  local_path: string | null;
  sha256: string | null;
  fetched_at: string | null;
  status: ManifestStatus;
  notes?: string | null;
};

type Job = { manifestItem: ProgramDocManifestItem; outPath: string };

const UA =
  process.env.USER_AGENT ??
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121 Safari/537.36";

function ensureDir(p: string) {
  fs.mkdirSync(p, { recursive: true });
}

function safeFileName(s: string) {
  return s.replace(/[^a-z0-9._-]+/gi, "_").replace(/^_+|_+$/g, "").slice(0, 180);
}

function sha256File(filePath: string): string {
  const hash = crypto.createHash("sha256");
  hash.update(fs.readFileSync(filePath));
  return hash.digest("hex");
}

function sha1(s: string): string {
  return crypto.createHash("sha1").update(s).digest("hex");
}

function parseDegreeLevel(level: unknown): DegreeLevel {
  const v = (level ?? "").toString().trim().toLowerCase();
  if (!v) return null;
  if (v === "b" || v.startsWith("bachelor")) return "Bachelor";
  if (v === "m" || v.startsWith("master")) return "Master";
  if (v === "d" || v.startsWith("doctor") || v.startsWith("phd")) return "Doctorate";
  return null;
}

function parseNumberMaybe(x: unknown): number | null {
  if (x === null || x === undefined || x === "") return null;
  const n = typeof x === "number" ? x : Number(String(x).replace(",", ".").trim());
  return Number.isFinite(n) ? n : null;
}

function parseNumberArray(xs: unknown): number[] {
  if (!Array.isArray(xs)) return [];
  return xs.map(parseNumberMaybe).filter((x): x is number => x !== null);
}

function normalizeUrl(u: string | null | undefined): string | null {
  const s = (u ?? "").trim();
  return s ? s : null;
}

function pickProgramName(e: ProgramEntry): string | null {
  const name = (e.programme_name_en ?? e.programme_name_de ?? e.programme_name_fr ?? e.programme ?? "")
    .toString()
    .trim();
  return name || null;
}

function pickCurriculumUrl(e: ProgramEntry): string | null {
  return (
    normalizeUrl(e.curriculum_unspecified_url) ??
    normalizeUrl(e.curriculum_en_url) ??
    normalizeUrl(e.curriculum_de_url) ??
    normalizeUrl(e.curriculum_fr_url)
  );
}

function pickProgrammeUrl(e: ProgramEntry): string | null {
  return (
    normalizeUrl(e.programme_url) ??
    normalizeUrl(e.programme_url_en) ??
    normalizeUrl(e.programme_url_de) ??
    normalizeUrl(e.programme_url_fr)
  );
}

function pickDocumentUrl(doc: FacultyDocument): string | null {
  return normalizeUrl(doc.file_url) ?? normalizeUrl(doc.document_url) ?? normalizeUrl(doc.url);
}

function pickDocumentLabel(doc: FacultyDocument): string | null {
  return ((doc.document_label ?? doc.label ?? "").toString().trim() || null);
}

function isCalameoUrl(u: string): boolean {
  try {
    const h = new URL(u).hostname.toLowerCase();
    return h.endsWith("calameo.com");
  } catch {
    return false;
  }
}

function detectSourceType(url: string): SourceType {
  const u = url.toLowerCase();
  if (isCalameoUrl(url) && (u.includes("/read/") || u.includes("/books/") || u.includes("/download/"))) return "calameo";
  if (u.endsWith(".pdf") || u.includes(".pdf?") || u.includes(".pdf#")) return "pdf";
  return "unknown";
}

function toCalameoReadUrl(anyCalameoUrl: string): string {
  try {
    const u = new URL(anyCalameoUrl);
    if (u.hostname.includes("calameo.com")) {
      u.pathname = u.pathname.replace(/^\/books\//i, "/read/");
      return u.toString();
    }
  } catch {}
  return anyCalameoUrl;
}

function looksLikePdf(buf: Buffer) {
  return buf.length >= 4 && buf[0] === 0x25 && buf[1] === 0x50 && buf[2] === 0x44 && buf[3] === 0x46;
}

const jar = new CookieJar();
const client = wrapper(
  axios.create({
    jar,
    withCredentials: true,
    maxRedirects: 10,
    timeout: 60_000,
    headers: {
      "User-Agent": UA,
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8,de;q=0.7",
    },
    validateStatus: (s) => s >= 200 && s < 400,
  })
);

function extractCalameoIdLoose(u: string): string | null {
  try {
    const url = new URL(u);
    const parts = url.pathname.split("/").filter(Boolean);
    const readIdx = parts.indexOf("read");
    if (readIdx !== -1 && parts[readIdx + 1]) return parts[readIdx + 1];
    const dlIdx = parts.indexOf("download");
    if (dlIdx !== -1 && parts[dlIdx + 1]) return parts[dlIdx + 1];
    return url.searchParams.get("bkcode");
  } catch {
    return null;
  }
}

function extractCalameoBkcodeFromHtml(html: string): string | null {
  const patterns = [
    /bkcode["']?\s*[:=]\s*["']([0-9a-f]{10,})["']/i,
    /"bkcode"\s*:\s*"([0-9a-f]{10,})"/i,
    /\/download\/([0-9a-f]{10,})\?/i,
    /"document"\s*:\s*\{\s*"id"\s*:\s*"([0-9a-f]{10,})"/i,
    /"bookKey"\s*:\s*"([0-9a-f]{10,})"/i,
  ];
  for (const re of patterns) {
    const m = html.match(re);
    if (m?.[1]) return m[1];
  }
  return null;
}

function calameoDownloadUrlFromBkcode(id: string) {
  return `https://www.calameo.com/download/${id}?bkcode=${id}`;
}

async function downloadFileFollowRedirects(
  url: string,
  outFile: string,
  referer?: string
): Promise<{ finalUrl: string; contentType: string; isPdf: boolean }> {
  const r = await client.get(url, {
    responseType: "arraybuffer",
    headers: {
      "User-Agent": UA,
      Accept: "application/pdf,application/octet-stream,*/*",
      ...(referer ? { Referer: referer } : {}),
    },
    validateStatus: (s) => s >= 200 && s < 400,
  });

  const ct = String((r.headers as any)["content-type"] ?? "");
  const finalUrl = (r.request?.res?.responseUrl as string | undefined) ?? url;
  const dataBuf = Buffer.from(r.data);
  const isPdf = ct.toLowerCase().includes("pdf") || looksLikePdf(dataBuf);

  if (isPdf) {
    fs.mkdirSync(path.dirname(outFile), { recursive: true });
    fs.writeFileSync(outFile, dataBuf);
  }

  return { finalUrl, contentType: ct, isPdf };
}

async function downloadCalameoToFile(sourceUrl: string, outPath: string): Promise<{ finalUrl: string; contentType: string; id: string }> {
  const viewerUrl = toCalameoReadUrl(sourceUrl);
  let id = extractCalameoIdLoose(sourceUrl) ?? extractCalameoIdLoose(viewerUrl);

  let html = "";
  try {
    const r = await client.get(viewerUrl, { responseType: "text", validateStatus: (s) => s >= 200 && s < 400 });
    html = String(r.data ?? "");
  } catch {}

  if ((!id || id.length < 10) && html) {
    id = extractCalameoBkcodeFromHtml(html) ?? id;
  }

  if (!id) throw new Error("Calaméo URL detected but no bkcode/id could be extracted");

  const dlUrl = calameoDownloadUrlFromBkcode(id);
  const { finalUrl, contentType, isPdf } = await downloadFileFollowRedirects(dlUrl, outPath, viewerUrl);
  if (!isPdf) {
    try { fs.unlinkSync(outPath); } catch {}
    throw new Error(`Calaméo download did not return a PDF (content-type=${contentType || "unknown"}, final=${finalUrl})`);
  }

  return { finalUrl, contentType, id };
}

function getArg(flag: string): string | null {
  const idx = process.argv.indexOf(flag);
  if (idx < 0) return null;
  const v = process.argv[idx + 1];
  if (!v || v.startsWith("--")) return null;
  return v;
}

function getArgInt(flag: string, def: number): number {
  const v = getArg(flag);
  if (!v) return def;
  const n = Number(v);
  return Number.isFinite(n) ? n : def;
}

function pLimit(concurrency: number) {
  let activeCount = 0;
  const queue: (() => void)[] = [];
  const next = () => {
    activeCount--;
    if (queue.length > 0) queue.shift()!();
  };
  const run = async <T>(fn: () => Promise<T>): Promise<T> => {
    if (activeCount >= concurrency) await new Promise<void>((resolve) => queue.push(resolve));
    activeCount++;
    try { return await fn(); } finally { next(); }
  };
  return run;
}

function buildProgramMetadata(e: ProgramEntry): NonNullable<ProgramDocManifestItem["program_metadata"]> {
  return {
    faculty: e.faculty ?? e.faculties?.[0] ?? null,
    degree_level: parseDegreeLevel(e.level),
    raw_level: e.level ?? null,
    total_ects: parseNumberMaybe(e.ects_points),
    program_name: pickProgramName(e),
    programme_name_en: e.programme_name_en ?? null,
    programme_name_de: e.programme_name_de ?? null,
    programme_name_fr: e.programme_name_fr ?? null,
    min_semesters: parseNumberMaybe(e.min_semesters),
    department: e.department ?? null,
    study_director: e.study_director ?? null,
    contact_mail: e.contact_mail ?? null,
    programme_url: pickProgrammeUrl(e),
    programme_url_en: normalizeUrl(e.programme_url_en),
    programme_url_de: normalizeUrl(e.programme_url_de),
    programme_url_fr: normalizeUrl(e.programme_url_fr),
    curriculum_url: pickCurriculumUrl(e),
    curriculum_de_url: normalizeUrl(e.curriculum_de_url),
    curriculum_fr_url: normalizeUrl(e.curriculum_fr_url),
    curriculum_en_url: normalizeUrl(e.curriculum_en_url),
    curriculum_unspecified_url: normalizeUrl(e.curriculum_unspecified_url),
    studyplan_metadata: e.studyplan_metadata ?? null,
  };
}

function buildDocumentMetadata(doc: FacultyDocument): ProgramDocManifestItem["document_metadata"] {
  return {
    faculty: doc.faculty ?? null,
    language: doc.language ?? null,
    category: doc.category ?? null,
    raw_level: doc.level ?? null,
    degree_level: parseDegreeLevel(doc.level),
    year: parseNumberMaybe(doc.year),
    ects: parseNumberMaybe(doc.ects),
    ects_values: parseNumberArray(doc.ects_values),
    program_name: doc.program_name ?? null,
    program_name_variants: Array.isArray(doc.program_name_variants) ? doc.program_name_variants : [],
    doc_label: pickDocumentLabel(doc),
    source_type_raw: doc.source_type ?? null,
    page_url: normalizeUrl(doc.page_url),
    document_url: normalizeUrl(doc.document_url),
    file_url: normalizeUrl(doc.file_url),
    existing_spider_path: doc.path ?? null,
    existing_spider_checksum: doc.checksum ?? null,
    existing_spider_file_status: doc.file_status ?? null,
    source_file: doc.source_file ?? null,
    source_index: parseNumberMaybe(doc.source_index),
    match_score: parseNumberMaybe(doc.match_score),
    match_reasons: Array.isArray(doc.match_reasons) ? doc.match_reasons : [],
  };
}

function buildProgramKey(pm: NonNullable<ProgramDocManifestItem["program_metadata"]>): string {
  return [
    (pm.faculty ?? "").trim().toLowerCase(),
    (pm.degree_level ?? "").trim().toLowerCase(),
    String(pm.total_ects ?? "").trim(),
    (pm.program_name ?? "").trim().toLowerCase(),
  ].join("|");
}

function buildManifestItem(scope: MatchScope, doc: FacultyDocument, program?: ProgramEntry): ProgramDocManifestItem | null {
  const source_url = pickDocumentUrl(doc);
  if (!source_url) return null;

  const pm = program ? buildProgramMetadata(program) : null;
  const dm = buildDocumentMetadata(doc);
  const program_key = pm ? buildProgramKey(pm) : null;

  // Include programme context in the doc key for matched docs so the same PDF can be represented
  // once per programme/level/ECTS mapping. For physical filenames, the sha keeps names stable.
  const doc_key_seed = scope === "matched_programme_document" ? `${program_key}|${source_url.toLowerCase()}` : source_url.toLowerCase();

  return {
    match_scope: scope,
    program_key,
    doc_key: sha1(doc_key_seed),
    program_metadata: pm,
    document_metadata: dm,
    source_url,
    source_type: detectSourceType(source_url),
    local_path: null,
    sha256: null,
    fetched_at: null,
    status: "failed",
    notes: null,
  };
}

function outPathForItem(outPdfs: string, item: ProgramDocManifestItem): string {
  const pm = item.program_metadata;
  const dm = item.document_metadata;
  const baseName = safeFileName([
    item.match_scope === "matched_programme_document" ? "matched" : "unmatched",
    pm?.faculty ?? dm.faculty ?? "UNK",
    pm?.degree_level ?? dm.degree_level ?? "UNK",
    pm?.total_ects ?? dm.ects ?? "UNK",
    pm?.program_name ?? dm.program_name ?? "UNK",
    dm.doc_label ?? "doc",
    item.doc_key,
  ].join("_")) + ".pdf";
  return path.join(outPdfs, baseName);
}

function loadJsonList<T>(inputPath: string): T[] {
  const raw = fs.readFileSync(path.resolve(process.cwd(), inputPath), "utf-8");
  const data = JSON.parse(raw);
  if (!Array.isArray(data)) throw new Error(`Expected JSON array in ${inputPath}`);
  return data as T[];
}

function enqueueItem(item: ProgramDocManifestItem, outPdfs: string, manifest: ProgramDocManifestItem[], jobs: Job[]) {
  const outPath = outPathForItem(outPdfs, item);

  if (fs.existsSync(outPath)) {
    item.local_path = outPath;
    item.sha256 = sha256File(outPath);
    item.fetched_at = new Date().toISOString();
    item.status = "already_present";
    item.notes = "file already existed on disk";
    manifest.push(item);
    return;
  }

  if (item.source_type === "unknown") {
    item.fetched_at = new Date().toISOString();
    item.status = "skipped_non_pdf";
    item.notes = "not a PDF URL and not a supported Calaméo URL";
    manifest.push(item);
    return;
  }

  jobs.push({ manifestItem: item, outPath });
}

async function run() {
  const input = getArg("--input") ?? process.argv[2] ?? null;
  const matchedInput = getArg("--matched-input") ?? input;
  const unmatchedInput = getArg("--unmatched-input");

  if (!matchedInput && !unmatchedInput) {
    throw new Error(
      "Usage: ts-node 01_download_faculty_docs_v3.ts --matched-input <programmes_with_faculty_documents_patched.json> --unmatched-input <unmatched_faculty_documents_remaining.json> [--out <folder>] [--concurrency N]"
    );
  }

  const defaultOut = path.resolve(process.cwd(), "./scrapy_crawler/outputs/faculty_docs_v3");
  const outRoot = path.resolve(process.cwd(), getArg("--out") ?? defaultOut);
  const outPdfs = path.join(outRoot, "pdfs");
  ensureDir(outPdfs);

  const concurrency = getArgInt("--concurrency", 6);
  const limit = pLimit(Math.max(1, concurrency));

  const manifest: ProgramDocManifestItem[] = [];
  const jobs: Job[] = [];

  if (matchedInput) {
    const entries = loadJsonList<ProgramEntry>(matchedInput);
    for (const e of entries) {
      for (const doc of e.documents ?? []) {
        const item = buildManifestItem("matched_programme_document", doc, e);
        if (item) enqueueItem(item, outPdfs, manifest, jobs);
      }
    }
  }

  if (unmatchedInput) {
    const docs = loadJsonList<FacultyDocument>(unmatchedInput);
    for (const doc of docs) {
      const item = buildManifestItem("unmatched_faculty_document", doc);
      if (item) enqueueItem(item, outPdfs, manifest, jobs);
    }
  }

  console.log(`Found ${jobs.length} docs to download (concurrency=${concurrency}).`);

  await Promise.all(
    jobs.map((job) =>
      limit(async () => {
        const m = job.manifestItem;
        try {
          if (m.source_type === "pdf") {
            const { finalUrl, contentType, isPdf } = await downloadFileFollowRedirects(m.source_url, job.outPath);
            if (!isPdf) {
              try { fs.unlinkSync(job.outPath); } catch {}
              throw new Error(`Not a PDF (content-type=${contentType || "unknown"}, final=${finalUrl})`);
            }
            m.local_path = job.outPath;
            m.sha256 = sha256File(job.outPath);
            m.fetched_at = new Date().toISOString();
            m.status = "downloaded";
            m.notes = `downloaded pdf (final=${finalUrl}, ct=${contentType || "unknown"})`;
            manifest.push(m);
            process.stdout.write(".");
            return;
          }

          if (m.source_type === "calameo") {
            try {
              const dl = await downloadCalameoToFile(m.source_url, job.outPath);
              m.local_path = job.outPath;
              m.sha256 = sha256File(job.outPath);
              m.fetched_at = new Date().toISOString();
              m.status = "downloaded";
              m.notes = `calameo downloaded (id=${dl.id}, final=${dl.finalUrl}, ct=${dl.contentType || "unknown"})`;
              manifest.push(m);
              process.stdout.write(".");
              return;
            } catch (e: any) {
              m.fetched_at = new Date().toISOString();
              m.status = "calameo_no_direct_pdf";
              m.notes = e?.message ?? String(e);
              manifest.push(m);
              process.stdout.write("c");
              return;
            }
          }

          m.fetched_at = new Date().toISOString();
          m.status = "skipped_non_pdf";
          m.notes = "unexpected source_type";
          manifest.push(m);
          process.stdout.write("s");
        } catch (e: any) {
          m.fetched_at = new Date().toISOString();
          m.status = "failed";
          m.notes = e?.message ?? String(e);
          manifest.push(m);
          process.stdout.write("x");
        }
      })
    )
  );

  process.stdout.write("\n");

  manifest.sort((a, b) => `${a.match_scope}|${a.program_key ?? ""}|${a.source_url}`.localeCompare(`${b.match_scope}|${b.program_key ?? ""}|${b.source_url}`));

  const manifestPath = path.join(outRoot, "_faculty_docs_manifest.json");
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), "utf-8");

  const stats = (k: ManifestStatus) => manifest.filter((x) => x.status === k).length;
  const matched = manifest.filter((x) => x.match_scope === "matched_programme_document").length;
  const unmatched = manifest.filter((x) => x.match_scope === "unmatched_faculty_document").length;

  console.log(`Wrote manifest: ${manifestPath}`);
  console.log(`Manifest rows: matched=${matched} unmatched=${unmatched} total=${manifest.length}`);
  console.log(
    `Stats: downloaded=${stats("downloaded")} already_present=${stats("already_present")} skipped_non_pdf=${stats("skipped_non_pdf")} calameo_no_direct_pdf=${stats("calameo_no_direct_pdf")} failed=${stats("failed")}`
  );
}

run().catch((e) => {
  console.error("Download failed:", e);
  process.exit(1);
});
