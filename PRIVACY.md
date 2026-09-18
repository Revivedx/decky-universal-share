# Privacy Policy — Decky Universal Share

**Last updated:** 2026-09-18 (plugin version 0.0.5a)

Decky Universal Share ("the plugin") is a [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin for the Steam Deck. It runs entirely on the user's own device. This document explains what data the plugin touches, how the optional Google Drive feature works, and how to contact us.

**Note:** as of version 0.0.5a, the Google Drive feature described below is implemented but temporarily disabled in the shipped plugin while its Google OAuth app goes through Google's verification process. This policy describes how it behaves once enabled.

## Summary

- The plugin does **not** operate any server of its own, does **not** collect analytics or telemetry, and does **not** send any data to the developer.
- All data the plugin handles (screenshots, settings) stays on the user's Steam Deck, except when the user explicitly chooses to share a screenshot (via the local QR feature, or the optional Google Drive integration described below).
- The developer has no access to, and never receives a copy of, any user's screenshots, Google account, or Google Drive contents.

## What the plugin accesses on the device

- **Steam's own screenshot files**, already saved locally by Steam itself (Steam button + R1/RB), under the standard `userdata/<account>/760/remote/<appid>/screenshots/` path. The plugin reads this folder to build the in-app gallery, and can delete a file from it only when the user explicitly taps "Delete" on that screenshot.
- **A local settings file** (storage-limit preference, auto-delete toggle, QR share duration) stored inside the plugin's own Decky-managed settings directory on the Deck.
- Nothing outside of Steam's own screenshots folder and the plugin's own settings folder is read, written, or scanned.

## Share via QR (local network only)

When the user chooses "Share via QR" for a screenshot, the plugin starts a temporary local HTTP server on the Deck's own LAN address and shows a QR code (generated **entirely on-device**, with no third-party QR/analytics service involved) that a phone on the same Wi-Fi network can scan to download that one file. This server:

- Serves only the single screenshot the user selected, under a random, high-entropy, one-time filename — never the whole gallery.
- Shuts itself down automatically after the first successful download (with a short grace period) or after a short timeout if nobody downloads it.
- Never leaves the local network; no screenshot or file data is transmitted to the developer or to any third-party server.

## Google Drive integration (optional, off unless the user links it)

Decky Universal Share optionally lets a user upload a screenshot to **their own** Google Drive. This feature is entirely opt-in:

- **What we access:** the plugin requests only the [`drive.file`](https://developers.google.com/drive/api/guides/api-specific-auth) OAuth scope — the narrowest scope Google Drive offers. This scope only ever grants access to files and folders that this plugin itself creates in the user's Drive (organized under a `decky-universal-share/screenshots/<Game Name>` folder structure it creates on first upload). The plugin cannot see, list, read, or modify any other file already in the user's Drive.
- **What we upload:** only the specific screenshot file the user explicitly chooses to upload, at the moment they choose to upload it. Nothing is uploaded automatically or in the background.
- **Where the session is stored:** signing in uses Google's OAuth "device flow" (the user approves access on their own phone/browser, not by giving the plugin a password). Google then issues a long-lived refresh token, which is stored **locally on the user's own Steam Deck only** — never transmitted to the developer or to any server other than Google's own OAuth endpoints. That local file is obfuscated at rest (not left as human-readable plaintext) as a defense-in-depth measure against casual exposure, though this is disclosed to the user as obfuscation rather than strong encryption before they link their account, alongside an explicit warning about what a compromised device could mean for that saved session.
- **Revoking access:** the user can unlink Google Drive at any time from the plugin's Share options panel. This deletes the locally stored session and revokes the token with Google directly, exactly like removing an app from your [Google Account's connected apps list](https://myaccount.google.com/permissions).
- **No server-side component:** there is no backend server operated by the developer that ever sees, proxies, stores, or logs any user's screenshots, Google account information, or Drive contents. All communication is directly between the user's own Steam Deck and Google's own servers.

## Data retention and deletion

- Screenshots are retained exactly as long as the user keeps them in Steam's own screenshots folder (or, in Google Drive, in the user's own Drive) — the plugin does not impose its own retention policy beyond the user's own configured, opt-in "auto-delete when over a storage limit" setting, which is off by default.
- Uninstalling the plugin removes its local settings and any locally stored Google session token from the Deck. It does not delete the user's Steam screenshots or anything already uploaded to their Google Drive.

## Children's privacy

This plugin is a general-purpose utility for Steam Deck screenshot management and is not directed at children. It does not knowingly collect any personal information, from children or otherwise, since it collects no personal information at all — see "Summary" above.

## Changes to this policy

If this policy changes, the updated version will be published at this same URL in the plugin's GitHub repository, with the "Last updated" date above revised accordingly.

## Contact

For questions about this policy or the plugin's data handling, contact: **decky.universal.share@gmail.com**

Source code (fully open and auditable): https://github.com/Revivedx/decky-universal-share
