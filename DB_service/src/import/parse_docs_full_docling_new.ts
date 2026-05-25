import fs from 'fs';
import path from 'path';
import os from 'os';
import { spawnSync } from 'child_process';

type ProgramMetadata = {
  degree_level?: 'Bachelor' | 'Master' | 'Doctorate' | string | null;
  raw_level?: string | null;
  total_ects?: number | null;
  programme_name_en?: string | null;
  programme_name_de?: string | null;
  programme_name_fr?: string | null;
  programme_url?: string | null;
  programme_url_en?: string | null;
  programme_url_de?: string | null;
  programme_url_fr?: string | null;
  curriculum_url?: string | null;
  curriculum_de_url?: string | null;
  curriculum_fr_url?: string | null;
  curriculum_en_url?: string | null;
  curriculum_unspecified_url?: string | null;
  [key: string]: unknown;
};

type ProgramDocManifestItem = {
  program_key: string;
  doc_key: string;
  faculty: string | null;
  degree_level: 'Bachelor' | 'Master' | 'Doctorate' | null;
  total_ects: number | null;
  program_name: string | null;
  programme_url: string | null;
  curriculum_url: string | null;
  doc_label: string | null;
  source_url: string;
  source_type: 'pdf' | 'calameo' | 'unknown';
  local_path: string | null;
  sha256: string | null;
  fetched_at: string | null;
  status: 'downloaded' | 'already_present' | 'skipped_non_pdf' | 'calameo_no_direct_pdf' | 'failed';
  notes?: string | null;
  program_metadata?: ProgramMetadata | null;
  document_metadata?: Record<string, unknown> | null;
};

type DoclingJson = {
  status: string;
  parser: string;
  title?: string | null;
  markdown: string;
  pages?: string[];
  headings?: Array<{ level: number; text: string; line_index?: number }>;
  tables?: Array<unknown>;
  meta?: Record<string, unknown>;
};

type ParsedDocIndexRow = {
  doc_key: string;
  parse_status: 'ok' | 'failed' | 'skipped_existing';
  parse_notes: string | null;
  parsed_at: string;
  local_path: string | null;
  output_path: string;
  raw_json_path: string;
  title: string | null;
  pages: number;
  sha256: string | null;
  source_url: string | null;
  source_type: string | null;
  program_key: string | null;

  level: string | null;
  ects_points: number | null;
  programme_name_en: string | null;
  programme_name_de: string | null;
  programme_name_fr: string | null;

  doc_label: string | null;
  programme_url: string | null;
  curriculum_url: string | null;
  fetched_at: string | null;
};

type DocGroup = {
  doc_key: string;
  items: ProgramDocManifestItem[];
  representative: ProgramDocManifestItem;
  resolved_pdf_path: string | null;
};

function ensureDir(p: string) {
  fs.mkdirSync(p, { recursive: true });
}

function getArg(flag: string): string | null {
  const idx = process.argv.indexOf(flag);
  if (idx < 0) return null;
  const v = process.argv[idx + 1];
  if (!v || v.startsWith('--')) return null;
  return v;
}

function hasFlag(flag: string) {
  return process.argv.includes(flag);
}

function collapseWs(s: string) {
  return s.replace(/\s+/g, ' ').trim();
}

function atomicWriteText(filePath: string, content: string) {
  ensureDir(path.dirname(filePath));
  const tmp = `${filePath}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(tmp, content, 'utf-8');
  fs.renameSync(tmp, filePath);
}

function atomicWriteJson(filePath: string, value: unknown) {
  atomicWriteText(filePath, JSON.stringify(value, null, 2));
}

function atomicWriteJsonl(filePath: string, rows: unknown[]) {
  const body = rows.map((r) => JSON.stringify(r)).join('\n') + (rows.length ? '\n' : '');
  atomicWriteText(filePath, body);
}

function normalizeCandidatePath(p: string) {
  return p.replace(/\\/g, path.sep);
}

function resolvePdfPath(item: ProgramDocManifestItem, outRoot: string): string | null {
  const candidates: string[] = [];

  if (item.local_path) {
    candidates.push(item.local_path);
    candidates.push(normalizeCandidatePath(item.local_path));
    candidates.push(path.join(outRoot, 'pdfs', path.basename(normalizeCandidatePath(item.local_path))));
  }

  if (item.doc_key) {
    candidates.push(path.join(outRoot, 'pdfs', `${item.doc_key}.pdf`));
  }

  if (item.sha256) {
    candidates.push(path.join(outRoot, 'pdfs', `${item.sha256}.pdf`));
  }

  for (const candidate of candidates) {
    try {
      if (candidate && fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
        return candidate;
      }
    } catch {
      // ignore broken candidates
    }
  }

  return null;
}

function itemScore(item: ProgramDocManifestItem, resolved: string | null) {
  let score = 0;
  if (resolved) score += 100;
  if (item.status === 'downloaded' || item.status === 'already_present') score += 20;
  if (item.source_type === 'pdf' || item.source_type === 'calameo') score += 5;
  if (item.sha256) score += 5;
  if (item.program_name) score += 2;
  if (item.faculty) score += 1;
  if (item.degree_level) score += 1;
  return score;
}

function pickRepresentative(items: ProgramDocManifestItem[], outRoot: string): { rep: ProgramDocManifestItem; resolved: string | null } {
  const ranked = items
    .map((item) => ({ item, resolved: resolvePdfPath(item, outRoot) }))
    .sort((a, b) => itemScore(b.item, b.resolved) - itemScore(a.item, a.resolved));

  return { rep: ranked[0].item, resolved: ranked[0].resolved };
}

function groupManifest(manifest: ProgramDocManifestItem[], outRoot: string): DocGroup[] {
  const okStatuses = new Set<ProgramDocManifestItem['status']>(['downloaded', 'already_present']);
  const byDocKey = new Map<string, ProgramDocManifestItem[]>();

  for (const item of manifest) {
    if (!okStatuses.has(item.status)) continue;
    if (!item.doc_key) continue;
    const arr = byDocKey.get(item.doc_key) ?? [];
    arr.push(item);
    byDocKey.set(item.doc_key, arr);
  }

  const groups: DocGroup[] = [];
  for (const [docKey, items] of byDocKey.entries()) {
    const { rep, resolved } = pickRepresentative(items, outRoot);
    groups.push({
      doc_key: docKey,
      items,
      representative: rep,
      resolved_pdf_path: resolved,
    });
  }

  groups.sort((a, b) => a.doc_key.localeCompare(b.doc_key));
  return groups;
}

function getProgramMeta(item: ProgramDocManifestItem): ProgramMetadata {
  return item.program_metadata ?? {};
}

function firstString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === 'string' && value.trim().length > 0) return value.trim();
  }
  return null;
}

function firstNumber(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
  }
  return null;
}

function buildCleanMetadata(group: DocGroup, title: string | null, pages: number) {
  const rep = group.representative;
  const pm = getProgramMeta(rep);

  return {
    parsed_at: new Date().toISOString(),
    title,
    pages,
    parser: 'docling',
    doc_key: group.doc_key,
    program_key: rep.program_key,

    programme_name_en: firstString(pm.programme_name_en),
    programme_name_de: firstString(pm.programme_name_de),
    programme_name_fr: firstString(pm.programme_name_fr),
    level: firstString(pm.degree_level, rep.degree_level),
    ects_points: firstNumber(pm.total_ects, rep.total_ects),

    doc_label: rep.doc_label,
    source_url: rep.source_url,
    source_type: rep.source_type,
    programme_url: firstString(pm.programme_url, rep.programme_url),
    programme_url_en: firstString(pm.programme_url_en),
    programme_url_de: firstString(pm.programme_url_de),
    programme_url_fr: firstString(pm.programme_url_fr),
    curriculum_url: firstString(pm.curriculum_url, rep.curriculum_url),
    curriculum_de_url: firstString(pm.curriculum_de_url),
    curriculum_fr_url: firstString(pm.curriculum_fr_url),
    curriculum_en_url: firstString(pm.curriculum_en_url),
    curriculum_unspecified_url: firstString(pm.curriculum_unspecified_url),
    local_path: group.resolved_pdf_path,
    sha256: rep.sha256,
    fetched_at: rep.fetched_at,
    notes: rep.notes ?? null,
  };
}

function renderHeader(group: DocGroup, title: string | null, pages: number) {
  const headerObj = buildCleanMetadata(group, title, pages);

  return `---METADATA_JSON---\n${JSON.stringify(headerObj, null, 2)}\n---/METADATA_JSON---\n\n`;
}

function renderParsedText(group: DocGroup, parsed: DoclingJson) {
  const pagesArr = parsed.pages && parsed.pages.some((p) => collapseWs(p).length > 0)
    ? parsed.pages
    : [parsed.markdown ?? ''];

  const pm = getProgramMeta(group.representative);
  const title = collapseWs(parsed.title || '') || firstString(pm.programme_name_en, pm.programme_name_de, pm.programme_name_fr, group.representative.program_name) || null;
  const header = renderHeader(group, title, pagesArr.length);
  const body = pagesArr
    .map((pageText, i) => `---PAGE ${i + 1}---\n${pageText ?? ''}\n`)
    .join('\n');

  return { text: header + body, title, pages: pagesArr.length };
}

function parseWithDocling(pdfPath: string, helperPath: string, pythonExec: string): DoclingJson {
  const tempOut = path.join(os.tmpdir(), `.docling_${Date.now()}_${Math.random().toString(36).slice(2)}.json`);

  const proc = spawnSync(pythonExec, [helperPath, pdfPath, '--out', tempOut], {
    encoding: 'utf-8',
    stdio: 'pipe',
    maxBuffer: 50 * 1024 * 1024,
  });

  if (proc.status !== 0) {
    const msg = (proc.stderr || proc.stdout || `exit=${proc.status}`).trim();
    throw new Error(`Docling helper failed: ${msg}`);
  }

  const raw = fs.readFileSync(tempOut, 'utf-8');
  fs.unlinkSync(tempOut);
  return JSON.parse(raw) as DoclingJson;
}

function runPreflight(helperPath: string, pythonExec: string) {
  if (!fs.existsSync(helperPath)) {
    throw new Error(`Missing helper script: ${helperPath}`);
  }

  const proc = spawnSync(pythonExec, [helperPath, '--preflight'], {
    encoding: 'utf-8',
    stdio: 'pipe',
    maxBuffer: 10 * 1024 * 1024,
  });

  if (proc.status !== 0) {
    const msg = (proc.stderr || proc.stdout || `exit=${proc.status}`).trim();
    throw new Error(`Docling preflight failed: ${msg}`);
  }
}

function tryReadExistingSummary(txtPath: string): { title: string | null; pages: number } {
  try {
    const raw = fs.readFileSync(txtPath, 'utf-8');
    const match = raw.match(/---METADATA_JSON---\n([\s\S]*?)\n---\/METADATA_JSON---/);
    if (!match) return { title: null, pages: 0 };
    const meta = JSON.parse(match[1]);
    return {
      title: typeof meta.title === 'string' ? meta.title : null,
      pages: typeof meta.pages === 'number' ? meta.pages : 0,
    };
  } catch {
    return { title: null, pages: 0 };
  }
}

function loadExistingIndex(indexPath: string): Map<string, ParsedDocIndexRow> {
  const map = new Map<string, ParsedDocIndexRow>();
  if (!fs.existsSync(indexPath)) return map;

  const lines = fs.readFileSync(indexPath, 'utf-8').split(/\r?\n/).filter(Boolean);
  for (const line of lines) {
    try {
      const row = JSON.parse(line) as ParsedDocIndexRow;
      if (row?.doc_key) map.set(row.doc_key, row);
    } catch {
      // ignore malformed rows from older runs
    }
  }
  return map;
}

function buildIndexRow(
  group: DocGroup,
  parseStatus: ParsedDocIndexRow['parse_status'],
  parseNotes: string | null,
  outputPath: string,
  rawJsonPath: string,
  title: string | null,
  pages: number,
): ParsedDocIndexRow {
  const rep = group.representative;
  const pm = getProgramMeta(rep);

  return {
    doc_key: group.doc_key,
    parse_status: parseStatus,
    parse_notes: parseNotes,
    parsed_at: new Date().toISOString(),
    local_path: group.resolved_pdf_path,
    output_path: outputPath,
    raw_json_path: rawJsonPath,
    title,
    pages,
    sha256: rep.sha256,
    source_url: rep.source_url,
    source_type: rep.source_type,
    program_key: rep.program_key,

    programme_name_en: firstString(pm.programme_name_en),
    programme_name_de: firstString(pm.programme_name_de),
    programme_name_fr: firstString(pm.programme_name_fr),
    level: firstString(pm.degree_level, rep.degree_level),
    ects_points: firstNumber(pm.total_ects, rep.total_ects),

    doc_label: rep.doc_label,
    programme_url: firstString(pm.programme_url, rep.programme_url),
    curriculum_url: firstString(pm.curriculum_url, rep.curriculum_url),
    fetched_at: rep.fetched_at,
  };
}

function buildRowFromExisting(group: DocGroup, outputPath: string, rawJsonPath: string): ParsedDocIndexRow {
  const summary = tryReadExistingSummary(outputPath);
  return buildIndexRow(group, 'skipped_existing', 'already_parsed', outputPath, rawJsonPath, summary.title, summary.pages);
}

function saveRowMap(indexPath: string, rowMap: Map<string, ParsedDocIndexRow>) {
  const rows = [...rowMap.values()].sort((a, b) => a.doc_key.localeCompare(b.doc_key));
  atomicWriteJsonl(indexPath, rows);
}

async function run() {
  const outRoot = getArg('--root') ?? path.resolve(process.cwd(), 'scrapy_crawler/outputs');
  const manifestPath = getArg('--manifest') ?? path.join(outRoot, '_program_docs_manifest.json');
  const parsedDir = getArg('--parsed-dir') ?? path.join(outRoot, 'parsed_fulltext_docling_new');
  const helperPath = getArg('--docling-helper') ?? path.resolve(process.cwd(), 'parse_with_docling_robust.py');
  const pythonExec = getArg('--python') ?? 'python';
  const reparseExisting = hasFlag('--reparse-existing');

  ensureDir(parsedDir);

  if (!fs.existsSync(manifestPath)) {
    throw new Error(`Missing manifest at: ${manifestPath}`);
  }

  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf-8')) as ProgramDocManifestItem[];
  const groups = groupManifest(manifest, outRoot);
  const indexPath = path.join(parsedDir, '_index.jsonl');
  const rawDir = path.join(parsedDir, '_raw_docling_json');
  const dedupManifestPath = path.join(parsedDir, '_manifest_dedup.json');
  const summaryPath = path.join(parsedDir, '_run_summary.json');
  const rowMap = loadExistingIndex(indexPath);

  ensureDir(rawDir);
  atomicWriteJson(dedupManifestPath, groups.map((g) => {
    const pm = getProgramMeta(g.representative);
    return {
      doc_key: g.doc_key,
      resolved_pdf_path: g.resolved_pdf_path,
      program_key: g.representative.program_key,
      programme_name_en: firstString(pm.programme_name_en),
      programme_name_de: firstString(pm.programme_name_de),
      programme_name_fr: firstString(pm.programme_name_fr),
      level: firstString(pm.degree_level, g.representative.degree_level),
      ects_points: firstNumber(pm.total_ects, g.representative.total_ects),
    };
  }));

  const needsParse = groups.some((g) => {
    const out = path.join(parsedDir, `${g.doc_key}.txt`);
    return !fs.existsSync(out) || reparseExisting;
  });
  if (needsParse) {
    runPreflight(helperPath, pythonExec);
  }

  let ok = 0;
  let failed = 0;
  let skippedExisting = 0;

  for (const group of groups) {
    const outputPath = path.join(parsedDir, `${group.doc_key}.txt`);
    const rawJsonPath = path.join(rawDir, `${group.doc_key}.json`);

    if (!group.resolved_pdf_path) {
      failed += 1;
      const row = buildIndexRow(
        group,
        'failed',
        'PDF not found via manifest local_path or outputs/pdfs basename fallback',
        outputPath,
        rawJsonPath,
        null,
        0,
      );
      rowMap.set(group.doc_key, row);
      saveRowMap(indexPath, rowMap);
      console.warn(`⚠️ Missing PDF for doc_key=${group.doc_key}`);
      continue;
    }

    if (!reparseExisting && fs.existsSync(outputPath)) {
      skippedExisting += 1;
      const row = buildRowFromExisting(group, outputPath, rawJsonPath);
      rowMap.set(group.doc_key, row);
      saveRowMap(indexPath, rowMap);
      console.log(`⏭️  Existing parse kept: ${group.doc_key}`);
      continue;
    }

    try {
      const parsed = parseWithDocling(group.resolved_pdf_path, helperPath, pythonExec);
      const rendered = renderParsedText(group, parsed);
      atomicWriteJson(rawJsonPath, parsed);
      atomicWriteText(outputPath, rendered.text);

      ok += 1;
      const row = buildIndexRow(group, 'ok', null, outputPath, rawJsonPath, rendered.title, rendered.pages);
      rowMap.set(group.doc_key, row);
      saveRowMap(indexPath, rowMap);
      console.log(`✅ Parsed ${path.basename(group.resolved_pdf_path)} -> ${group.doc_key}.txt`);
    } catch (e: any) {
      failed += 1;
      const row = buildIndexRow(
        group,
        'failed',
        e?.message ?? String(e),
        outputPath,
        rawJsonPath,
        null,
        0,
      );
      rowMap.set(group.doc_key, row);
      saveRowMap(indexPath, rowMap);
      console.warn(`⚠️ Failed parsing ${group.resolved_pdf_path}: ${row.parse_notes}`);
    }
  }

  const summary = {
    manifest_path: manifestPath,
    out_root: outRoot,
    parsed_dir: parsedDir,
    helper_path: helperPath,
    python: pythonExec,
    generated_at: new Date().toISOString(),
    total_manifest_rows: manifest.length,
    unique_doc_keys: groups.length,
    ok,
    skipped_existing: skippedExisting,
    failed,
  };

  atomicWriteJson(summaryPath, summary);
  console.log('\nDone.');
  console.log(JSON.stringify(summary, null, 2));
}

run().catch((e) => {
  console.error('❌ Parse failed:', e);
  process.exit(1);
});
