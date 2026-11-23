#!/usr/bin/env node
/**
 * Validate every *.glb under a directory with the Khronos glTF-Validator.
 *   node scripts/validate-glb.mjs <dir>
 * Fails on any validation error; also asserts the RevGate GLB contract
 * (1 mesh / 1 primitive, POSITION + NORMAL + COLOR_0 + _FACE_ID(FLOAT, SCALAR)).
 */
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";

import validator from "gltf-validator";

async function* walk(dir) {
  for (const ent of await readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) yield* walk(p);
    else if (ent.name.endsWith(".glb")) yield p;
  }
}

function parseGlbJson(buf) {
  const jsonLen = buf.readUInt32LE(12);
  return JSON.parse(buf.subarray(20, 20 + jsonLen).toString("utf8"));
}

const root = process.argv[2];
if (!root) {
  console.error("usage: node scripts/validate-glb.mjs <dir>");
  process.exit(2);
}

let files = 0;
let failed = 0;
const issueTotals = { errors: 0, warnings: 0, infos: 0, hints: 0 };
for await (const file of walk(root)) {
  files += 1;
  const buf = await readFile(file);
  const report = await validator.validateBytes(new Uint8Array(buf), { uri: path.basename(file), maxIssues: 50 });
  const { numErrors, numWarnings, numInfos, numHints } = report.issues;
  issueTotals.errors += numErrors;
  issueTotals.warnings += numWarnings;
  issueTotals.infos += numInfos;
  issueTotals.hints += numHints;

  const gltf = parseGlbJson(buf);
  const problems = [];
  if (gltf.meshes?.length) {
    const prims = gltf.meshes.flatMap((m) => m.primitives);
    if (gltf.meshes.length !== 1 || prims.length !== 1) problems.push("expected exactly 1 mesh / 1 primitive");
    const attrs = prims[0].attributes;
    for (const a of ["POSITION", "NORMAL", "COLOR_0", "_FACE_ID"]) if (!(a in attrs)) problems.push(`missing ${a}`);
    const fid = gltf.accessors[attrs._FACE_ID];
    if (fid && (fid.componentType !== 5126 || fid.type !== "SCALAR")) problems.push("_FACE_ID must be FLOAT SCALAR");
  }
  const bad = numErrors > 0 || problems.length > 0;
  if (bad) failed += 1;
  const rel = path.relative(root, file);
  console.log(`${bad ? "✗" : "✓"} ${rel}  errors=${numErrors} warnings=${numWarnings} infos=${numInfos}${problems.length ? "  " + problems.join("; ") : ""}`);
  for (const m of report.issues.messages.filter((x) => x.severity <= 1)) console.log(`    [${m.severity === 0 ? "E" : "W"}] ${m.code}: ${m.message} (${m.pointer ?? ""})`);
}

console.log(`\n${files} GLB file(s): ${files - failed} valid, ${failed} invalid — totals ${JSON.stringify(issueTotals)}`);
process.exit(files > 0 && failed === 0 ? 0 : 1);
