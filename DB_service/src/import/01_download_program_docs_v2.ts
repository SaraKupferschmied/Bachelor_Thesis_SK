import "../environments/environment";

import fs from "fs";
import path from "path";
import crypto from "crypto";
import axios from "axios";
import { CookieJar } from "tough-cookie";
import { wrapper } from "axios-cookiejar-support";

/**
 * Input: consolidated program JSON (program_links_with_ects_and_docs_enriched.json)
 * Output:
 *  - PDFs in <out>/pdfs/
 *  - Manifest at <out>/_program_docs_manifest.json
 *
 * Usage:
 *   ts-node 01_download_program_docs_v2.ts --input ./program_links_with_ects_and_docs_enriched.json --out ./scrapy_crawler/outputs/program_docs_v2
 */

type ProgramEntry = {
  programme_name_en?: string | null;
  programme_name_de?: string | null;
  programme_name_fr?: string | null;
  programme?: string | null;

  level?: string | null; // "B" | "M" | "D" in your file
  ects_points?: number | string | null;

  faculty?: string | null; // e.g. "SCIMED"
  faculties?: string[] | null;

  programme_url?: string | null;
  programme_url_en?: string | null;
  programme_url_de?: string | null;
  programme_url_fr?: string | null;

  curriculum_de_url?: string | null;
  curriculum_fr_url?: string | null;
  curriculum_en_url?: string | null;
  curriculum_unspecified_url?: string | null;

  documents?: { url: string; label?: string | null; source_type?: string | null }[] | null;
};

type ProgramDocManifestItem = {
  program_key: string;
  doc_key: string;

  faculty: string | null;
  degree_level: "Bachelor" | "Master" | "Doctorate" | null;
  total_ects: number | null;
  program_name: string | null;

  programme_url: string | null;
  curriculum_url: string | null;

  doc_label: string | null;
  source_url: string;
  source_type: "pdf" | "calameo" | "unknown";

  local_path: string | null;
  sha256: string | null;
  fetched_at: string | null;
  status:
    | "downloaded"
    | "already_present"
    | "skipped_non_pdf"
    | "calameo_no_direct_pdf"
    | "failed";
  notes?: string | null;
};

const UA =
  process.env.USER_AGENT ??
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121 Safari/537.36";

function ensureDir(p: string) {
  fs.mkdirSync(p, { recursive: true });
}

function safeFileName(s: string) {
  return s.replace(/[^a-z0-9._-]+/gi, "_").slice(0, 180);
}

function sha256File(filePath: string): string {
  const hash = crypto.createHash("sha256");
  hash.update(fs.readFileSync(filePath));
  return hash.digest("hex");
}

function sha1(s: string): string {
  return crypto.createHash("sha1").update(s).digest("hex");
}

function pickProgramName(e: ProgramEntry): string | null {
  const name =
    (e.programme_name_en ?? e.programme_name_de ?? e.programme_name_fr ?? e.programme ?? "")
      .toString()
      .trim();
  return name || null;
}

function parseDegreeLevel(level: any): "Bachelor" | "Master" | "Doctorate" | null {
  const v = (level ?? "").toString().trim().toLowerCase();
  if (!v) return null;
  if (v === "b" || v.startsWith("bachelor")) return "Bachelor";
  if (v === "m" || v.startsWith("master")) return "Master";
  if (v === "d" || v.startsWith("doctor")) return "Doctorate";
  return null;
}

function parseNumberMaybe(x: any): number | null {
  if (x === null || x === undefined) return null;
  const n = typeof x === "number" ? x : Number(String(x).replace(",", ".").trim());
  return Number.isFinite(n) ? n : null;
}

function normalizeUrl(u: string | null | undefined): string | null {
  const s = (u ?? "").trim();
  return s ? s : null;
}

function pickCurriculumUrl(e: ProgramEntry): string | null {
  return (
    normalizeUrl(e.curriculum_unspecified_url) ??
    normalizeUrl(e.curriculum_en_url) ??
    normalizeUrl(e.curriculum_de_url) ??
    normalizeUrl(e.curriculum_fr_url)
  );
}

function isCalameoUrl(u: string): boolean {
  try {
    const h = new URL(u).hostname.toLowerCase();
    return h.endsWith("calameo.com");
  } catch {
    return false;
  }
}

function detectSourceType(url: string): "pdf" | "calameo" | "unknown" {
  const u = url.toLowerCase();
  if (isCalameoUrl(u) && (u.includes("/read/") || u.includes("/books/") || u.includes("/download/"))) return "calameo";
  if (u.endsWith(".pdf") || u.includes(".pdf?")) return "pdf";
  return "unknown";
}

function toCalameoReadUrl(anyCalameoUrl: string): string {
  try {
    const u = new URL(anyCalameoUrl);
    if (u.hostname.includes("calameo.com")) {
      // Convert /books/<id> -> /read/<id>
      u.pathname = u.pathname.replace(/^\/books\//i, "/read/");
      return u.toString();
    }
  } catch {
    // ignore
  }
  return anyCalameoUrl;
}

function looksLikePdf(buf: Buffer) {
  // "%PDF"
  return buf.length >= 4 && buf[0] === 0x25 && buf[1] === 0x50 && buf[2] === 0x44 && buf[3] === 0x46;
}

/**
 * Cookie-jar axios client (same as the working file).
 * Important: Calaméo downloads often require having visited the viewer page first.
 */
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
      "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    },
    validateStatus: (s) => s >= 200 && s < 400,
  })
);

/**
 * Extract Calaméo ID from:
 * - /read/<id>
 * - /download/<id>
 * - query bkcode=<id>
 */
function extractCalameoIdLoose(u: string): string | null {
  try {
    const url = new URL(u);
    const parts = url.pathname.split("/").filter(Boolean);

    const readIdx = parts.indexOf("read");
    if (readIdx !== -1 && parts[readIdx + 1]) return parts[readIdx + 1];

    const dlIdx = parts.indexOf("download");
    if (dlIdx !== -1 && parts[dlIdx + 1]) return parts[dlIdx + 1];

    const bk = url.searchParams.get("bkcode");
    if (bk) return bk;

    return null;
  } catch {
    return null;
  }
}

/**
 * HTML fallback extraction (from the working file).
 */
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

/**
 * Download with redirects and validate PDF bytes.
 */
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

/**
 * Core Calaméo logic from the working file:
 * - derive id from URL if possible
 * - fetch viewer HTML once (for cookies + fallback id extraction)
 * - build https://www.calameo.com/download/<id>?bkcode=<id>
 * - download using referer=viewerUrl
 */
async function downloadCalameoToFile(sourceUrl: string, outPath: string): Promise<{ finalUrl: string; contentType: string; id: string }> {
  // normalize viewer url
  const viewerUrl = toCalameoReadUrl(sourceUrl);

  // 1) try id from URL
  let id = extractCalameoIdLoose(sourceUrl) ?? extractCalameoIdLoose(viewerUrl);

  // 2) fetch viewer HTML at least once (cookies/session + possible bkcode in HTML)
  let html = "";
  try {
    const r = await client.get(viewerUrl, { responseType: "text", validateStatus: (s) => s >= 200 && s < 400 });
    html = String(r.data ?? "");
  } catch {
    // ignore (some links may fail; we can still try if we already have id)
  }

  if ((!id || id.length < 10) && html) {
    const fromHtml = extractCalameoBkcodeFromHtml(html);
    if (fromHtml) id = fromHtml;
  }

  if (!id) {
    throw new Error("Calaméo URL detected but no bkcode/id could be extracted");
  }

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
    if (activeCount >= concurrency) {
      await new Promise<void>((resolve) => queue.push(resolve));
    }
    activeCount++;
    try {
      return await fn();
    } finally {
      next();
    }
  };

  return run;
}

async function run() {
  const input = getArg("--input") ?? process.argv[2];
  if (!input) {
    throw new Error(
      "Usage: ts-node 01_download_program_docs_v2.ts --input <program_links_with_ects_and_docs_enriched.json> [--out <folder>] [--concurrency N]"
    );
  }

  const defaultOut = path.resolve(process.cwd(), "./scrapy_crawler/outputs/program_docs_v2");
  const outRoot = path.resolve(process.cwd(), getArg("--out") ?? defaultOut);
  const outPdfs = path.join(outRoot, "pdfs");
  ensureDir(outPdfs);

  const concurrency = getArgInt("--concurrency", 6);
  const limit = pLimit(Math.max(1, concurrency));

  const raw = fs.readFileSync(path.resolve(process.cwd(), input), "utf-8");
  const entries: ProgramEntry[] = JSON.parse(raw);

  const manifest: ProgramDocManifestItem[] = [];

  type Job = { manifestItem: ProgramDocManifestItem; outPath: string };
  const jobs: Job[] = [];

  for (const e of entries) {
    const faculty = e.faculty ?? (e.faculties?.[0] ?? null);
    const degree_level = parseDegreeLevel(e.level);
    const total_ects = parseNumberMaybe(e.ects_points);
    const program_name = pickProgramName(e);

    const programme_url =
      normalizeUrl(e.programme_url) ??
      normalizeUrl(e.programme_url_en) ??
      normalizeUrl(e.programme_url_de) ??
      normalizeUrl(e.programme_url_fr);

    const curriculum_url = pickCurriculumUrl(e);

    const program_key = [
      (faculty ?? "").trim().toLowerCase(),
      (degree_level ?? "").trim().toLowerCase(),
      String(total_ects ?? "").trim(),
      (program_name ?? "").trim().toLowerCase(),
    ].join("|");

    for (const doc of e.documents ?? []) {
      if (!doc?.url) continue;

      const source_url = doc.url.trim();
      const doc_label = (doc.label ?? "").toString().trim() || null;
      const source_type = detectSourceType(source_url);
      const doc_key = sha1(source_url.toLowerCase());

      const item: ProgramDocManifestItem = {
        program_key,
        doc_key,
        faculty,
        degree_level,
        total_ects,
        program_name,
        programme_url,
        curriculum_url,
        doc_label,
        source_url,
        source_type,
        local_path: null,
        sha256: null,
        fetched_at: null,
        status: "failed",
        notes: null,
      };

      const baseName = safeFileName(
        `${faculty ?? "UNK"}_${degree_level ?? "UNK"}_${total_ects ?? "UNK"}_${program_name ?? "UNK"}_${doc_label ?? "doc"}_${doc_key}.pdf`
      );
      const outPath = path.join(outPdfs, baseName);

      if (fs.existsSync(outPath)) {
        item.local_path = outPath;
        item.sha256 = sha256File(outPath);
        item.fetched_at = new Date().toISOString();
        item.status = "already_present";
        item.notes = "file already existed on disk";
        manifest.push(item);
        continue;
      }

      if (source_type === "unknown") {
        item.status = "skipped_non_pdf";
        item.fetched_at = new Date().toISOString();
        item.notes = "not a pdf and not a calameo link";
        manifest.push(item);
        continue;
      }

      jobs.push({ manifestItem: item, outPath });
    }
  }

  console.log(`Found ${jobs.length} docs to download (concurrency=${concurrency}).`);

  await Promise.all(
    jobs.map((job) =>
      limit(async () => {
        const m = job.manifestItem;
        try {
          if (m.source_type === "pdf") {
            // Use the same session client too; safer for sites requiring cookies
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
              m.local_path = null;
              m.sha256 = null;
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
          m.local_path = null;
          m.sha256 = null;
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

  const manifestPath = path.join(outRoot, "_program_docs_manifest.json");
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), "utf-8");

  const stats = (k: ProgramDocManifestItem["status"]) => manifest.filter((x) => x.status === k).length;

  console.log(`✅ Wrote manifest: ${manifestPath}`);
  console.log(
    `Stats: downloaded=${stats("downloaded")} already_present=${stats("already_present")} skipped_non_pdf=${stats(
      "skipped_non_pdf"
    )} calameo_no_direct_pdf=${stats("calameo_no_direct_pdf")} failed=${stats("failed")}`
  );
}

run().catch((e) => {
  console.error("❌ Download failed:", e);
  process.exit(1);
});