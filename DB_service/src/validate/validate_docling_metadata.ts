import fs from "fs";
import path from "path";

type MatchStatus = "ok" | "mismatch" | "ambiguous";

type ReportRow = {
  file: string;
  doc_key: string | null;
  program_key: string | null;
  metadata_total_ects: number | null;
  detected_ects_values: number[];
  metadata_match_status: MatchStatus;
  title_sections_checked: string[];
};

function getArg(flag: string): string | null {
  const idx = process.argv.indexOf(flag);
  if (idx < 0) return null;
  const v = process.argv[idx + 1];
  if (!v || v.startsWith("--")) return null;
  return v;
}

function hasFlag(flag: string): boolean {
  return process.argv.includes(flag);
}

function listTxtFiles(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return listTxtFiles(full);
    if (entry.isFile() && entry.name.toLowerCase().endsWith(".txt")) return [full];
    return [];
  });
}

function uniqNums(arr: number[]): number[] {
  return [...new Set(arr)].sort((a, b) => a - b);
}

function normalizeWs(s: string): string {
  return s.replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
}

function stripHtmlComments(s: string): string {
  return s.replace(/<!--[\s\S]*?-->/g, " ");
}

function extractHeaderAndBody(raw: string): { header: any; body: string } {
  const m = raw.match(/^---METADATA_JSON---\s*\n([\s\S]*?)\n---\/METADATA_JSON---\s*\n?([\s\S]*)$/m);
  if (!m) throw new Error("Missing metadata block");
  return {
    header: JSON.parse(m[1]),
    body: m[2] ?? "",
  };
}

function renderFile(header: any, body: string): string {
  return `---METADATA_JSON---\n${JSON.stringify(header, null, 2)}\n---/METADATA_JSON---\n\n${body}`;
}

function getFirstFiveTitleSections(body: string): string[] {
  const lines = stripHtmlComments(body).split(/\r?\n/);
  const sections: string[] = [];

  for (const rawLine of lines) {
    const line = normalizeWs(rawLine);
    if (!line) continue;
    if (line.startsWith("##")) {
      sections.push(line);
      if (sections.length >= 5) break;
    }
  }

  return sections;
}

function extractECTSFromLine(line: string): number[] {
  const values: number[] = [];
  const patterns = [
    /(\d{2,3})\s*(?:ECTS|ECTS-Punkte|ECTS-Punkten|Kreditpunkte|credits?|crediti)\b/gi,
    /\b(?:ECTS|ECTS-Punkte|ECTS-Punkten|Kreditpunkte|credits?|crediti)\s*(\d{2,3})\b/gi,
  ];

  for (const re of patterns) {
    for (const m of line.matchAll(re)) {
      values.push(Number(m[1]));
    }
  }

  return uniqNums(values);
}

function main() {
  const dir = getArg("--dir") ?? process.argv[2] ?? path.resolve(process.cwd(), "scrapy_crawler/outputs/parsed_fulltext_docling");
  const write = hasFlag("--write");

  if (!fs.existsSync(dir)) {
    throw new Error(`Directory not found: ${dir}`);
  }

  const files = listTxtFiles(dir).filter((p) => path.basename(p) !== "_index.jsonl");

  let ok = 0;
  let mismatch = 0;
  let ambiguous = 0;
  let failed = 0;
  let rewritten = 0;

  const rows: ReportRow[] = [];

  for (const filePath of files) {
    try {
      const raw = fs.readFileSync(filePath, "utf-8");
      const { header, body } = extractHeaderAndBody(raw);

      const titleSections = getFirstFiveTitleSections(body);
      const detected = uniqNums(titleSections.flatMap(extractECTSFromLine));

      const metaEcts = typeof header.total_ects === "number" ? header.total_ects : null;
      let status: MatchStatus = "ambiguous";

      if (metaEcts !== null && detected.length >= 1) {
        if (detected.includes(metaEcts)) {
          status = "ok";
          ok++;
        } else {
          status = "mismatch";
          mismatch++;
        }
      } else {
        status = "ambiguous";
        ambiguous++;
      }

      const updatedHeader = {
        ...header,
        detected_ects_values: detected,
        metadata_match_status: status,
      };

      rows.push({
        file: path.basename(filePath),
        doc_key: header.doc_key ?? null,
        program_key: header.program_key ?? null,
        metadata_total_ects: metaEcts,
        detected_ects_values: detected,
        metadata_match_status: status,
        title_sections_checked: titleSections,
      });

      if (write) {
        fs.writeFileSync(filePath, renderFile(updatedHeader, body), "utf-8");
        rewritten++;
      }
    } catch (err) {
      failed++;
      rows.push({
        file: path.basename(filePath),
        doc_key: null,
        program_key: null,
        metadata_total_ects: null,
        detected_ects_values: [],
        metadata_match_status: "ambiguous",
        title_sections_checked: [],
      });
    }
  }

  const reportPath = path.join(dir, "_metadata_validation_report.json");
  fs.writeFileSync(
    reportPath,
    JSON.stringify(
      {
        generated_at: new Date().toISOString(),
        directory: dir,
        counts: {
          total_files: files.length,
          ok,
          mismatch,
          ambiguous,
          failed,
          rewritten,
        },
        rows,
      },
      null,
      2
    ),
    "utf-8"
  );

  console.log(`Done.\ntotal_files=${files.length}\nok=${ok}\nmismatch=${mismatch}\nambiguous=${ambiguous}\nfailed=${failed}\nrewritten=${rewritten}\nreport=${reportPath}`);
}

main();
