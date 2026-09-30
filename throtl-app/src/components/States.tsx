import { PlugZap, Refresh, TriangleAlert } from "./icons";

/** Skeleton shown while the first snapshot is on its way (§6.8). */
export function ConnectingState() {
  return (
    <>
      <section className="card overview">
        <div className="overview-top">
          <div className="metric">
            <span className="skel skel-label" />
            <span className="skel skel-hero" />
            <span className="skel skel-cap" />
          </div>
          <div className="metric">
            <span className="skel skel-label" />
            <span className="skel skel-hero" />
            <span className="skel skel-cap" />
          </div>
        </div>
        <div className="overview-divider" />
        <div className="skel skel-graph" />
      </section>
      <section className="card applications">
        <div className="skel skel-row" />
        <div className="skel skel-row" />
        <div className="skel skel-row" />
      </section>
    </>
  );
}

interface StateProps {
  message?: string | null;
  onRetry: () => void;
}

export function OfflineState({ message, onRetry }: StateProps) {
  return (
    <section className="card state">
      <span className="state-icon">
        <PlugZap size={32} />
      </span>
      <h2 className="state-title">Daemon not reachable</h2>
      <p className="state-text">
        {message ?? "The Throtl service runs in the background and needs root."}
      </p>
      <p className="state-hint">
        Check the service, then try again: <code>systemctl status throtl</code>
      </p>
      <div className="state-actions">
        <button type="button" className="btn btn-primary" onClick={onRetry}>
          <Refresh size={15} /> Retry
        </button>
        <button type="button" className="btn">
          Run <code>throtl-cli doctor</code>
        </button>
      </div>
    </section>
  );
}

export function DeniedState({ onRetry }: StateProps) {
  return (
    <section className="card state">
      <span className="state-icon warn">
        <TriangleAlert size={30} />
      </span>
      <h2 className="state-title">No access to the daemon socket</h2>
      <p className="state-text">
        Your account is not in the <code>throtl</code> group, so the socket
        (<code>/run/throtl/daemon.sock</code>) is not readable.
      </p>
      <p className="state-hint">
        Run the next command, then log out and back in (or run{" "}
        <code>newgrp throtl</code>):
      </p>
      <pre className="state-code">sudo usermod -aG throtl "$USER"</pre>
      <div className="state-actions">
        <button type="button" className="btn btn-primary" onClick={onRetry}>
          <Refresh size={15} /> Retry
        </button>
      </div>
    </section>
  );
}
