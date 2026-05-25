import fs from "fs";
import path from "path";

type ManifestItem = {
  doc_key: string;
  local_path: string | null;
  status: string;
  [key: string]: unknown;
};

function safeName(value: string): string {
  return value.replace(/[^a-z0-9._-]+/gi, "_").slice(0, 80);
}

const root = process.argv[2] ?? process.cwd();
const manifestPath = path.join(root, "_faculty_docs_manifest.json");
const pdfDir = path.join(root, "pdfs");
const backupPath = path.join(root, "_program_docs_manifest.backup.json");

if (!fs.existsSync(manifestPath)) {
  throw new Error(`Manifest not found: ${manifestPath}`);
}

const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8")) as ManifestItem[];

fs.copyFileSync(manifestPath, backupPath);

let renamed = 0;
let missing = 0;

for (const item of manifest) {
  if (!["downloaded", "already_present"].includes(item.status)) continue;
  if (!item.local_path || !item.doc_key) continue;

  const oldPath = path.join(pdfDir, path.basename(item.local_path));

  if (!fs.existsSync(oldPath)) {
    missing++;
    continue;
  }

  const ext = path.extname(oldPath) || ".pdf";
  const newName = safeName(item.doc_key) + ext;
  const newPath = path.join(pdfDir, newName);

  if (oldPath === newPath) continue;

  if (!fs.existsSync(newPath)) {
    fs.renameSync(oldPath, newPath);
    renamed++;
  }

  item.local_path = newPath;
}

fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2), "utf-8");

console.log(`Done.`);
console.log(`Renamed PDFs: ${renamed}`);
console.log(`Missing PDFs: ${missing}`);
console.log(`Backup manifest: ${backupPath}`);