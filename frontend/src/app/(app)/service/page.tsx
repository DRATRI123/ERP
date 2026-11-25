"use client";

import { CalendarClockIcon, Loader2Icon, PlusIcon, RefreshCwIcon, SearchIcon, ShieldCheckIcon, WrenchIcon } from "lucide-react";
import { useState } from "react";

import { StaticTable } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { StatCard } from "@/components/data/stat-card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { PartyPicker } from "@/components/pickers/party-picker";
import { ProductSearch } from "@/components/pickers/product-search";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field } from "@/components/ui/form-field";
import { Input, NativeSelect, Textarea } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizMutation, useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money, todayISO } from "@/lib/format";
import type { Party, Product } from "@/lib/types";

interface Contract {
  id: string; kind: "warranty" | "amc"; number: string; party_name: string | null; customer_phone: string | null; description: string;
  serial_no: string | null; start_date: string; end_date: string; visits_included: number; amount: string;
  state: "active" | "expired" | "upcoming" | "cancelled"; days_left: number;
}
interface Ticket {
  id: string; number: string; opened_on: string; party_name: string | null; customer_phone: string | null; description: string | null;
  serial_no: string | null; complaint: string; covered: boolean; status: "open" | "in_progress" | "waiting_parts" | "resolved" | "closed";
  assigned_to: string | null; resolution: string | null; contract: { number: string; kind: string; end_date: string } | null; age_days: number;
}
const T_STATUS: Record<Ticket["status"], [string, "warning" | "secondary" | "default" | "destructive"]> = {
  open: ["Open", "warning"], in_progress: ["Being fixed", "secondary"], waiting_parts: ["Waiting for parts", "destructive"], resolved: ["Resolved", "default"], closed: ["Closed", "secondary"],
};
const C_STATE: Record<Contract["state"], [string, "default" | "secondary" | "destructive" | "warning"]> = {
  active: ["Running", "default"], expired: ["Expired", "destructive"], upcoming: ["Starts later", "secondary"], cancelled: ["Cancelled", "secondary"],
};

export default function ServicePage() {
  const { can, isOwner } = useBusiness();
  const edit = isOwner || can("invoices.create");
  const [tab, setTab] = useState("calls");
  const [q, setQ] = useState("");
  const calls = useBizQuery<Ticket[]>(["service", "tickets", q], "/service/tickets", { status: "open", q: q || undefined }, { enabled: tab === "calls" });
  const done = useBizQuery<Ticket[]>(["service", "tickets", "done", q], "/service/tickets", { status: "resolved", q: q || undefined }, { enabled: tab === "done" });
  const contracts = useBizQuery<Contract[]>(["service", "contracts", q], "/service/contracts", { q: q || undefined }, { enabled: tab === "contracts" });
  const renewals = useBizQuery<Contract[]>(["service", "contracts", "renewals"], "/service/contracts", { ending_within: 30, kind: "amc" });
  const openCount = useBizQuery<Ticket[]>(["service", "tickets", ""], "/service/tickets", { status: "open" });
  const [newCall, setNewCall] = useState<Partial<Contract> | null>(null);
  const [newContract, setNewContract] = useState(false);
  const [editing, setEditing] = useState<Ticket | null>(null);
  const [renewing, setRenewing] = useState<Contract | null>(null);
  const oldest = (openCount.data ?? []).reduce((a, t) => Math.max(a, t.age_days), 0);

  const ticketCols = [
    { key: "n", header: "Call", mobile: "title" as const, cell: (t: Ticket) => <div><p className="font-medium">{t.party_name ?? "—"}<span className="text-muted-foreground"> · {t.description ?? "item"}</span></p>
      <p className="text-xs text-muted-foreground">{t.number} · {date(t.opened_on)}{t.serial_no ? ` · S/N ${t.serial_no}` : ""} — {t.complaint}</p></div> },
    { key: "c", header: "Cover", cell: (t: Ticket) => t.covered ? <Badge>{t.contract?.kind === "amc" ? "AMC" : "Warranty"}</Badge> : <Badge variant="secondary">Chargeable</Badge> },
    { key: "s", header: "Status", mobile: "aside" as const, cell: (t: Ticket) => <Badge variant={T_STATUS[t.status][1]}>{T_STATUS[t.status][0]}</Badge> },
    { key: "a", header: "Age", align: "right" as const, cell: (t: Ticket) => <span className={t.age_days > 3 && t.status !== "resolved" && t.status !== "closed" ? "font-medium text-destructive" : ""}>{t.age_days} d</span> },
    { key: "w", header: "Engineer", cell: (t: Ticket) => t.assigned_to ?? "—" },
    ...(edit ? [{ key: "x", header: "", mobile: "row" as const, cell: (t: Ticket) => <div className="flex justify-end"><Button size="sm" variant="outline" onClick={() => setEditing(t)}>Update</Button></div> }] : []),
  ];
  const contractCols = [
    { key: "n", header: "Contract", mobile: "title" as const, cell: (c: Contract) => <div><p className="font-medium">{c.party_name ?? "—"}<span className="text-muted-foreground"> · {c.description}</span></p>
      <p className="text-xs text-muted-foreground">{c.number} · {c.kind === "amc" ? "AMC" : "Warranty"}{c.serial_no ? ` · S/N ${c.serial_no}` : ""}</p></div> },
    { key: "d", header: "Period", cell: (c: Contract) => <span className="text-sm">{date(c.start_date)} – {date(c.end_date)}</span> },
    { key: "s", header: "Status", mobile: "aside" as const, cell: (c: Contract) => <div className="text-right sm:text-left"><Badge variant={C_STATE[c.state][1]}>{C_STATE[c.state][0]}</Badge>
      {c.state === "active" && c.days_left <= 30 && <p className="text-xs text-amber-700">{c.days_left} days left</p>}</div> },
    { key: "a", header: "Amount", align: "right" as const, cell: (c: Contract) => <span className="num">{Number(c.amount) ? money(c.amount) : "—"}</span> },
    ...(edit ? [{ key: "x", header: "", mobile: "row" as const, cell: (c: Contract) => <div className="flex justify-end gap-1">
      {c.state !== "cancelled" && <Button size="sm" variant="outline" onClick={() => setNewCall(c)}><WrenchIcon /> Service call</Button>}
      {c.kind === "amc" && c.state !== "cancelled" && c.days_left <= 45 && <Button size="sm" variant="outline" onClick={() => setRenewing(c)}><RefreshCwIcon /> Renew</Button>}</div> }] : []),
  ];
  const current = tab === "calls" ? calls : tab === "done" ? done : tab === "renewals" ? renewals : contracts;
  return (
    <>
      <PageHeader title="Service & AMC" description="Warranties registered when you sell, annual maintenance contracts, and service calls — with who is covered and what is pending."
        actions={edit && <><Button variant="outline" onClick={() => setNewContract(true)}><ShieldCheckIcon /> New AMC</Button><Button onClick={() => setNewCall({})}><PlusIcon /> Service call</Button></>} />
      <div className="mb-4 grid grid-cols-2 gap-3 sm:mb-6 sm:gap-4 lg:grid-cols-3">
        <StatCard label="Open service calls" icon={WrenchIcon} tone={openCount.data?.length ? "amber" : "green"} loading={openCount.isLoading} value={String(openCount.data?.length ?? "—")} sub={oldest ? `Oldest ${oldest} days` : undefined} />
        <StatCard label="AMC renewals due (30 days)" icon={CalendarClockIcon} loading={renewals.isLoading} value={String(renewals.data?.length ?? "—")}
          sub={renewals.data?.length ? money(renewals.data.reduce((a, c) => a + Number(c.amount), 0).toFixed(2)) : undefined} />
      </div>
      <Card className="overflow-hidden">
        <div className="flex flex-col gap-2 border-b p-3 sm:flex-row sm:items-center sm:justify-between">
          <Tabs value={tab} onValueChange={setTab}><TabsList className="max-w-full overflow-x-auto">
            <TabsTrigger value="calls">Open calls</TabsTrigger><TabsTrigger value="done">Resolved</TabsTrigger>
            <TabsTrigger value="contracts">Warranty & AMC</TabsTrigger><TabsTrigger value="renewals">Renewals due</TabsTrigger></TabsList></Tabs>
          <div className="relative sm:w-64"><SearchIcon className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
            <Input className="pl-8" placeholder="Serial no., phone or name" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        </div>
        {current.error ? <ErrorState error={current.error} retry={() => current.refetch()} /> : current.isLoading ? <TableSkeleton /> : !current.data?.length ? (
          <EmptyState icon={tab === "calls" || tab === "done" ? WrenchIcon : ShieldCheckIcon} title="Nothing here"
            description={tab === "contracts" ? "Set warranty months on a product and every sale registers its warranty here. Add AMCs with New AMC." : "Service calls you log appear here until they are resolved."} />
        ) : tab === "calls" || tab === "done"
          ? <StaticTable rows={current.data as Ticket[]} rowKey={(t) => t.id} columns={ticketCols} />
          : <StaticTable rows={current.data as Contract[]} rowKey={(c) => c.id} columns={contractCols} />}
      </Card>
      {newCall && <NewCall contract={newCall.id ? (newCall as Contract) : null} onClose={() => setNewCall(null)} />}
      {newContract && <NewContract onClose={() => setNewContract(false)} />}
      {editing && <UpdateCall ticket={editing} onClose={() => setEditing(null)} />}
      {renewing && <Renew contract={renewing} onClose={() => setRenewing(null)} />}
    </>
  );
}

function NewCall({ contract, onClose }: { contract: Contract | null; onClose: () => void }) {
  const [party, setParty] = useState<Party | null>(null);
  const [product, setProduct] = useState<Product | null>(null);
  const [f, setF] = useState({ customer_name: "", customer_phone: "", serial_no: "", complaint: "" });
  const save = useBizMutation<Record<string, unknown>, Ticket>("POST", "/service/tickets", { invalidate: [["service"]], onSuccess: onClose,
    success: (t) => `${t.number} opened — ${t.covered ? "covered" : "chargeable"}` });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setF((x) => ({ ...x, [k]: e.target.value }));
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>New service call</DialogTitle>
          <DialogDescription>{contract ? `${contract.number} · ${contract.description} — ${contract.state === "active" ? "covered" : "not covered now"}` : "Enter the serial number to find the warranty or AMC automatically."}</DialogDescription></DialogHeader>
        <form className="grid gap-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate({ contract_id: contract?.id ?? null, party_id: party?.id ?? null, product_id: product?.id ?? null,
          customer_name: f.customer_name || null, customer_phone: f.customer_phone || null, serial_no: f.serial_no || null, complaint: f.complaint }); }}>
          {!contract && <>
            <Field label="Customer" className="sm:col-span-2"><PartyPicker kind="customers" value={party} onChange={setParty} placeholder="Choose, or type a walk-in name below" /></Field>
            {!party && <><Field label="Walk-in name" htmlFor="sc-cn"><Input id="sc-cn" value={f.customer_name} onChange={set("customer_name")} /></Field>
              <Field label="Mobile" htmlFor="sc-cp"><Input id="sc-cp" inputMode="tel" value={f.customer_phone} onChange={set("customer_phone")} /></Field></>}
            <Field label="Item" className="sm:col-span-2">{product ? <p className="text-sm font-medium">{product.name} <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={() => setProduct(null)}>change</Button></p>
              : <ProductSearch onPick={setProduct} placeholder="Which machine / item (optional)" />}</Field>
            <Field label="Serial no." htmlFor="sc-sn"><Input id="sc-sn" value={f.serial_no} onChange={set("serial_no")} /></Field>
          </>}
          <Field label="Complaint" htmlFor="sc-cmp" className="sm:col-span-2"><Textarea id="sc-cmp" rows={3} value={f.complaint} onChange={set("complaint")} placeholder="Not heating; display shows E4" /></Field>
          <DialogFooter className="sm:col-span-2">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={f.complaint.trim().length < 3 || (!contract && !party && !f.customer_name.trim()) || save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />} Open call</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function UpdateCall({ ticket, onClose }: { ticket: Ticket; onClose: () => void }) {
  const [status, setStatus] = useState<Ticket["status"]>(ticket.status);
  const [resolution, setResolution] = useState(ticket.resolution ?? "");
  const save = useBizMutation<Record<string, unknown>>("PATCH", `/service/tickets/${ticket.id}`, { invalidate: [["service"]], success: "Service call updated", onSuccess: onClose });
  const needsNote = (status === "resolved" || status === "closed") && !resolution.trim();
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>{ticket.number} · {ticket.party_name}</DialogTitle><DialogDescription>{ticket.complaint}</DialogDescription></DialogHeader>
        <div className="space-y-4">
          <Field label="Status" htmlFor="uc-st"><NativeSelect id="uc-st" value={status} onChange={(e) => setStatus(e.target.value as Ticket["status"])}>
            {Object.entries(T_STATUS).map(([v, [l]]) => <option key={v} value={v}>{l}</option>)}</NativeSelect></Field>
          <Field label="What was done" htmlFor="uc-res"><Textarea id="uc-res" rows={3} value={resolution} onChange={(e) => setResolution(e.target.value)} placeholder="Replaced heating coil; tested OK" /></Field>
          {!ticket.covered && (status === "resolved" || status === "closed") && <p className="text-xs text-muted-foreground">Not covered: make a sales invoice for the parts and labour.</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={needsNote || save.isPending} onClick={() => save.mutate({ status, resolution: resolution || null })}>{save.isPending && <Loader2Icon className="animate-spin" />} Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function NewContract({ onClose }: { onClose: () => void }) {
  const [party, setParty] = useState<Party | null>(null);
  const [product, setProduct] = useState<Product | null>(null);
  const [f, setF] = useState({ kind: "amc", description: "", serial_no: "", start_date: todayISO(), months: "12", visits_included: "2", amount: "" });
  const save = useBizMutation<Record<string, unknown>>("POST", "/service/contracts", { invalidate: [["service"]], success: "Contract added", onSuccess: onClose });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF((x) => ({ ...x, [k]: e.target.value }));
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent wide>
        <DialogHeader><DialogTitle>New AMC / warranty</DialogTitle><DialogDescription>Bill the AMC amount on a sales invoice as usual; this keeps the cover and renewal date.</DialogDescription></DialogHeader>
        <form className="grid gap-4 sm:grid-cols-3" onSubmit={(e) => { e.preventDefault(); save.mutate({ kind: f.kind, party_id: party?.id, product_id: product?.id ?? null, description: f.description || null,
          serial_no: f.serial_no || null, start_date: f.start_date, months: Number(f.months), visits_included: Number(f.visits_included || 0), amount: f.amount || "0" }); }}>
          <Field label="Type" htmlFor="nc-kind"><NativeSelect id="nc-kind" value={f.kind} onChange={set("kind")}><option value="amc">AMC (paid)</option><option value="warranty">Warranty / extended</option></NativeSelect></Field>
          <Field label="Customer" className="sm:col-span-2"><PartyPicker kind="customers" value={party} onChange={setParty} /></Field>
          <Field label="Item" className="sm:col-span-2">{product ? <p className="text-sm font-medium">{product.name} <Button type="button" variant="link" size="sm" className="h-auto p-0" onClick={() => setProduct(null)}>change</Button></p>
            : <ProductSearch onPick={setProduct} placeholder="Machine / item covered" />}</Field>
          <Field label="Serial no." htmlFor="nc-sn"><Input id="nc-sn" value={f.serial_no} onChange={set("serial_no")} /></Field>
          {!product && <Field label="Description" htmlFor="nc-desc" className="sm:col-span-3"><Input id="nc-desc" value={f.description} onChange={set("description")} placeholder="RO water purifier, 2 units" /></Field>}
          <Field label="From" htmlFor="nc-from"><Input id="nc-from" type="date" value={f.start_date} onChange={set("start_date")} /></Field>
          <Field label="Months" htmlFor="nc-m"><Input id="nc-m" inputMode="numeric" value={f.months} onChange={set("months")} /></Field>
          <Field label="Visits included" htmlFor="nc-v" hint="0 = on call only"><Input id="nc-v" inputMode="numeric" value={f.visits_included} onChange={set("visits_included")} /></Field>
          <Field label="Amount" htmlFor="nc-amt"><Input id="nc-amt" inputMode="decimal" className="num" value={f.amount} onChange={set("amount")} /></Field>
          <DialogFooter className="sm:col-span-3">
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={!party || (!product && !f.description.trim()) || !(Number(f.months) > 0) || save.isPending}>{save.isPending && <Loader2Icon className="animate-spin" />} Add</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Renew({ contract, onClose }: { contract: Contract; onClose: () => void }) {
  const [months, setMonths] = useState("12");
  const [amount, setAmount] = useState(String(Number(contract.amount) || ""));
  const save = useBizMutation<Record<string, unknown>>("POST", `/service/contracts/${contract.id}/renew`, { invalidate: [["service"]], success: "AMC renewed", onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>Renew {contract.number}</DialogTitle><DialogDescription>{contract.party_name} · {contract.description}. The new AMC starts the day after {date(contract.end_date)}.</DialogDescription></DialogHeader>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Months" htmlFor="rn-m"><Input id="rn-m" inputMode="numeric" value={months} onChange={(e) => setMonths(e.target.value.replace(/\D/g, ""))} /></Field>
          <Field label="Amount" htmlFor="rn-a"><Input id="rn-a" inputMode="decimal" className="num" value={amount} onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} /></Field>
        </div>
        <DialogFooter><Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!(Number(months) > 0) || save.isPending} onClick={() => save.mutate({ months: Number(months), amount: amount || "0" })}>{save.isPending && <Loader2Icon className="animate-spin" />} Renew</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
