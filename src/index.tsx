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

const getScreenshots = callable<[offset: number, limit: number], ScreenshotPage>("get_screenshots");
const getScreenshotImage = callable<[path: string], string | null>("get_screenshot_image");
const deleteScreenshot = callable<[path: string], boolean>("delete_screenshot");
const getSettings = callable<[], Settings>("get_settings");
const setSettings = callable<[settings: Partial<Settings>], Settings>("set_settings");
const startQrShare = callable<[path: string, durationSeconds: number], ShareResult>("start_qr_share");
const stopQrShare = callable<[], void>("stop_qr_share");

const PAGE_SIZE = 5;

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

// Same size footprint as the image in the preview, so switching from the
// image PiP to the share PiP doesn't feel like a size jump.
const PIP_CONTENT_HEIGHT = "30vh";

const SHARE_METHOD_OPTIONS = [
  { data: "qr", label: "QR Code" },
  { data: "icloud", label: "iCloud (coming soon)" },
  { data: "googledrive", label: "Google Drive (coming soon)" },
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

  const onShareMethodChange = (option: { data: string; label: string }) => {
    setShareMethod(option.data);
    if (option.data === "qr") {
      onClose();
      openShareModal(item, onDeleted);
    } else {
      toaster.toast({ title: "Coming soon", body: `${option.label.replace(" (coming soon)", "")} isn't wired up yet.` });
    }
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
    <PanelSection title={total ? `Gallery (${total})` : "Gallery"}>
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

  const update = async (patch: Partial<Settings>) => {
    if (!settings) return;
    const next = { ...settings, ...patch };
    setLocalSettings(next); // immediate feedback in the UI
    const saved = await setSettings(next);
    setLocalSettings(saved);
  };

  const limitGb = settings ? settings.max_storage_mb / 1024 : 0;
  const summary = settings ? `Storage: ${settings.used_mb} MB / ${limitGb.toFixed(1)} GB` : "Storage: loading...";

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
            <SliderField
              label="Warning Limit"
              description="Gives a warning when your screenshots pass this size."
              value={Math.round(limitGb * 2) / 2}
              min={0.5}
              max={50}
              step={0.5}
              showValue
              valueSuffix=" GB"
              onChange={(value) => update({ max_storage_mb: Math.round(value * 1024) })}
            />
          </PanelSectionRow>

          {settings.over_limit && (
            <PanelSectionRow>
              <div style={{ color: "#f5a623" }}>
                Limit exceeded. Delete screenshots from the gallery (tap one and choose "Delete")
                or raise the limit above.
              </div>
            </PanelSectionRow>
          )}

          <PanelSectionRow>
            <ToggleField
              label="Auto-delete oldest when over limit"
              description="⚠ WARNING: if enabled, once you pass the Warning Limit above, this plugin will PERMANENTLY DELETE your oldest screenshots — from ANY game — without asking, until you're back under the limit. This cannot be undone. Leave this off unless you're sure."
              checked={settings.auto_delete}
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

  useEffect(() => {
    getSettings().then(setLocalSettings);
  }, []);

  const update = async (patch: Partial<Settings>) => {
    if (!settings) return;
    const next = { ...settings, ...patch };
    setLocalSettings(next); // immediate feedback in the UI
    const saved = await setSettings(next);
    setLocalSettings(saved);
  };

  const durationLabel =
    DURATION_OPTIONS.find((o) => o.data === settings?.qr_share_duration_seconds)?.label ?? "10 minutes";

  return (
    <PanelSection title="Share options">
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={!settings} onClick={() => setExpanded((e) => !e)}>
          Share options: {durationLabel} {expanded ? "▲" : "▼"}
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
  console.log("Decky Universal Share initializing")

  return {
    name: "Decky Universal Share",
    titleView: <div className={staticClasses.Title}>Universal Share</div>,
    content: <Content />,
    icon: <FaCamera />,
    onDismount() {},
  };
});
