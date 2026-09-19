# Omni-Revi-Transfer — for Decky

**What this app does:** Omni-Revi-Transfer is a plugin for the Steam Deck (installed through [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader)) that lets you browse, manage, and share the screenshots your Deck already takes — right from the in-game Quick Access Menu. You can preview them, delete old ones, share one instantly to your phone with a QR code, or upload one to your own Google Drive, without ever leaving your controller.

**Version 0.0.8** — see [CHANGELOG.md](CHANGELOG.md) for what changed in each release.

## Installing (no build tools needed)

1. Grab the latest `omni-revi-transfer-vX.Y.Z.zip` from this repo's [Releases](https://github.com/Revivedx/omni-revi-transfer/releases) page (or build one yourself, see below).
2. On the Deck (or any Linux machine running Decky Loader), open Decky's Quick Access Menu → **Settings** → enable **Developer Mode** if it isn't already.
3. In the Decky Settings' **Developer** tab, use **Install Plugin from ZIP** and pick the file.
4. Omni-Revi-Transfer should now show up in the plugin list — no compiling, no SSH, no Node/Python toolchain required on your end.
5. Google Drive and Discord need a one-time setup with **your own** Google / Discord app (the plugin ships no credentials of anyone's); see [Setting up Google Drive](#setting-up-google-drive) and [Setting up Discord](#setting-up-discord). QR sharing, Steam sharing, the gallery and the storage panel work without any setup.

## What it does (current scope)

- **Gallery**: lists Steam's native screenshots across *every* game (plus the special "SteamOS / Desktop" bucket Steam uses for shots taken outside a game), newest first, 5 per page, with Previous/Next navigation and a manual Refresh button.
- **Preview**: tapping a screenshot opens a full-resolution preview with **Share** and **Delete** actions.
- **Delete**: removes the screenshot file and its cached thumbnail. (Steam's own `760/screenshots.vdf` index isn't touched — Steam tolerates manually removed files and prunes the stale entry on its next scan, same as deleting the file from a file manager would.)
- **Share via QR**: starts a local, LAN-only HTTP server that serves *only* the selected screenshot, and shows a QR code (generated 100% locally — no third-party service involved) that a phone on the same Wi-Fi can scan to download it. See [Security notes](#security-notes-for-share-via-qr) below for how this is hardened.
- **Discord**: posts a screenshot, with the game name, to a Discord channel you pick. After the one-time [setup with your own Discord app](#setting-up-discord), link it from **Share options → Discord → Link Discord**: the Discord authorization page opens in the Deck's Steam browser (Discord's own login offers "log in with QR code" from your phone), you choose the channel, and the plugin stores the resulting webhook. Then choose **Discord** in the Share dropdown of any screenshot. Linking requires the Deck's sudo password, like Google Drive. See [Security notes for Discord](#security-notes-for-discord).
- **Steam account and Steam friends**: the Share dropdown of any screenshot has **Steam (my account)**, which uploads it to your own Steam account (Steam Cloud) with the privacy you pick, and **Steam friend (chat)**, which does what Steam's own Media → Share → friend does: you pick a friend (recent chats first, with profile pictures) and their chat window opens with the screenshot ready to send, where you can tag it as a spoiler before confirming. Pressing **B** while the screenshot is still unsent closes that chat and takes you back to the friend picker. Nothing is uploaded to your account for this. Choose the upload privacy for your own account (Private by default) in **Share options → Steam**. See [Security notes for Steam sharing](#security-notes-for-steam-sharing).
- **Auto-upload** *(off by default)*: **Share options** shows an "Auto-upload" toggle for Steam, and for Google Drive and Discord once they are linked. When on, every new screenshot is uploaded after a delay you choose per service with a slider (5–60 seconds, 10 by default). It only covers screenshots taken while the plugin is running, and never uploads the ones that already existed when you turned it on. Unlinking a service switches its toggle off. See [Security notes](#security-notes-for-google-drive) for what this means for privacy.
- **iPhone/iPad users**: no iCloud integration is offered (see [why](#why-it-works-this-way-design-decisions-worth-knowing) below). Share via QR works with Apple devices: scan the code with the Camera app, open the link in Safari, and save the image to Photos.
- **Google Drive upload** *(see [Google Drive status](#google-drive-status) below)*: uploads a screenshot to the user's own Google Drive, organized under `omni-revi-transfer/screenshots/<Game Name>` (or `SteamOS` for shots taken outside a game), with duplicate detection so re-uploading the same file is a no-op. Linking requires confirming the Deck's own sudo password first (see [Security notes for Google Drive](#security-notes-for-google-drive) below).
- **Storage panel**: a collapsible summary showing how much space Steam's screenshots are using, with a configurable warning limit (0.5–50 GB, or Unlimited). Exceeding it only shows a warning — it never blocks Steam from saving a new screenshot (the plugin doesn't control that). Alerts fire once per threshold crossing at 80/90/100% usage. There's an opt-in, **off-by-default** "auto-delete oldest when over limit" toggle for anyone who wants that risk; its description explicitly warns that it permanently deletes screenshots from *any* game without asking (and is automatically disabled when the limit is set to Unlimited).
- **Share options panel**: three blocks. **QR Link** sets how long a "Share via QR" link stays active before expiring on its own (1/5/10/30 minutes). **Steam** holds the upload privacy plus its Auto-upload toggle and delay slider. **Google Drive** and **Discord** are collapsible; each holds its Link/Unlink button and, once linked, its Auto-upload toggle and upload-delay slider.

## Why it works this way (design decisions worth knowing)

- **No custom capture, no button-combo hotkey.** Earlier iterations tried to capture screenshots ourselves (first via an L4+R4 button combo, then via `gamescopectl screenshot`). Both were abandoned:
  - The Deck's controller doesn't expose its real button protocol via generic Linux `evdev` — Steam owns it exclusively while running (confirmed empirically: zero evdev events reach the controller when pressing the paddles).
  - `gamescopectl screenshot` (gamescope's own debug command) did work, but Steam's native screenshot (Steam button + R1) is more reliable, doesn't capture Decky's own Quick Access Menu overlay (something our own capture couldn't cleanly avoid), and Steam already generates thumbnails for us.
- **The plugin backend runs as the normal `deck` user, not root.** Once capture stopped being our job, nothing left in the backend needs elevated privileges — it only reads/deletes files the `deck` user already owns.
- **The Steam account (and its screenshot folder) is auto-detected**, not configured by hand. `userdata/<accountID>/760/remote/` is located by scanning `userdata/` (single-account fast path) or by parsing `config/loginusers.vdf` for multi-account setups, converting the account's SteamID64 to its 32-bit folder name.
- **The QR-sharing HTTP server is hand-rolled on top of `asyncio`**, not `http.server`/`socketserver`/`wsgiref`. Those modules are simply not present in the packaged Python that Decky Loader uses to run plugin backends (confirmed on-device: `ModuleNotFoundError`, even for `wsgiref.simple_server`, which itself depends on `http.server`). `socket` and `asyncio` are available, so the server implements the bare minimum HTTP GET handling needed.
- **No iCloud sharing.** An earlier build listed "iCloud (coming soon)" in the Share menu; it was removed because it can't honestly be delivered. Apple provides no public API for third-party apps outside its own platforms to upload files into a user's iCloud Drive or Photos (unlike Google Drive's OAuth `drive.file` scope). The only official route, CloudKit Web Services, stores data in a container belonging to an app of the developer's own (so files wouldn't appear in the Files or Photos apps unless a companion iOS app existed), requires a paid Apple Developer Program membership, and uses a browser-redirect sign-in that doesn't fit a controller-driven Deck. Unofficial workarounds that log in with an Apple ID password and 2FA violate Apple's terms and would mean storing Apple credentials on the Deck, which contradicts this project's privacy stance. Share via QR already covers iPhones without any of that.
- **Discord posts to a channel, never to a DM, and the login isn't a QR.** Discord has no device flow and no scope that lets an app post or DM *as the user* (that would be a self-bot, against Discord's terms). The one legitimate route is the `webhook.incoming` OAuth scope, which creates a webhook in a channel the user picks. So the link runs through the Deck's own browser with a `localhost` redirect, and uploads go through the webhook. For "a DM to myself", create a private server with one channel and link that.
- **Steam sharing runs in the frontend, and the friend chat uses an undocumented API.** Uploading to a Steam account is something only Steam's own client can do, so it goes through `SteamClient.Screenshots.UploadLocalScreenshot` from the plugin's frontend, not the Python backend. For friends, an earlier build uploaded the screenshot and sent its link, which was clumsy; the plugin now reuses the mechanism behind Steam's own Media → Share → friend option instead: it opens the friend's chat window and stages the image in it (`ChatView.SetFileToUpload`), so Steam's own chat handles the upload, the spoiler tag and the confirmation. That goes through Steam's internal chat store, which is not a documented API and may change with a Steam update; every use is feature-checked, and if it stops working the plugin falls back to just opening the chat. The chat has to be opened in Steam's own registered browser context (not a DOM window); passing anything else creates a stray chat that floats over everything and never receives the controller's focus. While the screenshot waits unsent in the chat, the plugin watches the controller's B button and, only while that chat tab is open with the image still staged, closes it and reopens the friend picker.
- **The screenshot list is cached per folder.** Walking every file is O(library size); it used to happen every 2 seconds and several times per menu open, which cost ~11% of a CPU core with 20,000 screenshots. A folder's timestamp changes when a file is added or removed, so the index re-reads only folders that changed (and distrusts timestamps younger than 3 seconds, for SD cards with coarse timestamps). Measured on a Deck: with 20,000 screenshots a gallery refresh takes ~0.6 ms instead of ~358 ms and the plugin's memory stays ~17 MB lower. Around 15 MB of the plugin's ~37 MB resident memory is the Python runtime itself, similar to other Decky plugins.
- **Reddit is not implemented.** Since late 2025 Reddit requires manual approval (typically weeks, and it can be denied) before any new app may use its API, and it offers no device flow either. It will be revisited only if that approval is granted.
- **Modals use `ModalRoot`, not `ConfirmModal`.** `ConfirmModal` always renders its own OK/Cancel button pair with no documented way to hide either one, and — the actual bug that took a few rounds to track down — it does **not** close itself when those buttons are pressed; the caller has to explicitly call the `.Close()` handle that `showModal()` returns. `ModalRoot` forces no buttons at all, letting the content define exactly which actions exist (Share, Delete, Stop sharing), while its `onCancel` prop is what the controller's B button fires.

## Google Drive status

Google Drive works with **your own Google app**: the plugin ships no Google credentials. You create a (free) OAuth client in your own Google Cloud project, enter its client ID and secret once in the plugin, and they stay on your Deck (obfuscated). Nothing is shared with anyone else, the uploads are tied to a project you control, and the plugin doesn't depend on a shared app passing Google's verification. It only requests the non-sensitive `drive.file` scope (files the plugin itself creates). You can revoke access any time from your [Google Account's connected apps](https://myaccount.google.com/permissions).

## Setting up Google Drive

One time, about 10 minutes, best done on a computer:

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a project (any name).
2. **APIs & Services → Library**, search for **Google Drive API** and click **Enable**.
3. **Google Auth Platform** (older UI: *OAuth consent screen*) → **Get started**: any app name, your email as support and contact address, audience **External**.
4. **Data access → Add or remove scopes**: add only `.../auth/drive.file` and save.
5. **Audience → Publish app** (status "In production"). Without this, Google expires the login every 7 days and only listed test users can link. `drive.file` is a non-sensitive scope, so publishing your own app for your own use needs no Google review. If Google shows an "unverified app" notice while you link, that is your own app: continue.
6. **Clients → Create client**, type **TVs and Limited Input devices**, and copy the **Client ID** and **Client secret**.
7. On the Deck: Quick Access Menu → Omni-Revi-Transfer → **Share options → Google Drive → Set up Google Drive**, paste both, **Save**.
8. **Link Google Drive** → confirm the Deck's password → scan the QR with your phone and approve.

Typing a ~70-character client ID with the on-screen keyboard is tedious. Alternative: in Desktop Mode (or over SSH) create `google_credentials.json` in the plugin folder (`~/homebrew/plugins/Omni-Revi-Transfer/`) with the shape of `google_credentials.example.json`; the plugin reads it like the saved values. Values entered in the plugin take priority over that file.

## Setting up Discord

One time, about 5 minutes:

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create a **New Application** (any name).
2. **OAuth2 → Redirects → Add Redirect**: `http://localhost:47821/callback`, then **Save Changes** (without saving it isn't stored).
3. Copy the **Application ID** (General Information) and, under OAuth2, **Reset Secret** and copy the **Client Secret**.
4. On the Deck: **Share options → Discord → Set up Discord**, paste both, **Save**.
5. **Link Discord**: the authorization page opens in the Deck's Steam browser; log in (Discord's QR login works), then pick the server and channel. You need a server where you can manage webhooks; a private server of your own works as a "DM to myself" (Discord's **+ → Create My Own**).

No Discord review is needed for the `webhook.incoming` scope.

## Personal build

For developers who want the plugin on their own devices (another Deck, or another Linux PC running Decky Loader) with their credentials already inside:

- `npm run package` builds the **public** zip: `omni-revi-transfer-vX.Y.Z.zip`, which never contains credentials.
- `npm run package:personal` builds `omni-revi-transfer-vX.Y.Z-personal.zip`, which bundles your gitignored `google_credentials.json` and `discord_credentials.json` so nothing needs to be set up on the target device. **Never publish this one**: it contains your secrets. It refuses to build if a credentials file is missing.

## Security notes (for "Share via QR")

The local share server has no authentication or TLS, so it's hardened deliberately:

- The public URL uses a **high-entropy random token** as the filename, not the real Steam screenshot filename (which follows a guessable date pattern).
- **QR codes are generated locally**, in the plugin's own frontend bundle, via the [`qrcode-generator`](https://github.com/kazuhikoarase/qrcode-generator) library — no screenshot URL, LAN IP, or any other data is ever sent to a third-party service to render the code.
- The server **shuts down automatically after the first successful download** (with a short grace period, in case the phone's browser needs a second request to finish saving), or after the configured maximum duration if nobody downloads it.
- Only the one chosen file is exposed (copied into an isolated staging folder), never the whole screenshots library.

This does mean: while a share is active, anyone else on the same network who somehow obtained the exact URL could also download that one file — treat it like a temporary, one-time link, same as you would with any quick file-share tool on a home network.

## Security notes (for Google Drive)

Full details live in `PRIVACY.md`, but in short:

- Only the [`drive.file`](https://developers.google.com/drive/api/guides/api-specific-auth) scope is requested — the plugin can only see/create files it uploads itself, never browse the rest of the user's Drive.
- Linking uses Google's OAuth **device flow**: the user approves on their own phone/browser by scanning a QR code, never by typing a password into the plugin.
- The resulting refresh token is stored **locally on the Deck only**, inside the plugin's own settings folder, and is **obfuscated at rest** (XOR'd with a key derived from the device's own `/etc/machine-id`, base64-encoded) — this is explicitly *obfuscation, not real encryption*. It raises the bar against casual/accidental exposure (e.g. someone `cat`-ing the file out of curiosity) but would not stop someone with root access to a compromised Deck from reversing it, since the process itself must be able to decrypt it with no human input. This limitation is disclosed to the user, not hidden.
- Before starting the link flow, the plugin requires the user to re-enter **this Deck's own sudo password** (verified via `sudo -k -S -v`, never logged, piped straight to stdin so it never appears in `ps`) as a step-up confirmation — so someone who picks up an already-unlocked Deck can't silently link their own Google account to it. The risk of the obfuscated (not encrypted) token is disclosed on that same screen before the password prompt.
- Unlinking revokes the token with Google directly and deletes the local file — equivalent to removing the app from your [Google Account's connected apps list](https://myaccount.google.com/permissions).
- The plugin ships **no** OAuth credentials: you create your own Google app and enter its client ID/secret, which are stored only on your Deck, obfuscated at rest like the linked session (obfuscation, not real encryption; the same limits apply). Because each user has their own, nothing about anyone else's app is exposed. The gitignored `google_credentials.json` (see `google_credentials.example.json`) is only for developers and personal builds, and GitHub's push protection stops such files from ever being committed.

## Security notes (for Steam sharing)

- Nothing goes through any server of this project: uploads and chat messages are done by your own Steam client, to your own account.
- The upload privacy is **Private** by default. Choosing **Public** makes every screenshot you upload to your account (including auto-uploads) visible to everyone on your profile, and the settings panel warns about it.
- **Steam friend (chat)** doesn't upload anything to your account or send anything by itself: it opens the chat with the image staged, and you confirm sending there.
- Uploaded screenshots count against your Steam Cloud space, and this plugin cannot delete them from Steam. Manage or remove them from your Steam profile's screenshots page.
- The friend picker reads your Steam client's friends list (names, profile pictures and recent chats), on the Deck only.

## Security notes (for auto-upload)

- Off by default and opt-in per service, and only offered while that service is linked. The toggle's description says plainly that uploads happen without asking.
- There's no filter: a screenshot that happens to show something private will be uploaded like any other, which is the trade-off of not confirming each one. The delay (5–60 seconds, 10 by default, adjustable per service) is a window to delete a screenshot (or switch the toggle off) before it goes out, since the toggles and delays are checked again continuously.
- Steam gives plugins no "screenshot taken" event for Google Drive and Discord uploads, so new files are found by checking the screenshots folders' timestamps every couple of seconds, and only while one of those toggles is on (nothing is scanned otherwise); nothing is read or sent until the delay has passed.
- Failed auto-uploads are not retried, and a toast reports the failure.

## Security notes (for Discord)

- Only the `webhook.incoming` scope is requested. It can create a webhook in the one channel you choose; it cannot read messages, servers, or your account. The access token Discord returns is discarded immediately and never stored.
- What is stored is the **webhook URL**, obfuscated at rest the same way as the Google session (obfuscation, not real encryption). Anyone holding that URL can post to that channel, which is why linking requires the Deck's sudo password.
- The login callback is received by a listener bound to `127.0.0.1` only (nothing on your network can reach it), protected by a random single-use `state` value, and shut down after one attempt or 5 minutes.
- Only the screenshot you choose is uploaded, when you choose it. Messages are sent with mentions disabled, so a game name can never ping `@everyone`.
- **Unlink Discord** deletes the webhook on Discord and the local copy. You can also remove it any time under the channel's Integrations → Webhooks.
- Likewise the Discord application's client id/secret are your own, entered in the plugin and stored only on your Deck, obfuscated. They identify your app, not you, and each link is approved by you on Discord. `discord_credentials.json` (see `discord_credentials.example.json`) exists only for developers and personal builds.

## Dependencies

**Runtime (bundled into `dist/index.js`):**
| Package | Why |
|---|---|
| [`@decky/api`](https://www.npmjs.com/package/@decky/api) | Frontend↔backend RPC (`callable`, events), plugin registration |
| [`@decky/ui`](https://www.npmjs.com/package/@decky/ui) | Steam-styled UI components (panels, buttons, sliders, modals) |
| [`react-icons`](https://www.npmjs.com/package/react-icons) | Icons (camera, arrows, refresh) |
| [`qrcode-generator`](https://www.npmjs.com/package/qrcode-generator) | Fully local, dependency-free QR code rendering |
| `tslib` | TypeScript helper runtime |

**Backend:** Python standard library only (`asyncio`, `socket`, `secrets`, `shutil`, `json`, `re`, `base64`, `hashlib`, `ssl`, `urllib.request`/`urllib.parse`) plus the `decky` module Decky Loader itself provides. No pip packages are vendored.

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
1a. (Optional, for your own dev copy) copy `google_credentials.example.json` to `google_credentials.json` and `discord_credentials.example.json` to `discord_credentials.json` (both gitignored) and fill in your client id/secret. `npm run deploy` uploads them when present, so your Deck is already set up; `npm run deploy -- --no-credentials` deploys like a fresh public install, to test the in-plugin "Set up" flow. Placeholder values from the example files are ignored.
1b. (Only needed to work on the Discord integration) create an application at the [Discord Developer Portal](https://discord.com/developers/applications), add the redirect `http://localhost:47821/callback` under OAuth2, and copy `discord_credentials.example.json` to `discord_credentials.json` (gitignored) with its client id/secret.
2. `npm run deploy` — builds the frontend, stops `plugin_loader` on the Deck, uploads the plugin over SFTP, and restarts the service. (Stopping the service before uploading avoids a hot-reload race that could otherwise leave an orphaned, runaway plugin process — see the comments in `scripts/deploy.mjs`.)
3. `npm run build` / `npm run watch` still work standalone if you just want to compile without deploying.
4. `npm run package` — builds the frontend and produces the public `release/omni-revi-transfer-vX.Y.Z.zip` (no credentials inside; `npm run package:personal` makes a `-personal.zip` with yours, see [Personal build](#personal-build)), laid out exactly the way Decky Loader expects for a manual "Install Plugin from ZIP" (see [Installing](#installing-no-build-tools-needed) above). This doesn't need the official [decky CLI](https://github.com/SteamDeckHomebrew/cli) (which is Linux/macOS-only) — since this plugin has no native backend to cross-compile, zipping the already-built files ourselves (via the `archiver` package) is equivalent for our case. Verified end-to-end on a real Deck: extracting the zip the same way Decky's installer would and starting `plugin_loader` loads the plugin cleanly.

## Not currently used

These files are part of the original [decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template) this project was bootstrapped from, and are left in place but **not used** by this plugin:

- `backend/` (`Dockerfile`, `Makefile`, `entrypoint.sh`, `src/main.c`) — scaffold for an optional native (C) backend. This plugin is pure Python + TypeScript.
- `assets/logo.png` — not referenced anywhere in `src/`.
- `.vscode/build.sh`, `config.sh`, `setup.sh`, `tasks.json`, `defsettings.json` — the template's original VSCode-task-based build/deploy flow (Linux-only CLI tooling, a different `settings.json` shape). Fully superseded by `npm run deploy` above.
- `defaults/defaults.txt` — template documentation for an optional folder of default config/theme files to ship; this plugin doesn't have any.
- `py_modules/.keep` — placeholder; no vendored Python dependencies are used.
