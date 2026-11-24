"use client";

import { BookPlusIcon, ReceiptIcon } from "lucide-react";
import { useState } from "react";

import { DataTable, StaticTable, type Column } from "@/components/data/data-table";
import { PageHeader } from "@/components/data/page-header";
import { ErrorState, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ExpenseDialog } from "@/features/accounting/expense-dialog";
import { JournalDialog, type Account } from "@/features/accounting/journal-dialog";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { date, money, titleCase } from "@/lib/format";

interface Journal { id: string; number: string; entry_date: string; source_type: string; source_ref: string | null; narration: string; reverses_id: string | null; lines: { account_id: string; debit: string; credit: string }[] }
interface Expense { id: string; number: string; expense_date: string; description: string; total: string; status: string; account_id: string; reference: string | null }
const TYPES = ["asset", "liability", "equity", "income", "expense"];

export default function AccountingPage() {
  const { can, isOwner } = useBusiness();
  const [journal, setJournal] = useState(false);
  const [expense, setExpense] = useState(false);
  const accounts = useBizQuery<Account[]>(["accounts"], "/accounting/accounts", { with_balances: true });
  const byId = new Map((accounts.data ?? []).map((a) => [a.id, a]));
  const post = isOwner || can("accounting.post");
  const jCols: Column<Journal>[] = [
    { key: "n", header: "Number", cell: (j) => <span className="font-medium">{j.number}</span> },
    { key: "d", header: "Date", cell: (j) => date(j.entry_date) },
    { key: "s", header: "Source", cell: (j) => <div className="flex items-center gap-2"><Badge variant={j.source_type === "manual" ? "blue" : "secondary"}>{titleCase(j.source_type)}</Badge>{j.reverses_id && <Badge variant="destructive">Reversal</Badge>}</div> },
    { key: "r", header: "Narration", cell: (j) => <span className="block max-w-md truncate">{j.narration}</span> },
    { key: "a", header: "Amount", align: "right", cell: (j) => money(j.lines.reduce((s, l) => s + Number(l.debit), 0)) },
  ];
  const eCols: Column<Expense>[] = [
    { key: "n", header: "Number", cell: (e) => <span className="font-medium">{e.number}</span> },
    { key: "d", header: "Date", cell: (e) => date(e.expense_date) },
    { key: "h", header: "Head", cell: (e) => byId.get(e.account_id)?.name ?? "—" },
    { key: "x", header: "Description", cell: (e) => <span className="block max-w-sm truncate">{e.description}</span>, hideOnMobile: true },
    { key: "s", header: "Status", cell: (e) => <Badge variant={e.status === "cancelled" ? "destructive" : "default"}>{titleCase(e.status)}</Badge> },
    { key: "t", header: "Total", align: "right", cell: (e) => <span className="font-medium">{money(e.total)}</span> },
  ];
  return (
    <>
      <PageHeader title="Accounting" description="Double-entry books, posted automatically from every invoice, purchase, payment and stock movement."
        actions={<>
          {(isOwner || can("expenses.manage")) && <Button variant="outline" onClick={() => setExpense(true)}><ReceiptIcon /> Record expense</Button>}
          {post && <Button onClick={() => setJournal(true)}><BookPlusIcon /> Manual journal</Button>}
        </>} />
      <Tabs defaultValue="accounts">
        <TabsList><TabsTrigger value="accounts">Chart of accounts</TabsTrigger><TabsTrigger value="journals">Journals</TabsTrigger><TabsTrigger value="expenses">Expenses</TabsTrigger></TabsList>
        <TabsContent value="accounts" className="grid gap-4 lg:grid-cols-2">
          {accounts.isLoading ? <Card><TableSkeleton /></Card> : accounts.error ? <Card><ErrorState error={accounts.error} /></Card> : TYPES.map((t) => {
            const rows = (accounts.data ?? []).filter((a) => a.type === t);
            if (!rows.length) return null;
            const total = rows.reduce((s, a) => s + Number(a.balance ?? 0), 0);
            return (
              <Card key={t} className="overflow-hidden">
                <div className="flex items-center justify-between border-b px-4 py-3"><p className="font-semibold">{titleCase(t)}</p><span className="num text-sm text-muted-foreground">{money(total)}</span></div>
                <StaticTable rows={rows} rowKey={(a) => a.id} columns={[
                  { key: "c", header: "Code", cell: (a) => <span className="font-mono text-xs text-muted-foreground">{a.code}</span> },
                  { key: "n", header: "Account", cell: (a) => <span className={a.is_active ? "" : "text-muted-foreground line-through"}>{a.name}</span> },
                  { key: "b", header: "Balance", align: "right", cell: (a) => money(a.balance ?? "0") },
                ]} />
              </Card>
            );
          })}
        </TabsContent>
        <TabsContent value="journals"><DataTable<Journal> queryKey={["journals"]} path="/accounting/journals" columns={jCols} searchPlaceholder="Journal number or reference…" /></TabsContent>
        <TabsContent value="expenses"><DataTable<Expense> queryKey={["expenses"]} path="/accounting/expenses" columns={eCols} searchParam={null} /></TabsContent>
      </Tabs>
      <JournalDialog open={journal} onOpenChange={setJournal} accounts={accounts.data ?? []} />
      <ExpenseDialog open={expense} onOpenChange={setExpense} accounts={accounts.data ?? []} />
    </>
  );
}
