"use client";

import { DownloadIcon, FileJson2Icon } from "lucide-react";
import { useState } from "react";

import { MoneyTable, type MCol } from "@/components/data/money-table";
import { PageHeader } from "@/components/data/page-header";
import { EwayPanel } from "@/features/gst/eway-panel";
import { ErrorState, TableSkeleton } from "@/components/data/states";
import { PeriodPicker, PRESETS } from "@/components/pickers/period";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBizQuery } from "@/hooks/use-business";
import { downloadCsv } from "@/lib/csv";
import { titleCase } from "@/lib/format";

type Rows = Record<string, unknown>[];
const TAX: MCol[] = [{ key: "taxable_value", header: "Taxable", money: true }, { key: "igst", header: "IGST", money: true }, { key: "cgst", header: "CGST", money: true }, { key: "sgst", header: "SGST", money: true }, { key: "cess", header: "Cess", money: true }];

function downloadJson(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const a = document.createElement("a"); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function Section({ title, rows, cols }: { title: string; rows?: Rows; cols: MCol[] }) {
  return <Card className="overflow-hidden"><CardHeader className="flex-row items-center justify-between pb-3"><CardTitle>{title}</CardTitle><Badge variant="secondary">{rows?.length ?? 0}</Badge></CardHeader><MoneyTable rows={rows ?? []} cols={cols} /></Card>;
}

export default function GstPage() {
  const [period, setPeriod] = useState(PRESETS.last_month!());
  const q = { date_from: period.from, date_to: period.to };
  const g1 = useBizQuery<Record<string, unknown>>(["gstr1"], "/gst/gstr1", q);
  const g3 = useBizQuery<Record<string, Record<string, Record<string, string>>>>(["gstr3b"], "/gst/gstr3b", q);
  const hsn = useBizQuery<{ b2b: Rows; b2c: Rows }>(["hsn"], "/gst/hsn-summary", q);
  const sales = useBizQuery<{ rows: Rows }>(["sales-register"], "/gst/sales-register", q);
  const purchases = useBizQuery<{ rows: Rows }>(["purchase-register"], "/gst/purchase-register", q);
  const regCols: MCol[] = [{ key: "number", header: "Number" }, { key: "doc_date", header: "Date" }, { key: "party", header: "Party" }, { key: "party_gstin", header: "GSTIN" }, ...TAX, { key: "total", header: "Total", money: true }];
  const csv = (name: string, rows: Rows, cols: MCol[]) => downloadCsv(name, cols.map((c) => c.header), rows.map((r) => cols.map((c) => r[c.key] as string)));

  return (
    <>
      <PageHeader title="GST & tax" description="Returns prepared from your finalized documents — review here, then file on the GST portal."
        actions={<PeriodPicker value={period} onChange={setPeriod} />} />
      <Tabs defaultValue="gstr1">
        <TabsList><TabsTrigger value="gstr1">GSTR-1</TabsTrigger><TabsTrigger value="gstr3b">GSTR-3B</TabsTrigger>
          <TabsTrigger value="hsn">HSN summary</TabsTrigger><TabsTrigger value="sales">Sales register</TabsTrigger><TabsTrigger value="purchases">Purchase register</TabsTrigger><TabsTrigger value="eway">E-way bills</TabsTrigger></TabsList>
        <TabsContent value="gstr1" className="space-y-4">
          {g1.isLoading ? <Card><TableSkeleton /></Card> : g1.error ? <Card><ErrorState error={g1.error} /></Card> : g1.data && <>
            <div className="flex items-center justify-between"><Badge variant="warning">{String(g1.data.status)}</Badge>
              <Button variant="outline" onClick={() => downloadJson(`GSTR1_${period.from}_${period.to}.json`, g1.data)}><FileJson2Icon /> Download JSON</Button></div>
            <Section title="B2B — registered buyers" rows={g1.data.b2b as Rows} cols={[{ key: "ctin", header: "Buyer GSTIN" }, { key: "number", header: "Invoice" }, { key: "date", header: "Date" }, { key: "place_of_supply", header: "POS" }, ...TAX]} />
            <Section title="B2C large (inter-state > ₹1 lakh)" rows={g1.data.b2cl as Rows} cols={[{ key: "number", header: "Invoice" }, { key: "place_of_supply", header: "POS" }, ...TAX]} />
            <Section title="B2C small" rows={g1.data.b2cs as Rows} cols={[{ key: "place_of_supply", header: "POS" }, { key: "rate", header: "Rate", render: (r) => `${Number(r.rate)}%` }, { key: "supply_type", header: "Type" }, ...TAX]} />
            <Section title="Credit / debit notes (registered)" rows={g1.data.cdnr as Rows} cols={[{ key: "ctin", header: "GSTIN" }, { key: "number", header: "Note" }, ...TAX]} />
            <Section title="Exports" rows={g1.data.exp as Rows} cols={[{ key: "number", header: "Invoice" }, ...TAX]} />
          </>}
        </TabsContent>
        <TabsContent value="gstr3b">
          {g3.isLoading ? <Card><TableSkeleton /></Card> : g3.error ? <Card><ErrorState error={g3.error} /></Card> : g3.data && (
            <div className="space-y-4">
              <div className="flex justify-end"><Button variant="outline" onClick={() => downloadJson(`GSTR3B_${period.from}_${period.to}.json`, g3.data)}><FileJson2Icon /> Download JSON</Button></div>
              {(["3.1", "4"] as const).map((sec) => g3.data![sec] && (
                <Card key={sec} className="overflow-hidden"><CardHeader className="pb-3"><CardTitle>{sec === "3.1" ? "3.1 Outward supplies and inward supplies on reverse charge" : "4 Eligible ITC"}</CardTitle></CardHeader>
                  <MoneyTable total={false} rows={Object.entries(g3.data![sec]!).map(([k, v]) => ({ item: titleCase(k.replace(/^[a-z0-9]+_/, "")), ...v }))}
                    cols={[{ key: "item", header: "Nature of supply" }, ...(sec === "3.1" ? [{ key: "taxable_value", header: "Taxable", money: true }] : []), ...TAX.slice(1)]} /></Card>
              ))}
            </div>
          )}
        </TabsContent>
        <TabsContent value="hsn" className="space-y-4">
          {hsn.data && (["b2b", "b2c"] as const).map((k) => (
            <Section key={k} title={k === "b2b" ? "HSN — B2B" : "HSN — B2C"} rows={hsn.data![k]} cols={[{ key: "hsn_sac", header: "HSN" }, { key: "description", header: "Description" }, { key: "uqc", header: "UQC" },
              { key: "quantity", header: "Qty", align: "right" }, { key: "rate", header: "Rate", render: (r) => `${Number(r.rate)}%` }, ...TAX, { key: "total", header: "Total", money: true }]} />
          ))}
        </TabsContent>
        {([["sales", sales], ["purchases", purchases]] as const).map(([k, reg]) => (
          <TabsContent key={k} value={k}>
            <Card className="overflow-hidden">
              <CardHeader className="flex-row items-center justify-between pb-3"><CardTitle>{k === "sales" ? "Sales register" : "Purchase register"}</CardTitle>
                <Button variant="outline" size="sm" disabled={!reg.data?.rows.length} onClick={() => csv(`${k}-register.csv`, reg.data!.rows, regCols)}><DownloadIcon /> CSV</Button></CardHeader>
              {reg.isLoading ? <TableSkeleton /> : reg.error ? <ErrorState error={reg.error} /> : <MoneyTable rows={reg.data?.rows ?? []} cols={regCols} />}
            </Card>
          </TabsContent>
        ))}
        <TabsContent value="eway"><EwayPanel /></TabsContent>
      </Tabs>
    </>
  );
}
