"use client";

import { ArrowDownIcon, ArrowUpIcon, ChevronLeftIcon, ChevronRightIcon, SearchIcon } from "lucide-react";
import * as React from "react";

import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useBizQuery } from "@/hooks/use-business";
import { useDebounced } from "@/hooks/use-debounce";
import type { Page } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface Column<T> {
  key: string;
  header: React.ReactNode;
  cell: (row: T) => React.ReactNode;
  align?: "left" | "right" | "center";
  sortable?: boolean; // client-side sort within the page
  sortValue?: (row: T) => string | number;
  className?: string;
  hideOnMobile?: boolean;
}

/** Server-paginated list of a business endpoint returning Page<T>, with search and filters. */
export function DataTable<T extends object>({
  queryKey, path, columns, filters, toolbar, searchPlaceholder = "Search…", searchParam = "q",
  onRowClick, empty, pageSize = 25, rowKey,
}: {
  queryKey: string[]; path: string; columns: Column<T>[]; filters?: Record<string, string | string[] | boolean | undefined>;
  toolbar?: React.ReactNode; searchPlaceholder?: string; searchParam?: string | null;
  onRowClick?: (row: T) => void; empty?: React.ReactNode; pageSize?: number; rowKey?: (row: T, i: number) => string;
}) {
  const [search, setSearch] = React.useState("");
  const [page, setPage] = React.useState({ key: "", offset: 0 });
  const [sort, setSort] = React.useState<{ key: string; dir: 1 | -1 } | null>(null);
  const q = useDebounced(search);
  // A new search or filter starts again at the first page.
  const pageKey = `${q}|${JSON.stringify(filters ?? {})}`;
  const offset = page.key === pageKey ? page.offset : 0;
  const setOffset = (o: number) => setPage({ key: pageKey, offset: o });
  const query = { limit: pageSize, offset, ...(searchParam ? { [searchParam]: q || undefined } : {}), ...(filters ?? {}) };
  const { data, isLoading, error, refetch, isFetching } = useBizQuery<Page<T>>(queryKey, path, query, { placeholderData: (p) => p });

  let rows = data?.items ?? [];
  if (sort) {
    const col = columns.find((c) => c.key === sort.key);
    if (col?.sortValue) {
      rows = [...rows].sort((a, b) => {
        const va = col.sortValue!(a), vb = col.sortValue!(b);
        return (va < vb ? -1 : va > vb ? 1 : 0) * sort.dir;
      });
    }
  }
  const total = data?.total ?? 0;

  return (
    <Card className="overflow-hidden">
      {(searchParam || toolbar) && (
        <div className="flex flex-col gap-2 border-b p-3 sm:flex-row sm:items-center">
          {searchParam && (
            <div className="relative w-full sm:max-w-xs">
              <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={searchPlaceholder} className="pl-8" />
            </div>
          )}
          <div className="flex flex-1 flex-wrap items-center gap-2 sm:justify-end">{toolbar}</div>
        </div>
      )}
      {isLoading ? <TableSkeleton cols={Math.min(columns.length, 6)} /> : error ? (
        <ErrorState error={error} retry={() => refetch()} />
      ) : rows.length === 0 ? (
        empty ?? <EmptyState title={q ? "Nothing matches your search" : "Nothing here yet"} />
      ) : (
        <Table className={cn(isFetching && "opacity-70 transition-opacity")}>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              {columns.map((c) => (
                <TableHead key={c.key} className={cn(c.align === "right" && "text-right", c.align === "center" && "text-center",
                  c.hideOnMobile && "hidden md:table-cell", c.className)}>
                  {c.sortable ? (
                    <button type="button" className="inline-flex items-center gap-1 uppercase hover:text-foreground"
                      onClick={() => setSort((s) => (s?.key === c.key ? { key: c.key, dir: s.dir === 1 ? -1 : 1 } : { key: c.key, dir: 1 }))}>
                      {c.header}
                      {sort?.key === c.key && (sort.dir === 1 ? <ArrowUpIcon className="size-3" /> : <ArrowDownIcon className="size-3" />)}
                    </button>
                  ) : c.header}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row, i) => (
              <TableRow key={rowKey ? rowKey(row, i) : ((row as { id?: string }).id ?? i)} onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={cn(onRowClick && "cursor-pointer")}>
                {columns.map((c) => (
                  <TableCell key={c.key} className={cn(c.align === "right" && "text-right num", c.align === "center" && "text-center",
                    c.hideOnMobile && "hidden md:table-cell", c.className)}>
                    {c.cell(row)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {total > pageSize && (
        <div className="flex items-center justify-between border-t px-3 py-2 text-sm text-muted-foreground">
          <span className="num">{offset + 1}–{Math.min(offset + pageSize, total)} of {total}</span>
          <div className="flex gap-1">
            <Button variant="outline" size="icon-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - pageSize))} aria-label="Previous page"><ChevronLeftIcon /></Button>
            <Button variant="outline" size="icon-sm" disabled={offset + pageSize >= total} onClick={() => setOffset(offset + pageSize)} aria-label="Next page"><ChevronRightIcon /></Button>
          </div>
        </div>
      )}
    </Card>
  );
}

/** Simple table for small, already-loaded arrays (reports, settings lists). */
export function StaticTable<T>({ rows, columns, rowKey, footer }: {
  rows: T[]; columns: Column<T>[]; rowKey: (row: T, i: number) => string; footer?: React.ReactNode;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          {columns.map((c) => <TableHead key={c.key} className={cn(c.align === "right" && "text-right", c.hideOnMobile && "hidden md:table-cell")}>{c.header}</TableHead>)}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r, i) => (
          <TableRow key={rowKey(r, i)}>
            {columns.map((c) => <TableCell key={c.key} className={cn(c.align === "right" && "text-right num", c.hideOnMobile && "hidden md:table-cell", c.className)}>{c.cell(r)}</TableCell>)}
          </TableRow>
        ))}
        {footer}
      </TableBody>
    </Table>
  );
}
