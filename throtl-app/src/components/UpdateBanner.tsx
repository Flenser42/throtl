import { isTauri } from "../lib/ipc";
import { RELEASES_URL } from "../lib/update";
import { Download, X } from "./icons";

/** Opens the release page in the user's browser (never inside the webview). */
export async function openReleases(): Promise<void> {
  if (isTauri()) {
    try {
      const mod = await import("@tauri-apps/plugin-opener");
      await mod.openUrl(RELEASES_URL);
      return;
    } catch {
      /* fall through to window.open */
    }
  }
  window.open(RELEASES_URL, "_blank", "noreferrer");
}

interface Props {
  version: string;
  current: string;
  onDismiss: () => void;
}

export function UpdateBanner({ version, current, onDismiss }: Props) {
  return (
    <div className="update-banner" role="status">
      <Download size={15} />
      <span>
        Throtl <b>{version}</b> is available — you have {current}.
      </span>
      <button type="button" className="btn" onClick={() => void openReleases()}>
        View release
      </button>
      <button type="button" className="icon-btn" aria-label="Dismiss" onClick={onDismiss}>
        <X size={14} />
      </button>
    </div>
  );
}
