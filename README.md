# Decky Universal Share

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for the Steam Deck that turns the screenshots Steam already takes natively (Steam button + R1/RB) into a browsable, manageable, shareable gallery — right from the Quick Access Menu.

## Installing (no build tools needed)

1. Grab the latest `decky-universal-share-vX.Y.Z.zip` from this repo's [Releases](https://github.com/Revivedx/decky-universal-share/releases) page (or build one yourself, see below).
2. On the Deck (or any Linux machine running Decky Loader), open Decky's Quick Access Menu → **Settings** → enable **Developer Mode** if it isn't already.
3. In the Decky Settings' **Developer** tab, use **Install Plugin from ZIP** and pick the file.
4. Decky Universal Share should now show up in the plugin list — no compiling, no SSH, no Node/Python toolchain required on your end.

## What it does (current scope)

- **Gallery**: lists Steam's native screenshots across *every* game (plus the special "SteamOS / Desktop" bucket Steam uses for shots taken outside a game), newest first, 5 per page, with Previous/Next navigation and a manual Refresh button.
- **Preview**: tapping a screenshot opens a full-resolution preview with **Share** and **Delete** actions.
- **Delete**: removes the screenshot file and its cached thumbnail. (Steam's own `760/screenshots.vdf` index isn't touched — Steam tolerates manually removed files and prunes the stale entry on its next scan, same as deleting the file from a file manager would.)
- **Share via QR**: starts a local, LAN-only HTTP server that serves *only* the selected screenshot, and shows a QR code (generated 100% locally — no third-party service involved) that a phone on the same Wi-Fi can scan to download it. See [Security notes](#security-notes-for-share-via-qr) below for how this is hardened.
- **Share menu placeholders**: the Share dropdown also lists "iCloud" and "Google Drive" as future options. They currently only show a "Coming soon" toast — no integration exists yet.
- **Storage panel**: a collapsible summary showing how much space Steam's screenshots are using, with a configurable warning limit (0.5–50 GB). Exceeding it only shows a warning — it never blocks Steam from saving a new screenshot (the plugin doesn't control that). There's an opt-in, **off-by-default** "auto-delete oldest when over limit" toggle for anyone who wants that risk; its description explicitly warns that it permanently deletes screenshots from *any* game without asking.
- **Share options panel**: lets you configure how long a "Share via QR" link stays active before expiring on its own (1/5/10/30 minutes).

## Why it works this way (design decisions worth knowing)

- **No custom capture, no button-combo hotkey.** Earlier iterations tried to capture screenshots ourselves (first via an L4+R4 button combo, then via `gamescopectl screenshot`). Both were abandoned:
  - The Deck's controller doesn't expose its real button protocol via generic Linux `evdev` — Steam owns it exclusively while running (confirmed empirically: zero evdev events reach the controller when pressing the paddles).
  - `gamescopectl screenshot` (gamescope's own debug command) did work, but Steam's native screenshot (Steam button + R1) is more reliable, doesn't capture Decky's own Quick Access Menu overlay (something our own capture couldn't cleanly avoid), and Steam already generates thumbnails for us.
- **The plugin backend runs as the normal `deck` user, not root.** Once capture stopped being our job, nothing left in the backend needs elevated privileges — it only reads/deletes files the `deck` user already owns.
- **The Steam account (and its screenshot folder) is auto-detected**, not configured by hand. `userdata/<accountID>/760/remote/` is located by scanning `userdata/` (single-account fast path) or by parsing `config/loginusers.vdf` for multi-account setups, converting the account's SteamID64 to its 32-bit folder name.
- **The QR-sharing HTTP server is hand-rolled on top of `asyncio`**, not `http.server`/`socketserver`/`wsgiref`. Those modules are simply not present in the packaged Python that Decky Loader uses to run plugin backends (confirmed on-device: `ModuleNotFoundError`, even for `wsgiref.simple_server`, which itself depends on `http.server`). `socket` and `asyncio` are available, so the server implements the bare minimum HTTP GET handling needed.
- **Modals use `ModalRoot`, not `ConfirmModal`.** `ConfirmModal` always renders its own OK/Cancel button pair with no documented way to hide either one, and — the actual bug that took a few rounds to track down — it does **not** close itself when those buttons are pressed; the caller has to explicitly call the `.Close()` handle that `showModal()` returns. `ModalRoot` forces no buttons at all, letting the content define exactly which actions exist (Share, Delete, Stop sharing), while its `onCancel` prop is what the controller's B button fires.

## Security notes (for "Share via QR")

The local share server has no authentication or TLS, so it's hardened deliberately:

- The public URL uses a **high-entropy random token** as the filename, not the real Steam screenshot filename (which follows a guessable date pattern).
- **QR codes are generated locally**, in the plugin's own frontend bundle, via the [`qrcode-generator`](https://github.com/kazuhikoarase/qrcode-generator) library — no screenshot URL, LAN IP, or any other data is ever sent to a third-party service to render the code.
- The server **shuts down automatically after the first successful download** (with a short grace period, in case the phone's browser needs a second request to finish saving), or after the configured maximum duration if nobody downloads it.
- Only the one chosen file is exposed (copied into an isolated staging folder), never the whole screenshots library.

This does mean: while a share is active, anyone else on the same network who somehow obtained the exact URL could also download that one file — treat it like a temporary, one-time link, same as you would with any quick file-share tool on a home network.

## Dependencies

**Runtime (bundled into `dist/index.js`):**
| Package | Why |
|---|---|
| [`@decky/api`](https://www.npmjs.com/package/@decky/api) | Frontend↔backend RPC (`callable`, events), plugin registration |
| [`@decky/ui`](https://www.npmjs.com/package/@decky/ui) | Steam-styled UI components (panels, buttons, sliders, modals) |
| [`react-icons`](https://www.npmjs.com/package/react-icons) | Icons (camera, arrows, refresh) |
| [`qrcode-generator`](https://www.npmjs.com/package/qrcode-generator) | Fully local, dependency-free QR code rendering |
| `tslib` | TypeScript helper runtime |

**Backend:** Python standard library only (`asyncio`, `socket`, `secrets`, `shutil`, `json`, `re`, `base64`, `urllib.parse`) plus the `decky` module Decky Loader itself provides. No pip packages are vendored.

**Dev tooling:**
| Package | Why |
|---|---|
| `rollup` + `@decky/rollup` | Bundles `src/index.tsx` into `dist/index.js` |
| `typescript` | Type checking |
| `node-ssh` | Powers `scripts/deploy.mjs`, our own SSH/SFTP deploy script (see below) |
| `archiver` | Powers `scripts/package.mjs`, builds the distributable install zip |

## Development workflow

This repo's deploy path is custom (built while developing on Windows against a physical Deck over SSH), not the template's original VSCode-task-based flow:

1. Copy your Deck's connection info into a root-level `settings.json` (gitignored):
   ```json
   { "deckIP": "192.168.x.x", "deckPort": "22", "deckUser": "deck", "deckPass": "..." }
   ```
2. `npm run deploy` — builds the frontend, stops `plugin_loader` on the Deck, uploads the plugin over SFTP, and restarts the service. (Stopping the service before uploading avoids a hot-reload race that could otherwise leave an orphaned, runaway plugin process — see the comments in `scripts/deploy.mjs`.)
3. `npm run build` / `npm run watch` still work standalone if you just want to compile without deploying.
4. `npm run package` — builds the frontend and produces `release/decky-universal-share-vX.Y.Z.zip`, laid out exactly the way Decky Loader expects for a manual "Install Plugin from ZIP" (see [Installing](#installing-no-build-tools-needed) above). This doesn't need the official [decky CLI](https://github.com/SteamDeckHomebrew/cli) (which is Linux/macOS-only) — since this plugin has no native backend to cross-compile, zipping the already-built files ourselves (via the `archiver` package) is equivalent for our case. Verified end-to-end on a real Deck: extracting the zip the same way Decky's installer would and starting `plugin_loader` loads the plugin cleanly.

## Not currently used

These files are part of the original [decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template) this project was bootstrapped from, and are left in place but **not used** by this plugin:

- `backend/` (`Dockerfile`, `Makefile`, `entrypoint.sh`, `src/main.c`) — scaffold for an optional native (C) backend. This plugin is pure Python + TypeScript.
- `assets/logo.png` — not referenced anywhere in `src/`.
- `.vscode/build.sh`, `config.sh`, `setup.sh`, `tasks.json`, `defsettings.json` — the template's original VSCode-task-based build/deploy flow (Linux-only CLI tooling, a different `settings.json` shape). Fully superseded by `npm run deploy` above.
- `defaults/defaults.txt` — template documentation for an optional folder of default config/theme files to ship; this plugin doesn't have any.
- `py_modules/.keep` — placeholder; no vendored Python dependencies are used.
