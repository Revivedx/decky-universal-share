# Development

How to build, test on a Deck and publish a release. If you just want to use the plugin, see the [README](README.md).

## Setup

`npm install`, then put your Deck's connection info in a root-level `settings.json` (gitignored):

```json
{ "deckIP": "192.168.x.x", "deckPort": "22", "deckUser": "deck", "deckPass": "..." }
```

**Credentials for your own dev copy (optional).** Copy `google_credentials.example.json` to `google_credentials.json` and `discord_credentials.example.json` to `discord_credentials.json` (both gitignored) and fill in your app's client ID and secret (create the apps as in the README's setup guides). Deploying uploads them when present, so your Deck needs no setup. Keys entered inside the plugin take priority over these files, and the placeholder values from the examples are ignored.

## Commands

| Command | What it does |
|---|---|
| `npm run deploy` | Builds, stops `plugin_loader` on the Deck, uploads the plugin over SFTP and restarts it. (Stopping first avoids a hot-reload race that can leave a runaway plugin process; see `scripts/deploy.mjs`.) |
| `npm run deploy -- --no-credentials` | Deploys like a fresh public install, to test the in-plugin "Set up" flow. |
| `npm run build` / `npm run watch` | Compile only. |
| `npm run package` | Builds the **public** zip `release/omni-revi-transfer-vX.Y.Z.zip` (no credentials) and copies the installer files next to it. |
| `npm run package:personal` | Builds `...-personal.zip` with your credentials for your own devices. **Never publish it.** It refuses to build if a credentials file is missing. |

The zip is made with `archiver` instead of the official [decky CLI](https://github.com/SteamDeckHomebrew/cli) (Linux/macOS only), which is equivalent because the plugin has no native backend to compile.

## Publishing a release

1. Bump `version` in `package.json`, add the entry to `CHANGELOG.md`, and refresh the **Latest update** section of the README and the version in `PRIVACY.md`.
2. `npm run package`.
3. Create a GitHub Release tagged `vX.Y.Z` and attach **three files** from `release/`: `omni-revi-transfer-vX.Y.Z.zip`, `Omni-Revi-Transfer-Installer.desktop` and `omni-revi-transfer-installer.sh`. The installer looks for an asset named `omni-revi-transfer-v*.zip` in the latest release, and the `.desktop` fetches the script from the `main` branch when it isn't next to it.
4. **Never** attach the `-personal` zip: it contains your OAuth credentials.
