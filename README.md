# Omni-Revi-Transfer — for Decky

**What the plugin does:** Omni-Revi-Transfer is a plugin for the Steam Deck (installed through [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader)) that lets you browse, manage and share the screenshots your Deck already takes, right from the in-game Quick Access Menu: send one to your phone with a QR code, to your Steam account or a Steam friend, to a Discord channel, or to your own Google Drive, without ever leaving your controller.

## Latest update — v0.0.9

- **Desktop Mode installer**: a `.desktop` file to install, configure your keys and uninstall the plugin with a small menu ([how](#download-and-install)).
- **Share to Discord and Steam**: post to a Discord channel, upload to your Steam account, or open a Steam friend's chat with the screenshot ready to send, plus optional **auto-upload** with a delay you choose.
- **Your own keys**: Google Drive and Discord use *your* app's credentials, so nothing secret ships with the plugin ([Google Drive](#setting-up-google-drive), [Discord](#setting-up-discord)).
- **Much lighter on big libraries**: menus and auto-upload stay instant even with thousands of screenshots.

Everything else, release by release, is in the [CHANGELOG](CHANGELOG.md).

## What it does

- **Gallery and preview**: every game's Steam screenshots (plus "SteamOS / Desktop" shots), newest first, 5 per page. Open one for a full-size preview with **Share** and **Delete**.
- **Share** (dropdown in the preview):
  - **QR Code**: a phone on the same Wi-Fi scans it and downloads the image (iPhone and Android alike). Nothing leaves your network.
  - **Steam (my account)**: uploads it to your Steam account with the privacy you pick (Private by default).
  - **Steam friend (chat)**: pick a friend (recent chats first) and their chat opens with the screenshot ready to send, like Steam's own Media → Share. You can tag it as a spoiler; **B** goes back to the friend list.
  - **Discord**: posts it, with the game name, to a channel you choose.
  - **Google Drive**: uploads it to `omni-revi-transfer/screenshots/<Game>` in your own Drive; re-uploading the same file is skipped.
- **Auto-upload** (off by default): Steam, Google Drive and Discord each get a toggle and a delay slider (5-60 s, 10 by default) in **Share options**. It only covers screenshots taken while the plugin runs, never the ones you already had.
- **Storage panel**: how much space your screenshots use, with a warning limit (0.5-50 GB or Unlimited) and alerts at 80/90/100%. An optional, off-by-default "auto-delete oldest when over the limit" exists and warns that it deletes from *any* game.
- **Share options**: link or unlink Google Drive and Discord, choose the Steam upload privacy, and set how long a QR link stays active.

## Why it works this way

- **It uses Steam's own screenshots** (Steam button + R1): the plugin reads and manages what Steam already saves, and finds your Steam account and screenshot folders by itself, including multi-account setups.
- **QR sharing is a tiny LAN-only server** written on `asyncio`, because the Python that Decky bundles has no `http.server`. It serves only the chosen file, under a random name.
- **Discord goes to a channel, never a DM.** Discord offers no legitimate way for an app to post or DM *as you* (that would be a self-bot). The allowed route is a webhook in a channel you pick, linked through the Deck's Steam browser. For a "DM to myself", create a private server with one channel.
- **Steam sharing runs inside Steam's own client**, since only it can upload to your account. The friend chat uses Steam's internal chat, which isn't a documented API and could change with a Steam update; if it stops working, the plugin falls back to just opening the chat.
- **You bring your own Google / Discord app**, so there is no shared secret to leak or to be rate limited or revoked for everyone at once.
- **The screenshot list is cached per folder**, so opening the menu and auto-upload checks stay instant even with 20,000 screenshots (measured on a Deck: ~0.6 ms per gallery refresh instead of ~358 ms).

## Download and install

Everything is on the [Releases page](https://github.com/Revivedx/omni-revi-transfer/releases/latest). Choose **one** of the two ways:

| | **Installer** (`.desktop`) | **ZIP** (manual) |
|---|---|---|
| File to download | `Omni-Revi-Transfer-Installer.desktop` | `omni-revi-transfer-vX.Y.Z.zip` |
| Where | Desktop Mode, double-click | Game Mode: Decky → Settings → Developer → *Install Plugin from ZIP* |
| Needs Decky's Developer Mode | No | Yes |
| Update / uninstall | From the same menu | Install the new zip over it / remove it in Decky |
| Entering your Google Drive / Discord keys | **Easy**: a "Configure keys" menu, also offered right after installing | **Harder**: only inside the plugin with the on-screen keyboard, or by creating the credential files by hand |
| Recommended | **Yes** | If you prefer not to run a script |

The installer is recommended because the keys are long strings that are tedious to type with the Deck's on-screen keyboard. Either way, Decky Loader must already be installed ([decky.xyz](https://decky.xyz)).

**Installer, step by step**

1. In **Desktop Mode**, download `Omni-Revi-Transfer-Installer.desktop`.
2. Right-click it → **Properties → Permissions** → tick **Is executable**, then double-click it. (Browsers don't download files as executable; if KDE asks whether to trust the launcher, launch it.)
3. Pick **Install**. It asks for the Deck's password once (the one you use for `sudo`; if you never set one, it tells you how) and then offers to enter your Google Drive and Discord keys. You can skip both and do it later with **Configure keys**, which appears once the plugin is installed.
4. Back in **Game Mode**, the plugin is in the Quick Access menu.

The installer only touches `~/homebrew/{plugins,settings,data,logs}/Omni-Revi-Transfer` and restarts Decky's `plugin_loader`. It is a plain shell script (`installer/omni-revi-transfer-installer.sh`) you can read first, it also works from a terminal (`status | install | configure | uninstall`), and it keeps a log in `~/.cache/omni-revi-transfer-installer.log`. If a release zip sits next to it or in `~/Downloads`, it uses that instead of downloading.

**ZIP, step by step**: download `omni-revi-transfer-vX.Y.Z.zip`, enable **Developer Mode** in Decky's settings, then in its **Developer** tab choose **Install Plugin from ZIP**.

**Need your Google Drive or Discord keys?** QR, Steam sharing, the gallery and the storage panel work without any setup. Google Drive and Discord need one you create yourself, and the guides below explain it step by step: [Setting up Google Drive](#setting-up-google-drive) (about 10 minutes) and [Setting up Discord](#setting-up-discord) (about 5).

## Setting up Google Drive

One time, best done on a computer:

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a project (any name).
2. **APIs & Services → Library**, search for **Google Drive API** and click **Enable**.
3. **Google Auth Platform** (older UI: *OAuth consent screen*) → **Get started**: any app name, your email as support and contact address, audience **External**.
4. **Data access → Add or remove scopes**: add only `.../auth/drive.file` and save.
5. **Audience → Publish app** (status "In production"). Without this, Google expires the login every 7 days and only listed test users can link. `drive.file` is a non-sensitive scope, so publishing your own app for your own use needs no Google review. If Google shows an "unverified app" notice while you link, that is your own app: continue.
6. **Clients → Create client**, type **TVs and Limited Input devices**, and copy the **Client ID** and **Client secret**.
7. Enter them with the installer's **Configure keys**, or in the plugin: **Share options → Google Drive → Set up Google Drive**.
8. **Link Google Drive** → confirm the Deck's password → scan the QR with your phone and approve.

Alternative to typing the ~70-character client ID: in Desktop Mode (or over SSH) create `google_credentials.json` in the plugin folder (`~/homebrew/plugins/Omni-Revi-Transfer/`) with the shape of `google_credentials.example.json`. Values entered in the plugin take priority over that file.

## Setting up Discord

One time:

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create a **New Application** (any name).
2. **OAuth2 → Redirects → Add Redirect**: `http://localhost:47821/callback`, then **Save Changes** (without saving it isn't stored).
3. Copy the **Application ID** (General Information) and, under OAuth2, **Reset Secret** and copy the **Client Secret**.
4. Enter them with the installer's **Configure keys**, or in the plugin: **Share options → Discord → Set up Discord**.
5. **Link Discord**: the authorization page opens in the Deck's Steam browser; log in (Discord's QR login works), then pick the server and channel. You need a server where you can manage webhooks; a private server of your own works as a "DM to myself" (Discord's **+ → Create My Own**).

No Discord review is needed for the `webhook.incoming` scope.

## Personal build

For developers who want the plugin on their own devices (another Deck, or another Linux PC running Decky Loader) with their credentials already inside:

- `npm run package` builds the **public** zip: `omni-revi-transfer-vX.Y.Z.zip`, which never contains credentials.
- `npm run package:personal` builds `omni-revi-transfer-vX.Y.Z-personal.zip`, which bundles your gitignored `google_credentials.json` and `discord_credentials.json` so nothing needs to be set up on the target device. **Never publish this one**: it contains your secrets. It refuses to build if a credentials file is missing.

## Security

The short version, since the usual worry is someone getting hold of your keys. More detail in [PRIVACY.md](PRIVACY.md).

- **Your keys never leave your Deck.** Your Google / Discord app keys and your linked sessions are stored only in the plugin's settings folder on your Deck (private to the system). There is no server and no telemetry, and the developer receives nothing.
- **Nothing secret ships with the plugin.** The release and this repository contain no credentials: every user creates their own app, so there is no shared secret to steal, and GitHub's push protection blocks accidental commits of one.
- **Honest limit: obfuscated, not encrypted.** Saved keys and sessions are scrambled with a key derived from the Deck itself. That stops casual snooping (a copied or accidentally opened file), but not someone with root access to your Deck, because the plugin itself has to read them. Even then, a stolen client ID and secret alone can't reach your accounts, since access still has to be approved by you, and you can reset the secret any time in Google or Discord.
- **Linking needs the Deck's password**, so someone who picks up your unlocked Deck can't attach their own account.
- **Minimum access, revocable any time.** Google: only `drive.file`, meaning files the plugin creates, never the rest of your Drive. Discord: only `webhook.incoming`, meaning it can post into the one channel you pick and can't read anything. Steam sharing goes through your own Steam client. **Unlink** in the plugin revokes the token with Google or deletes the Discord webhook; you can also use your [Google connected apps](https://myaccount.google.com/permissions) or the channel's Integrations settings.
- **Sharing safeguards.** A QR link is a random one-time address, only on your local network, serving only that file, and it closes after the download or the timeout (anyone on your Wi-Fi with that exact address could fetch it meanwhile). Auto-upload is off by default and has no filter: it uploads whatever you screenshot, and the delay is your window to delete it first. Steam uploads count against your Cloud space and the plugin can't delete them; the **Public** privacy makes them visible on your profile.
- **The installer** is a readable shell script, asks for your password once, and only touches the plugin's folders.

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
1a. (Optional, for your own dev copy) copy `google_credentials.example.json` to `google_credentials.json` and `discord_credentials.example.json` to `discord_credentials.json` (both gitignored) and fill in your client id/secret. `npm run deploy` uploads them when present, so your Deck is already set up; `npm run deploy -- --no-credentials` deploys like a fresh public install, to test the in-plugin "Set up" flow. Placeholder values from the example files are ignored. (For Discord, create the application as in [Setting up Discord](#setting-up-discord).)
2. `npm run deploy` — builds the frontend, stops `plugin_loader` on the Deck, uploads the plugin over SFTP, and restarts the service. (Stopping the service before uploading avoids a hot-reload race that could otherwise leave an orphaned, runaway plugin process — see the comments in `scripts/deploy.mjs`.)
3. `npm run build` / `npm run watch` still work standalone if you just want to compile without deploying.
4. `npm run package` — builds the frontend and produces the public `release/omni-revi-transfer-vX.Y.Z.zip` (no credentials inside; `npm run package:personal` makes a `-personal.zip` with yours, see [Personal build](#personal-build)), laid out exactly the way Decky Loader expects for a manual "Install Plugin from ZIP" (see [Download and install](#download-and-install) above). This doesn't need the official [decky CLI](https://github.com/SteamDeckHomebrew/cli) (which is Linux/macOS-only) — since this plugin has no native backend to cross-compile, zipping the already-built files ourselves (via the `archiver` package) is equivalent for our case.

## Publishing a release (maintainer notes)

1. Bump `version` in `package.json`, add the entry to `CHANGELOG.md`, and refresh the **Latest update** section at the top of this README and the version in `PRIVACY.md`.
2. `npm run package` builds `release/omni-revi-transfer-vX.Y.Z.zip` (public, no credentials) and copies `omni-revi-transfer-installer.sh` and `Omni-Revi-Transfer-Installer.desktop` next to it.
3. Create a GitHub Release tagged `vX.Y.Z` and attach **those three files**: `omni-revi-transfer-vX.Y.Z.zip`, `Omni-Revi-Transfer-Installer.desktop` and `omni-revi-transfer-installer.sh`. The installer looks for an asset named `omni-revi-transfer-v*.zip` in the latest release, and the `.desktop` fetches the script from this repository's `main` branch when it isn't next to it.
4. **Never** attach the `...-personal.zip` (`npm run package:personal`): it contains the developer's own OAuth credentials.

## Not currently used

These files are part of the original [decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template) this project was bootstrapped from, and are left in place but **not used** by this plugin:

- `backend/` (`Dockerfile`, `Makefile`, `entrypoint.sh`, `src/main.c`) — scaffold for an optional native (C) backend. This plugin is pure Python + TypeScript.
- `assets/logo.png` — not referenced anywhere in `src/`.
- `.vscode/build.sh`, `config.sh`, `setup.sh`, `tasks.json`, `defsettings.json` — the template's original VSCode-task-based build/deploy flow (Linux-only CLI tooling, a different `settings.json` shape). Fully superseded by `npm run deploy` above.
- `defaults/defaults.txt` — template documentation for an optional folder of default config/theme files to ship; this plugin doesn't have any.
- `py_modules/.keep` — placeholder; no vendored Python dependencies are used.
