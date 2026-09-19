#!/usr/bin/env bash
# Omni-Revi-Transfer installer for Decky Loader (Steam Deck, Desktop Mode).
#
# Double-click the .desktop launcher next to this file (or run this script). It shows a small menu:
#   Install / Update      download or pick up the release zip, install it, optionally enter your keys
#   Configure keys        set (or replace) your own Google Drive / Discord app credentials
#   Uninstall             remove the plugin (and optionally its settings)
# Options that don't apply are simply not offered (e.g. "Configure" before the plugin is installed).
#
# It only ever touches ~/homebrew/{plugins,settings,data,logs}/Omni-Revi-Transfer and restarts
# Decky's plugin_loader service. Administrator rights are requested ONCE per action (a normal
# password prompt), and only for the part that needs them.
#
# Command-line use (also how it is tested):
#   omni-revi-transfer-installer.sh status
#   omni-revi-transfer-installer.sh install   [--zip PATH_OR_URL] [--yes]
#   omni-revi-transfer-installer.sh configure                 (keys from the OMNI_* variables below)
#   omni-revi-transfer-installer.sh uninstall [--keep-settings] [--yes]
#   keys for install/configure: OMNI_GOOGLE_ID / OMNI_GOOGLE_SECRET / OMNI_DISCORD_ID / OMNI_DISCORD_SECRET
#
# Source: https://github.com/Revivedx/omni-revi-transfer (installer/ folder). Read it before running it.

set -u

PLUGIN_NAME="Omni-Revi-Transfer"
REPO="Revivedx/omni-revi-transfer"
GUIDE_GOOGLE_URL="https://github.com/${REPO}#setting-up-google-drive"
GUIDE_DISCORD_URL="https://github.com/${REPO}#setting-up-discord"
TITLE="Omni-Revi-Transfer for Decky"
LOG_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/omni-revi-transfer-installer.log"
SELF="$(readlink -f "$0" 2>/dev/null || echo "$0")"
SELF_DIR="$(dirname "$SELF")"

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null
log() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"$LOG_FILE" 2>/dev/null; }

# ---------------------------------------------------------------- where things live
resolve_homebrew() {
  if [ -n "${OMNI_HOMEBREW_DIR:-}" ]; then HOMEBREW="$OMNI_HOMEBREW_DIR"; return; fi
  local home_dir user
  if [ "$(id -u)" = 0 ]; then
    user="${SUDO_USER:-}"
    [ -z "$user" ] && [ -n "${PKEXEC_UID:-}" ] && user="$(getent passwd "$PKEXEC_UID" | cut -d: -f1)"
    home_dir="$(getent passwd "${user:-deck}" | cut -d: -f6)"
  else
    home_dir="$HOME"
  fi
  HOMEBREW="${home_dir:-/home/deck}/homebrew"
}
resolve_homebrew
PLUGINS_DIR="$HOMEBREW/plugins"
PLUGIN_DIR="$PLUGINS_DIR/$PLUGIN_NAME"
SETTINGS_DIR="$HOMEBREW/settings/$PLUGIN_NAME"

is_installed() { [ -f "$PLUGIN_DIR/plugin.json" ]; }
installed_version() { sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$PLUGIN_DIR/package.json" 2>/dev/null | head -1; }
has_saved() { [ -f "$SETTINGS_DIR/$1_oauth_client.json" ]; }   # $1: google | discord
google_linked() { [ -f "$SETTINGS_DIR/google_drive.json" ]; }
discord_linked() { [ -f "$SETTINGS_DIR/discord.json" ]; }

# ---------------------------------------------------------------- dialogs (kdialog, or the terminal)
UI="tty"
if [ "${OMNI_UI:-auto}" = "kdialog" ] || { [ "${OMNI_UI:-auto}" = "auto" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && command -v kdialog >/dev/null 2>&1; }; then
  UI="kdialog"
fi

ui_info()  { log "info: $*"; if [ "$UI" = kdialog ]; then kdialog --title "$TITLE" --msgbox "$*"; else printf '\n%s\n' "$*"; fi; }
ui_error() { log "error: $*"; if [ "$UI" = kdialog ]; then kdialog --title "$TITLE" --error "$*"; else printf '\n[!] %s\n' "$*" >&2; fi; }
ui_toast() { log "toast: $*"; if [ "$UI" = kdialog ]; then kdialog --title "$TITLE" --passivepopup "$*" 4 >/dev/null 2>&1 & else printf '... %s\n' "$*"; fi; }

# ui_menu <text> <tag> <label> [<tag> <label> ...]  -> prints the chosen tag (exit 1 if cancelled)
ui_menu() {
  local text="$1"; shift
  if [ "$UI" = kdialog ]; then kdialog --title "$TITLE" --menu "$text" "$@"; return; fi
  printf '\n%s\n' "$text" >&2
  local tags=() i=1
  while [ $# -gt 0 ]; do tags+=("$1"); printf '  %d) %s\n' "$i" "$2" >&2; i=$((i + 1)); shift 2; done
  local pick; read -r -p "Choose: " pick </dev/tty || return 1
  [[ "$pick" =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "${#tags[@]}" ] || return 1
  printf '%s\n' "${tags[$((pick - 1))]}"
}

# ui_yesno <text> [yes-label] [no-label]
ui_yesno() {
  if [ "$UI" = kdialog ]; then
    kdialog --title "$TITLE" --yes-label "${2:-Yes}" --no-label "${3:-No}" --yesno "$1"; return
  fi
  local a; read -r -p "$1 [y/N] " a </dev/tty || return 1
  [[ "$a" =~ ^[Yy] ]]
}

# ui_three <text> <yes-label> <no-label> <cancel-label>  -> prints yes | no | cancel
ui_three() {
  if [ "$UI" = kdialog ]; then
    kdialog --title "$TITLE" --yes-label "$2" --no-label "$3" --cancel-label "$4" --yesnocancel "$1"
    case $? in 0) echo yes ;; 1) echo no ;; *) echo cancel ;; esac; return
  fi
  local a; read -r -p "$1  [1=$2 / 2=$3 / 3=$4] " a </dev/tty || { echo cancel; return; }
  case "$a" in 1) echo yes ;; 2) echo no ;; *) echo cancel ;; esac
}

ui_input()    { if [ "$UI" = kdialog ]; then kdialog --title "$TITLE" --inputbox "$1" "${2:-}"; else local v; read -r -p "$1 " v </dev/tty || return 1; printf '%s\n' "$v"; fi; }
ui_password() { if [ "$UI" = kdialog ]; then kdialog --title "$TITLE" --password "$1"; else local v; read -r -s -p "$1 " v </dev/tty || return 1; echo >&2; printf '%s\n' "$v"; fi; }

# ---------------------------------------------------------------- key validation (same rules as the plugin)
valid_google_id()     { [[ "$1" =~ ^[^[:space:]]{1,180}\.apps\.googleusercontent\.com$ ]]; }
valid_discord_id()    { [[ "$1" =~ ^[0-9]{15,25}$ ]]; }
valid_secret()        { [[ "$1" =~ ^[^[:space:]]{8,200}$ ]]; }

G_ID="" G_SECRET="" D_ID="" D_SECRET=""

# ask_keys google|discord : prompts until valid, skipped, or cancelled. Sets G_* / D_*.
ask_keys() {
  local kind="$1" label guide idtext idcheck
  if [ "$kind" = google ]; then
    label="Google Drive"; guide="$GUIDE_GOOGLE_URL"; idcheck=valid_google_id
    idtext="Google Client ID (ends in .apps.googleusercontent.com):"
  else
    label="Discord"; guide="$GUIDE_DISCORD_URL"; idcheck=valid_discord_id
    idtext="Discord Application (client) ID (a long number):"
  fi
  local choice id secret
  while true; do
    choice="$(ui_three "Set up $label now?

You need your OWN $label app (free); the guide lists every step. You can also skip this and do it later from the plugin (Share options) or by running this installer again." "Enter keys" "Skip for now" "Open the guide")"
    case "$choice" in
      cancel) xdg-open "$guide" >/dev/null 2>&1 & continue ;;
      no) return 0 ;;
    esac
    id="$(ui_input "$idtext")" || return 0
    id="$(printf '%s' "$id" | tr -d '[:space:]')"
    "$idcheck" "$id" || { ui_error "That doesn't look like a valid $label ID. Check it and try again."; continue; }
    secret="$(ui_password "$label client secret:")" || return 0
    secret="$(printf '%s' "$secret" | tr -d '[:space:]')"
    valid_secret "$secret" || { ui_error "The client secret looks too short or empty. Try again."; continue; }
    if [ "$kind" = google ]; then G_ID="$id"; G_SECRET="$secret"; else D_ID="$id"; D_SECRET="$secret"; fi
    return 0
  done
}

# ---------------------------------------------------------------- privileged part (one prompt per action)
PYTHON="$(command -v python3 || true)"
WORKDIR=""
prepare_workdir() {
  [ -n "$WORKDIR" ] && return
  WORKDIR="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/omni-installer.XXXXXX")" && chmod 700 "$WORKDIR"
  trap 'rm -rf "$WORKDIR"' EXIT
}

run_root() {
  if [ "$(id -u)" = 0 ]; then "$@"
  elif [ -n "${OMNI_ROOT_WRAPPER:-}" ]; then $OMNI_ROOT_WRAPPER "$@"          # test hook
  elif [ "$UI" = kdialog ] && command -v pkexec >/dev/null 2>&1; then pkexec "$@"
  else sudo "$@"
  fi
}

# Runs as root. Only touches ~/homebrew/{plugins,settings,data,logs}/Omni-Revi-Transfer and restarts plugin_loader.
write_root_helper() {
  cat >"$WORKDIR/root_apply.py" <<'PY'
import base64, hashlib, json, os, shutil, subprocess, sys, zipfile

p = json.load(open(sys.argv[1]))
NAME = p["plugin_name"]
hb = p["homebrew"]
plugin_dir = f"{hb}/plugins/{NAME}"
settings_dir = f"{hb}/settings/{NAME}"

def obfuscation_key():
    # Same as the plugin's _obfuscation_key(): sha256("omni-revi-transfer:" + machine-id)
    with open("/etc/machine-id", "r", encoding="utf-8") as f:
        machine_id = f.read().strip()
    return hashlib.sha256(f"omni-revi-transfer:{machine_id}".encode("utf-8")).digest()

def write_keys(kind, keys):
    os.makedirs(settings_dir, exist_ok=True)
    key = obfuscation_key()
    raw = json.dumps({"client_id": keys["id"], "client_secret": keys["secret"]}).encode("utf-8")
    blob = base64.b64encode(bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)))
    path = f"{settings_dir}/{kind}_oauth_client.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
    print(f"saved {kind} keys")

def remove_keys(kind):
    try:
        os.remove(f"{settings_dir}/{kind}_oauth_client.json")
        print(f"removed saved {kind} keys")
    except FileNotFoundError:
        pass

def restart_loader():
    if not p.get("no_restart"):
        subprocess.run(["systemctl", "restart", "plugin_loader"], check=False)
        print("restarted plugin_loader")

action = p["action"]
if action == "install":
    with zipfile.ZipFile(p["zip"]) as z:
        names = z.namelist()
        if f"{NAME}/plugin.json" not in names or f"{NAME}/main.py" not in names:
            sys.exit("that zip is not an Omni-Revi-Transfer release")
        for n in names:
            if not n.startswith(f"{NAME}/") or ".." in n.split("/"):
                sys.exit(f"unexpected path in the zip: {n}")
        os.makedirs(f"{hb}/plugins", exist_ok=True)
        shutil.rmtree(plugin_dir, ignore_errors=True)
        z.extractall(f"{hb}/plugins")
    print("installed plugin files")
if action in ("install", "configure"):
    if p.get("google"): write_keys("google", p["google"])
    if p.get("discord"): write_keys("discord", p["discord"])
    if p.get("remove_google"): remove_keys("google")
    if p.get("remove_discord"): remove_keys("discord")
if action == "uninstall":
    shutil.rmtree(plugin_dir, ignore_errors=True)
    if p.get("delete_settings"):
        for sub in ("settings", "data", "logs"):
            shutil.rmtree(f"{hb}/{sub}/{NAME}", ignore_errors=True)
    print("removed plugin" + (" and its settings" if p.get("delete_settings") else ""))
if action in ("install", "uninstall"):
    restart_loader()
PY
}

make_payload() {   # $1 = action
  ACTION="$1" ZIP="${ZIP_PATH:-}" HB="$HOMEBREW" NAME="$PLUGIN_NAME" \
  G_ID="$G_ID" G_SECRET="$G_SECRET" D_ID="$D_ID" D_SECRET="$D_SECRET" \
  REMOVE_G="${REMOVE_G:-0}" REMOVE_D="${REMOVE_D:-0}" DEL_SETTINGS="${DEL_SETTINGS:-0}" NOREST="${OMNI_NO_RESTART:-0}" \
  "$PYTHON" - "$WORKDIR/payload.json" <<'PY'
import json, os, sys
e = os.environ
payload = {
    "action": e["ACTION"], "plugin_name": e["NAME"], "homebrew": e["HB"], "zip": e["ZIP"] or None,
    "google": {"id": e["G_ID"], "secret": e["G_SECRET"]} if e["G_ID"] and e["G_SECRET"] else None,
    "discord": {"id": e["D_ID"], "secret": e["D_SECRET"]} if e["D_ID"] and e["D_SECRET"] else None,
    "remove_google": e["REMOVE_G"] == "1", "remove_discord": e["REMOVE_D"] == "1",
    "delete_settings": e["DEL_SETTINGS"] == "1", "no_restart": e["NOREST"] == "1",
}
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(payload, f)
PY
}

apply_as_root() {   # $1 = action ; prints the helper's output, returns its status
  prepare_workdir; write_root_helper; make_payload "$1" || return 1
  local out rc
  out="$(run_root "$PYTHON" "$WORKDIR/root_apply.py" "$WORKDIR/payload.json" 2>&1)"; rc=$?
  log "apply $1 -> rc=$rc: $out"
  printf '%s\n' "$out"
  case $rc in
    126|127) echo "Administrator permission was not granted." ;;
  esac
  return $rc
}

# ---------------------------------------------------------------- prerequisites
need_tools() {
  local missing=() t
  for t in python3 unzip; do command -v "$t" >/dev/null 2>&1 || missing+=("$t"); done
  [ ${#missing[@]} -eq 0 ] || { ui_error "Missing tools: ${missing[*]}"; return 1; }
}

check_decky() {
  [ -d "$PLUGINS_DIR" ] && return 0
  ui_error "Decky Loader doesn't seem to be installed (no folder at $PLUGINS_DIR).

Install Decky Loader first from https://decky.xyz and open Game Mode once, then run this again."
  return 1
}

check_password() {
  [ "$(id -u)" = 0 ] && return 0
  [ -n "${OMNI_ROOT_WRAPPER:-}" ] && return 0
  local state; state="$(passwd -S "$(id -un)" 2>/dev/null | awk '{print $2}')"
  [ "$state" = "P" ] || [ -z "$state" ] && return 0
  ui_error "This Deck has no administrator password set, and installing a plugin needs one.

Open Konsole, run:   passwd
choose a password, then run this installer again."
  return 1
}

# ---------------------------------------------------------------- finding the release zip
ZIP_PATH=""; ZIP_SOURCE=""; ZIP_VERSION=""

zip_version() { unzip -p "$1" "$PLUGIN_NAME/package.json" 2>/dev/null | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' | head -1; }

find_local_zip() {   # newest public release zip next to the installer, in Downloads or on the Desktop
  local dir f best=""
  for dir in "$SELF_DIR" "$HOME/Downloads" "$HOME/Desktop"; do
    for f in "$dir"/omni-revi-transfer-v*.zip; do
      [ -f "$f" ] || continue
      case "$f" in *-personal.zip) continue ;; esac
      if [ -z "$best" ] || [ "$(printf '%s\n%s\n' "$best" "$f" | sort -V | tail -1)" = "$f" ]; then best="$f"; fi
    done
  done
  printf '%s' "$best"
}

github_zip_url() {
  curl -fsSL --max-time 20 "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null | "$PYTHON" -c '
import json, re, sys
try:
    assets = json.load(sys.stdin).get("assets", [])
except Exception:
    sys.exit(1)
for a in assets:
    if re.fullmatch(r"omni-revi-transfer-v[0-9A-Za-z.]+\.zip", a.get("name", "")):
        print(a["browser_download_url"]); break
'
}

resolve_zip() {   # $1 = optional path or URL. Sets ZIP_PATH / ZIP_SOURCE / ZIP_VERSION. Returns 1 if none.
  prepare_workdir
  local src="${1:-}"
  if [ -z "$src" ]; then src="$(find_local_zip)"; fi
  if [ -z "$src" ]; then
    ui_toast "Looking for the latest release on GitHub..."
    src="$(github_zip_url)"
    [ -n "$src" ] || { ui_error "Couldn't find a release to install.

Either there's no internet connection, or no release has been published yet. You can also download omni-revi-transfer-vX.Y.Z.zip yourself and put it next to this installer or in Downloads."; return 1; }
  fi
  case "$src" in
    http://*|https://*)
      ui_toast "Downloading ${src##*/} ..."
      curl -fL --retry 2 --max-time 300 -o "$WORKDIR/plugin.zip" "$src" || { ui_error "The download failed."; return 1; }
      ZIP_PATH="$WORKDIR/plugin.zip"; ZIP_SOURCE="GitHub (${src##*/})" ;;
    *)
      [ -f "$src" ] || { ui_error "File not found: $src"; return 1; }
      ZIP_PATH="$src"; ZIP_SOURCE="$src" ;;
  esac
  ZIP_VERSION="$(zip_version "$ZIP_PATH")"
  [ -n "$ZIP_VERSION" ] || { ui_error "That file isn't an Omni-Revi-Transfer release zip."; return 1; }
}

# ---------------------------------------------------------------- actions
report_apply() {   # $1 = output, $2 = rc, $3 = success message
  if [ "$2" -eq 0 ]; then ui_info "$3"; else ui_error "Something went wrong:

$1

Details: $LOG_FILE"; fi
}

do_install() {   # interactive (or --yes)
  need_tools && check_decky && check_password || return 1
  resolve_zip "${OPT_ZIP:-}" || return 1
  local verb="Install"; is_installed && verb="Update"
  if [ "${OPT_YES:-0}" != 1 ]; then
    ui_yesno "$verb Omni-Revi-Transfer $ZIP_VERSION?

Source: $ZIP_SOURCE
$(is_installed && echo "Currently installed: $(installed_version). Your settings and keys are kept.")" "$verb" "Cancel" || return 0
    has_saved google || ask_keys google
    has_saved discord || ask_keys discord
  fi
  ui_toast "Installing... enter your password when asked."
  local out rc; out="$(apply_as_root install)"; rc=$?
  report_apply "$out" "$rc" "Omni-Revi-Transfer $ZIP_VERSION is installed.

Go back to Game Mode: it appears in the Quick Access menu (the ... button), under the plugins.
$( { [ -n "$G_ID" ] || has_saved google; } && echo "Google Drive keys: saved." || echo "Google Drive: not set up yet (Share options in the plugin, or run this again).")
$( { [ -n "$D_ID" ] || has_saved discord; } && echo "Discord keys: saved." || echo "Discord: not set up yet.")"
  return $rc
}

do_configure() {
  is_installed || { ui_error "Install the plugin first."; return 1; }
  need_tools && check_password || return 1
  local what
  if [ "${OPT_YES:-0}" != 1 ]; then
    what="$(ui_menu "Which keys do you want to set?" google "Google Drive" discord "Discord" both "Both")" || return 0
    if [ "$what" != discord ]; then
      if google_linked; then
        ui_info "Google Drive is currently linked, and changing its keys would break that link.

Unlink it first in the plugin (Share options > Google Drive > Unlink), then set the new keys here."
      else ask_keys google; fi
    fi
    [ "$what" != google ] && ask_keys discord
  fi
  if [ -z "$G_ID$D_ID" ]; then ui_info "No keys were entered, nothing changed."; return 0; fi
  local out rc; out="$(apply_as_root configure)"; rc=$?
  report_apply "$out" "$rc" "Keys saved. Open the plugin's Share options in Game Mode and use \"Link\"."
  return $rc
}

do_uninstall() {
  is_installed || { ui_error "The plugin isn't installed."; return 1; }
  need_tools && check_password || return 1
  DEL_SETTINGS=1; [ "${OPT_KEEP_SETTINGS:-0}" = 1 ] && DEL_SETTINGS=0
  if [ "${OPT_YES:-0}" != 1 ]; then
    local warn=""
    google_linked && warn="$warn
- Google Drive is still linked. Unlink it in the plugin first, or revoke it at https://myaccount.google.com/permissions"
    discord_linked && warn="$warn
- Discord is still linked. Unlink it in the plugin first, or delete the webhook in the channel's Integrations settings"
    [ -n "$warn" ] && ui_info "Before removing: the plugin can't revoke those accesses once it is gone.$warn"
    ui_yesno "Remove Omni-Revi-Transfer from this Deck?" "Remove" "Cancel" || return 0
    if ui_yesno "Also delete its settings, saved keys and linked sessions?

(Choose No to keep them for a later reinstall.)" "Delete them" "Keep them"; then DEL_SETTINGS=1; else DEL_SETTINGS=0; fi
  fi
  ui_toast "Removing... enter your password when asked."
  local out rc; out="$(apply_as_root uninstall)"; rc=$?
  report_apply "$out" "$rc" "Omni-Revi-Transfer was removed."
  return $rc
}

show_status() {
  if is_installed; then echo "installed: yes (version $(installed_version))"; else echo "installed: no"; fi
  echo "homebrew dir: $HOMEBREW"
  for k in google discord; do
    printf '%s keys saved: %s\n' "$k" "$(has_saved "$k" && echo yes || echo no)"
  done
  echo "google linked: $(google_linked && echo yes || echo no)"
  echo "discord linked: $(discord_linked && echo yes || echo no)"
}

main_menu() {
  local choice
  while true; do
    if is_installed; then
      choice="$(ui_menu "Omni-Revi-Transfer $(installed_version) is installed." \
        configure "Configure keys (Google Drive / Discord)" \
        install "Update / reinstall" \
        uninstall "Uninstall" \
        quit "Exit")" || return 0
    else
      choice="$(ui_menu "Omni-Revi-Transfer is not installed yet.

It adds screenshot sharing (QR, Steam, Discord, Google Drive) to Decky's Quick Access menu." \
        install "Install" \
        quit "Exit")" || return 0
    fi
    G_ID="" G_SECRET="" D_ID="" D_SECRET=""
    case "$choice" in
      install) do_install ;;
      configure) do_configure ;;
      uninstall) do_uninstall ;;
      *) return 0 ;;
    esac
  done
}

# ---------------------------------------------------------------- entry point
OPT_ZIP=""; OPT_YES=0; OPT_KEEP_SETTINGS=0
cmd="${1:-menu}"; [ $# -gt 0 ] && shift
while [ $# -gt 0 ]; do
  case "$1" in
    --zip) OPT_ZIP="${2:-}"; shift ;;
    --yes) OPT_YES=1 ;;
    --keep-settings) OPT_KEEP_SETTINGS=1 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
G_ID="${OMNI_GOOGLE_ID:-}"; G_SECRET="${OMNI_GOOGLE_SECRET:-}"; D_ID="${OMNI_DISCORD_ID:-}"; D_SECRET="${OMNI_DISCORD_SECRET:-}"
if [ -n "$G_ID" ] && { ! valid_google_id "$G_ID" || ! valid_secret "$G_SECRET"; }; then echo "invalid Google keys" >&2; exit 2; fi
if [ -n "$D_ID" ] && { ! valid_discord_id "$D_ID" || ! valid_secret "$D_SECRET"; }; then echo "invalid Discord keys" >&2; exit 2; fi

log "start: cmd=$cmd ui=$UI homebrew=$HOMEBREW"
case "$cmd" in
  status)    show_status ;;
  install)   do_install ;;
  configure) OPT_YES=1; do_configure ;;
  uninstall) do_uninstall ;;
  menu)      main_menu ;;
  *)         echo "usage: $0 [status|install|configure|uninstall] [--zip PATH_OR_URL] [--yes] [--keep-settings]" >&2; exit 2 ;;
esac
