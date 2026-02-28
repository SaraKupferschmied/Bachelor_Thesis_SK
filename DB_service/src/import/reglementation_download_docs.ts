import "../environments/environment";

import fs from "fs";
import path from "path";
import crypto from "crypto";
import axios from "axios";
import { CookieJar } from "tough-cookie";
import { wrapper } from "axios-cookiejar-support";

type SpiderRow = {
  title?: string | null;
  tree?: string | null;
  source?: string | null;     // document page url
  pdf_url?: string | null;    // direct pdf url
  notes?: string | null;
  pdf_filename?: string | null;
};

export type ReglementationDocManifestItem = {
  reg_doc_key: string;

  tree: string;
  title: string;

  document_page_url: string;
  pdf_url: string | null;

  local_path: string | null;
  sha256: string | null;
  fetched_at: string | null;

  status: "downloaded" | "already_present" | "failed";
  notes?: string | null;
};

function ensureDir(p: string) {
  fs.mkdirSync(p, { recursive: true });
}

function collapseWs(s: string) {
  return s.replace(/\s+/g, " ").trim();
}

function safeFileName(s: string) {
  return s.replace(/[^a-z0-9._-]+/gi, "_").replace(/^_+|_+$/g, "").slice(0, 160);
}

function sha256Buf(buf: Buffer): string {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

function sha1String(s: string): string {
  return crypto.createHash("sha1").update(s, "utf-8").digest("hex");
}

function getArg(flag: string): string | null {
  const idx = process.argv.indexOf(flag);
  if (idx < 0) return null;
  const v = process.argv[idx + 1];
  if (!v || v.startsWith("--")) return null;
  return v;
}

function isPdfMagic(buf: Buffer) {
  // %PDF-
  return buf.length >= 5 && buf[0] === 0x25 && buf[1] === 0x50 && buf[2] === 0x44 && buf[3] === 0x46 && buf[4] === 0x2d;
}

/**
 * Parses:
 * - JSONL (one JSON object per line)
 * - JSON array/object
 * - OR your "as-is" format: multiple JSON objects separated by commas (not wrapped in [])
 *   including optional trailing commas.
 */
function parseLooseJsonObjects(inputPath: string): any[] {
  const raw = fs.readFileSync(inputPath, "utf-8").trim();
  if (!raw) return [];

  // JSONL: try line-by-line first if file ends with .jsonl
  if (inputPath.toLowerCase().endsWith(".jsonl")) {
    const out: any[] = [];
    for (const line of raw.split(/\r?\n/)) {
      const t = line.trim();
      if (!t) continue;
      try {
        out.push(JSON.parse(t));
      } catch {
        // ignore bad lines
      }
    }
    return out;
  }

  // Strict JSON: array or object
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return parsed;
    if (parsed && typeof parsed === "object") {
      if (Array.isArray((parsed as any).items)) return (parsed as any).items;
      if (Array.isArray((parsed as any).documents)) return (parsed as any).documents;
    }
    // single object
    return [parsed];
  } catch {
    // continue
  }

  // Loose mode: extract top-level {...} objects from a comma-separated stream
  const out: any[] = [];
  let i = 0;
  while (i < raw.length) {
    // find next '{'
    const start = raw.indexOf("{", i);
    if (start < 0) break;

    let depth = 0;
    let inStr = false;
    let esc = false;
    let end = -1;

    for (let j = start; j < raw.length; j++) {
      const ch = raw[j];

      if (inStr) {
        if (esc) {
          esc = false;
        } else if (ch === "\\") {
          esc = true;
        } else if (ch === '"') {
          inStr = false;
        }
        continue;
      } else {
        if (ch === '"') {
          inStr = true;
          continue;
        }
        if (ch === "{") depth++;
        if (ch === "}") depth--;
        if (depth === 0) {
          end = j;
          break;
        }
      }
    }

    if (end < 0) break;

    const chunk = raw.slice(start, end + 1);
    try {
      out.push(JSON.parse(chunk));
    } catch {
      // ignore malformed objects
    }
    i = end + 1;
  }

  return out;
}

function normalizeRow(r: any): { title: string; tree: string; documentPageUrl: string; pdfUrl: string | null; notes: string | null } | null {
  if (!r || typeof r !== "object") return null;
  const row = r as SpiderRow;

  const title = collapseWs(String(row.title ?? "")).trim();
  const tree = collapseWs(String(row.tree ?? "")).trim();
  const documentPageUrl = collapseWs(String(row.source ?? "")).trim();
  const pdfUrl = row.pdf_url ? collapseWs(String(row.pdf_url)).trim() : null;
  const notes = row.notes ? collapseWs(String(row.notes)) : null;

  if (!title || !documentPageUrl) return null;
  return { title, tree, documentPageUrl, pdfUrl, notes };
}

function extractDocId(documentPageUrl: string): string | null {
  const m = documentPageUrl.match(/\/document\/(\d+)/i);
  return m?.[1] ?? null;
}

/**
 * Try to find a PDF URL inside the HTML.
 */
function extractPdfUrlFromHtml(html: string, pageUrl: string): string | null {
  const candidates: string[] = [];

  // 1) direct .pdf in HTML
  const pdfRx = /https?:\/\/[^\s"'<>]+\.pdf(\?[^\s"'<>]+)?/gi;
  for (const m of html.matchAll(pdfRx)) candidates.push(m[0]);

  // 2) any url containing "download" or "pdf"
  const genericRx = /(https?:\/\/[^\s"'<>]+(?:download|pdf)[^\s"'<>]*)/gi;
  for (const m of html.matchAll(genericRx)) candidates.push(m[0]);

  // 3) relative href/src containing "download" or "pdf"
  const relRx = /(?:href|src)\s*=\s*"(\/[^"]*(?:download|pdf)[^"]*)"/gi;
  for (const m of html.matchAll(relRx)) {
    try {
      candidates.push(new URL(m[1], pageUrl).toString());
    } catch {}
  }

  const uniq = Array.from(new Set(candidates.map((x) => x.trim()).filter(Boolean)));

  uniq.sort((a, b) => {
    const score = (u: string) => {
      const s = u.toLowerCase();
      let k = 0;
      if (s.endsWith(".pdf") || s.includes(".pdf?")) k += 6;
      if (s.includes("/download/") || s.includes("download")) k += 4;
      if (s.includes("/legal/")) k += 1;
      return k;
    };
    return score(b) - score(a);
  });

  return uniq[0] ?? null;
}

function buildPdfEndpointCandidates(documentPageUrl: string): string[] {
  const id = extractDocId(documentPageUrl);
  if (!id) return [];

  let base: URL;
  try {
    base = new URL(documentPageUrl);
  } catch {
    return [];
  }

  const origin = base.origin; // https://webapps.unifr.ch
  const lang = (documentPageUrl.match(/\/legal\/(de|fr)\//i)?.[1] ?? "de").toLowerCase();

  // These are guesses; validated by magic bytes
  return [
    `${origin}/legal/${lang}/download/${id}`,        // <— this one is correct for your examples
    `${origin}/legal/${lang}/document/${id}/pdf`,
    `${origin}/legal/${lang}/document/${id}/download`,
    `${origin}/legal/${lang}/document/${id}?download=pdf`,
    `${origin}/legal/${lang}/document/${id}?format=pdf`,
  ];
}

async function fetchPdfBytes(http: any, url: string): Promise<{ buf: Buffer; finalUrl: string; contentType: string }> {
  const resp = await http.get(url, {
    responseType: "arraybuffer",
    headers: { Accept: "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8" },
    validateStatus: (s: number) => s >= 200 && s < 400,
  });

  const buf = Buffer.from(resp.data as ArrayBuffer);
  const ct = String(resp.headers?.["content-type"] ?? "");
  const finalUrl = (resp.request?.res?.responseUrl as string | undefined) ?? url;
  return { buf, finalUrl, contentType: ct };
}

async function resolvePdfCandidates(http: any, documentPageUrl: string): Promise<{ urls: string[]; notes: string | null }> {
  // Try HTML extraction first (when pdf_url missing), then fallbacks
  try {
    const htmlResp = await http.get(documentPageUrl, {
      responseType: "text",
      headers: { Accept: "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8" },
      validateStatus: (s: number) => s >= 200 && s < 400,
    });

    const html = String(htmlResp.data ?? "");
    const fromHtml = extractPdfUrlFromHtml(html, documentPageUrl);
    if (fromHtml) {
      const fallbacks = buildPdfEndpointCandidates(documentPageUrl);
      const urls = [fromHtml, ...fallbacks.filter((u) => u !== fromHtml)];
      return { urls, notes: "pdf url extracted from html (plus fallbacks)" };
    }
  } catch {
    // ignore, use fallbacks below
  }

  const fallbacks = buildPdfEndpointCandidates(documentPageUrl);
  return { urls: fallbacks, notes: fallbacks.length ? "no pdf in html; trying fallback endpoints" : "could not build fallback endpoints" };
}

async function run() {
  const inputPath = getArg("--input") ?? path.resolve(process.cwd(), "reglementation_docs.json"); // can be json/jsonl/loose
  const outRoot = getArg("--out") ?? path.resolve(process.cwd(), "outputs/reglementation_docs");
  const concurrency = Number(getArg("--concurrency") ?? "6");

  if (!fs.existsSync(inputPath)) throw new Error(`Input not found: ${inputPath}`);

  ensureDir(outRoot);
  const pdfDir = path.join(outRoot, "pdfs");
  ensureDir(pdfDir);

  const rawRows = parseLooseJsonObjects(inputPath);
  const rows = rawRows.map(normalizeRow).filter(Boolean) as {
    title: string;
    tree: string;
    documentPageUrl: string;
    pdfUrl: string | null;
    notes: string | null;
  }[];

  if (!rows.length) throw new Error(`No usable rows found in ${inputPath}. Expected objects with at least {title, source}.`);

  const jar = new CookieJar();
  const http = wrapper(
    axios.create({
      jar,
      withCredentials: true,
      timeout: 60_000,
      maxRedirects: 5,
      headers: { "User-Agent": "Mozilla/5.0 (compatible; UniFR-reglementation-downloader/1.0)" },
    })
  );

  const manifest: ReglementationDocManifestItem[] = [];
  const queue = rows.map((r) => ({ ...r }));

  async function worker() {
    while (queue.length) {
      const item = queue.shift();
      if (!item) return;

      const { title, tree, documentPageUrl, pdfUrl, notes } = item;
      const reg_doc_key = sha1String(documentPageUrl);

      // Use TITLE as filename (as requested) + short hash suffix to avoid collisions
      const baseName = safeFileName(`${title}_${reg_doc_key.slice(0, 10)}.pdf`);
      const localPath = path.join(pdfDir, baseName);

      const rec: ReglementationDocManifestItem = {
        reg_doc_key,
        tree,
        title,
        document_page_url: documentPageUrl,
        pdf_url: pdfUrl,
        local_path: localPath,
        sha256: null,
        fetched_at: null,
        status: "failed",
        notes: notes ?? null,
      };

      // already present?
      if (fs.existsSync(localPath) && fs.statSync(localPath).size > 1000) {
        const buf = fs.readFileSync(localPath);
        if (isPdfMagic(buf)) {
          rec.sha256 = sha256Buf(buf);
          rec.fetched_at = new Date().toISOString();
          rec.status = "already_present";
          rec.notes = rec.notes ?? "file already present on disk";
          manifest.push(rec);
          console.log(`↩️  Already present: ${path.basename(localPath)}`);
          continue;
        }
      }

      try {
        const tryUrls: string[] = [];

        if (pdfUrl) {
          tryUrls.push(pdfUrl);
          rec.notes = rec.notes ?? "pdf url from spider output";
        } else {
          const resolved = await resolvePdfCandidates(http, documentPageUrl);
          tryUrls.push(...resolved.urls);
          rec.notes = rec.notes ?? resolved.notes;
        }

        // If still nothing, one more fallback: build endpoints directly
        if (!tryUrls.length) {
          tryUrls.push(...buildPdfEndpointCandidates(documentPageUrl));
          rec.notes = rec.notes ?? "no candidates found; using endpoint guesses";
        }

        let downloaded: { buf: Buffer; finalUrl: string; contentType: string } | null = null;
        let lastNote: string | null = rec.notes ?? null;

        for (const u of Array.from(new Set(tryUrls))) {
          try {
            const got = await fetchPdfBytes(http, u);
            if (isPdfMagic(got.buf)) {
              downloaded = got;
              rec.pdf_url = got.finalUrl;
              lastNote = lastNote ?? "download ok";
              break;
            } else {
              lastNote = `not pdf from ${u} (content-type=${got.contentType})`;
            }
          } catch (e: any) {
            lastNote = `failed ${u}: ${e?.message ?? String(e)}`;
          }
        }

        if (!downloaded) {
          rec.status = "failed";
          rec.local_path = null;
          rec.notes = lastNote ?? "could not download a valid PDF from any candidate url";
          manifest.push(rec);
          console.warn(`❌ Failed: ${documentPageUrl} :: ${rec.notes}`);
          continue;
        }

        fs.writeFileSync(localPath, downloaded.buf);
        rec.sha256 = sha256Buf(downloaded.buf);
        rec.fetched_at = new Date().toISOString();
        rec.status = "downloaded";
        rec.notes = lastNote;
        manifest.push(rec);

        console.log(`✅ Downloaded: ${path.basename(localPath)}`);
      } catch (e: any) {
        rec.status = "failed";
        rec.local_path = null;
        rec.notes = e?.message ?? String(e);
        manifest.push(rec);
        console.warn(`❌ Failed: ${documentPageUrl} :: ${rec.notes}`);
      }
    }
  }

  const workers = Array.from({ length: Math.max(1, concurrency) }, () => worker());
  await Promise.all(workers);

  manifest.sort((a, b) => a.tree.localeCompare(b.tree) || a.title.localeCompare(b.title));

  const manifestPath = path.join(outRoot, "_reglementation_docs_manifest.json");
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), "utf-8");

  const ok = manifest.filter((m) => m.status === "downloaded" || m.status === "already_present").length;
  const failed = manifest.filter((m) => m.status === "failed").length;

  console.log(`\nDone. ok=${ok} failed=${failed}`);
  console.log(`Manifest: ${manifestPath}`);
  console.log(`PDFs: ${pdfDir}`);
}

run().catch((e) => {
  console.error("❌ Downloader failed:", e);
  process.exit(1);
});