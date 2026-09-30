import type { Talker } from "../lib/model";

interface Props {
  talkers: Talker[];
  windowLabel: string;
}

export function TopTalkers({ talkers, windowLabel }: Props) {
  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title">Top talkers</div>
        <div className="card-sub" style={{ marginLeft: "auto" }}>
          {windowLabel}
        </div>
      </div>
      <div className="talkers">
        {talkers.map((t) => (
          <div className="talker" key={t.name}>
            <span className="appicon" style={{ background: t.gradient }}>
              {t.initial}
            </span>
            <span className="name">{t.name}</span>
            <span className="bar">
              <i style={{ width: `${Math.round(t.ratio * 100)}%` }} />
            </span>
            <span className="val">{t.value.toFixed(2)}</span>
          </div>
        ))}
      </div>
      <div className="hint" style={{ textAlign: "left", padding: "14px 0 0" }}>
        Rates are 1-minute rolling averages.
      </div>
    </div>
  );
}
