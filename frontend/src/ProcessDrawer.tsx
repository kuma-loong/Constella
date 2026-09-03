import { useEffect, useRef, useState } from "preact/hooks";
import { fmtDuration, fmtGiB, formatTime } from "./format";
import { jobsHref, type ProcessDetail } from "./process-details";

export function ProcessDrawer({
  detail,
  active,
  onClose,
}: {
  detail: ProcessDetail;
  active: boolean;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const copyTimerRef = useRef(0);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const command = detail.cmdline || detail.exe || detail.name;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) {
      return;
    }
    if (!dialog.open) {
      dialog.showModal();
    }
    window.requestAnimationFrame(() => dialog.querySelector<HTMLButtonElement>("[data-process-close]")?.focus());
    return () => {
      if (dialog.open) {
        dialog.close();
      }
      window.clearTimeout(copyTimerRef.current);
    };
  }, []);

  async function copyCommand() {
    try {
      await navigator.clipboard.writeText(command);
      setCopyState("copied");
      window.clearTimeout(copyTimerRef.current);
      copyTimerRef.current = window.setTimeout(() => setCopyState("idle"), 1400);
    } catch {
      setCopyState("error");
    }
  }

  return (
    <dialog
      ref={dialogRef}
      class="process-drawer"
      aria-labelledby="process-drawer-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <header class="process-drawer-head">
        <div>
          <span>{active ? "Running process" : "Process ended"}</span>
          <h2 id="process-drawer-title">{detail.task}</h2>
        </div>
        <button class="guide-close" type="button" data-process-close onClick={onClose} aria-label="Close process details">×</button>
      </header>

      <div class="process-drawer-body">
        {!active ? <div class="process-ended-note">This process is no longer present in the latest snapshot.</div> : null}

        <dl class="process-meta">
          <Meta label="User" value={detail.user} />
          <Meta label="PID" value={String(detail.pid)} />
          <Meta label="Parent PID" value={detail.ppid == null ? "n/a" : String(detail.ppid)} />
          <Meta label="Runtime" value={fmtDuration(detail.runtime)} />
          <Meta label="Started" value={detail.processStartTime == null ? "n/a" : formatTime(detail.processStartTime)} />
          <Meta label="Type" value={detail.kind} />
          <Meta label="Node" value={detail.nodeId} />
          <Meta label="Host" value={detail.hostname || "n/a"} />
        </dl>

        <section class="process-drawer-section">
          <div class="process-section-label">
            <h3>Accelerators</h3>
            <span>{fmtGiB(detail.totalMemoryMb)} total</span>
          </div>
          <div class="process-device-list">
            {detail.devices.map((device) => (
              <div key={device.gpuUuid}>
                <span class="gpu-pill">{device.label}</span>
                <strong>{fmtGiB(device.memoryMb)}</strong>
              </div>
            ))}
          </div>
        </section>

        <section class="process-drawer-section">
          <div class="process-section-label"><h3>Command</h3></div>
          <code class="process-command">{command}</code>
          {detail.exe && detail.exe !== command ? <p class="process-executable">{detail.exe}</p> : null}
        </section>

        {detail.detailStatus || detail.detailError ? (
          <section class="process-drawer-section process-detail-state">
            <div class="process-section-label"><h3>Detail status</h3></div>
            <p>{detail.detailError || detail.detailStatus}</p>
          </section>
        ) : null}
      </div>

      <footer class="process-drawer-actions">
        <a class="process-action is-primary" href={jobsHref(detail)} onClick={onClose}>View in Jobs</a>
        <button class="process-action" type="button" onClick={copyCommand}>
          {copyState === "copied" ? "Copied" : copyState === "error" ? "Copy failed" : "Copy command"}
        </button>
      </footer>
    </dialog>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
