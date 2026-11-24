import { code128Bars } from "@/lib/code128";

export function Barcode({ value, height = 56, module = 1.6 }: { value: string; height?: number; module?: number }) {
  let bars: number[];
  try { bars = code128Bars(value); } catch { return <p className="text-xs text-destructive">Cannot encode “{value}”</p>; }
  const quiet = 10;
  const width = bars.reduce((a, b) => a + b, 0) + quiet * 2;
  const starts = bars.reduce<number[]>((acc, w, i) => [...acc, (acc[i] ?? quiet) + w], [quiet]);
  const rects = bars.map((w, i) => (i % 2 === 0 ? <rect key={i} x={starts[i]! * module} y={0} width={w * module} height={height} /> : null));
  return (
    <svg viewBox={`0 0 ${width * module} ${height + 16}`} width={width * module} height={height + 16} role="img" aria-label={`Barcode ${value}`} className="bg-white">
      <g fill="#000">{rects}</g>
      <text x={(width * module) / 2} y={height + 13} textAnchor="middle" fontSize="11" fontFamily="monospace" fill="#000">{value}</text>
    </svg>
  );
}
