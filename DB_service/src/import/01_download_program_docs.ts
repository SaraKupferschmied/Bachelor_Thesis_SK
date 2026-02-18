import "../environments/environment";

import fs from "fs";
import path from "path";
import crypto from "crypto";
import axios from "axios";

type StudyplanEntry = {
  faculty?: string;            // e.g. "SES", "SCIMED" :contentReference[oaicite:4]{index=4}
  category?: string;           // e.g. "bachelor", "master", "nebenfach" :contentReference[oaicite:5]{index=5}
  lang?: string;
  year?: number;
  ects?: number | null;
  program?: any;               // SES uses program object, SCIMED uses program string :contentReference[oaicite:6]{index=6}
  documents?: { url: string; label?: string; source_type?: string }[];
  files?: { url: string; path: string; checksum?: string; status?: string }[];
};

type ProgramDocManifestItem = {
  faculty: string | null;
  category: string | null;
  program_name: string | null;
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

function pickProgramName(entry: StudyplanEntry): string | null {
  // SCIMED example uses `program` as string; SES/Theology uses object with name_de etc. :contentReference[oaicite:7]{index=7}
  const p = entry.program;
  if (!p) return null;
  if (typeof p === "string") return p.trim() || null;

  const name =
    (p.name_de ?? p.name_en ?? p.name_fr ?? p.name ?? "").toString().trim();
  return name || null;
}

function detectSourceType(url: string): "pdf" | "calameo" | "unknown" {
  const u = url.toLowerCase();
  if (u.includes("calameo.com/read/")) return "calameo";
  if (u.endsWith(".pdf") || u.includes(".pdf?")) return "pdf";
  return "unknown";
}

/** Returns absolute file paths to JSON files from a list of args (files or folders). */
function expandJsonInputs(args: string[]): string[] {
  const out: string[] = [];

  for (const a of args) {
    const abs = path.resolve(process.cwd(), a);
    if (!fs.existsSync(abs)) continue;

    const stat = fs.statSync(abs);

    if (stat.isDirectory()) {
      // load all jsons in this folder (non-recursive; make recursive if you want)
      const files = fs
        .readdirSync(abs)
        .filter((f) => f.toLowerCase().endsWith(".json"))
        // optional: only studyplans
        .filter((f) => f.toLowerCase().includes("studyplan") || f.toLowerCase().includes("studyplans"))
        .map((f) => path.join(abs, f));

      out.push(...files);
    } else if (stat.isFile() && abs.toLowerCase().endsWith(".json")) {
      out.push(abs);
    }
  }

  // de-duplicate
  return Array.from(new Set(out));
}

/**
 * Best-effort: try to find a direct PDF URL from a Calaméo read page.
 * If Calaméo download is disabled, there may be no direct PDF.
 * (Calaméo says download requires publisher authorization.) :contentReference[oaicite:8]{index=8}
 */
async function tryGetCalameoDirectPdfUrl(readUrl: string): Promise<string | null> {
  const html = (await axios.get(readUrl, { responseType: "text" })).data as string;

  // Some pages embed something like ... ".pdf" links or a "downloadUrl".
  const pdfMatch =
    html.match(/https?:\/\/[^"' ]+\.pdf(\?[^"' ]*)?/i) ??
    html.match(/"downloadUrl"\s*:\s*"([^"]+)"/i);

  if (!pdfMatch) return null;

  const candidate = (pdfMatch[1] ?? pdfMatch[0]).replace(/\\u002F/g, "/").replace(/\\\//g, "/");
  if (!candidate.toLowerCase().includes(".pdf")) return null;
  return candidate;
}

async function downloadToFile(url: string, outPath: string): Promise<void> {
  const resp = await axios.get(url, { responseType: "arraybuffer", maxRedirects: 5 });
  fs.writeFileSync(outPath, Buffer.from(resp.data));
}

async function run() {
  const rawArgs = process.argv.slice(2);
  const inputPaths = expandJsonInputs(rawArgs);

  if (inputPaths.length === 0) {
    throw new Error(
      "Usage: ts-node 01_download_program_docs.ts <studyplans.json|folder> [more...]"
    );
  }

  const outRoot = path.resolve(process.cwd(), "./scrapy_crawler/outputs/faculty_downloads");
  const outFull = path.join(outRoot, "full");
  ensureDir(outFull);

  const manifest: ProgramDocManifestItem[] = [];

  for (const abs of inputPaths) {
    const raw = fs.readFileSync(abs, "utf-8");
    const entries: StudyplanEntry[] = JSON.parse(raw);

    for (const entry of entries) {
      const faculty = entry.faculty ?? null;
      const category = entry.category ?? null;
      const program_name = pickProgramName(entry);

      // If crawler already downloaded files with a `path`, reuse them (SCIMED-style). :contentReference[oaicite:9]{index=9}
      const alreadyDownloadedByUrl = new Map<string, string>();
      (entry.files ?? []).forEach((f) => {
        if (f?.url && f?.path) alreadyDownloadedByUrl.set(f.url, f.path);
      });

      for (const doc of entry.documents ?? []) {
        const source_url = doc.url;
        const doc_label = doc.label?.trim() ?? null;
        const source_type = detectSourceType(source_url);

        // 1) reuse existing crawler file if present
        const existingRel = alreadyDownloadedByUrl.get(source_url);
        if (existingRel) {
          const localAbs = path.join(outRoot, existingRel);
          if (fs.existsSync(localAbs)) {
            manifest.push({
              faculty,
              category,
              program_name,
              doc_label,
              source_url,
              source_type,
              local_path: localAbs,
              sha256: sha256File(localAbs),
              fetched_at: new Date().toISOString(),
              status: "already_present",
              notes: "reused entry.files[].path",
            });
            continue;
          }
        }

        // 2) decide output file name
        const urlHash = crypto.createHash("sha1").update(source_url).digest("hex");
        const baseName = safeFileName(`${faculty ?? "UNK"}_${category ?? "UNK"}_${program_name ?? "UNK"}_${doc_label ?? "doc"}_${urlHash}.pdf`);
        const outPath = path.join(outFull, baseName);

        if (fs.existsSync(outPath)) {
          manifest.push({
            faculty,
            category,
            program_name,
            doc_label,
            source_url,
            source_type,
            local_path: outPath,
            sha256: sha256File(outPath),
            fetched_at: new Date().toISOString(),
            status: "already_present",
            notes: "file already existed on disk",
          });
          continue;
        }

        try {
          if (source_type === "pdf") {
            await downloadToFile(source_url, outPath);
            manifest.push({
              faculty,
              category,
              program_name,
              doc_label,
              source_url,
              source_type,
              local_path: outPath,
              sha256: sha256File(outPath),
              fetched_at: new Date().toISOString(),
              status: "downloaded",
              notes: null,
            });
            continue;
          }

          if (source_type === "calameo") {
            const direct = await tryGetCalameoDirectPdfUrl(source_url);
            if (!direct) {
              manifest.push({
                faculty,
                category,
                program_name,
                doc_label,
                source_url,
                source_type,
                local_path: null,
                sha256: null,
                fetched_at: new Date().toISOString(),
                status: "calameo_no_direct_pdf",
                notes: "No direct PDF found; publisher likely disabled download",
              });
              continue;
            }
            await downloadToFile(direct, outPath);
            manifest.push({
              faculty,
              category,
              program_name,
              doc_label,
              source_url,
              source_type,
              local_path: outPath,
              sha256: sha256File(outPath),
              fetched_at: new Date().toISOString(),
              status: "downloaded",
              notes: `downloaded via discovered direct pdf: ${direct}`,
            });
            continue;
          }

          manifest.push({
            faculty,
            category,
            program_name,
            doc_label,
            source_url,
            source_type,
            local_path: null,
            sha256: null,
            fetched_at: new Date().toISOString(),
            status: "skipped_non_pdf",
            notes: "not a pdf and not a calameo read link",
          });
        } catch (e: any) {
          manifest.push({
            faculty,
            category,
            program_name,
            doc_label,
            source_url,
            source_type,
            local_path: null,
            sha256: null,
            fetched_at: new Date().toISOString(),
            status: "failed",
            notes: e?.message ?? String(e),
          });
        }
      }
    }
  }

  const manifestPath = path.join(outRoot, "_program_docs_manifest.json");
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), "utf-8");
  console.log(`✅ Wrote manifest: ${manifestPath}`);
  console.log(
    `Stats: downloaded=${manifest.filter((x) => x.status === "downloaded").length
    } already_present=${manifest.filter((x) => x.status === "already_present").length
    } calameo_no_direct_pdf=${manifest.filter((x) => x.status === "calameo_no_direct_pdf").length
    } failed=${manifest.filter((x) => x.status === "failed").length}`
  );
}


run().catch((e) => {
  console.error("❌ Download failed:", e);
  process.exit(1);
});
