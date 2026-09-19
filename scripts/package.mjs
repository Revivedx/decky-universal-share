// Builds a distributable zip that anyone can install on their own Steam Deck
// (or any Linux box running Decky Loader) via Decky's "Install Plugin from
// ZIP" option under Developer Settings — no build tools required on their end.
//
// Layout matches what Decky Loader expects (see README.md's "Distribution"
// section, and https://wiki.deckbrew.xyz for the authoritative reference):
//
//   omni-revi-transfer-vX.Y.Z.zip
//     Omni-Revi-Transfer/
//       dist/
//         index.js
//       package.json
//       plugin.json
//       main.py
//       README.md
//       LICENSE

import { createWriteStream, existsSync, mkdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
// archiver v8 is pure ESM and dropped the old `archiver('zip', opts)`
// factory-function API in favor of exporting the format classes directly.
import { ZipArchive } from "archiver";

const rootDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

function loadJSON(relPath) {
  return JSON.parse(readFileSync(path.join(rootDir, relPath), "utf-8"));
}

const pluginMeta = loadJSON("plugin.json");
const packageMeta = loadJSON("package.json");

const pluginName = pluginMeta.name;
const version = packageMeta.version;

const releaseDir = path.join(rootDir, "release");
const zipPath = path.join(releaseDir, `omni-revi-transfer-v${version}${process.argv.includes("--personal") ? "-personal" : ""}.zip`);

// Files that must exist for the plugin to install and run at all.
const requiredFiles = ["dist/index.js", "package.json", "plugin.json", "main.py"];
// Nice-to-have files Decky's own docs recommend including alongside a submission.
const optionalFiles = ["README.md", "LICENSE"];

// Two kinds of zip:
//
//   npm run package            PUBLIC (the one to publish). Ships NO credentials: every user enters
//                              their own Google / Discord app in the plugin (Share options -> Set up),
//                              so no secret of mine is distributed.
//   npm run package:personal   PERSONAL BUILD. Bundles google_credentials.json and
//                              discord_credentials.json (gitignored) so the developer's own devices
//                              work without any setup. Named "...-personal.zip"; NEVER publish it.
//
// The credential files are gitignored, so they never reach git either way.
const personal = process.argv.includes("--personal");
const mainPySource = readFileSync(path.join(rootDir, "main.py"), "utf-8");
const CREDENTIAL_FILES = [
  ["google_credentials.json", "GOOGLE_DRIVE_ENABLED"],
  ["discord_credentials.json", "DISCORD_ENABLED"],
];
if (personal) {
  for (const [file, flag] of CREDENTIAL_FILES) {
    if (!new RegExp(`${flag}\\s*=\\s*True`).test(mainPySource)) continue;
    if (!existsSync(path.join(rootDir, file))) {
      console.error(
        `--personal needs ${file} but it is missing (see ${file.replace(".json", ".example.json")}). ` +
          `Create it, or build the public zip with "npm run package".`
      );
      process.exit(1);
    }
    optionalFiles.push(file);
  }
  console.log("  PERSONAL BUILD: bundling your OAuth credentials. Do NOT publish this zip.");
} else {
  console.log("  Public build: no credentials are bundled (users set up their own in the plugin).");
}
if (!personal && optionalFiles.some((file) => /credentials\.json$/.test(file))) {
  throw new Error("Refusing to build a public zip that includes a credentials file.");
}

function assertBuilt() {
  const missing = requiredFiles.filter((f) => !existsSync(path.join(rootDir, f)));
  if (missing.length) {
    throw new Error(
      `Missing required file(s): ${missing.join(", ")}. Run "npm run build" first (this script is normally invoked via "npm run package", which does that for you).`
    );
  }
}

async function main() {
  assertBuilt();
  mkdirSync(releaseDir, { recursive: true });

  const output = createWriteStream(zipPath);
  const archive = new ZipArchive({ zlib: { level: 9 } });

  const done = new Promise((resolve, reject) => {
    output.on("close", resolve);
    archive.on("error", reject);
  });

  archive.pipe(output);

  for (const file of requiredFiles) {
    archive.file(path.join(rootDir, file), { name: `${pluginName}/${file}` });
  }
  for (const file of optionalFiles) {
    if (existsSync(path.join(rootDir, file))) {
      archive.file(path.join(rootDir, file), { name: `${pluginName}/${file}` });
    } else {
      console.warn(`  skipped (missing): ${file}`);
    }
  }

  await archive.finalize();
  await done;

  console.log(`Packaged ${pluginName} v${version} -> ${path.relative(rootDir, zipPath)}`);
  console.log(`(${archive.pointer()} bytes)`);
}

main().catch((err) => {
  console.error(err.message ?? err);
  process.exit(1);
});
