#!/usr/bin/env node
// CSV → 컬럼별 통계 요약 JSON (외부 라이브러리 0, Node 20+ 표준 라이브러리만).
//
// 호출 예 (wave-5 매니페스트 long_flags):
//   csv_summary.js --csv-path /work/in.csv --out-json /work/out.json
//
// stdout 으로 JSON 한 줄 (return.format=json):
//   {"rows": 100, "columns": ["a","b"], "stats": {...}, "out_json": "..."}

const fs = require("fs");
const path = require("path");

function parseArgs(argv) {
  const a = { csv_path: "", out_json: "/work/out.json", delim: "," };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    const v = argv[i + 1];
    if (k === "--csv-path") { a.csv_path = v; i++; }
    else if (k === "--out-json") { a.out_json = v; i++; }
    else if (k === "--delim") { a.delim = v; i++; }
  }
  return a;
}

function parseCsv(text, delim) {
  // 단순 CSV — 쌍따옴표 escape 미지원 (예제 수준).
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  if (lines.length < 2) return { columns: [], rows: [] };
  const columns = lines[0].split(delim).map((s) => s.trim());
  const rows = lines.slice(1).map((ln) => ln.split(delim).map((s) => s.trim()));
  return { columns, rows };
}

function isNumeric(s) {
  return s !== "" && !isNaN(Number(s));
}

function summarize(rows, columns) {
  const stats = {};
  columns.forEach((col, idx) => {
    const values = rows.map((r) => r[idx]).filter((v) => v !== undefined);
    const numeric = values.filter(isNumeric).map(Number);
    if (numeric.length > 0 && numeric.length === values.length) {
      const n = numeric.length;
      const sum = numeric.reduce((a, b) => a + b, 0);
      const mean = sum / n;
      const sorted = [...numeric].sort((a, b) => a - b);
      const median = n % 2 === 0
        ? (sorted[n / 2 - 1] + sorted[n / 2]) / 2
        : sorted[Math.floor(n / 2)];
      const variance = numeric.reduce((acc, x) => acc + (x - mean) ** 2, 0) / n;
      stats[col] = {
        type: "numeric", count: n,
        min: sorted[0], max: sorted[n - 1],
        mean: Number(mean.toFixed(6)),
        median: Number(median.toFixed(6)),
        stddev: Number(Math.sqrt(variance).toFixed(6)),
      };
    } else {
      const uniq = new Set(values);
      stats[col] = {
        type: "categorical", count: values.length,
        unique: uniq.size,
        top: [...uniq].slice(0, 5),
      };
    }
  });
  return stats;
}

function main() {
  const a = parseArgs(process.argv);
  if (!a.csv_path) {
    console.error("--csv-path required");
    process.exit(2);
  }
  if (!fs.existsSync(a.csv_path)) {
    console.error(`csv not found: ${a.csv_path}`);
    process.exit(2);
  }
  const text = fs.readFileSync(a.csv_path, "utf-8");
  const { columns, rows } = parseCsv(text, a.delim);
  const stats = summarize(rows, columns);

  const outDir = path.dirname(a.out_json);
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
  const summary = { rows: rows.length, columns, stats, out_json: a.out_json };
  fs.writeFileSync(a.out_json, JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary));
}

main();
