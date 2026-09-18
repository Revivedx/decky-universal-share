"""Decky Universal Share - backend.

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
import json
import os
import re
import secrets
import shutil
import socket
import urllib.parse
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


def _resolve_app_name(appid: str) -> str:
    if appid == "7":
        return "SteamOS / Desktop"
    manifest = os.path.join(STEAM_APPMANIFEST_DIR, f"appmanifest_{appid}.acf")
    try:
        with open(manifest, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        match = re.search(r'"name"\s*"([^"]+)"', content)
        if match:
            return match.group(1)
    except OSError:
        pass
    return f"Game ({appid})"


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
    if not settings.get("auto_delete"):
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
    return {
        "max_storage_mb": max(50, max_storage_mb),
        "auto_delete": bool(raw.get("auto_delete", DEFAULT_SETTINGS["auto_delete"])),
        "qr_share_duration_seconds": max(30, min(3600, qr_share_duration_seconds)),
    }


class Plugin:
    async def get_screenshots(self, offset: int = 0, limit: int = 5) -> dict:
        """Paginates Steam's native screenshots across all games, newest first."""
        offset = max(0, offset)
        limit = max(1, limit)

        await _enforce_auto_delete(_load_settings())
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
        used_mb = round(_total_storage_bytes() / (1024 * 1024), 1)
        settings["used_mb"] = used_mb
        settings["over_limit"] = used_mb > settings["max_storage_mb"]
        settings["account_detected"] = _detect_steam_account_id() is not None
        return settings

    async def set_settings(self, new_settings: dict) -> dict:
        validated = _validate_settings(new_settings)
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

    async def _main(self) -> None:
        decky.logger.info("Universal Share started (indexing Steam's native screenshots).")
        account_id = _detect_steam_account_id()
        if account_id is None:
            decky.logger.warning("Could not detect the Steam account under userdata/.")
        else:
            decky.logger.info(f"Steam account detected: {account_id}")

    async def _unload(self) -> None:
        await _share_server.stop()
        decky.logger.info("Universal Share stopped.")

    async def _uninstall(self) -> None:
        decky.logger.info("Universal Share uninstalled.")
