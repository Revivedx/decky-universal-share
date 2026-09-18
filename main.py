"""Omni-Revi-Transfer - backend.

V1 scope: a gallery and manager for the screenshots Steam already takes
natively (Steam button + R1/RB), instead of the plugin capturing on its own.

Decision history (in case this is revisited later):

- L4+R4 button combo to trigger a capture: investigated and dropped. The
  Deck's internal controller doesn't expose its real protocol (including
  L4/R4) via generic evdev -- Steam consumes it exclusively while running
  (always, in Game Mode). Confirmed on hardware: zero events reach the
  controller's evdev interfaces when pressing the paddles.

- Capturing screenshots ourselves via `gamescopectl screenshot`: it worked
  (it's gamescope's official command to dump the composited screen), but it
  was dropped in favor of Steam's native screenshots because:
  (a) Steam already has a physical shortcut that works at all times,
  (b) its capture doesn't include overlays like Decky's quick access menu,
  something our own capture did include and couldn't cleanly avoid, and
  (c) Steam already generates its own thumbnails, avoiding an ffmpeg dependency.
"""
from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

import decky

# --- Steam: account location and detection ----------------------------------

STEAM_HOME = os.path.join(decky.DECKY_USER_HOME, ".local", "share", "Steam")
STEAM_USERDATA_ROOT = os.path.join(STEAM_HOME, "userdata")
STEAM_LOGINUSERS_VDF = os.path.join(STEAM_HOME, "config", "loginusers.vdf")
STEAM_APPMANIFEST_DIR = os.path.join(STEAM_HOME, "steamapps")
# Valve's fixed offset to go from a SteamID64 (individual, public universe)
# to the 32-bit "account id" used as the folder name under userdata/.
STEAM_ID64_BASE = 76561197960265728

_SCREENSHOT_EXTENSIONS = (".jpg", ".jpeg", ".png")


def _list_userdata_account_ids() -> list[str]:
    try:
        return [d for d in os.listdir(STEAM_USERDATA_ROOT) if d.isdigit()]
    except OSError:
        return []


def _account_id_from_loginusers() -> Optional[str]:
    try:
        with open(STEAM_LOGINUSERS_VDF, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return None

    candidates = []  # (account_id, most_recent, timestamp)
    for match in re.finditer(r'"(\d{17})"\s*\{([^{}]*)\}', content):
        steamid64, block = match.group(1), match.group(2)
        account_id = str(int(steamid64) - STEAM_ID64_BASE)
        most_recent = re.search(r'"MostRecent"\s*"1"', block) is not None
        ts_match = re.search(r'"Timestamp"\s*"(\d+)"', block)
        timestamp = int(ts_match.group(1)) if ts_match else 0
        candidates.append((account_id, most_recent, timestamp))

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[1], c[2]), reverse=True)
    return candidates[0][0]


def _detect_steam_account_id() -> Optional[str]:
    account_ids = _list_userdata_account_ids()
    if len(account_ids) == 1:
        return account_ids[0]
    detected = _account_id_from_loginusers()
    if detected and detected in account_ids:
        return detected
    return account_ids[0] if account_ids else None


def _steam_remote_root() -> Optional[str]:
    account_id = _detect_steam_account_id()
    if account_id is None:
        return None
    remote = os.path.join(STEAM_USERDATA_ROOT, account_id, "760", "remote")
    return remote if os.path.isdir(remote) else None


def _steamapps_dirs() -> list[str]:
    """Every Steam library's steamapps/ folder: the internal one plus any
    others (e.g. a microSD card) listed in libraryfolders.vdf. A game
    installed on the card has its appmanifest there, not in the internal
    library."""
    dirs = [STEAM_APPMANIFEST_DIR]
    try:
        with open(os.path.join(STEAM_APPMANIFEST_DIR, "libraryfolders.vdf"), "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return dirs
    for library_path in re.findall(r'"path"\s*"([^"]+)"', content):
        candidate = os.path.join(library_path, "steamapps")
        if candidate not in dirs:
            dirs.append(candidate)
    return dirs


def _resolve_app_name(appid: str) -> str:
    if appid == "7":
        return "SteamOS / Desktop"
    for steamapps_dir in _steamapps_dirs():
        manifest = os.path.join(steamapps_dir, f"appmanifest_{appid}.acf")
        try:
            with open(manifest, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except OSError:
            continue
        match = re.search(r'"name"\s*"([^"]+)"', content)
        if match:
            return match.group(1)
    return f"Game ({appid})"


def _resolve_drive_folder_name(appid: str) -> str:
    """Like _resolve_app_name, but a plain "SteamOS" for Drive folder names
    (no "/ Desktop" suffix -- that's fine as an in-app label, less so as a
    folder name)."""
    if appid == "7":
        return "SteamOS"
    return _resolve_app_name(appid)


# --- Screenshot listing and metadata -----------------------------------------

def _list_steam_screenshots() -> list[dict]:
    """Walks userdata/<id>/760/remote/<appid>/screenshots/ for every game."""
    remote_root = _steam_remote_root()
    if remote_root is None:
        return []

    results = []
    try:
        appid_dirs = os.listdir(remote_root)
    except OSError:
        return []

    for appid in appid_dirs:
        shots_dir = os.path.join(remote_root, appid, "screenshots")
        if not os.path.isdir(shots_dir):
            continue
        try:
            entries = os.scandir(shots_dir)
        except OSError:
            continue
        with entries:
            for e in entries:
                if not e.is_file() or not e.name.lower().endswith(_SCREENSHOT_EXTENSIONS):
                    continue
                thumb_path = os.path.join(shots_dir, "thumbnails", e.name)
                results.append({
                    "path": e.path,
                    "filename": e.name,
                    "appid": appid,
                    "modified": e.stat().st_mtime,
                    "size": e.stat().st_size,
                    "thumbnail_path": thumb_path if os.path.isfile(thumb_path) else e.path,
                })

    results.sort(key=lambda item: item["modified"], reverse=True)
    return results


def _is_inside_steam_screenshots(path: str) -> Optional[str]:
    """Returns the real path if it falls inside a valid screenshots/ folder."""
    remote_root = _steam_remote_root()
    if remote_root is None:
        return None
    real = os.path.realpath(path)
    base = os.path.realpath(remote_root)
    if not real.startswith(base + os.sep) or os.path.basename(os.path.dirname(real)) != "screenshots":
        return None
    return real


def _extract_appid_from_screenshot_path(real_path: str) -> Optional[str]:
    """Screenshots live at .../remote/<appid>/screenshots/<file>, so the appid
    is just the parent-of-parent directory name."""
    screenshots_dir = os.path.dirname(real_path)
    appid_dir = os.path.dirname(screenshots_dir)
    appid = os.path.basename(appid_dir)
    return appid if appid.isdigit() else None


def _file_to_data_uri(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _total_storage_bytes() -> int:
    return sum(item["size"] for item in _list_steam_screenshots())


def _delete_screenshot_files(path: str) -> bool:
    """Deletes a screenshot and its cached thumbnail. `path` must already be a validated real path."""
    try:
        os.remove(path)
    except OSError as e:
        decky.logger.warning(f"Could not delete {path}: {e}")
        return False
    thumb = os.path.join(os.path.dirname(path), "thumbnails", os.path.basename(path))
    if os.path.isfile(thumb):
        try:
            os.remove(thumb)
        except OSError:
            pass
    return True


async def _enforce_auto_delete(settings: dict) -> int:
    """If auto_delete is on and the limit was exceeded, deletes the oldest files until back under it.

    Returns how many files were deleted (0 if nothing applied).
    """
    # max_storage_mb == 0 means "Unlimited" -- there's no limit to enforce,
    # and treating 0 bytes as a real limit here would try to delete
    # everything. The frontend already disables this toggle when Unlimited
    # is selected, but this guard is what actually prevents catastrophe if
    # that combination ever ends up saved anyway.
    if not settings.get("auto_delete") or settings.get("max_storage_mb", 0) == 0:
        return 0

    limit_bytes = settings["max_storage_mb"] * 1024 * 1024
    items = _list_steam_screenshots()  # already sorted newest to oldest
    total_bytes = sum(item["size"] for item in items)
    if total_bytes <= limit_bytes:
        return 0

    deleted = 0
    for item in reversed(items):  # oldest to newest
        if total_bytes <= limit_bytes:
            break
        if _delete_screenshot_files(item["path"]):
            total_bytes -= item["size"]
            deleted += 1

    if deleted:
        decky.logger.info(f"Auto-delete: removed {deleted} screenshot(s) for exceeding the limit.")
        await decky.emit("auto_delete_performed", deleted)
    return deleted


# Checked from get_screenshots() (the closest proxy we have to "a screenshot
# was just taken", since we don't control Steam's own capture) and from
# get_settings(). Emits once per threshold *crossing*, not on every check,
# by remembering the highest threshold already alerted for; that memory
# resets once usage drops back under 80% (e.g. after deleting files), so a
# later re-crossing alerts again instead of staying silent forever.
STORAGE_ALERT_THRESHOLDS = (100, 90, 80)


async def _check_storage_alerts() -> None:
    settings = _load_settings()
    if settings.get("max_storage_mb", 0) == 0:
        return  # Unlimited: no threshold makes sense against no limit.
    limit_bytes = settings["max_storage_mb"] * 1024 * 1024
    used_bytes = _total_storage_bytes()
    pct = (used_bytes / limit_bytes * 100) if limit_bytes else 0
    last_alerted = settings.get("_last_storage_alert", 0)

    crossed = next((t for t in STORAGE_ALERT_THRESHOLDS if pct >= t > last_alerted), None)
    if crossed is not None:
        settings["_last_storage_alert"] = crossed
        _save_settings(settings)
        decky.logger.info(f"Storage alert: usage crossed {crossed}% ({pct:.1f}% used).")
        await decky.emit("storage_threshold_reached", crossed)
    elif pct < 80 and last_alerted:
        settings["_last_storage_alert"] = 0
        _save_settings(settings)


# --- QR sharing (local HTTP server) ------------------------------------------

# Only a copy of the chosen file is served, from our own staging folder (not
# the real Steam folder), so the rest of the screenshot library isn't exposed
# over the network while the server is active.
#
# Hardened on purpose because the server has no TLS or login: anyone on the
# same network could try to guess the URL while it's active.
# - The public name is a high-entropy random token (not the real Steam
#   filename, which follows a guessable date pattern).
# - After the first successful download, a grace period is given (instead of
#   shutting down immediately) in case the phone's browser needs more than
#   one request to finish saving (preview + separate save action).
# - It also shuts down on its own after the configured maximum time, in case
#   nobody downloads it.
#
# Important: the server lives in the backend (the plugin's own process), NOT
# tied to the frontend modal's lifecycle -- closing the share window on the
# Deck to go check the phone must NOT shut it down.
SHARE_STAGING_DIR = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "share")
SHARE_DEFAULT_DURATION_SECONDS = 600
SHARE_GRACE_PERIOD_SECONDS = 60


async def _handle_share_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    expected_name: str,
    file_path: str,
    content_type: str,
    on_downloaded,
) -> None:
    """Minimal hand-rolled HTTP GET server.

    `http.server`/`socketserver`/`wsgiref.simple_server` are not available in
    the packaged Python that decky-loader uses to run plugins (confirmed on a
    real Deck: ModuleNotFoundError even for `wsgiref.simple_server`, which
    internally depends on `http.server`). `socket` and `asyncio` are
    available, so this implements the bare minimum: parse the request line,
    serve the file if the name exactly matches the expected token, and close
    the connection.
    """
    served = False
    try:
        request_line = await reader.readline()
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        try:
            method, raw_path, _ = request_line.decode("latin-1").split(None, 2)
        except ValueError:
            return

        requested_name = urllib.parse.unquote(raw_path.lstrip("/"))

        if method != "GET" or requested_name != expected_name or not os.path.isfile(file_path):
            writer.write(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return

        with open(file_path, "rb") as f:
            data = f.read()

        header = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(data)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("latin-1")
        writer.write(header + data)
        await writer.drain()
        served = True
    except (OSError, ConnectionError) as e:
        decky.logger.debug(f"share-server: connection interrupted: {e}")
    finally:
        writer.close()
        if served:
            on_downloaded()


def _get_lan_ip() -> str:
    # Standard trick: opening a "connected" UDP socket to an external IP
    # doesn't send any real traffic, it just makes the OS pick the correct
    # outbound interface, from which the local IP on that network can be read.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class _ShareServer:
    def __init__(self) -> None:
        self._server: Optional[asyncio.AbstractServer] = None
        self._auto_stop_task: Optional[asyncio.Task] = None

    async def start(self, file_path: str, duration_seconds: int = SHARE_DEFAULT_DURATION_SECONDS) -> Optional[str]:
        await self.stop()  # only one file is shared at a time

        shutil.rmtree(SHARE_STAGING_DIR, ignore_errors=True)
        os.makedirs(SHARE_STAGING_DIR, exist_ok=True)
        ext = os.path.splitext(file_path)[1].lower()
        public_name = secrets.token_urlsafe(16) + ext
        staged_path = os.path.join(SHARE_STAGING_DIR, public_name)
        shutil.copyfile(file_path, staged_path)
        content_type = "image/png" if ext == ".png" else "image/jpeg"

        def on_downloaded() -> None:
            decky.logger.info(
                f"QR share: downloaded, shutting down in {SHARE_GRACE_PERIOD_SECONDS}s (grace period)."
            )
            asyncio.get_event_loop().create_task(decky.emit("qr_share_downloaded"))
            if self._auto_stop_task is not None:
                self._auto_stop_task.cancel()
            self._auto_stop_task = asyncio.get_event_loop().create_task(
                self._auto_stop(SHARE_GRACE_PERIOD_SECONDS)
            )

        handler = functools.partial(
            _handle_share_request,
            expected_name=public_name,
            file_path=staged_path,
            content_type=content_type,
            on_downloaded=on_downloaded,
        )
        try:
            self._server = await asyncio.start_server(handler, "0.0.0.0", 0)
        except OSError as e:
            decky.logger.warning(f"Could not start the share server: {e}")
            return None

        port = self._server.sockets[0].getsockname()[1]
        url = f"http://{_get_lan_ip()}:{port}/{public_name}"
        self._auto_stop_task = asyncio.get_event_loop().create_task(self._auto_stop(duration_seconds))
        decky.logger.info(f"Sharing (token hidden) at {url}, expires in {duration_seconds}s or on first download.")
        return url

    async def _auto_stop(self, duration_seconds: int) -> None:
        await asyncio.sleep(duration_seconds)
        decky.logger.info(f"QR share: shut down on its own after {duration_seconds}s with no download.")
        await self.stop()

    async def stop(self) -> None:
        if self._auto_stop_task is not None:
            self._auto_stop_task.cancel()
            self._auto_stop_task = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        shutil.rmtree(SHARE_STAGING_DIR, ignore_errors=True)


_share_server = _ShareServer()


# --- Google Drive (OAuth device flow + upload) -------------------------------

# Feature flag: enabled on the `test` branch; keep it False on `main` until
# Google's OAuth verification (see PRIVACY.md) is approved, so public
# releases don't ship Drive linking (and its "unverified app" warning)
# early. Nothing below this flag is removed -- it's fully implemented and
# tested, just gated. The frontend mirrors this flag in src/index.tsx.
GOOGLE_DRIVE_ENABLED = True

# Device flow ("TVs and Limited Input devices" OAuth client) is used instead
# of a loopback-redirect flow: it needs no local HTTP server at all (just
# outbound HTTPS calls), which fits a gamepad-driven, no-keyboard device much
# better -- the user approves on their phone by scanning a QR that encodes
# Google's verification_url_complete.
#
# The client ID/secret are meant to be embedded in the distributed app;
# Google's own docs treat "installed app" / device-flow clients as public,
# not confidential (the security boundary is user consent, not secrecy of
# these values) -- unlike a server-side OAuth client's secret. Even so, they
# live in `google_credentials.json` (gitignored, see .gitignore) instead of
# hardcoded here, since this repo is public and GitHub's own push-protection
# flags OAuth client secrets on sight -- keeping them out of git avoids that
# entirely, with no change to how they're used at runtime. That file is
# deployed/packaged alongside main.py like any other plugin file (see
# scripts/deploy.mjs and scripts/package.mjs); see
# google_credentials.example.json for the expected shape.
GOOGLE_CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "google_credentials.json")


def _load_google_oauth_credentials() -> tuple:
    try:
        with open(GOOGLE_CREDENTIALS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("client_id"), data.get("client_secret")
    except (OSError, ValueError, json.JSONDecodeError):
        return None, None


GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET = _load_google_oauth_credentials()
# Deliberately narrow scope: drive.file only grants access to files this app
# itself creates, never the rest of the user's Drive. Both a privacy
# best-practice and it keeps this app out of Google's "restricted scope"
# review tier, which requires a formal security assessment.
GOOGLE_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
GOOGLE_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"

GOOGLE_TOKEN_PATH = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "google_drive.json")

# In-progress device-flow state. Kept server-side only (never sent to the
# frontend) since there's no need for the UI to see the raw device_code.
_google_device_flow_state: dict = {}

# The Python distribution decky-loader uses to run plugin backends has broken
# default SSL verify paths -- confirmed on a real Deck: `ssl.create_default_context()`
# with no arguments fails every HTTPS request with CERTIFICATE_VERIFY_FAILED /
# "unable to get local issuer certificate", even though the system's own
# `python3` (a different interpreter) verifies the exact same host just fine.
# The fix is to explicitly point at the system's real CA bundle instead of
# relying on OpenSSL's own (apparently misconfigured, in this runtime)
# auto-detection.
_CA_BUNDLE_CANDIDATES = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/pki/tls/cert.pem",
)


def _build_ssl_context() -> ssl.SSLContext:
    for candidate in _CA_BUNDLE_CANDIDATES:
        if os.path.isfile(candidate):
            return ssl.create_default_context(cafile=candidate)
    decky.logger.warning("No CA bundle found in known locations; HTTPS certificate validation may fail.")
    return ssl.create_default_context()


_SSL_CONTEXT = _build_ssl_context()


def _http_post_form(url: str, fields: dict) -> dict:
    """Blocking form-encoded POST with a JSON response. Always run via an executor."""
    data = urllib.parse.urlencode(fields).encode("ascii")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"error": "http_error", "error_description": body}
    except OSError as e:
        return {"error": "network_error", "error_description": str(e)}


def _http_json_request(url: str, method: str, payload: Optional[dict], access_token: str) -> dict:
    """Blocking JSON request against the Drive API (search/create folder calls)."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"error": {"message": body}}
    except OSError as e:
        return {"error": {"message": str(e)}}


def _get_or_create_drive_folder(access_token: str, name: str, parent_id: Optional[str]) -> Optional[str]:
    """Finds an existing folder by name (among files this app can see) or creates it.

    `drive.file` scope can't browse the user's whole Drive, but it can search
    among files/folders the app itself created -- which is exactly what this
    needs, since the app is the one that creates this folder in the first place.
    """
    query_parts = ["name = '" + name.replace("'", "\\'") + "'", "mimeType = 'application/vnd.google-apps.folder'", "trashed = false"]
    if parent_id:
        query_parts.append(f"'{parent_id}' in parents")
    query = " and ".join(query_parts)
    search_url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode({"q": query, "fields": "files(id)"})
    result = _http_json_request(search_url, "GET", None, access_token)
    files = result.get("files") or []
    if files:
        return files[0]["id"]

    payload = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        payload["parents"] = [parent_id]
    created = _http_json_request("https://www.googleapis.com/drive/v3/files", "POST", payload, access_token)
    return created.get("id")


def _drive_file_exists(access_token: str, filename: str, folder_id: str) -> bool:
    """Checks (by exact name, inside the given folder) whether this screenshot
    was already uploaded, so re-uploading the same file doesn't create a
    duplicate copy under a slightly different name."""
    query = " and ".join([
        "name = '" + filename.replace("'", "\\'") + "'",
        "trashed = false",
        f"'{folder_id}' in parents",
    ])
    url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode({"q": query, "fields": "files(id)"})
    result = _http_json_request(url, "GET", None, access_token)
    return bool(result.get("files"))


async def _get_drive_game_folder_id(access_token: str, appid: str) -> Optional[str]:
    """Returns the id of "omni-revi-transfer/screenshots/<Game Name>" in the
    user's Drive, creating any of the three levels on first use and caching
    their ids locally afterwards (one folder id per appid, plus the two
    shared parent folder ids)."""
    token_data = _load_google_token()
    if token_data is None:
        return None

    root_id = token_data.get("root_folder_id")
    if not root_id:
        root_id = await _run_blocking(_get_or_create_drive_folder, access_token, "omni-revi-transfer", None)
        if root_id is None:
            decky.logger.warning("Google Drive: could not create/find the app's root folder.")
            return None
        token_data["root_folder_id"] = root_id
        _save_google_token(token_data)

    screenshots_id = token_data.get("screenshots_folder_id")
    if not screenshots_id:
        screenshots_id = await _run_blocking(_get_or_create_drive_folder, access_token, "screenshots", root_id)
        if screenshots_id is None:
            decky.logger.warning("Google Drive: could not create/find the screenshots subfolder.")
            return None
        token_data["screenshots_folder_id"] = screenshots_id
        _save_google_token(token_data)

    game_folder_ids = token_data.get("game_folder_ids", {})
    cached_game_id = game_folder_ids.get(appid)
    if cached_game_id:
        return cached_game_id

    game_name = _resolve_drive_folder_name(appid)
    game_id = await _run_blocking(_get_or_create_drive_folder, access_token, game_name, screenshots_id)
    if game_id is None:
        decky.logger.warning(f"Google Drive: could not create/find the folder for '{game_name}'.")
        return None

    game_folder_ids[appid] = game_id
    token_data["game_folder_ids"] = game_folder_ids
    _save_google_token(token_data)
    return game_id


def _upload_file_to_drive(access_token: str, path: str, folder_id: Optional[str]) -> dict:
    """Blocking multipart upload (metadata + content in one request, up to ~5MB)."""
    filename = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    with open(path, "rb") as f:
        file_bytes = f.read()

    metadata_dict = {"name": filename}
    if folder_id:
        metadata_dict["parents"] = [folder_id]

    boundary = "omni_revi_transfer_boundary"
    metadata = json.dumps(metadata_dict).encode("utf-8")
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode("utf-8")
        + metadata
        + f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode("utf-8")
        + file_bytes
        + f"\r\n--{boundary}--".encode("utf-8")
    )

    req = urllib.request.Request(
        GOOGLE_UPLOAD_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
            resp.read()
        return {"ok": True, "error": None}
    except urllib.error.HTTPError as e:
        decky.logger.warning(f"Google Drive upload failed ({e.code}): {e.read().decode('utf-8', errors='ignore')}")
        return {"ok": False, "error": "upload_failed"}
    except OSError as e:
        decky.logger.warning(f"Google Drive upload failed: {e}")
        return {"ok": False, "error": "upload_failed"}


async def _run_blocking(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


async def _verify_sudo_password(password: str) -> bool:
    """`sudo -k` forces a fresh prompt (ignoring any cached sudo timestamp
    from something else), then `-S -v` reads the password from stdin and
    validates it without running an actual privileged command."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-k", "-S", "-v",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        decky.logger.warning("sudo is not available; cannot verify the password.")
        return False

    assert proc.stdin is not None
    proc.stdin.write((password + "\n").encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()
    returncode = await proc.wait()
    return returncode == 0


# --- At-rest obfuscation for the stored refresh token -----------------------
#
# Honest limitation, on purpose not oversold anywhere in the UI: this is
# obfuscation, not real encryption. The key is derived locally and the
# process needs to decrypt it with no human input (so it can silently
# refresh the access token before an upload), which means anyone with the
# same level of access as this plugin (the `deck` user, or root) can derive
# the exact same key and reverse it. What this DOES raise the bar against is
# casual/accidental exposure -- e.g. someone `cat`-ing the file out of
# curiosity, or a settings folder shared for support without realizing it
# holds a live credential -- instead of a plain-text token being immediately
# recognizable. Real protection against a live root compromise would require
# either not persisting the token at all, or a passphrase never stored on
# disk; both trade away the "stays linked silently" convenience this was
# built for, so they're offered as opt-in choices rather than forced here.
def _obfuscation_key() -> bytes:
    try:
        with open("/etc/machine-id", "r", encoding="utf-8") as f:
            machine_id = f.read().strip()
    except OSError:
        machine_id = decky.DECKY_USER_HOME  # still device-local, a reasonable fallback
    return hashlib.sha256(f"omni-revi-transfer:{machine_id}".encode("utf-8")).digest()


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _load_obfuscated_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            blob = f.read().strip()
        raw = _xor_bytes(base64.b64decode(blob.encode("ascii")), _obfuscation_key())
        return json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _save_obfuscated_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = json.dumps(data).encode("utf-8")
    blob = base64.b64encode(_xor_bytes(raw, _obfuscation_key())).decode("ascii")
    with open(path, "w", encoding="utf-8") as f:
        f.write(blob)
    try:
        os.chmod(path, 0o600)  # these files hold long-lived secrets
    except OSError:
        pass


def _delete_file_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _load_google_token() -> Optional[dict]:
    return _load_obfuscated_json(GOOGLE_TOKEN_PATH)


def _save_google_token(data: dict) -> None:
    _save_obfuscated_json(GOOGLE_TOKEN_PATH, data)


def _delete_google_token() -> None:
    _delete_file_quietly(GOOGLE_TOKEN_PATH)


async def _get_google_access_token() -> Optional[str]:
    """Returns a fresh access token by exchanging the stored refresh_token.

    No session/expiry is enforced by this plugin: Google's refresh token
    already lives on its own natural lifecycle (valid indefinitely unless
    revoked, unused for 6 months, or the app is still in "Testing" publishing
    status, in which case Google itself expires it after 7 days). This is
    intentional -- see README.md for the reasoning.
    """
    token_data = _load_google_token()
    if token_data is None:
        return None
    result = await _run_blocking(_http_post_form, GOOGLE_TOKEN_URL, {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": token_data["refresh_token"],
        "grant_type": "refresh_token",
    })
    if "access_token" not in result:
        decky.logger.warning(f"Google Drive: failed to refresh the access token: {result}")
        return None
    return result["access_token"]


# --- Discord (OAuth webhook.incoming + upload through the webhook) ------------
#
# Discord has no device flow, no PKCE, and no scope that lets an app post or
# DM *as the user* (doing that with a user token is a self-bot, against
# Discord's terms). The one legitimate route is the `webhook.incoming` scope:
# the user authorizes, picks a channel, and Discord creates a webhook for it
# and returns its URL in the token response. Uploads then go through that
# webhook, which can post to a channel but cannot send DMs (a private server
# of your own works as a "DM to myself").
#
# Because there's no device flow, the link happens in the Deck's own Steam
# browser: the authorize URL redirects to http://localhost:<port>/callback,
# which is served by a short-lived listener bound to 127.0.0.1 only.

# Feature flag, mirrored in src/index.tsx. Enabled on `test`, off on `main`.
DISCORD_ENABLED = True

DISCORD_CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discord_credentials.json")


def _load_discord_oauth_credentials() -> tuple:
    try:
        with open(DISCORD_CREDENTIALS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("client_id"), data.get("client_secret")
    except (OSError, ValueError, json.JSONDecodeError):
        return None, None


DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET = _load_discord_oauth_credentials()
DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
# Discord requires the redirect URI to match a registered one exactly (no
# wildcard ports), so the listener uses one fixed port.
DISCORD_REDIRECT_PORT = 47821
DISCORD_REDIRECT_URI = f"http://localhost:{DISCORD_REDIRECT_PORT}/callback"
DISCORD_LINK_TIMEOUT_SECONDS = 300
# Discord's edge rejects urllib's default User-Agent, so a descriptive one is required.
DISCORD_USER_AGENT = "DiscordBot (https://github.com/Revivedx/omni-revi-transfer, 0.0.5b)"
DISCORD_WEBHOOK_URL_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
)

DISCORD_TOKEN_PATH = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "discord.json")


def _discord_request(
    url: str,
    method: str,
    form: Optional[dict] = None,
    body: Optional[bytes] = None,
    content_type: Optional[str] = None,
) -> tuple:
    """Blocking request; returns (http_status, parsed_json). Status 0 = network error."""
    headers = {"User-Agent": DISCORD_USER_AGENT}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("ascii")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = body
        if content_type:
            headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except OSError as e:
        return 0, {"error": str(e)}
    try:
        return status, (json.loads(raw.decode("utf-8")) if raw else {})
    except ValueError:
        return status, {"error": "non_json_response"}


def _discord_exchange_code(code: str) -> Optional[dict]:
    """Exchanges the authorization code for the webhook Discord created.

    The response also carries an access_token; it's discarded on purpose
    (never stored): the scope only allows creating that webhook, and
    revoking it could make Discord delete the webhook we just got.
    The response body is never logged since it contains the webhook token.
    """
    status, result = _discord_request(DISCORD_TOKEN_URL, "POST", form={
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
    })
    webhook = result.get("webhook") if isinstance(result, dict) else None
    url = webhook.get("url") if isinstance(webhook, dict) else None
    if status != 200 or not url or not url.startswith(DISCORD_WEBHOOK_URL_PREFIXES):
        decky.logger.warning(f"Discord: code exchange failed (HTTP {status}, error={result.get('error')!r}).")
        return None
    return {
        "webhook_url": url,
        "guild_id": webhook.get("guild_id"),
        "channel_id": webhook.get("channel_id"),
        "linked_at": time.time(),
    }


_DISCORD_CALLBACK_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'><title>Omni-Revi-Transfer</title>"
    "<style>body{{font-family:sans-serif;background:#1b2838;color:#fff;text-align:center;padding-top:20vh}}</style>"
    "</head><body><h2>{title}</h2><p>{message}</p></body></html>"
)


class _DiscordLinkServer:
    """One-shot loopback listener that receives Discord's OAuth redirect."""

    def __init__(self) -> None:
        self._server: Optional[asyncio.AbstractServer] = None
        self._timeout_task: Optional[asyncio.Task] = None
        self._state: Optional[str] = None
        self.status = "idle"  # idle | pending | success | denied | expired | error

    async def start(self) -> Optional[str]:
        await self.stop()
        self._state = secrets.token_urlsafe(24)
        try:
            # 127.0.0.1 only: nothing on the LAN can reach this listener.
            self._server = await asyncio.start_server(self._handle, "127.0.0.1", DISCORD_REDIRECT_PORT)
        except OSError as e:
            decky.logger.warning(f"Discord: could not listen on port {DISCORD_REDIRECT_PORT}: {e}")
            self._state = None
            return None
        self.status = "pending"
        self._timeout_task = asyncio.get_event_loop().create_task(self._expire())
        query = urllib.parse.urlencode({
            "client_id": DISCORD_CLIENT_ID,
            "response_type": "code",
            "scope": "webhook.incoming",
            "redirect_uri": DISCORD_REDIRECT_URI,
            "state": self._state,
        })
        return f"{DISCORD_AUTHORIZE_URL}?{query}"

    async def _expire(self) -> None:
        await asyncio.sleep(DISCORD_LINK_TIMEOUT_SECONDS)
        if self.status == "pending":
            self.status = "expired"
        await self.stop(keep_status=True)

    async def stop(self, keep_status: bool = False) -> None:
        current = asyncio.current_task()
        if self._timeout_task is not None and self._timeout_task is not current:
            self._timeout_task.cancel()
        self._timeout_task = None
        self._state = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if not keep_status and self.status == "pending":
            self.status = "idle"

    async def _respond(self, writer: asyncio.StreamWriter, code: int, title: str, message: str) -> None:
        page = _DISCORD_CALLBACK_PAGE.format(title=title, message=message).encode("utf-8")
        reason = "OK" if code == 200 else "Bad Request" if code == 400 else "Not Found"
        writer.write(
            f"HTTP/1.1 {code} {reason}\r\nContent-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(page)}\r\nConnection: close\r\n\r\n".encode("latin-1") + page
        )
        await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        finished = False
        try:
            request_line = await reader.readline()
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            try:
                method, raw_path, _ = request_line.decode("latin-1").split(None, 2)
            except ValueError:
                return
            parsed = urllib.parse.urlsplit(raw_path)
            if method != "GET" or parsed.path != "/callback":
                await self._respond(writer, 404, "Not found", "")
                return

            params = urllib.parse.parse_qs(parsed.query)
            given_state = (params.get("state") or [""])[0]
            if self._state is None or not secrets.compare_digest(given_state, self._state):
                await self._respond(writer, 400, "Link failed", "This link request isn't valid. Start again from the plugin.")
                return
            self._state = None  # single use

            if "error" in params or not params.get("code"):
                self.status = "denied"
                await self._respond(writer, 200, "Not linked", "Authorization was cancelled. You can return to your Deck.")
            else:
                linked = await _run_blocking(_discord_exchange_code, params["code"][0])
                if linked is None:
                    self.status = "error"
                    await self._respond(writer, 200, "Link failed", "Discord didn't complete the link. Check the plugin log on your Deck.")
                else:
                    _save_obfuscated_json(DISCORD_TOKEN_PATH, linked)
                    self.status = "success"
                    await self._respond(writer, 200, "Discord linked", "You can close this and return to your Deck.")
            finished = True
        except (OSError, ConnectionError) as e:
            decky.logger.debug(f"Discord link listener: connection interrupted: {e}")
        finally:
            writer.close()
            if finished:
                asyncio.get_event_loop().create_task(self.stop(keep_status=True))


_discord_link_server = _DiscordLinkServer()


def _discord_webhook_url() -> Optional[str]:
    data = _load_obfuscated_json(DISCORD_TOKEN_PATH)
    url = data.get("webhook_url") if data else None
    # The file is user-writable; only ever POST to a real Discord webhook endpoint.
    if isinstance(url, str) and url.startswith(DISCORD_WEBHOOK_URL_PREFIXES):
        return url
    return None


def _upload_file_to_discord(webhook_url: str, path: str, content: str) -> dict:
    """Blocking multipart upload through the webhook (message text + one attachment)."""
    filename = os.path.basename(path)
    mime = "image/png" if os.path.splitext(path)[1].lower() == ".png" else "image/jpeg"
    with open(path, "rb") as f:
        file_bytes = f.read()

    payload = json.dumps({
        "content": content,
        # Game names are arbitrary text; never let one ping @everyone or a role.
        "allowed_mentions": {"parse": []},
        "attachments": [{"id": 0, "filename": filename}],
    }).encode("utf-8")
    boundary = "omni_revi_transfer_" + secrets.token_hex(8)
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
        f"Content-Type: application/json\r\n\r\n".encode("utf-8")
        + payload
        + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {mime}\r\n\r\n".encode("utf-8")
        + file_bytes
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    status, result = _discord_request(
        webhook_url + "?wait=true", "POST", body=body, content_type=f"multipart/form-data; boundary={boundary}"
    )
    if status in (200, 204):
        return {"ok": True, "error": None}
    decky.logger.warning(f"Discord upload failed (HTTP {status}, error={result.get('error')!r}, code={result.get('code')!r}).")
    if status == 404:
        return {"ok": False, "error": "not_linked"}  # the webhook was deleted on Discord's side
    if status == 413:
        return {"ok": False, "error": "too_large"}
    if status == 429:
        return {"ok": False, "error": "rate_limited"}
    return {"ok": False, "error": "upload_failed"}


async def _upload_screenshot_to_drive(real: str) -> dict:
    """`real` must already be validated with _is_inside_steam_screenshots."""
    access_token = await _get_google_access_token()
    if access_token is None:
        return {"ok": False, "error": "not_linked"}

    appid = _extract_appid_from_screenshot_path(real) or "7"
    folder_id = await _get_drive_game_folder_id(access_token, appid)
    if folder_id:
        already_there = await _run_blocking(_drive_file_exists, access_token, os.path.basename(real), folder_id)
        if already_there:
            return {"ok": False, "error": "duplicate"}

    return await _run_blocking(_upload_file_to_drive, access_token, real, folder_id)


async def _upload_screenshot_to_discord(real: str) -> dict:
    """`real` must already be validated with _is_inside_steam_screenshots."""
    webhook_url = _discord_webhook_url()
    if webhook_url is None:
        return {"ok": False, "error": "not_linked"}

    appid = _extract_appid_from_screenshot_path(real) or "7"
    result = await _run_blocking(
        _upload_file_to_discord, webhook_url, real, f"**{_resolve_drive_folder_name(appid)}**"
    )
    if result.get("error") == "not_linked":
        _delete_file_quietly(DISCORD_TOKEN_PATH)  # webhook no longer exists; drop the stale link
    return result


# --- Settings -----------------------------------------------------------------

SETTINGS_PATH = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "config.json")
DEFAULT_SETTINGS = {
    # By default this only warns (nothing is auto-deleted). "auto_delete" is
    # opt-in: if enabled, exceeding the limit automatically deletes the
    # oldest screenshots (from any game) until back under the limit -- this
    # is destructive and irreversible, so it starts off and its description
    # in the UI must warn about it clearly.
    "max_storage_mb": 2048,
    "auto_delete": False,
    "qr_share_duration_seconds": SHARE_DEFAULT_DURATION_SECONDS,
    # Opt-in, and only offered once the matching service is linked. Each new
    # screenshot is uploaded automatically shortly after it's taken.
    "auto_upload_google_drive": False,
    "auto_upload_discord": False,
    # Seconds between taking a screenshot and its auto-upload, per service.
    "auto_upload_delay_google_drive": 10,
    "auto_upload_delay_discord": 10,
}


def _load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, json.JSONDecodeError):
        saved = {}
    return {**DEFAULT_SETTINGS, **saved}


def _save_settings(settings: dict) -> None:
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f)


def _validate_settings(raw: dict) -> dict:
    try:
        max_storage_mb = int(raw.get("max_storage_mb", DEFAULT_SETTINGS["max_storage_mb"]))
    except (TypeError, ValueError):
        max_storage_mb = DEFAULT_SETTINGS["max_storage_mb"]
    try:
        qr_share_duration_seconds = int(
            raw.get("qr_share_duration_seconds", DEFAULT_SETTINGS["qr_share_duration_seconds"])
        )
    except (TypeError, ValueError):
        qr_share_duration_seconds = DEFAULT_SETTINGS["qr_share_duration_seconds"]
    # 0 is the sentinel for "Unlimited" and is left untouched; any other
    # value is floored at 50 MB so the limit can't be set unusably tiny.
    if max_storage_mb != 0:
        max_storage_mb = max(50, max_storage_mb)
    auto_delete = bool(raw.get("auto_delete", DEFAULT_SETTINGS["auto_delete"]))
    if max_storage_mb == 0:
        auto_delete = False  # doesn't make sense with no limit; see _enforce_auto_delete's guard too
    return {
        "max_storage_mb": max_storage_mb,
        "auto_delete": auto_delete,
        "qr_share_duration_seconds": max(30, min(3600, qr_share_duration_seconds)),
        "auto_upload_google_drive": bool(
            raw.get("auto_upload_google_drive", DEFAULT_SETTINGS["auto_upload_google_drive"])
        ),
        "auto_upload_discord": bool(raw.get("auto_upload_discord", DEFAULT_SETTINGS["auto_upload_discord"])),
        "auto_upload_delay_google_drive": _clamp_auto_upload_delay(
            raw.get("auto_upload_delay_google_drive", DEFAULT_SETTINGS["auto_upload_delay_google_drive"])
        ),
        "auto_upload_delay_discord": _clamp_auto_upload_delay(
            raw.get("auto_upload_delay_discord", DEFAULT_SETTINGS["auto_upload_delay_discord"])
        ),
    }


def _disable_auto_upload(setting_key: str) -> None:
    """Turns an auto-upload toggle off (used when its service is unlinked)."""
    settings = _load_settings()
    if settings.get(setting_key):
        settings[setting_key] = False
        _save_settings(settings)


# --- Auto-upload of new screenshots ---------------------------------------------
#
# Steam gives no "screenshot taken" hook to a plugin, so new files are found
# by polling the screenshots folders. Screenshots that already exist when the
# plugin starts are only remembered, never uploaded (turning this on must not
# dump the whole existing library into someone's Drive/Discord), and ones
# taken while the plugin isn't running are never picked up. Each new file
# waits the user's chosen delay for each service (which also lets Steam finish
# writing it), and the toggles and delays are read again on every check, so
# switching a service off during that window cancels its upload.

AUTO_UPLOAD_DEFAULT_DELAY_SECONDS = 10
AUTO_UPLOAD_MIN_DELAY_SECONDS = 5
AUTO_UPLOAD_MAX_DELAY_SECONDS = 60
AUTO_UPLOAD_POLL_SECONDS = 2

# (display name, toggle setting, delay setting)
_AUTO_UPLOAD_SERVICES = (
    ("Google Drive", "auto_upload_google_drive", "auto_upload_delay_google_drive"),
    ("Discord", "auto_upload_discord", "auto_upload_delay_discord"),
)


def _clamp_auto_upload_delay(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = AUTO_UPLOAD_DEFAULT_DELAY_SECONDS
    return max(AUTO_UPLOAD_MIN_DELAY_SECONDS, min(AUTO_UPLOAD_MAX_DELAY_SECONDS, value))


class _AutoUploader:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._seen: set = set()
        # path -> {"first_seen": monotonic time, "size": latest size,
        #          "prev_size": size at the previous check, "done": services handled}
        self._pending: dict = {}

    async def start(self) -> None:
        await self.stop()
        self._seen = {item["path"] for item in await _run_blocking(_list_steam_screenshots)}
        self._pending = {}
        self._task = asyncio.get_event_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.sleep(AUTO_UPLOAD_POLL_SECONDS)
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # a bad tick must never kill the watcher
                decky.logger.warning(f"Auto-upload: tick failed: {e!r}")

    async def _tick(self) -> None:
        now = time.monotonic()
        settings = _load_settings()
        sizes = {item["path"]: item["size"] for item in await _run_blocking(_list_steam_screenshots)}

        for path, size in sizes.items():
            if path not in self._seen:
                self._seen.add(path)
                self._pending[path] = {"first_seen": now, "size": size, "prev_size": None, "done": set()}

        for path in list(self._pending):
            entry = self._pending[path]
            if path not in sizes:
                del self._pending[path]  # deleted (by the user or storage auto-delete) before its turn
                continue
            entry["prev_size"], entry["size"] = entry["size"], sizes[path]
            still_being_written = entry["size"] != entry["prev_size"]

            for service, toggle_key, delay_key in _AUTO_UPLOAD_SERVICES:
                if service in entry["done"]:
                    continue
                if now - entry["first_seen"] < _clamp_auto_upload_delay(settings.get(delay_key)):
                    continue
                if still_being_written:
                    continue
                entry["done"].add(service)  # decided now: uploaded, or skipped because it's off
                if settings.get(toggle_key):
                    await self._upload(service, path)

            if len(entry["done"]) == len(_AUTO_UPLOAD_SERVICES):
                del self._pending[path]

    async def _upload(self, service: str, path: str) -> None:
        real = _is_inside_steam_screenshots(path)
        if real is None:
            return
        if service == "Google Drive":
            if not (GOOGLE_DRIVE_ENABLED and _load_google_token() is not None):
                return
            upload = _upload_screenshot_to_drive
        else:
            if not (DISCORD_ENABLED and _discord_webhook_url() is not None):
                return
            upload = _upload_screenshot_to_discord

        filename = os.path.basename(real)
        result = await upload(real)
        error = result.get("error")
        if result.get("ok"):
            decky.logger.info(f"Auto-upload: {filename} sent to {service}.")
        elif error == "duplicate":
            return  # already there; nothing worth telling the user
        else:
            decky.logger.warning(f"Auto-upload: {filename} to {service} failed ({error}).")
        await decky.emit("auto_upload_result", service, filename, bool(result.get("ok")), error)


_auto_uploader = _AutoUploader()


class Plugin:
    async def get_screenshots(self, offset: int = 0, limit: int = 5) -> dict:
        """Paginates Steam's native screenshots across all games, newest first."""
        offset = max(0, offset)
        limit = max(1, limit)

        await _enforce_auto_delete(_load_settings())
        await _check_storage_alerts()
        all_items = _list_steam_screenshots()
        total = len(all_items)
        page_items = all_items[offset:offset + limit]

        items = []
        for entry in page_items:
            try:
                items.append({
                    "filename": entry["filename"],
                    "path": entry["path"],
                    "appName": _resolve_app_name(entry["appid"]),
                    "modified": entry["modified"],
                    "thumbnail": _file_to_data_uri(entry["thumbnail_path"]),
                })
            except OSError as e:
                decky.logger.warning(f"Could not process {entry['path']}: {e}")

        return {"total": total, "offset": offset, "limit": limit, "items": items}

    async def get_screenshot_image(self, path: str) -> Optional[str]:
        """Full-resolution image (for the preview), not the thumbnail."""
        real = _is_inside_steam_screenshots(path)
        if real is None or not os.path.isfile(real):
            decky.logger.warning(f"Invalid path when requesting the full image: {path}")
            return None
        return _file_to_data_uri(real)

    async def delete_screenshot(self, path: str) -> bool:
        real = _is_inside_steam_screenshots(path)
        if real is None:
            decky.logger.warning(f"Attempted to delete outside of Steam's screenshots: {path}")
            return False
        # Note: 760/screenshots.vdf (Steam's internal index) is not updated.
        # Steam tolerates manually deleted files and prunes the orphaned
        # entry on its next scan, same as deleting the file from a file
        # manager would.
        return _delete_screenshot_files(real)

    async def get_settings(self) -> dict:
        settings = _load_settings()
        await _enforce_auto_delete(settings)
        await _check_storage_alerts()
        settings = _load_settings()  # re-read: the calls above may have updated it
        used_mb = round(_total_storage_bytes() / (1024 * 1024), 1)
        settings["used_mb"] = used_mb
        settings["over_limit"] = settings["max_storage_mb"] != 0 and used_mb > settings["max_storage_mb"]
        settings["account_detected"] = _detect_steam_account_id() is not None
        return settings

    async def set_settings(self, new_settings: dict) -> dict:
        validated = _validate_settings(new_settings)
        # An auto-upload toggle can't be on for a service that isn't linked.
        if _load_google_token() is None:
            validated["auto_upload_google_drive"] = False
        if _discord_webhook_url() is None:
            validated["auto_upload_discord"] = False
        # Preserve internal bookkeeping (e.g. which storage alert threshold
        # was last fired) that isn't part of the user-editable settings the
        # frontend sends, so saving a setting doesn't wipe it and cause a
        # threshold to re-alert needlessly.
        existing = _load_settings()
        if "_last_storage_alert" in existing:
            validated["_last_storage_alert"] = existing["_last_storage_alert"]
        _save_settings(validated)
        return await self.get_settings()

    async def start_qr_share(self, path: str, duration_seconds: int = SHARE_DEFAULT_DURATION_SECONDS) -> dict:
        """Starts a local HTTP server serving only that file. Returns the LAN URL."""
        real = _is_inside_steam_screenshots(path)
        if real is None or not os.path.isfile(real):
            decky.logger.warning(f"Invalid path when sharing: {path}")
            return {"url": None, "error": "invalid_path"}
        duration_seconds = max(30, min(3600, int(duration_seconds)))
        url = await _share_server.start(real, duration_seconds)
        if url is None:
            return {"url": None, "error": "server_failed"}
        return {"url": url, "error": None}

    async def stop_qr_share(self) -> None:
        await _share_server.stop()

    async def google_drive_status(self) -> dict:
        if not GOOGLE_DRIVE_ENABLED:
            return {"linked": False, "enabled": False}
        return {"linked": _load_google_token() is not None, "enabled": True}

    async def verify_sudo_password(self, password: str) -> bool:
        """Checks `password` against the real Deck user password via sudo.

        Used as a step-up confirmation before starting the Google Drive link
        flow, so linking a (different) account requires proving physical
        possession of the unlocked Deck -- someone who just picked up an
        already-unlocked Deck can't silently link their own account. The
        password is piped straight to sudo's stdin (never put on a command
        line, so it never shows up in `ps`) and is never logged or stored.
        """
        return await _verify_sudo_password(password)

    async def start_google_drive_link(self) -> dict:
        """Starts the OAuth device flow. Returns the QR/code info to show the user."""
        if not GOOGLE_DRIVE_ENABLED:
            return {"error": "disabled"}
        result = await _run_blocking(_http_post_form, GOOGLE_DEVICE_CODE_URL, {
            "client_id": GOOGLE_CLIENT_ID,
            "scope": GOOGLE_DRIVE_SCOPE,
        })
        if "device_code" not in result:
            decky.logger.warning(f"Google Drive: failed to start the device flow: {result}")
            return {"error": "start_failed"}

        _google_device_flow_state.clear()
        _google_device_flow_state.update(result)
        _google_device_flow_state["_started_at"] = time.monotonic()

        return {
            "verification_url": result.get("verification_url") or result.get("verification_uri"),
            "verification_url_complete": result.get("verification_url_complete") or result.get("verification_uri_complete"),
            "user_code": result["user_code"],
            "interval": result.get("interval", 5),
            "expires_in": result.get("expires_in", 1800),
            "error": None,
        }

    async def poll_google_drive_link(self) -> dict:
        """Call this every `interval` seconds after start_google_drive_link()."""
        if not GOOGLE_DRIVE_ENABLED:
            return {"status": "error"}
        if "device_code" not in _google_device_flow_state:
            return {"status": "error"}

        elapsed = time.monotonic() - _google_device_flow_state["_started_at"]
        if elapsed > _google_device_flow_state.get("expires_in", 1800):
            _google_device_flow_state.clear()
            return {"status": "expired"}

        result = await _run_blocking(_http_post_form, GOOGLE_TOKEN_URL, {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "device_code": _google_device_flow_state["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })

        if "access_token" in result:
            _save_google_token({
                "refresh_token": result["refresh_token"],
                "linked_at": time.time(),
            })
            _google_device_flow_state.clear()
            return {"status": "success"}

        error = result.get("error")
        if error in ("authorization_pending", "slow_down"):
            return {"status": "pending"}

        decky.logger.warning(f"Google Drive: link failed: {result}")
        _google_device_flow_state.clear()
        return {"status": "error"}

    async def unlink_google_drive(self) -> None:
        if not GOOGLE_DRIVE_ENABLED:
            return
        token_data = _load_google_token()
        if token_data:
            await _run_blocking(_http_post_form, GOOGLE_REVOKE_URL, {"token": token_data["refresh_token"]})
        _delete_google_token()
        _disable_auto_upload("auto_upload_google_drive")

    async def upload_screenshot_to_drive(self, path: str) -> dict:
        if not GOOGLE_DRIVE_ENABLED:
            return {"ok": False, "error": "not_linked"}
        real = _is_inside_steam_screenshots(path)
        if real is None or not os.path.isfile(real):
            decky.logger.warning(f"Invalid path when uploading to Drive: {path}")
            return {"ok": False, "error": "invalid_path"}
        return await _upload_screenshot_to_drive(real)

    async def discord_status(self) -> dict:
        if not DISCORD_ENABLED:
            return {"linked": False, "enabled": False}
        return {"linked": _discord_webhook_url() is not None, "enabled": True}

    async def start_discord_link(self) -> dict:
        """Starts the loopback listener and returns the Discord authorize URL,
        which the frontend opens in the Deck's own Steam browser."""
        if not DISCORD_ENABLED:
            return {"auth_url": None, "error": "disabled"}
        if not (DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET):
            decky.logger.warning(f"Discord: {DISCORD_CREDENTIALS_PATH} is missing or incomplete.")
            return {"auth_url": None, "error": "not_configured"}
        auth_url = await _discord_link_server.start()
        if auth_url is None:
            return {"auth_url": None, "error": "port_busy"}
        return {"auth_url": auth_url, "error": None, "expires_in": DISCORD_LINK_TIMEOUT_SECONDS}

    async def poll_discord_link(self) -> dict:
        """pending | success | denied | expired | error | idle"""
        if not DISCORD_ENABLED:
            return {"status": "error"}
        return {"status": _discord_link_server.status}

    async def cancel_discord_link(self) -> None:
        await _discord_link_server.stop()

    async def unlink_discord(self) -> None:
        if not DISCORD_ENABLED:
            return
        url = _discord_webhook_url()
        if url:
            # Deleting the webhook needs no auth beyond its own token; a
            # failure (e.g. already deleted) is fine, the local copy goes anyway.
            await _run_blocking(_discord_request, url, "DELETE")
        _delete_file_quietly(DISCORD_TOKEN_PATH)
        _disable_auto_upload("auto_upload_discord")

    async def upload_screenshot_to_discord(self, path: str) -> dict:
        if not DISCORD_ENABLED:
            return {"ok": False, "error": "not_linked"}
        real = _is_inside_steam_screenshots(path)
        if real is None or not os.path.isfile(real):
            decky.logger.warning(f"Invalid path when uploading to Discord: {path}")
            return {"ok": False, "error": "invalid_path"}
        return await _upload_screenshot_to_discord(real)

    async def _main(self) -> None:
        decky.logger.info("Omni-Revi-Transfer started (indexing Steam's native screenshots).")
        if GOOGLE_DRIVE_ENABLED and not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
            decky.logger.warning(
                f"Google Drive is enabled but {GOOGLE_CREDENTIALS_PATH} is missing or incomplete; "
                "the Drive link flow will fail until it's restored."
            )
        if DISCORD_ENABLED and not (DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET):
            decky.logger.warning(
                f"Discord is enabled but {DISCORD_CREDENTIALS_PATH} is missing or incomplete; "
                "the Discord link flow will fail until it's restored."
            )
        account_id = _detect_steam_account_id()
        if account_id is None:
            decky.logger.warning("Could not detect the Steam account under userdata/.")
        else:
            decky.logger.info(f"Steam account detected: {account_id}")
        await _auto_uploader.start()

    async def _unload(self) -> None:
        await _auto_uploader.stop()
        await _share_server.stop()
        await _discord_link_server.stop()
        decky.logger.info("Omni-Revi-Transfer stopped.")

    async def _uninstall(self) -> None:
        decky.logger.info("Omni-Revi-Transfer uninstalled.")
