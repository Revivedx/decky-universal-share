import {
  Button,
  ButtonItem,
  DropdownItem,
  Focusable,
  ModalRoot,
  PanelSection,
  PanelSectionRow,
  showModal,
  SliderField,
  staticClasses,
  TextField,
  ToggleField,
} from "@decky/ui";
import {
  addEventListener,
  removeEventListener,
  callable,
  definePlugin,
  toaster,
} from "@decky/api"
import { useCallback, useEffect, useMemo, useState } from "react";
import { FaArrowDown, FaArrowUp, FaCamera, FaSyncAlt } from "react-icons/fa";
import qrcode from "qrcode-generator";

interface ScreenshotItem {
  filename: string;
  path: string;
  appName: string;
  modified: number;
  thumbnail: string;
}

interface ScreenshotPage {
  total: number;
  offset: number;
  limit: number;
  items: ScreenshotItem[];
}

interface Settings {
  max_storage_mb: number;
  auto_delete: boolean;
  qr_share_duration_seconds: number;
  used_mb: number;
  over_limit: boolean;
  account_detected: boolean;
}

interface ShareResult {
  url: string | null;
  error: "invalid_path" | "server_failed" | null;
}

interface GoogleDriveStatus {
  linked: boolean;
}

interface GoogleDriveLinkStart {
  verification_url?: string;
  verification_url_complete?: string;
  user_code?: string;
  interval: number;
  expires_in: number;
  error: "start_failed" | null;
}

interface GoogleDriveLinkPoll {
  status: "pending" | "success" | "expired" | "error";
}

interface GoogleDriveUploadResult {
  ok: boolean;
  error: "invalid_path" | "not_linked" | "upload_failed" | "duplicate" | null;
}

const getScreenshots = callable<[offset: number, limit: number], ScreenshotPage>("get_screenshots");
const getScreenshotImage = callable<[path: string], string | null>("get_screenshot_image");
const deleteScreenshot = callable<[path: string], boolean>("delete_screenshot");
const getSettings = callable<[], Settings>("get_settings");
const setSettings = callable<[settings: Partial<Settings>], Settings>("set_settings");
const startQrShare = callable<[path: string, durationSeconds: number], ShareResult>("start_qr_share");
const stopQrShare = callable<[], void>("stop_qr_share");
const getGoogleDriveStatus = callable<[], GoogleDriveStatus>("google_drive_status");
const verifySudoPassword = callable<[password: string], boolean>("verify_sudo_password");
const startGoogleDriveLink = callable<[], GoogleDriveLinkStart>("start_google_drive_link");
const pollGoogleDriveLink = callable<[], GoogleDriveLinkPoll>("poll_google_drive_link");
const unlinkGoogleDrive = callable<[], void>("unlink_google_drive");
const uploadScreenshotToDrive = callable<[path: string], GoogleDriveUploadResult>("upload_screenshot_to_drive");

const PAGE_SIZE = 5;

// Feature flag: mirrors GOOGLE_DRIVE_ENABLED in main.py. Google's OAuth app
// is still in "Testing" status (100 user cap, 7-day sessions) pending
// verification (see PRIVACY.md), so the Google Drive option is hidden from
// the UI for this release. Flip both flags back to true once approved --
// nothing about the Drive integration itself was removed.
const GOOGLE_DRIVE_ENABLED = false;

// Generated 100% locally (no calls to any external service) so nobody's
// share URL is exposed to a third party. `qrcode-generator` is a
// dependency-free library that runs inside the plugin's own bundle.
function qrCodeDataUrl(text: string): string {
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  return qr.createDataURL(6, 8);
}

const DURATION_OPTIONS = [
  { data: 60, label: "1 minute" },
  { data: 300, label: "5 minutes" },
  { data: 600, label: "10 minutes" },
  { data: 1800, label: "30 minutes" },
];

// Uniform 0.5 GB steps from 0.5 up to 50 GB, plus one extra step for
// Unlimited at the end. The SliderField itself just moves over plain
// integer indices into this array -- that's the one thing about it we're
// fully certain works, so the "which GB value is this position" logic lives
// here instead of in slider props whose exact notch/step behavior we can't
// visually verify ourselves.
// 0 is the shared backend sentinel for "no limit" (see main.py).
const STORAGE_LIMIT_UNLIMITED = 0;
const STORAGE_LIMIT_VALUES_GB: number[] = [
  ...Array.from({ length: 100 }, (_, i) => Math.round((0.5 + i * 0.5) * 10) / 10), // 0.5 .. 50
  STORAGE_LIMIT_UNLIMITED, // Unlimited, always last
];
const STORAGE_LIMIT_LAST_INDEX = STORAGE_LIMIT_VALUES_GB.length - 1;

// Fuller sentence shown prominently above the slider, e.g.
// "2.5 GB limit set" / "No limit set".
function storageLimitDescription(gb: number): string {
  return gb === STORAGE_LIMIT_UNLIMITED ? "No limit set" : `${gb} GB limit set`;
}

// Compact form used in the collapsed summary button, e.g. "2.5 GB" / "Unlimited".
function storageLimitShort(gb: number): string {
  return gb === STORAGE_LIMIT_UNLIMITED ? "Unlimited" : `${gb} GB`;
}

// No notchLabels here: we tried labeling both endpoints and 5 GB
// checkpoints, and neither rendered reliably (checkpoints showed up at the
// wrong spot, e.g. "50 GB" as "5 GB"; even the two endpoints didn't show at
// all on a retry) -- SliderField's real notch-placement logic lives in
// Steam's own bundle, not something we can inspect or trust here. The exact
// current value is shown prominently above the slider instead, driven
// directly by the saved setting rather than by notch positioning.

function storageLimitIndexForMb(maxStorageMb: number): number {
  if (maxStorageMb === 0) return STORAGE_LIMIT_LAST_INDEX;
  const gb = maxStorageMb / 1024;
  let bestIndex = 0;
  let bestDiff = Infinity;
  for (let i = 0; i < STORAGE_LIMIT_LAST_INDEX; i++) {
    const diff = Math.abs(STORAGE_LIMIT_VALUES_GB[i] - gb);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestIndex = i;
    }
  }
  return bestIndex;
}

// Same size footprint as the image in the preview, so switching from the
// image PiP to the share PiP doesn't feel like a size jump.
const PIP_CONTENT_HEIGHT = "30vh";

const SHARE_METHOD_OPTIONS = [
  { data: "qr", label: "QR Code" },
  { data: "icloud", label: "iCloud (coming soon)" },
  ...(GOOGLE_DRIVE_ENABLED ? [{ data: "googledrive", label: "Google Drive" }] : []),
];

// ModalRoot is used instead of ConfirmModal: the latter always forces its
// own visible OK/Cancel buttons with no documented way to hide them, AND --
// the real bug -- it doesn't close itself when they're pressed; you have to
// explicitly call the `.Close()` that showModal() returns. ModalRoot forces
// no buttons at all, and its `onCancel` prop is exactly what the B button fires.
function openShareModal(item: ScreenshotItem, onDeleted: () => void) {
  const modal = showModal(
    <ShareModalContent
      item={item}
      onGoBack={() => {
        modal.Close();
        openPreview(item, onDeleted);
      }}
      onStopped={() => {
        modal.Close();
        toaster.toast({ title: "Sharing stopped", body: item.filename });
      }}
    />
  );
}

// The share server lives in the backend, not tied to this modal's lifecycle.
// Closing this window (to go check the phone) must NOT shut it down -- that's
// why there's no stopQrShare() on unmount. It only shuts down via its own
// timer, after a download (with a grace period), or if the user presses
// "Stop sharing now" by hand.
function ShareModalContent({
  item,
  onGoBack,
  onStopped,
}: {
  item: ScreenshotItem;
  onGoBack: () => void;
  onStopped: () => void;
}) {
  const [starting, setStarting] = useState(true);
  const [shareUrl, setShareUrl] = useState<string | undefined>();
  const [downloaded, setDownloaded] = useState(false);

  const qrDataUrl = useMemo(() => (shareUrl ? qrCodeDataUrl(shareUrl) : undefined), [shareUrl]);

  useEffect(() => {
    let cancelled = false;
    getSettings().then((settings) => {
      if (cancelled) return;
      startQrShare(item.path, settings.qr_share_duration_seconds).then((result) => {
        if (cancelled) return;
        setStarting(false);
        if (result.url) {
          setShareUrl(result.url);
        } else {
          toaster.toast({ title: "Couldn't start sharing", body: "Check the plugin log" });
        }
      });
    });
    return () => {
      cancelled = true;
    };
  }, [item.path]);

  // The backend gives a grace period after the first download instead of
  // shutting down instantly (in case the phone needs another request to
  // finish saving the image) — this just reflects the notice on screen.
  useEffect(() => {
    const listener = addEventListener<[]>("qr_share_downloaded", () => setDownloaded(true));
    return () => removeEventListener("qr_share_downloaded", listener);
  }, []);

  const onStop = async () => {
    await stopQrShare();
    onStopped();
  };

  return (
    <ModalRoot onCancel={onGoBack} closeModal={onGoBack} bHideCloseIcon={false}>
      <div
        style={{
          minHeight: PIP_CONTENT_HEIGHT,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: "8px" }}>Share via QR</div>

        {starting && <div>Starting...</div>}

        {shareUrl && (
          <div style={{ textAlign: "center" }}>
            {qrDataUrl && <img src={qrDataUrl} alt="QR code" style={{ width: "180px", height: "180px" }} />}
            <div style={{ fontSize: "0.7em", opacity: 0.7, wordBreak: "break-all", margin: "4px 0" }}>
              {shareUrl}
            </div>
            {downloaded ? (
              <div style={{ color: "#4caf50", marginBottom: "8px" }}>
                ✓ Downloaded — stays active briefly in case you need it again, then stops on its own.
              </div>
            ) : (
              <div style={{ fontSize: "0.7em", opacity: 0.6, marginBottom: "8px" }}>
                Scan on a phone on the same Wi-Fi to download. Pressing B keeps sharing running
                until it's downloaded or it times out.
              </div>
            )}
            <ButtonItem layout="below" onClick={onStop}>
              Stop sharing now
            </ButtonItem>
          </div>
        )}
      </div>
    </ModalRoot>
  );
}

// OAuth device flow: Google gives us a short code plus a verification URL.
// Rather than asking the user to type the code, the QR encodes
// `verification_url_complete` (the code pre-filled) so approving is just
// "scan with your phone, tap allow" — no typing on the Deck at all. This
// polls poll_google_drive_link() on the interval Google itself specifies.
function GoogleDriveLinkModal({ onLinked, onClose }: { onLinked: () => void; onClose: () => void }) {
  const [state, setState] = useState<"starting" | "waiting" | "success" | "expired" | "error">("starting");
  const [info, setInfo] = useState<GoogleDriveLinkStart | undefined>();

  useEffect(() => {
    let cancelled = false;
    let pollTimer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      const result = await pollGoogleDriveLink();
      if (cancelled) return;
      if (result.status === "pending") {
        pollTimer = setTimeout(poll, (info?.interval ?? 5) * 1000);
      } else if (result.status === "success") {
        setState("success");
        onLinked();
      } else {
        setState(result.status === "expired" ? "expired" : "error");
      }
    };

    startGoogleDriveLink().then((result) => {
      if (cancelled) return;
      if (result.error || !result.user_code) {
        setState("error");
        return;
      }
      setInfo(result);
      setState("waiting");
      pollTimer = setTimeout(poll, result.interval * 1000);
    });

    return () => {
      cancelled = true;
      if (pollTimer) clearTimeout(pollTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const qrTarget = info?.verification_url_complete ?? info?.verification_url;
  const qrDataUrl = useMemo(() => (qrTarget ? qrCodeDataUrl(qrTarget) : undefined), [qrTarget]);

  return (
    <ModalRoot onCancel={onClose} closeModal={onClose} bHideCloseIcon={false}>
      <div
        style={{
          minHeight: PIP_CONTENT_HEIGHT,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: "8px" }}>Link Google Drive</div>

        {state === "starting" && <div>Starting...</div>}

        {state === "waiting" && info && (
          <>
            {qrDataUrl && <img src={qrDataUrl} alt="QR code" style={{ width: "180px", height: "180px" }} />}
            <div style={{ fontSize: "0.75em", opacity: 0.7, margin: "4px 0" }}>
              Scan with your phone, then approve access. Code: <strong>{info.user_code}</strong>
            </div>
            <div style={{ fontSize: "0.7em", opacity: 0.6 }}>Waiting for approval...</div>
          </>
        )}

        {state === "success" && <div style={{ color: "#4caf50" }}>✓ Linked! You can close this window.</div>}
        {state === "expired" && <div>The code expired before it was approved. Try again from Share options.</div>}
        {state === "error" && <div>Couldn't link Google Drive. Check the plugin log.</div>}
      </div>
    </ModalRoot>
  );
}

function openGoogleDriveLinkModal(onLinked: () => void) {
  const modal = showModal(<GoogleDriveLinkModal onLinked={onLinked} onClose={() => modal.Close()} />);
}

// Step-up confirmation shown before starting the link flow: linking saves a
// (locally obfuscated, not truly encrypted -- see main.py) session on disk
// so you don't have to re-approve on every use. Requiring the Deck's own
// password here means someone who picks up an already-unlocked Deck can't
// silently link their own Google account on it. The password is sent once,
// straight to sudo's stdin on the backend, and is never logged or stored.
function GoogleDriveConfirmModal({ onConfirmed, onClose }: { onConfirmed: () => void; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const [checking, setChecking] = useState(false);
  const [errorShown, setErrorShown] = useState(false);

  const onContinue = async () => {
    if (!password) return;
    setChecking(true);
    setErrorShown(false);
    try {
      const ok = await verifySudoPassword(password);
      if (ok) {
        onConfirmed();
      } else {
        setErrorShown(true);
      }
    } finally {
      setPassword("");
      setChecking(false);
    }
  };

  return (
    <ModalRoot onCancel={onClose} closeModal={onClose} bHideCloseIcon={false}>
      <div style={{ minHeight: PIP_CONTENT_HEIGHT, display: "flex", flexDirection: "column", justifyContent: "center" }}>
        <div style={{ fontWeight: 600, marginBottom: "8px" }}>Link Google Drive</div>
        <div style={{ fontSize: "0.75em", opacity: 0.8, marginBottom: "10px" }}>
          Linking saves a session on this Deck so you won't have to re-approve every time. It's
          obfuscated on disk, not left as plain text, but it isn't full encryption — if this Deck
          were ever compromised, that saved session could be at risk. Enter this Deck's password
          to confirm it's really you before continuing.
        </div>
        {/* `bIsPassword` alone didn't mask the input in practice, so the native
            HTML `type="password"` is forced through too -- TextFieldProps'
            declared type doesn't include `type` (it extends the generic
            HTMLAttributes, not InputHTMLAttributes), but the underlying
            element is a real <input>, so this still reaches it. This causes
            a harmless TS2322 build warning (not a build failure) since
            `type` isn't part of the declared prop type. */}
        <TextField
          bIsPassword
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onContinue();
          }}
          focusOnMount
        />
        {errorShown && (
          <div style={{ color: "#f44336", fontSize: "0.75em", marginTop: "6px" }}>
            Incorrect password. Try again.
          </div>
        )}
        <div style={{ marginTop: "10px" }}>
          <ButtonItem layout="below" disabled={!password || checking} onClick={onContinue}>
            {checking ? "Checking..." : "Continue"}
          </ButtonItem>
        </div>
      </div>
    </ModalRoot>
  );
}

function openGoogleDriveConfirmModal(onLinked: () => void) {
  const modal = showModal(
    <GoogleDriveConfirmModal
      onConfirmed={() => {
        modal.Close();
        openGoogleDriveLinkModal(onLinked);
      }}
      onClose={() => modal.Close()}
    />
  );
}

// Important: OK and Cancel (the controller's B button always fires Cancel)
// only close the preview, with no destructive actions — "Delete" used to
// live in the Cancel slot and B would delete the screenshot by accident.
// Share and Delete are now plain buttons inside the content, never tied to
// OK/Cancel.
function PreviewModalContent({
  item,
  onDeleted,
  onClose,
}: {
  item: ScreenshotItem;
  onDeleted: () => void;
  onClose: () => void;
}) {
  const [src, setSrc] = useState<string>(item.thumbnail);
  const [shareMethod, setShareMethod] = useState("qr");

  useEffect(() => {
    let cancelled = false;
    getScreenshotImage(item.path).then((fullImage) => {
      if (!cancelled && fullImage) setSrc(fullImage);
    });
    return () => {
      cancelled = true;
    };
  }, [item.path]);

  const onDelete = async () => {
    const ok = await deleteScreenshot(item.path);
    if (ok) {
      toaster.toast({ title: "Screenshot deleted", body: item.filename });
      onDeleted();
      onClose();
    } else {
      toaster.toast({ title: "Couldn't delete the screenshot", body: item.filename });
    }
  };

  const onShareMethodChange = async (option: { data: string; label: string }) => {
    setShareMethod(option.data);
    if (option.data === "qr") {
      onClose();
      openShareModal(item, onDeleted);
      return;
    }
    if (option.data === "googledrive") {
      const status = await getGoogleDriveStatus();
      if (!status.linked) {
        toaster.toast({ title: "Google Drive isn't linked", body: "Link it from Share options first." });
        return;
      }
      toaster.toast({ title: "Uploading to Google Drive...", body: item.filename });
      const result = await uploadScreenshotToDrive(item.path);
      if (result.ok) {
        toaster.toast({ title: "Uploaded to Google Drive", body: item.filename });
      } else if (result.error === "duplicate") {
        toaster.toast({ title: "Already on Google Drive", body: `${item.filename} was uploaded before.` });
      } else {
        toaster.toast({ title: "Upload failed", body: "Check the plugin log for details." });
      }
      return;
    }
    toaster.toast({ title: "Coming soon", body: `${option.label.replace(" (coming soon)", "")} isn't wired up yet.` });
  };

  return (
    <ModalRoot onCancel={onClose} closeModal={onClose} bHideCloseIcon={false}>
      <div style={{ minHeight: PIP_CONTENT_HEIGHT, display: "flex", flexDirection: "column", justifyContent: "center" }}>
        <div style={{ fontWeight: 600 }}>{item.filename}</div>
        <div style={{ fontSize: "0.8em", opacity: 0.7, marginBottom: "8px" }}>{item.appName}</div>
        <img
          src={src}
          alt={item.filename}
          style={{ maxWidth: "90%", maxHeight: PIP_CONTENT_HEIGHT, borderRadius: 4, display: "block", margin: "0 auto" }}
        />
        <div style={{ marginTop: "8px" }}>
          <DropdownItem
            label="Share"
            rgOptions={SHARE_METHOD_OPTIONS}
            selectedOption={shareMethod}
            onChange={onShareMethodChange}
          />
        </div>
        <div style={{ marginTop: "8px" }}>
          <ButtonItem layout="below" onClick={onDelete}>
            Delete
          </ButtonItem>
        </div>
      </div>
    </ModalRoot>
  );
}

function openPreview(item: ScreenshotItem, onDeleted: () => void) {
  const modal = showModal(
    <PreviewModalContent item={item} onDeleted={onDeleted} onClose={() => modal.Close()} />
  );
}

function GalleryRow({ item, onOpen }: { item: ScreenshotItem; onOpen: () => void }) {
  const [highlighted, setHighlighted] = useState(false);

  return (
    <Focusable
      style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        cursor: "pointer",
        padding: "4px",
        borderRadius: "4px",
        backgroundColor: highlighted ? "rgba(255, 255, 255, 0.15)" : "transparent",
      }}
      onActivate={onOpen}
      onFocus={() => setHighlighted(true)}
      onBlur={() => setHighlighted(false)}
      onMouseEnter={() => setHighlighted(true)}
      onMouseLeave={() => setHighlighted(false)}
    >
      <img
        src={item.thumbnail}
        alt={item.filename}
        style={{
          width: "96px",
          height: "60px",
          objectFit: "cover",
          borderRadius: "4px",
          flexShrink: 0,
        }}
      />
      <div style={{ overflow: "hidden" }}>
        <div
          style={{
            fontSize: "0.8em",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {item.filename}
        </div>
        <div style={{ fontSize: "0.75em", opacity: 0.6 }}>{item.appName}</div>
      </div>
    </Focusable>
  );
}

function Gallery() {
  const [expanded, setExpanded] = useState(false);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ScreenshotPage | undefined>();
  const [loading, setLoading] = useState(false);

  // Fetches only the requested batch (5 images) instead of reloading the whole plugin.
  const loadPage = useCallback(async (requestedOffset: number) => {
    setLoading(true);
    try {
      const result = await getScreenshots(Math.max(0, requestedOffset), PAGE_SIZE);
      setPage(result);
      setOffset(result.offset);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPage(0);
  }, [loadPage]);

  // If auto-delete ran (limit exceeded), the current page is no longer
  // valid — go back to the start to reflect the real state.
  useEffect(() => {
    const listener = addEventListener<[count: number]>("auto_delete_performed", () => {
      loadPage(0);
    });
    return () => removeEventListener("auto_delete_performed", listener);
  }, [loadPage]);

  const total = page?.total ?? 0;
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;
  const refreshCurrentPage = () => loadPage(offset);

  return (
    <PanelSection title="Gallery">
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => setExpanded((e) => !e)}>
          {total ? `Gallery (${total})` : "Gallery"} {expanded ? "▲" : "▼"}
        </ButtonItem>
      </PanelSectionRow>

      {expanded && (
        <>
          {loading && (
            <PanelSectionRow>
              <div>Loading...</div>
            </PanelSectionRow>
          )}

          {!loading && page?.items.length === 0 && (
            <PanelSectionRow>
              <div>
                No Steam screenshots yet. Take one with the Steam button + R1
                (or RB) and refresh here.
              </div>
            </PanelSectionRow>
          )}

          {page?.items.map((item) => (
            <PanelSectionRow key={item.path}>
              <GalleryRow item={item} onOpen={() => openPreview(item, refreshCurrentPage)} />
            </PanelSectionRow>
          ))}

          <PanelSectionRow>
            <Focusable style={{ display: "flex", gap: "8px" }}>
              <Button
                style={{ width: "64px", display: "flex", justifyContent: "center", flexShrink: 0 }}
                disabled={!hasPrev || loading}
                onClick={() => loadPage(offset - PAGE_SIZE)}
              >
                <FaArrowUp />
              </Button>
              <Button
                style={{ width: "64px", display: "flex", justifyContent: "center", flexShrink: 0 }}
                disabled={!hasNext || loading}
                onClick={() => loadPage(offset + PAGE_SIZE)}
              >
                <FaArrowDown />
              </Button>
            </Focusable>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={loading} onClick={() => loadPage(0)}>
              <FaSyncAlt /> Refresh
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
}

function StoragePanel() {
  const [expanded, setExpanded] = useState(false);
  const [settings, setLocalSettings] = useState<Settings | undefined>();

  const refresh = useCallback(() => {
    getSettings().then(setLocalSettings);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const listener = addEventListener<[count: number]>("auto_delete_performed", (count) => {
      toaster.toast({
        title: "Auto-delete ran",
        body: `${count} old screenshot(s) removed to stay under your limit.`,
      });
      refresh();
    });
    return () => removeEventListener("auto_delete_performed", listener);
  }, [refresh]);

  // Fired by the backend (checked whenever the gallery loads/refreshes,
  // the closest proxy we have to "right after a new screenshot was taken",
  // since Steam -- not us -- does the actual capturing) the first time
  // usage crosses 80/90/100%. `critical: true` is the only "make this red
  // and urgent" knob the toast API exposes; there's no free-form color.
  useEffect(() => {
    const listener = addEventListener<[threshold: number]>("storage_threshold_reached", (threshold) => {
      if (threshold >= 100) {
        toaster.toast({ title: "Storage limit reached", body: "You're at or over your configured limit.", critical: true });
      } else if (threshold >= 90) {
        toaster.toast({ title: "Storage critical (90%)", body: "You're almost at your limit.", critical: true });
      } else {
        toaster.toast({ title: "Storage warning (80%)", body: "Your screenshots are taking up a lot of space." });
      }
      refresh();
    });
    return () => removeEventListener("storage_threshold_reached", listener);
  }, [refresh]);

  const update = async (patch: Partial<Settings>) => {
    if (!settings) return;
    const next = { ...settings, ...patch };
    setLocalSettings(next); // immediate feedback in the UI
    const saved = await setSettings(next);
    setLocalSettings(saved);
  };

  const isUnlimited = settings?.max_storage_mb === STORAGE_LIMIT_UNLIMITED;
  const usedPct = settings && !isUnlimited && settings.max_storage_mb > 0 ? (settings.used_mb / settings.max_storage_mb) * 100 : 0;
  const tierColor = usedPct >= 100 ? "#f44336" : usedPct >= 90 ? "#ff7043" : usedPct >= 80 ? "#f5a623" : undefined;
  const tierMessage =
    usedPct >= 100
      ? 'Limit exceeded. Delete screenshots from the gallery (tap one and choose "Delete") or raise the limit above.'
      : usedPct >= 90
      ? "Storage is critically full (90%+ used). Consider deleting some screenshots soon."
      : usedPct >= 80
      ? "Storage warning: 80% or more of your limit is used."
      : undefined;
  const summary = settings
    ? `Storage: ${settings.used_mb} MB / ${storageLimitShort(settings.max_storage_mb / 1024)}`
    : "Storage: loading...";

  const onLimitChange = (index: number) => {
    const gb = STORAGE_LIMIT_VALUES_GB[index];
    const mb = gb === STORAGE_LIMIT_UNLIMITED ? 0 : Math.round(gb * 1024);
    update(mb === 0 ? { max_storage_mb: 0, auto_delete: false } : { max_storage_mb: mb });
  };

  return (
    <PanelSection title="Storage">
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={!settings} onClick={() => setExpanded((e) => !e)}>
          {summary} {expanded ? "▲" : "▼"}
        </ButtonItem>
      </PanelSectionRow>

      {expanded && settings && (
        <>
          {!settings.account_detected && (
            <PanelSectionRow>
              <div style={{ color: "#f5a623" }}>
                Couldn't detect your Steam account in userdata/. The gallery might show up empty.
              </div>
            </PanelSectionRow>
          )}

          <PanelSectionRow>
            <div style={{ fontSize: "1.1em", fontWeight: 700, marginBottom: "4px" }}>
              {storageLimitDescription(settings.max_storage_mb / 1024)}
            </div>
            {isUnlimited && (
              <div style={{ fontSize: "0.65em", opacity: 0.6, marginBottom: "6px" }}>
                Not recommended on Decks with a small SSD.
              </div>
            )}
            <SliderField
              label="Warning Limit"
              description="Gives a warning when your screenshots pass this size."
              value={storageLimitIndexForMb(settings.max_storage_mb)}
              min={0}
              max={STORAGE_LIMIT_LAST_INDEX}
              step={1}
              onChange={onLimitChange}
            />
          </PanelSectionRow>

          {tierColor && tierMessage && (
            <PanelSectionRow>
              <div style={{ color: tierColor, fontWeight: usedPct >= 90 ? 600 : undefined }}>{tierMessage}</div>
            </PanelSectionRow>
          )}

          <PanelSectionRow>
            <ToggleField
              label="Auto-delete oldest when over limit"
              description={
                isUnlimited
                  ? "Not applicable with no limit set."
                  : "⚠ WARNING: if enabled, once you pass the Warning Limit above, this plugin will PERMANENTLY DELETE your oldest screenshots — from ANY game — without asking, until you're back under the limit. This cannot be undone. Leave this off unless you're sure."
              }
              checked={settings.auto_delete}
              disabled={isUnlimited}
              onChange={(checked) => update({ auto_delete: checked })}
            />
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
}

function ShareOptionsPanel() {
  const [expanded, setExpanded] = useState(false);
  const [settings, setLocalSettings] = useState<Settings | undefined>();
  const [driveLinked, setDriveLinked] = useState<boolean | undefined>();
  const [unlinking, setUnlinking] = useState(false);

  const refreshDriveStatus = useCallback(() => {
    if (!GOOGLE_DRIVE_ENABLED) return;
    getGoogleDriveStatus().then((s) => setDriveLinked(s.linked));
  }, []);

  useEffect(() => {
    getSettings().then(setLocalSettings);
    refreshDriveStatus();
  }, [refreshDriveStatus]);

  const update = async (patch: Partial<Settings>) => {
    if (!settings) return;
    const next = { ...settings, ...patch };
    setLocalSettings(next); // immediate feedback in the UI
    const saved = await setSettings(next);
    setLocalSettings(saved);
  };

  const onUnlinkDrive = async () => {
    setUnlinking(true);
    try {
      await unlinkGoogleDrive();
      toaster.toast({ title: "Google Drive unlinked", body: "Access has been revoked." });
      refreshDriveStatus();
    } finally {
      setUnlinking(false);
    }
  };

  return (
    <PanelSection title="Share options">
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={!settings} onClick={() => setExpanded((e) => !e)}>
          Share options {expanded ? "▲" : "▼"}
        </ButtonItem>
      </PanelSectionRow>

      {expanded && settings && (
        <PanelSectionRow>
          <DropdownItem
            label="QR link stays active for"
            description="How long a 'Share via QR' link stays valid if nobody downloads it."
            rgOptions={DURATION_OPTIONS}
            selectedOption={settings.qr_share_duration_seconds}
            onChange={(option) => update({ qr_share_duration_seconds: option.data })}
          />
        </PanelSectionRow>
      )}

      {expanded && GOOGLE_DRIVE_ENABLED && (
        <PanelSectionRow>
          {driveLinked ? (
            <ButtonItem layout="below" disabled={unlinking} onClick={onUnlinkDrive}>
              {unlinking ? "Unlinking..." : "Unlink Google Drive"}
            </ButtonItem>
          ) : (
            <ButtonItem
              layout="below"
              disabled={driveLinked === undefined}
              onClick={() => openGoogleDriveConfirmModal(refreshDriveStatus)}
            >
              Link Google Drive
            </ButtonItem>
          )}
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

function Content() {
  return (
    <>
      <Gallery />
      <StoragePanel />
      <ShareOptionsPanel />
    </>
  );
}

export default definePlugin(() => {
  console.log("Omni-Revi-Transfer initializing")

  return {
    name: "Omni-Revi-Transfer",
    titleView: <div className={staticClasses.Title}>Omni-Revi-Transfer</div>,
    content: <Content />,
    icon: <FaCamera />,
    onDismount() {},
  };
});
