import { useEffect, useRef, useState } from "react";

export type Series = { name: string; slot: number; points: { t: number; v: number | null }[] };

const H = 240;
const M = { top: 12, right: 200, bottom: 28, left: 44 };

/** Round tick values across the domain: 4 to 6 of them at a step of 5, 10, 20 or 25 percent of a unit. */
function niceTicks([lo, hi]: [number, number]): number[] {
  const step = [0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10, 20, 50, 100].find((s) => (hi - lo) / s <= 5) ?? (hi - lo) / 4;
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toFixed(6)));
  return out;
}

const time = (t: number) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** One measure, one axis, 2px lines; legend, direct end labels, crosshair tooltip (dataviz method). */
export default function LineChart({
  title,
  series,
  format,
  domain = [0, 1],
  xFormat = time,
}: {
  title: string;
  series: Series[];
  format: (v: number) => string;
  domain?: [number, number];
  xFormat?: (t: number) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<SVGSVGElement>(null);
  const box = useRef<HTMLElement>(null);
  // Drawn at the width it's shown at, so text stays at its set size instead of scaling with the page.
  const [W, setW] = useState(760);
  const times = [...new Set(series.flatMap((s) => s.points.map((p) => p.t)))].sort((a, b) => a - b);
  const drawn = times.length >= 2;
  useEffect(() => {
    if (!box.current) return;
    const watch = new ResizeObserver(([e]) => setW(Math.max(360, Math.round(e.contentRect.width))));
    watch.observe(box.current);
    return () => watch.disconnect();
  }, [drawn]);
  if (!drawn) return <p className="muted">{title}: not enough data yet.</p>;
  const [t0, t1] = [times[0], times[times.length - 1]];
  const x = (t: number) => M.left + ((t - t0) / (t1 - t0)) * (W - M.left - M.right);
  const y = (v: number) => M.top + (1 - (v - domain[0]) / (domain[1] - domain[0])) * (H - M.top - M.bottom);
  const ticks = niceTicks(domain);
  const xTicks = times.filter((_, i) => i % Math.ceil(times.length / Math.max(2, Math.floor(W / 140))) === 0);

  const path = (pts: Series["points"]) => {
    let d = "";
    let pen = false;
    for (const p of pts) {
      if (p.v == null) {
        pen = false;
        continue;
      }
      d += `${pen ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`;
      pen = true;
    }
    return d;
  };

  // End labels, with leader lines when they would collide.
  const ends = series
    .map((s) => {
      const last = [...s.points].reverse().find((p) => p.v != null);
      return last ? { s, last, ly: y(last.v!) } : null;
    })
    .filter((e): e is NonNullable<typeof e> => e !== null)
    .sort((a, b) => a.ly - b.ly);
  for (let i = 1; i < ends.length; i++) ends[i].ly = Math.max(ends[i].ly, ends[i - 1].ly + 16);

  function move(e: React.MouseEvent) {
    const box = ref.current!.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    let best = 0;
    times.forEach((t, i) => {
      if (Math.abs(x(t) - px) < Math.abs(x(times[best]) - px)) best = i;
    });
    setHover(times[best]);
  }

  return (
    <figure className="chart viz-root" ref={box}>
      <figcaption>{title}</figcaption>
      <div className="legend">
        {series.map((s) => (
          <span key={s.name}>
            <i style={{ background: `var(--series-${s.slot})` }} /> {s.name}
          </span>
        ))}
      </div>
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} onMouseMove={move} onMouseLeave={() => setHover(null)}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={M.left} x2={W - M.right} y1={y(v)} y2={y(v)} className="grid" />
            <text x={M.left - 6} y={y(v) + 4} textAnchor="end" className="axis">
              {format(v)}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text key={t} x={x(t)} y={H - 8} textAnchor="middle" className="axis">
            {xFormat(t)}
          </text>
        ))}
        {series.map((s) => (
          <path key={s.name} d={path(s.points)} fill="none" stroke={`var(--series-${s.slot})`} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {ends.map(({ s, last, ly }) => (
          <g key={s.name}>
            <line x1={x(last.t)} y1={y(last.v!)} x2={W - M.right + 8} y2={ly} className="leader" />
            <circle cx={x(last.t)} cy={y(last.v!)} r={4} fill={`var(--series-${s.slot})`} className="ring" />
            <text x={W - M.right + 12} y={ly + 4} className="label">
              {s.name} {format(last.v!)}
            </text>
          </g>
        ))}
        {hover != null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={M.top} y2={H - M.bottom} className="crosshair" />
            {series.map((s) => {
              const p = s.points.find((q) => q.t === hover);
              return p?.v != null ? <circle key={s.name} cx={x(hover)} cy={y(p.v)} r={4} fill={`var(--series-${s.slot})`} className="ring" /> : null;
            })}
          </g>
        )}
        <rect x={M.left} y={M.top} width={W - M.left - M.right} height={H - M.top - M.bottom} fill="transparent" />
      </svg>
      {hover != null && (
        <div className="tooltip">
          <strong>{xFormat(hover)}</strong>
          {series.map((s) => {
            const p = s.points.find((q) => q.t === hover);
            return (
              <span key={s.name}>
                <i style={{ background: `var(--series-${s.slot})` }} /> {s.name}: {p?.v != null ? format(p.v) : "—"}
              </span>
            );
          })}
        </div>
      )}
    </figure>
  );
}
