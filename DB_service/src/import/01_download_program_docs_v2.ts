import "../environments/environment";

import fs from "fs";
import path from "path";
import crypto from "crypto";
import axios from "axios";

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
  // stable identifiers for review & later import
  program_key: string; // derived from faculty|degree|ects|name
  doc_key: string;     // derived from url

  // program info (for convenience during review)
  faculty: string | null;
  degree_level: "Bachelor" | "Master" | "Doctorate" | null;
  total_ects: number | null;
  program_name: string | null;

  // source hints
  programme_url: string | null;
  curriculum_url: string | null;

  // doc info
  doc_label: string | null;
  source_url: string;
  source_type: "pdf" | "calameo" | "unknown";

  // download result
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

function detectSourceType(url: string): "pdf" | "calameo" | "unknown" {
  const u = url.toLowerCase();
  if (u.includes("calameo.com/read/")) return "calameo";
  if (u.endsWith(".pdf") || u.includes(".pdf?")) return "pdf";
  return "unknown";
}

/**
 * Best-effort: try to find a direct PDF URL from a Calaméo read page.
 * If Calaméo download is disabled, there may be no direct PDF.
 */
async function tryGetCalameoDirectPdfUrl(readUrl: string): Promise<string | null> {
  const html = (await axios.get(readUrl, { responseType: "text" })).data as string;

  const pdfMatch =
    html.match(/https?:\/\/[^"' ]+\.pdf(\?[^"' ]*)?/i) ??
    html.match(/"downloadUrl"\s*:\s*"([^"]+)"/i);

  if (!pdfMatch) return null;

  const candidate = (pdfMatch[1] ?? pdfMatch[0])
    .replace(/\\u002F/g, "/")
    .replace(/\\\//g, "/");

  if (!candidate.toLowerCase().includes(".pdf")) return null;
  return candidate;
}

async function downloadToFile(url: string, outPath: string): Promise<void> {
  const resp = await axios.get(url, { responseType: "arraybuffer", maxRedirects: 5, timeout: 60_000 });
  fs.writeFileSync(outPath, Buffer.from(resp.data));
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

function hasFlag(flag: string): boolean {
  return process.argv.includes(flag);
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

  // Keep same relative root convention as your old scripts.
  const defaultOut = path.resolve(process.cwd(), "./scrapy_crawler/outputs/program_docs_v2");
  const outRoot = path.resolve(process.cwd(), getArg("--out") ?? defaultOut);
  const outPdfs = path.join(outRoot, "pdfs");
  ensureDir(outPdfs);

  const concurrency = getArgInt("--concurrency", 6);
  const limit = pLimit(Math.max(1, concurrency));

  const raw = fs.readFileSync(path.resolve(process.cwd(), input), "utf-8");
  const entries: ProgramEntry[] = JSON.parse(raw);

  const manifest: ProgramDocManifestItem[] = [];

  // Prepare a flat list of download jobs so we can run with concurrency
  type Job = { manifestItem: ProgramDocManifestItem; outPath: string; resolvedUrl: string | null };
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

      // Output file name is stable by doc_key + a bit of context
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

      // resolve if calaméo
      if (source_type === "unknown") {
        item.status = "skipped_non_pdf";
        item.fetched_at = new Date().toISOString();
        item.notes = "not a pdf and not a calameo read link";
        manifest.push(item);
        continue;
      }

      jobs.push({ manifestItem: item, outPath, resolvedUrl: null });
    }
  }

  console.log(`Found ${jobs.length} docs to download (concurrency=${concurrency}).`);

  await Promise.all(
    jobs.map((job) =>
      limit(async () => {
        const m = job.manifestItem;
        try {
          let urlToFetch: string | null = m.source_url;
          if (m.source_type === "calameo") {
            const direct = await tryGetCalameoDirectPdfUrl(m.source_url);
            if (!direct) {
              m.local_path = null;
              m.sha256 = null;
              m.fetched_at = new Date().toISOString();
              m.status = "calameo_no_direct_pdf";
              m.notes = "No direct PDF found; publisher likely disabled download";
              manifest.push(m);
              return;
            }
            urlToFetch = direct;
            m.notes = `downloaded via discovered direct pdf: ${direct}`;
          }

          if (!urlToFetch) {
            m.fetched_at = new Date().toISOString();
            m.status = "failed";
            m.notes = "no url to fetch";
            manifest.push(m);
            return;
          }

          await downloadToFile(urlToFetch, job.outPath);
          m.local_path = job.outPath;
          m.sha256 = sha256File(job.outPath);
          m.fetched_at = new Date().toISOString();
          m.status = "downloaded";
          manifest.push(m);

          process.stdout.write(".");
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
    `Stats: downloaded=${stats("downloaded")} already_present=${stats("already_present")} skipped_non_pdf=${stats("skipped_non_pdf")} calameo_no_direct_pdf=${stats("calameo_no_direct_pdf")} failed=${stats("failed")}`
  );
}

run().catch((e) => {
  console.error("❌ Download failed:", e);
  process.exit(1);
});
