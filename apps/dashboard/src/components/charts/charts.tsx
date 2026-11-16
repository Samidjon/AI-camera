"use client";

import { useId, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Small dependency-free SVG charts for the Analytics page.
 * Rules (data-viz method): one axis, thin marks with 4px rounded data-ends on the baseline,
 * recessive grid, hover tooltip on every mark, text in ink tokens (never the series colour),
 * a table view for every chart, colours from the validated --viz-* roles.
 */

export interface Point {
  label: string;
  value: number;
  /** Longer label for the tooltip / table (defaults to label). */
  title?: string;
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

function fmt(n: number): string {
  return Number.isInteger(n) ? n.toLocaleString() : n.toFixed(1);
}

function Tooltip({
  x,
  y,
  children,
}: {
  x: string;
  y: string;
  children: React.ReactNode;
}) {
  return (
    <div
      role="status"
      className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-md border bg-popover px-2 py-1 text-xs whitespace-nowrap text-popover-foreground shadow-md"
      style={{ left: x, top: `calc(${y} - 6px)` }}
    >
      {children}
    </div>
  );
}

/** Table view (accessibility + exact numbers). */
export function DataTable({
  rows,
  columns,
}: {
  rows: (string | number)[][];
  columns: string[];
}) {
  return (
    <div className="max-h-64 overflow-auto rounded-md border">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-muted text-muted-foreground">
          <tr>
            {columns.map((c, i) => (
              <th
                key={c}
                className={cn(
                  "px-2 py-1 font-medium",
                  i ? "text-right" : "text-left",
                )}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t">
              {r.map((v, j) => (
                <td
                  key={j}
                  className={cn(
                    "px-2 py-1",
                    j ? "text-right font-mono tabular-nums" : "",
                  )}
                >
                  {typeof v === "number" ? fmt(v) : v}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Chart / table switch shared by every chart panel. */
export function ChartFrame({
  table,
  children,
  empty,
}: {
  table: React.ReactNode;
  children: React.ReactNode;
  empty?: string | null;
}) {
  const [asTable, setAsTable] = useState(false);
  if (empty)
    return (
      <p className="py-8 text-center text-sm text-muted-foreground">{empty}</p>
    );
  return (
    <div className="flex flex-col gap-2">
      <div className="flex justify-end">
        <button
          type="button"
          className="text-[11px] text-muted-foreground underline-offset-2 hover:underline"
          onClick={() => setAsTable((v) => !v)}
        >
          {asTable ? "Show chart" : "Show table"}
        </button>
      </div>
      {asTable ? table : children}
    </div>
  );
}

/** Single-series column chart (time series of counts). */
export function ColumnChart({
  data,
  unit = "",
  color = "var(--viz-1)",
  height = 180,
  tickEvery,
  ariaLabel,
}: {
  data: Point[];
  unit?: string;
  color?: string;
  height?: number;
  tickEvery?: number;
  ariaLabel: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640;
  const padL = 32;
  const padB = 20;
  const padT = 8;
  const max = niceMax(Math.max(0, ...data.map((d) => d.value)));
  const n = Math.max(1, data.length);
  const step = (W - padL) / n;
  const gap = n > 60 ? 1 : 2;
  const bw = Math.max(1, step - gap);
  const plotH = height - padB - padT;
  const y = (v: number) => padT + plotH - (v / max) * plotH;
  const every = tickEvery ?? Math.max(1, Math.ceil(n / 8));
  const grid = [0, 0.5, 1].map((f) => max * f);
  const h = hover != null ? data[hover] : null;

  return (
    <div className="relative" onMouseLeave={() => setHover(null)}>
      <svg
        viewBox={`0 0 ${W} ${height}`}
        className="w-full"
        role="img"
        aria-label={ariaLabel}
      >
        {grid.map((g) => (
          <g key={g}>
            <line
              x1={padL}
              x2={W}
              y1={y(g)}
              y2={y(g)}
              stroke={g === 0 ? "var(--viz-axis)" : "var(--viz-grid)"}
              strokeWidth={1}
            />
            <text
              x={padL - 6}
              y={y(g) + 3}
              textAnchor="end"
              className="fill-muted-foreground text-[13px] tabular-nums"
            >
              {fmt(g)}
            </text>
          </g>
        ))}
        {data.map((d, i) => {
          const x = padL + i * step + gap / 2;
          const top = y(d.value);
          const hgt = Math.max(0, padT + plotH - top);
          const r = Math.min(4, bw / 2, hgt);
          return (
            <g key={i} onMouseEnter={() => setHover(i)}>
              {/* hit target taller and wider than the mark */}
              <rect
                x={padL + i * step}
                y={padT}
                width={step}
                height={plotH}
                fill="transparent"
              />
              {d.value > 0 && (
                <path
                  d={`M${x},${padT + plotH} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${padT + plotH} Z`}
                  fill={color}
                  opacity={hover == null || hover === i ? 1 : 0.55}
                />
              )}
              {i % every === 0 && (
                <text
                  x={x + bw / 2}
                  y={height - 6}
                  textAnchor="middle"
                  className="fill-muted-foreground text-[13px]"
                >
                  {d.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {h && hover != null && (
        <Tooltip x={`${((padL + hover * step + step / 2) / W) * 100}%`} y="0%">
          <span className="text-muted-foreground">{h.title ?? h.label}</span>{" "}
          <b className="font-mono">
            {fmt(h.value)}
            {unit && ` ${unit}`}
          </b>
        </Tooltip>
      )}
    </div>
  );
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Weekday × hour heatmap, one-hue sequential ramp (lightest = zero). */
export function Heatmap({
  cells,
  ariaLabel,
  unit = "events",
}: {
  cells: { weekday: number; hour: number; value: number }[];
  ariaLabel: string;
  unit?: string;
}) {
  const [hover, setHover] = useState<{
    weekday: number;
    hour: number;
    value: number;
  } | null>(null);
  const max = Math.max(0, ...cells.map((c) => c.value));
  const level = (v: number) =>
    v <= 0 || max === 0 ? 0 : Math.min(5, 1 + Math.floor((v / max) * 4.999));
  const cw = 22;
  const ch = 18;
  const padL = 34;
  const padT = 16;
  const W = padL + 24 * cw;
  const H = padT + 7 * ch;
  const id = useId();
  return (
    <div className="relative" onMouseLeave={() => setHover(null)}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label={ariaLabel}
      >
        {[0, 3, 6, 9, 12, 15, 18, 21].map((h) => (
          <text
            key={h}
            x={padL + h * cw + cw / 2}
            y={10}
            textAnchor="middle"
            className="fill-muted-foreground text-[11px]"
          >
            {String(h).padStart(2, "0")}
          </text>
        ))}
        {DAYS.map((d, i) => (
          <text
            key={d}
            x={padL - 6}
            y={padT + i * ch + ch / 2 + 3}
            textAnchor="end"
            className="fill-muted-foreground text-[11px]"
          >
            {d}
          </text>
        ))}
        {cells.map((c) => (
          <rect
            key={`${id}-${c.weekday}-${c.hour}`}
            x={padL + c.hour * cw + 1}
            y={padT + c.weekday * ch + 1}
            width={cw - 2}
            height={ch - 2}
            rx={3}
            fill={`var(--viz-seq-${level(c.value)})`}
            stroke={hover === c ? "var(--foreground)" : "none"}
            strokeWidth={1.5}
            onMouseEnter={() => setHover(c)}
          />
        ))}
      </svg>
      <div className="mt-1 flex items-center justify-end gap-1 text-[10px] text-muted-foreground">
        0
        {[0, 1, 2, 3, 4, 5].map((l) => (
          <span
            key={l}
            className="inline-block size-2.5 rounded-sm"
            style={{ background: `var(--viz-seq-${l})` }}
          />
        ))}
        {fmt(max)}
      </div>
      {hover && (
        <Tooltip
          x={`${((padL + hover.hour * cw + cw / 2) / W) * 100}%`}
          y={`${((padT + hover.weekday * ch) / H) * 100}%`}
        >
          <span className="text-muted-foreground">
            {DAYS[hover.weekday]} {String(hover.hour).padStart(2, "0")}:00
          </span>{" "}
          <b className="font-mono">{fmt(hover.value)}</b> {unit}
        </Tooltip>
      )}
    </div>
  );
}

/** Horizontal bars for comparing cameras / zones / lines (sorted by the caller). */
export function BarList({
  rows,
  unit = "",
  color = "var(--viz-1)",
}: {
  rows: { label: string; sub?: string; value: number }[];
  unit?: string;
  color?: string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <ul className="flex flex-col gap-2">
      {rows.map((r, i) => (
        <li
          key={i}
          className="group grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-3 text-xs"
          title={`${r.label}: ${fmt(r.value)} ${unit}`}
        >
          <span className="truncate">
            {r.label}
            {r.sub && (
              <span className="ml-1 text-muted-foreground">{r.sub}</span>
            )}
          </span>
          <span className="h-3 rounded-[4px] bg-muted">
            <span
              className="block h-3 rounded-[4px] transition-opacity group-hover:opacity-80"
              style={{
                width: `${(r.value / max) * 100}%`,
                background: color,
                minWidth: r.value > 0 ? 3 : 0,
              }}
            />
          </span>
          <span className="text-right font-mono tabular-nums">
            {fmt(r.value)}
            {unit && (
              <span className="ml-0.5 text-muted-foreground">{unit}</span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}
