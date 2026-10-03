import { AnimatedNumber } from "./AnimatedNumber";
import { LiveGraph } from "./LiveGraph";
import { splitRate } from "../lib/format";
import type { DashboardModel } from "../lib/model";

/** The one "monitor" surface: hero rates, inline status, and the live graph. */
export function Overview({
  model,
  peakApp,
  onGlobals,
}: {
  model: DashboardModel;
  peakApp: string;
  onGlobals: () => void;
}) {
  const down = splitRate(model.downKbit, model.unit, 1);
  const up = splitRate(model.upKbit, model.unit, 2);
  const limited = model.apps.filter((a) => !a.unattributed && a.armed).length;
  const caps = [
    model.globalDownLimit != null
      ? `↓ ${splitRate(model.globalDownLimit, model.unit, 0).value}`
      : "↓ unlimited",
    model.globalUpLimit != null
      ? `↑ ${splitRate(model.globalUpLimit, model.unit, 0).value}`
      : "↑ unlimited",
  ].join("   ");

  return (
    <section className="card overview">
      <div className="overview-top">
        <div className="metric">
          <span className="metric-label">Download</span>
          <div className="metric-value mono t-down">
            <AnimatedNumber
              value={model.downKbit}
              format={(n) => splitRate(n, model.unit, 1).value}
            />
            <span className="metric-unit">{down.unit}</span>
          </div>
          <span className="metric-caption">matched to {model.matchedApps} apps</span>
        </div>

        <div className="metric">
          <span className="metric-label">Upload</span>
          <div className="metric-value mono t-up">
            <AnimatedNumber
              value={model.upKbit}
              format={(n) => splitRate(n, model.unit, 2).value}
            />
            <span className="metric-unit">{up.unit}</span>
          </div>
          <span className="metric-caption">{limited} apps limited</span>
        </div>

        <div className="overview-stats">
          <div className="stat">
            <span className="stat-label">Active rules</span>
            <span className="stat-value mono">
              {model.activeRules} <i>of {model.totalRules}</i>
            </span>
          </div>
          <button type="button" className="stat stat-btn" onClick={onGlobals}>
            <span className="stat-label">Global caps</span>
            <span className="stat-value mono">{caps}</span>
          </button>
          <div className="stat">
            <span className="stat-label">Profile</span>
            <span className="stat-value mono">{model.profile}</span>
          </div>
        </div>
      </div>

      <div className="overview-divider" />

      <LiveGraph
        history={model.history}
        matchedApps={model.matchedApps}
        peakApp={peakApp}
        unit={model.unit}
      />
    </section>
  );
}
