const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const inrCompact = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", notation: "compact", maximumFractionDigits: 1 });
const num = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 });

/** Money arrives from the API as decimal strings; never do arithmetic on floats for storage. */
export function money(v: string | number | null | undefined, opts?: { compact?: boolean }): string {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "number" ? v : Number(v);
  if (Number.isNaN(n)) return String(v);
  return opts?.compact ? inrCompact.format(n) : inr.format(n);
}

export function qty(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  return num.format(Number(v));
}

export function date(v: string | null | undefined): string {
  if (!v) return "—";
  const d = new Date(v.length === 10 ? `${v}T00:00:00` : v);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function dateTime(v: string | null | undefined): string {
  if (!v) return "—";
  return new Date(v).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function todayISO(): string {
  const d = new Date();
  const off = d.getTimezoneOffset();
  return new Date(d.getTime() - off * 60_000).toISOString().slice(0, 10);
}

export function monthStartISO(): string {
  return `${todayISO().slice(0, 8)}01`;
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("");
}

/** Decimal-safe arithmetic for live previews (4 decimal places, like the backend). */
export function dec(v: string | number | null | undefined): bigint {
  const s = String(v ?? "0").trim() || "0";
  const neg = s.startsWith("-");
  const [i = "0", f = ""] = s.replace("-", "").split(".");
  const val = BigInt(i || "0") * 10000n + BigInt((f + "0000").slice(0, 4));
  return neg ? -val : val;
}
export function decStr(v: bigint, places = 2): string {
  const neg = v < 0n;
  const a = neg ? -v : v;
  const scaled = places === 2 ? (a + 50n) / 100n : a;
  const div = places === 2 ? 100n : 10000n;
  const s = `${scaled / div}.${(scaled % div).toString().padStart(places, "0")}`;
  return neg ? `-${s}` : s;
}
