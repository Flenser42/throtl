import { useEffect, useRef, useState } from "react";

function reduceMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Smoothly counts from the previous value to the next (~400ms, easeOutCubic).
 * Starts settled, cancels any in-flight frame, clamps progress and snaps to the
 * exact target so the displayed number can never drift from the real one.
 */
export function useCountUp(value: number, duration = 400): number {
  const [display, setDisplay] = useState(value);
  const fromRef = useRef(value);
  const rafRef = useRef(0);
  const timerRef = useRef(0);
  const firstRef = useRef(true);

  useEffect(() => {
    const cancel = () => {
      cancelAnimationFrame(rafRef.current);
      window.clearTimeout(timerRef.current);
    };

    if (firstRef.current) {
      firstRef.current = false;
      fromRef.current = value;
      setDisplay(value);
      return;
    }
    if (reduceMotion()) {
      fromRef.current = value;
      setDisplay(value);
      return;
    }

    const from = fromRef.current;
    if (from === value) {
      fromRef.current = value;
      setDisplay(value);
      return;
    }

    const start = performance.now();
    const step = (now: number) => {
      const p = Math.min(1, Math.max(0, (now - start) / duration));
      const eased = 1 - Math.pow(1 - p, 3);
      const current = p < 1 ? from + (value - from) * eased : value;
      fromRef.current = current; // origin for a mid-flight retarget
      setDisplay(current);
      if (p < 1) rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    // Safety net: if rAF is throttled (headless/background), snap to the target.
    timerRef.current = window.setTimeout(() => {
      fromRef.current = value;
      setDisplay(value);
    }, duration + 80);

    return cancel;
  }, [value, duration]);

  return display;
}

export function AnimatedNumber({
  value,
  format,
}: {
  value: number;
  format: (n: number) => string;
}) {
  const shown = useCountUp(value);
  return <>{format(shown)}</>;
}
