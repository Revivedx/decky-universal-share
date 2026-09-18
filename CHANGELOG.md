# Changelog

All notable changes to Decky Universal Share are documented in this file. Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.0.5a] - 2026-09-18

### Added
- Google Drive upload from the screenshot preview's Share menu, organized under `decky-universal-share/screenshots/<Game Name>` (or `SteamOS` for shots taken outside a game), with duplicate-upload detection.
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
