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
const zipPath = path.join(releaseDir, `omni-revi-transfer-v${version}.zip`);

// Files that must exist for the plugin to install and run at all.
const requiredFiles = ["dist/index.js", "package.json", "plugin.json", "main.py"];
// Nice-to-have files Decky's own docs recommend including alongside a submission.
const optionalFiles = ["README.md", "LICENSE"];

// google_credentials.json / discord_credentials.json hold the real OAuth client
// id/secret and are gitignored, so they never reach git. Every install needs its
// app's client to link an account, so they ride along in the zip -- but only
// while the matching feature flag is on (a build with a feature off must not
// ship its secret for nothing). If a flag is on and the file is missing, the
// zip would contain a feature that can't work, so packaging stops instead.
const mainPySource = readFileSync(path.join(rootDir, "main.py"), "utf-8");
function requireCredentials(file, flag) {
  if (!existsSync(path.join(rootDir, file))) {
    console.error(
      `${flag} is True but ${file} is missing, so the zip would ship a feature that can't work. ` +
        `Restore the file (see ${file.replace(".json", ".example.json")}) or set the flag to False in main.py and src/index.tsx.`
    );
    process.exit(1);
  }
}
const googleDriveEnabled = /GOOGLE_DRIVE_ENABLED\s*=\s*True/.test(mainPySource);
if (googleDriveEnabled) {
  requireCredentials("google_credentials.json", "GOOGLE_DRIVE_ENABLED");
  optionalFiles.push("google_credentials.json");
} else {
  console.log("  GOOGLE_DRIVE_ENABLED is False: google_credentials.json will NOT be bundled in this zip.");
}

// Same rule for discord_credentials.json (DISCORD_ENABLED in main.py).
const discordEnabled = /DISCORD_ENABLED\s*=\s*True/.test(mainPySource);
if (discordEnabled) {
  requireCredentials("discord_credentials.json", "DISCORD_ENABLED");
  optionalFiles.push("discord_credentials.json");
} else {
  console.log("  DISCORD_ENABLED is False: discord_credentials.json will NOT be bundled in this zip.");
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
