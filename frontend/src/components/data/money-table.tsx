"use client";

import type * as React from "react";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { money } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface MCol { key: string; header: string; money?: boolean; align?: "right"; render?: (row: Record<string, unknown>) => React.ReactNode }

/** Report table: columns flagged ``money`` are formatted and totalled in the footer. */
export function MoneyTable({ rows, cols, total = true, empty = "No rows for this period." }: { rows: Record<string, unknown>[]; cols: MCol[]; total?: boolean; empty?: string }) {
  if (!rows.length) return <p className="px-4 py-8 text-center text-sm text-muted-foreground">{empty}</p>;
  return (
    <Table>
      <TableHeader><TableRow className="hover:bg-transparent">{cols.map((c) => <TableHead key={c.key} className={cn((c.money || c.align) && "text-right")}>{c.header}</TableHead>)}</TableRow></TableHeader>
      <TableBody>
        {rows.map((r, i) => (
          <TableRow key={i}>{cols.map((c) => (
            <TableCell key={c.key} className={cn((c.money || c.align) && "num text-right")}>
              {c.render ? c.render(r) : c.money ? money(r[c.key] as string) : String(r[c.key] ?? "—")}
            </TableCell>))}</TableRow>
        ))}
        {total && cols.some((c) => c.money) && (
          <TableRow className="bg-muted/40 font-semibold hover:bg-muted/40">
            {cols.map((c, i) => <TableCell key={c.key} className={cn(c.money && "num text-right")}>
              {c.money ? money(rows.reduce((s, r) => s + Number(r[c.key] ?? 0), 0)) : i === 0 ? "Total" : ""}</TableCell>)}
          </TableRow>
        )}
      </TableBody>
    </Table>
  );
}
