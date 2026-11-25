"use client";

import { CheckCircle2Icon, CopyIcon, Loader2Icon, MessageCircleIcon, PlayIcon, PlusIcon, ScaleIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { StaticTable } from "@/components/data/data-table";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { StatCard } from "@/components/data/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect } from "@/components/ui/input";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money, todayISO } from "@/lib/format";

interface Account { id: string; code: string; name: string; type: string; subtype: string; is_active: boolean }

// ---------------------------------------------------------------------------- budgets

export function BudgetsPanel() {
  const { can, isOwner } = useBusiness();
  const accounts = useBizQuery<Account[]>(["accounts"], "/accounting/accounts");
  const grid = useBizQuery<{ financial_year: string; from: string; to: string; months: string[]; budgets: Record<string, Record<string, string>> }>(["budgets"], "/budgets");
  const [month, setMonth] = useState(todayISO().slice(0, 7));
  const start = `${month}-01`;
  const end = new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)), 0).toLocaleDateString("en-CA");
  const rep = useBizQuery<{ rows: { account_id: string; code: string; name: string; type: string; budget: string; actual: string; variance: string; used_percent: number | null; favourable: boolean }[]; totals: Record<string, string> }>(
    ["budget-report", month], "/budgets/report", { date_from: start, date_to: end });
  const [editing, setEditing] = useState<Account | null>(null);
  const pl = (accounts.data ?? []).filter((a) => (a.type === "income" || a.type === "expense") && a.is_active);
  const editable = isOwner || can("accounting.post");
  return (
    <div className="space-y-4">
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b p-3">
          <Input type="month" className="w-44" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month" />
          {editable && <NativeSelect className="w-64" value="" onChange={(e) => setEditing(pl.find((a) => a.id === e.target.value) ?? null)} aria-label="Set budget">
            <option value="">+ Set budget for an account…</option>{pl.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</NativeSelect>}
        </div>
        {rep.error ? <ErrorState error={rep.error} /> : !rep.data ? <TableSkeleton /> : !rep.data.rows.length ? (
          <EmptyState title="No budgets yet" description="Set a monthly budget for your main expenses (rent, salaries, marketing) and income; this compares it with the books every month." />
        ) : (
          <StaticTable rows={rep.data.rows} rowKey={(r) => r.account_id} columns={[
            { key: "a", header: "Account", mobile: "title", cell: (r) => <span className="font-medium">{r.code} {r.name}<span className="ml-2 text-xs font-normal text-muted-foreground">{r.type}</span></span> },
            { key: "b", header: "Budget", align: "right", cell: (r) => <span className="num">{money(r.budget)}</span> },
            { key: "c", header: "Actual", align: "right", cell: (r) => <span className="num">{money(r.actual)}</span> },
            { key: "bar", header: "", mobile: "hide", cell: (r) => r.used_percent !== null && <div className="h-2 w-28 overflow-hidden rounded-full bg-muted"><div className={`h-full ${r.favourable ? "bg-primary" : "bg-destructive"}`} style={{ width: `${Math.min(100, r.used_percent)}%` }} /></div> },
            { key: "v", header: "Difference", align: "right", mobile: "aside", cell: (r) => <span className={`num font-medium ${r.favourable ? "text-primary" : "text-destructive"}`}>{Number(r.variance) > 0 ? "+" : ""}{money(r.variance)}{r.used_percent !== null && <span className="block text-xs font-normal">{r.used_percent}%</span>}</span> },
            ...(editable ? [{ key: "e", header: "", mobile: "row" as const, cell: (r: { account_id: string }) => <Button size="sm" variant="ghost" onClick={() => setEditing(pl.find((a) => a.id === r.account_id) ?? null)}>Edit</Button> }] : []),
          ]} />)}
      </Card>
      {editing && grid.data && <BudgetDialog account={editing} months={grid.data.months} fyStart={grid.data.from} current={grid.data.budgets[editing.id] ?? {}} onClose={() => setEditing(null)} />}
    </div>
  );
}

function BudgetDialog({ account, months, fyStart, current, onClose }: { account: Account; months: string[]; fyStart: string; current: Record<string, string>; onClose: () => void }) {
  const [vals, setVals] = useState(months.map((m) => (current[m] ? String(Number(current[m])) : "")));
  const save = useBizMutation<Record<string, unknown>>("PUT", "/budgets", { invalidate: [["budgets"], ["budget-report"]], success: "Budget saved", onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>Budget — {account.code} {account.name}</DialogTitle><DialogDescription>Monthly amounts for the financial year. Type the first and press “Same every month”.</DialogDescription></DialogHeader>
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 lg:grid-cols-6">
          {months.map((m, i) => (
            <Field key={m} label={new Date(m).toLocaleDateString("en-IN", { month: "short", year: "2-digit" })} htmlFor={`bg-${i}`}>
              <Input id={`bg-${i}`} inputMode="decimal" className="num" value={vals[i]} onChange={(e) => setVals((v) => v.map((x, j) => (j === i ? e.target.value.replace(/[^\d.]/g, "") : x)))} /></Field>))}
        </div>
        <DialogFooter>
          <Button variant="outline" className="mr-auto" onClick={() => setVals((v) => v.map(() => v[0] ?? ""))}>Same every month</Button>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={save.isPending} onClick={() => save.mutate({ account_id: account.id, fy_start: fyStart, months: vals.map((v) => v || "0") })}>Save budget</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------- cash closing

const NOTES = ["500", "200", "100", "50", "20", "10", "5", "2", "1"];

export function CashClosingPanel() {
  const { can, isOwner } = useBusiness();
  const [day, setDay] = useState(todayISO());
  const pos = useBizQuery<{ account: string; opening: string; received: string; paid: string; expected: string; closed: { counted: string; difference: string; denominations: Record<string, string | number>; notes: string | null } | null }>(["cash-position", day], "/cash-closing/position", { on: day });
  const [count, setCount] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState("");
  const close = useBizMutation<Record<string, unknown>>("POST", "/cash-closing", { invalidate: [["cash-position"], ["accounts"]], success: "Cash closed for the day", onSuccess: () => setCount({}) });
  const counted = NOTES.reduce((s, n) => s + Number(n) * Number(count[n] || 0), 0) + Number(count.coins || 0);
  const p = pos.data;
  const diff = p ? counted - Number(p.expected) : 0;
  return (
    <div className="space-y-4">
      <Input type="date" className="w-44" value={day} max={todayISO()} onChange={(e) => setDay(e.target.value)} aria-label="Day" />
      {!p ? <TableSkeleton rows={3} /> : <>
        <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
          <StatCard label="Opening cash" icon={ScaleIcon} value={money(p.opening)} />
          <StatCard label="Received today" icon={ScaleIcon} tone="green" value={money(p.received)} />
          <StatCard label="Paid out today" icon={ScaleIcon} tone="amber" value={money(p.paid)} />
          <StatCard label="Should be in the drawer" icon={ScaleIcon} tone="blue" value={money(p.expected)} />
        </div>
        {p.closed ? (
          <Card><CardContent className="flex flex-wrap items-center gap-3 text-sm"><CheckCircle2Icon className="size-5 text-primary" />
            Closed: counted <b className="num">{money(p.closed.counted)}</b>,
            {Number(p.closed.difference) === 0 ? " exactly right." : <span className={Number(p.closed.difference) < 0 ? "text-destructive" : "text-primary"}> {Number(p.closed.difference) < 0 ? "short" : "excess"} <b className="num">{money(String(Math.abs(Number(p.closed.difference))))}</b> (posted to Cash {Number(p.closed.difference) < 0 ? "Shortage" : "Excess"}).</span>}
            {p.closed.notes && <span className="text-muted-foreground">— {p.closed.notes}</span>}</CardContent></Card>
        ) : (isOwner || can("accounting.post")) && (
          <Card><CardHeader><CardTitle>Count the drawer</CardTitle></CardHeader><CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-3 sm:grid-cols-5 lg:grid-cols-10">
              {NOTES.map((n) => <Field key={n} label={`₹${n} ×`} htmlFor={`cc-${n}`}><Input id={`cc-${n}`} inputMode="numeric" className="num" value={count[n] ?? ""} onChange={(e) => setCount((c) => ({ ...c, [n]: e.target.value.replace(/\D/g, "") }))} /></Field>)}
              <Field label="Coins ₹" htmlFor="cc-coins"><Input id="cc-coins" inputMode="decimal" className="num" value={count.coins ?? ""} onChange={(e) => setCount((c) => ({ ...c, coins: e.target.value.replace(/[^\d.]/g, "") }))} /></Field>
            </div>
            <div className="flex flex-wrap items-center gap-4 text-sm">
              <span>Counted <b className="num text-lg">{money(counted.toFixed(2))}</b></span>
              <span className={diff === 0 ? "text-primary" : diff < 0 ? "text-destructive" : "text-amber-700"}>{diff === 0 ? "Matches the books" : `${diff < 0 ? "Short" : "Excess"} ${money(Math.abs(diff).toFixed(2))}`}</span>
              <Input className="max-w-sm" placeholder="Note (e.g. ₹10 given as change by mistake)" value={notes} onChange={(e) => setNotes(e.target.value)} />
              <Button disabled={close.isPending} onClick={() => close.mutate({ closing_date: day, denominations: { ...Object.fromEntries(NOTES.map((n) => [n, Number(count[n] || 0)])), coins: count.coins || "0" }, notes: notes || null })}>
                {close.isPending && <Loader2Icon className="animate-spin" />} Close the day</Button>
            </div>
          </CardContent></Card>
        )}
      </>}
    </div>
  );
}

// ---------------------------------------------------------------------------- fixed assets

interface Asset { id: string; code: string; name: string; acquired_on: string; cost: string; accumulated_depreciation: string; book_value: string | null; method: string; it_block: string | null; disposed_on: string | null }
interface ItRow { block: string; label: string; rate: string; opening_wdv: string; additions_180_plus: string; additions_under_180: string; sale_proceeds: string; depreciation: string; closing_wdv: string }

export function AssetsPanel() {
  const { can, isOwner } = useBusiness();
  const list = useBizQuery<Asset[]>(["fixed-assets"], "/accounting/fixed-assets");
  const it = useBizQuery<{ financial_year: string; blocks: ItRow[]; total_depreciation: string; blocks_available: { key: string; label: string; rate: string }[]; note: string }>(["it-depreciation"], "/assets/it-depreciation");
  const [adding, setAdding] = useState(false);
  const run = useBizMutation<{ period_end: string }>("POST", "/accounting/fixed-assets/depreciation", { invalidate: [["fixed-assets"], ["accounts"]], success: "Depreciation posted up to that date" });
  const edit = isOwner || can("accounting.post");
  const blocks = Object.fromEntries((it.data?.blocks_available ?? []).map((b) => [b.key, `${b.label} (${Number(b.rate)}%)`]));
  return (
    <div className="space-y-4">
      {edit && <div className="flex flex-wrap gap-2">
        <Button onClick={() => setAdding(true)}><PlusIcon /> Add asset</Button>
        <Button variant="outline" disabled={run.isPending} onClick={() => run.mutate({ period_end: new Date(new Date().getFullYear(), new Date().getMonth(), 0).toLocaleDateString("en-CA") })}><PlayIcon /> Post depreciation to last month-end</Button>
      </div>}
      <Card className="overflow-hidden"><CardHeader><CardTitle>Asset register (books)</CardTitle></CardHeader>
        {!list.data ? <TableSkeleton /> : !list.data.length ? <EmptyState title="No fixed assets" description="Computers, furniture, machines, vehicles: add them to depreciate them in the books and in the tax schedule." /> : (
          <StaticTable rows={list.data} rowKey={(a) => a.id} columns={[
            { key: "n", header: "Asset", mobile: "title", cell: (a) => <div><p className="font-medium">{a.name}</p><p className="text-xs text-muted-foreground">{a.code} · {date(a.acquired_on)}{a.it_block ? ` · IT: ${blocks[a.it_block] ?? a.it_block}` : ""}</p></div> },
            { key: "c", header: "Cost", align: "right", cell: (a) => <span className="num">{money(a.cost)}</span> },
            { key: "d", header: "Depreciated", align: "right", cell: (a) => <span className="num">{money(a.accumulated_depreciation)}</span> },
            { key: "b", header: "Book value", align: "right", mobile: "aside", cell: (a) => a.disposed_on ? <Badge variant="secondary">Sold {date(a.disposed_on)}</Badge> : <span className="num font-medium">{money(a.book_value ?? String(Number(a.cost) - Number(a.accumulated_depreciation)))}</span> },
          ]} />)}
      </Card>
      <Card className="overflow-hidden"><CardHeader><CardTitle>Income-tax depreciation by block — FY {it.data?.financial_year}</CardTitle></CardHeader>
        {!it.data ? <TableSkeleton /> : !it.data.blocks.length ? <EmptyState title="No assets assigned to a tax block" description="Choose the Income-tax block when adding an asset (computer 40%, plant 15%, furniture 10%…)." /> : <>
          <StaticTable rows={it.data.blocks} rowKey={(b) => b.block} columns={[
            { key: "l", header: "Block", mobile: "title", cell: (b) => <span className="font-medium">{b.label} <span className="text-xs text-muted-foreground">{Number(b.rate)}%</span></span> },
            { key: "o", header: "Opening WDV", align: "right", cell: (b) => <span className="num">{money(b.opening_wdv)}</span> },
            { key: "a", header: "Added ≥180 / <180 days", align: "right", cell: (b) => <span className="num">{money(b.additions_180_plus)} / {money(b.additions_under_180)}</span> },
            { key: "s", header: "Sold", align: "right", cell: (b) => <span className="num">{money(b.sale_proceeds)}</span> },
            { key: "d", header: "Depreciation", align: "right", mobile: "aside", cell: (b) => <span className="num font-medium">{money(b.depreciation)}</span> },
            { key: "c", header: "Closing WDV", align: "right", cell: (b) => <span className="num">{money(b.closing_wdv)}</span> },
          ]} />
          <CardContent className="border-t text-sm">Total <b className="num">{money(it.data.total_depreciation)}</b>. <span className="text-xs text-muted-foreground">{it.data.note}</span></CardContent>
        </>}
      </Card>
      {adding && <AssetDialog blocks={it.data?.blocks_available ?? []} onClose={() => setAdding(false)} />}
    </div>
  );
}

function AssetDialog({ blocks, onClose }: { blocks: { key: string; label: string; rate: string }[]; onClose: () => void }) {
  const accounts = useBizQuery<Account[]>(["accounts"], "/accounting/accounts");
  const fixed = (accounts.data ?? []).filter((a) => a.subtype === "fixed_asset");
  const money_ = (accounts.data ?? []).filter((a) => ["bank", "cash", "payable", "loan", "capital"].includes(a.subtype));
  const [f, setF] = useState({ code: "", name: "", asset_account_id: "", acquired_on: todayISO(), cost: "", method: "written_down", annual_rate: "", useful_life_months: "", it_block: "", from: "" });
  const save = useBizMutation<Record<string, unknown>>("POST", "/accounting/fixed-assets", { invalidate: [["fixed-assets"], ["it-depreciation"], ["accounts"]], success: "Asset added", onSuccess: onClose });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF((x) => ({ ...x, [k]: e.target.value }));
  const acc = f.asset_account_id || fixed[0]?.id || "";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>Add fixed asset</DialogTitle></DialogHeader>
        <div className="grid gap-4 sm:grid-cols-4">
          <Field label="Code" htmlFor="as-code"><Input id="as-code" value={f.code} onChange={set("code")} placeholder="LAP-01" /></Field>
          <Field label="Name" htmlFor="as-name" className="sm:col-span-3"><Input id="as-name" value={f.name} onChange={set("name")} placeholder="Billing laptop" /></Field>
          <Field label="Asset account" htmlFor="as-acc" className="sm:col-span-2"><NativeSelect id="as-acc" value={acc} onChange={set("asset_account_id")}>{fixed.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</NativeSelect></Field>
          <Field label="Bought on" htmlFor="as-on"><Input id="as-on" type="date" value={f.acquired_on} onChange={set("acquired_on")} /></Field>
          <Field label="Cost (₹)" htmlFor="as-cost"><Input id="as-cost" inputMode="decimal" className="num" value={f.cost} onChange={set("cost")} /></Field>
          <Field label="Book method" htmlFor="as-m"><NativeSelect id="as-m" value={f.method} onChange={set("method")}><option value="written_down">Written-down value</option><option value="straight_line">Straight line</option></NativeSelect></Field>
          {f.method === "written_down" ? <Field label="Rate % per year" htmlFor="as-r"><Input id="as-r" inputMode="decimal" value={f.annual_rate} onChange={set("annual_rate")} placeholder="e.g. 40" /></Field>
            : <Field label="Life (months)" htmlFor="as-l"><Input id="as-l" inputMode="numeric" value={f.useful_life_months} onChange={set("useful_life_months")} placeholder="36" /></Field>}
          <Field label="Income-tax block" htmlFor="as-it" className="sm:col-span-2"><NativeSelect id="as-it" value={f.it_block} onChange={set("it_block")}><option value="">— Not in the tax schedule —</option>
            {blocks.map((b) => <option key={b.key} value={b.key}>{b.label} ({Number(b.rate)}%)</option>)}</NativeSelect></Field>
          <Field label="Paid from (posts the purchase)" htmlFor="as-from" className="sm:col-span-2"><NativeSelect id="as-from" value={f.from} onChange={set("from")}><option value="">Already in the books</option>
            {money_.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</NativeSelect></Field>
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!f.code || !f.name || !(Number(f.cost) > 0) || !acc || save.isPending} onClick={() => save.mutate({
            code: f.code, name: f.name, asset_account_id: acc, acquired_on: f.acquired_on, cost: f.cost, method: f.method,
            ...(f.method === "written_down" ? { annual_rate: f.annual_rate || "0" } : { useful_life_months: Number(f.useful_life_months || 0) }),
            it_block: f.it_block || null, capitalise_from_account_id: f.from || null })}>Add asset</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------- balance confirmation (party page)

interface Confirmation { id: string; as_of: string; balance: string; token: string; status: "sent" | "confirmed" | "disputed"; response_note: string | null; their_balance: string | null; created_at: string }

export function BalanceConfirmationCard({ partyId, phone, name }: { partyId: string; phone: string | null; name: string }) {
  const { can, isOwner } = useBusiness();
  const list = useBizQuery<Confirmation[]>(["confirmations", partyId], "/balance-confirmations", { party_id: partyId }, { enabled: isOwner || can("accounting.view") });
  const send = useBizMutation<{ party_id: string }, Confirmation>("POST", "/balance-confirmations", { invalidate: [["confirmations"]], success: "Confirmation link ready" });
  if (!(isOwner || can("accounting.view"))) return null;
  const link = (c: Confirmation) => `${window.location.origin}/confirm/${c.token}`;
  const share = (c: Confirmation) => {
    const text = `Dear ${name}, please confirm your balance with us as on ${date(c.as_of)}: ${money(String(Math.abs(Number(c.balance))))} ${Number(c.balance) >= 0 ? "due to us" : "due to you"}. Statement and confirm: ${link(c)}`;
    window.open(`https://wa.me/${phone ? `91${phone.replace(/\D/g, "").slice(-10)}` : ""}?text=${encodeURIComponent(text)}`, "_blank", "noopener");
  };
  const STATUS = { sent: ["warning", "Waiting"], confirmed: ["default", "Confirmed"], disputed: ["destructive", "Disputed"] } as const;
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between"><CardTitle>Balance confirmation</CardTitle>
        <Button size="sm" disabled={send.isPending} onClick={() => send.mutate({ party_id: partyId }, { onSuccess: (c) => share(c) })}>{send.isPending ? <Loader2Icon className="animate-spin" /> : <MessageCircleIcon />} Send on WhatsApp</Button></CardHeader>
      <CardContent className="space-y-2 text-sm">
        {!list.data?.length ? <p className="text-muted-foreground">Send the ledger balance for them to confirm with one tap (useful at year end and for audit).</p> : list.data.slice(0, 5).map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-2 border-b pb-2 last:border-0">
            <Badge variant={STATUS[c.status][0]}>{STATUS[c.status][1]}</Badge>
            <span>as on {date(c.as_of)} · <b className="num">{money(c.balance)}</b></span>
            {c.status === "disputed" && <span className="text-destructive">They say {c.their_balance ? money(c.their_balance) : "—"}{c.response_note ? `: ${c.response_note}` : ""}</span>}
            {c.status === "sent" && <Button size="sm" variant="ghost" className="ml-auto" onClick={() => { void navigator.clipboard.writeText(link(c)); toast.success("Link copied"); }}><CopyIcon /> Copy link</Button>}
          </div>))}
      </CardContent>
    </Card>
  );
}
