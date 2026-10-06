import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import { X } from "./icons";

interface Step {
  /** CSS selector for the element to spotlight (first match). */
  target: string;
  title: string;
  body: string;
}

/**
 * The guided tour walks a first-time user through the dashboard: status pill,
 * live rates, graph, toolbar, the table and its arm switch, and the header
 * actions. Each step spotlights one element and explains it in one or two
 * sentences. Steps whose target element is not on screen (e.g. the arm switch
 * when there are no apps yet) are skipped.
 */
const STEPS: Step[] = [
  {
    target: '[data-tour="status"]',
    title: "Status & profile",
    body: "This pill tells you whether Throtl is limiting and which profile is active. Click it to pause or resume all shaping; the arrow opens the profile menu.",
  },
  {
    target: '[data-tour="readout"]',
    title: "Your live rates",
    body: "Download (green) and upload (brass), updated every second. Below them: active rules, your global caps (click to edit) and the current profile.",
  },
  {
    target: '[data-tour="graph"]',
    title: "Live traffic",
    body: "Traffic over time. Hover for the exact values, click to pin them, and switch the window from 30 s to All. The readout below shows min / avg / max and the busiest app.",
  },
  {
    target: '[data-tour="toolbar"]',
    title: "Filter & sort",
    body: "Type to focus on one app (Ctrl/⌘ K jumps here), sort by download, upload or name, and add a new rule with the button on the right.",
  },
  {
    target: '[data-tour="table"]',
    title: "The process table",
    body: "Each row is one application — its rates, limit, priority and budget. Apps that run many processes are grouped into a single row and summed.",
  },
  {
    target: '[data-tour="arm"]',
    title: "The arm switch",
    body: "This little square arms the limit. Green means it is being enforced; click it to let the app run unlimited again — without deleting the rule.",
  },
  {
    target: '[data-tour="actions"]',
    title: "Statistics, budgets & settings",
    body: "These open as panels from the right. Press L to toggle light/dark, Esc to close, and the ? button anytime to take this tour again.",
  },
];

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const CARD_WIDTH = 320;
const MARGIN = 12;
const PAD = 6;

export function Tour({ onClose, onSetup }: { onClose: () => void; onSetup?: () => void }) {
  // Only tour the elements that actually exist; fall back to the full list if
  // nothing matches (e.g. the dashboard has not loaded).
  const steps = useMemo(() => {
    const visible = STEPS.filter((s) => document.querySelector(s.target));
    return visible.length > 0 ? visible : STEPS;
  }, []);

  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const [cardH, setCardH] = useState(180);
  const cardRef = useRef<HTMLDivElement>(null);

  const step = steps[index];
  const isLast = index === steps.length - 1;

  const measure = useCallback(() => {
    const el = document.querySelector<HTMLElement>(step.target);
    const r = el?.getBoundingClientRect();
    if (!r || (r.width === 0 && r.height === 0)) {
      setRect(null);
      return;
    }
    setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
    if (cardRef.current) setCardH(cardRef.current.offsetHeight);
  }, [step]);

  // Scroll the target into view on step change, then measure.
  useEffect(() => {
    document
      .querySelector<HTMLElement>(step.target)
      ?.scrollIntoView({ block: "center", inline: "nearest" });
    measure();
  }, [step, measure]);

  // Keep the spotlight glued to its element: on scroll/resize, and on a short
  // interval so the 1 Hz table re-sort does not leave it stranded.
  useEffect(() => {
    const id = window.setInterval(measure, 500);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, { passive: true });
    return () => {
      window.clearInterval(id);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure);
    };
  }, [measure]);

  // Focus the dialog on open and hand focus back to the trigger on close.
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    cardRef.current?.focus();
    return () => previous?.focus?.();
  }, []);

  const next = useCallback(() => {
    if (isLast) {
      onClose();
    } else {
      setIndex((i) => i + 1);
    }
  }, [isLast, onClose]);

  const prev = useCallback(() => setIndex((i) => Math.max(0, i - 1)), []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      } else if (event.key === "ArrowRight") {
        next();
      } else if (event.key === "ArrowLeft") {
        prev();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, prev, onClose]);

  let cardStyle: CSSProperties;
  if (!rect) {
    cardStyle = { top: "50%", left: "50%", transform: "translate(-50%, -50%)" };
  } else {
    const left = Math.max(
      MARGIN,
      Math.min(
        rect.left + rect.width / 2 - CARD_WIDTH / 2,
        window.innerWidth - CARD_WIDTH - MARGIN,
      ),
    );
    // Prefer below the target; flip above, then clamp, so the buttons never
    // end up past the viewport edge.
    let top = rect.top + rect.height + MARGIN;
    if (top + cardH > window.innerHeight - MARGIN) {
      const above = rect.top - MARGIN - cardH;
      top = above >= MARGIN ? above : Math.max(MARGIN, window.innerHeight - cardH - MARGIN);
    }
    cardStyle = { top, left };
  }

  return (
    <>
      <div className="tour-backdrop" aria-hidden="true" />
      {rect && (
        <div
          className="tour-highlight"
          style={{
            top: rect.top - PAD,
            left: rect.left - PAD,
            width: rect.width + PAD * 2,
            height: rect.height + PAD * 2,
          }}
        />
      )}
      <div
        className="tour-card"
        style={cardStyle}
        role="dialog"
        aria-modal="true"
        aria-label={`Tour step ${index + 1} of ${steps.length}: ${step.title}`}
        tabIndex={-1}
        ref={cardRef}
      >
        <div className="tour-card-head">
          <span className="tour-step mono">
            {index + 1} / {steps.length}
          </span>
          <button type="button" className="tour-close" aria-label="Close tour" onClick={onClose}>
            <X size={14} />
          </button>
        </div>
        <div className="tour-title">{step.title}</div>
        <div className="tour-body">{step.body}</div>
        <div className="tour-actions">
          <button type="button" className="btn" onClick={onClose}>
            Skip
          </button>
          <span className="tour-nav">
            <button type="button" className="btn" onClick={prev} disabled={index === 0}>
              Back
            </button>
            {isLast && onSetup && (
              <button type="button" className="btn btn-primary" onClick={onSetup}>
                Set up Throtl
              </button>
            )}
            <button type="button" className="btn btn-primary" onClick={next}>
              {isLast ? "Done" : "Next"}
            </button>
          </span>
        </div>
      </div>
    </>
  );
}
