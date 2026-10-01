import { useCallback, useEffect, useState } from "react";

import { isMock } from "../lib/ipc";
import { fetchLatest, isNewer } from "../lib/update";

const PREF = "throtl-check-updates";

function preference(): boolean {
  return localStorage.getItem(PREF) !== "0";
}

/**
 * Checks the public release API once per start (unless disabled) and returns
 * the newer version, if any. `?update=X.Y.Z` forces a banner for screenshots;
 * `?static=1` disables the network call so the mock stays offline.
 */
export function useUpdateCheck(currentVersion: string) {
  const [latest, setLatest] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [enabled, setEnabled] = useState(preference);

  const check = useCallback(async (): Promise<string | null> => {
    const found = await fetchLatest();
    const next = found && isNewer(currentVersion, found) ? found : null;
    setLatest(next);
    setDismissed(false);
    return next;
  }, [currentVersion]);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const forced = params.get("update");
    if (forced) {
      setLatest(forced);
      return;
    }
    if (isMock || !enabled || params.has("static")) return;
    void check();
  }, [enabled, check]);

  const setEnabledPref = (value: boolean) => {
    setEnabled(value);
    localStorage.setItem(PREF, value ? "1" : "0");
    if (value) {
      void check();
    } else {
      setLatest(null);
    }
  };

  return {
    latest: dismissed ? null : latest,
    dismiss: () => setDismissed(true),
    enabled,
    setEnabled: setEnabledPref,
    check,
  };
}
