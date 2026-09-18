# Changelog

All notable changes to Omni-Revi-Transfer are documented in this file. Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Discord sharing**: link a Discord channel from Share options (OAuth `webhook.incoming`, completed in the Deck's Steam browser through a `localhost` callback) and post screenshots to it from the Share dropdown. Includes the same sudo-password confirmation as Google Drive, obfuscated storage of the webhook, single-use `state` protection, mentions disabled on posts, and webhook deletion on unlink. Posts to a channel only; Discord offers no legitimate way to DM as the user.
- `discord_credentials.example.json`, and `DISCORD_ENABLED` flags (on for `test`, off for `main`) with the same release-zip gating as Google credentials.
- `PRIVACY.md` and README sections for Discord.

### Changed
- Google Drive is enabled on the `test` branch (`GOOGLE_DRIVE_ENABLED = True` in `main.py` and `src/index.tsx`) for testing under the renamed project; `main` keeps it off until Google's verification is approved.
- Token storage helpers (`_load_obfuscated_json` / `_save_obfuscated_json`) are now shared between Google Drive and Discord.

### Removed
- The "iCloud (coming soon)" placeholder in the Share menu. Apple offers no public API to upload into a user's iCloud Drive/Photos from a third-party app off Apple platforms; the rationale is documented in the README's design decisions. Share via QR remains the way to send screenshots to iPhones.

## [0.0.5b] - 2026-09-18

### Changed
- Renamed the project from "Decky Universal Share" to **Omni-Revi-Transfer — for Decky** (GitHub repo, package/plugin names, in-app title, docs, and the Google Drive upload folder path).

## [0.0.5a] - 2026-09-18

### Added
- Google Drive upload from the screenshot preview's Share menu, organized under `decky-universal-share/screenshots/<Game Name>` (or `SteamOS` for shots taken outside a game) at the time of this release — see [Unreleased](#unreleased) above for the later folder-path rename, with duplicate-upload detection.
- Google OAuth device-flow linking (scan a QR, approve on your phone) with a step-up sudo-password confirmation gate before a link can start, and an explicit on-screen disclosure of what a compromised Deck could mean for the saved session.
- At-rest obfuscation (not full encryption, disclosed as such) for the locally stored Google session token.
- "Unlimited" option for the storage warning limit, alongside the existing 0.5–50 GB range.
- Storage usage alerts that fire once per threshold crossing at 80/90/100%.
- `PRIVACY.md`, written to support Google's OAuth app verification process.
- `npm run package` / `scripts/package.mjs`: builds a distributable install `.zip` for manual sideloading, with no build tools required on the installing end.
- `google_credentials.example.json`: documents the shape of the (gitignored) local file main.py loads Google OAuth credentials from.

### Changed
- Google OAuth client id/secret moved out of `main.py` into a gitignored `google_credentials.json`, loaded at runtime, since GitHub's push protection flags OAuth secrets in public repos.
- `scripts/package.mjs` only bundles `google_credentials.json` into the release zip once `GOOGLE_DRIVE_ENABLED` is `True`, so the real secret never ships inside a public release artifact while the feature is off.
- Storage and Share options panels are now collapsible, and the current storage limit is shown as a prominent sentence above the slider.
- Screenshot preview modal rebuilt on `ModalRoot` instead of `ConfirmModal`, so the controller's B button only closes the preview and never triggers Delete.
- Password fields (the sudo confirmation prompt) are now properly masked and submit on Enter.
- Project metadata (`package.json`, `plugin.json`, `LICENSE`) fully rebranded from the original decky-plugin-template placeholders.

### Fixed
- Auto-delete and storage-alert logic now correctly skip enforcement entirely when the limit is set to Unlimited, instead of risking treating "0 MB" as a real (and catastrophic) limit.

### Known limitations
- Google Drive is implemented but shipped **disabled** (`GOOGLE_DRIVE_ENABLED = False`) pending Google's OAuth app verification — see `README.md`'s "Google Drive status" section.

## [0.0.1] - 2026-09-17

### Added
- Initial release: gallery of Steam's native screenshots (5 per page, Previous/Next), full-resolution preview, Delete.
- Local, LAN-only "Share via QR" with a locally generated QR code, one-time random filename, and automatic shutdown after download or timeout.
- Storage panel with a configurable warning limit (0.5–50 GB) and an opt-in, off-by-default auto-delete-oldest toggle.
- Automatic Steam account/screenshot-folder detection (single and multi-account).
