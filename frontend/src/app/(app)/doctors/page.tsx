"use client";

import { PencilIcon, PlusIcon, StethoscopeIcon } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/data/page-header";
import { StaticTable } from "@/components/data/data-table";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/data/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { DoctorDialog, type Doctor } from "@/features/pharmacy/pharmacy";
import { useBizQuery, useBusiness } from "@/hooks/use-business";
import { useDebounced } from "@/hooks/use-debounce";
import { money } from "@/lib/format";

interface DoctorSale { doctor_id: string; bills: number; total: string }

export default function DoctorsPage() {
  const { can, isOwner } = useBusiness();
  const manage = isOwner || can("customers.manage");
  const [q, setQ] = useState("");
  const query = useDebounced(q, 250);
  const list = useBizQuery<Doctor[]>(["doctors"], "/doctors", { q: query || undefined, include_inactive: true });
  const sales = useBizQuery<{ rows: DoctorSale[] }>(["doctor-sales-month"], "/reports/doctor-sales", undefined,
    { enabled: isOwner || can("reports.view") });
  const byDoctor = new Map((sales.data?.rows ?? []).map((r) => [r.doctor_id, r]));
  const [editing, setEditing] = useState<{ doctor?: Doctor; key: string } | null>(null);

  return (
    <>
      <PageHeader title="Doctors" description="Prescribing doctors: printed on bills of Schedule H / H1 / X drugs, kept in the schedule register, and used for doctor-wise sales."
        actions={manage && <Button onClick={() => setEditing({ key: `new-${Date.now()}` })}><PlusIcon /> Add doctor</Button>} />
      <Card className="overflow-hidden">
        <div className="border-b p-3"><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name, clinic, phone or registration no." className="max-w-md" aria-label="Search doctors" /></div>
        {list.error ? <ErrorState error={list.error} retry={() => list.refetch()} /> : list.isLoading ? <TableSkeleton /> : !list.data?.length ? (
          <EmptyState icon={StethoscopeIcon} title={q ? "No doctor matches" : "No doctors yet"}
            description="Add the doctors whose prescriptions you fill. You can also add one from the bill while billing."
            action={manage && !q && <Button onClick={() => setEditing({ key: `new-${Date.now()}` })}><PlusIcon /> Add doctor</Button>} />
        ) : (
          <StaticTable rows={list.data} rowKey={(d) => d.id} columns={[
            { key: "name", header: "Doctor", mobile: "title", cell: (d) => (
              <div><p className="font-medium">{d.name}{!d.is_active && <Badge variant="secondary" className="ml-2">Inactive</Badge>}</p>
                <p className="text-xs text-muted-foreground">{[d.qualification, d.registration_no && `Reg. ${d.registration_no}`].filter(Boolean).join(" · ")}</p></div>) },
            { key: "clinic", header: "Clinic", cell: (d) => d.clinic ?? "—" },
            { key: "phone", header: "Phone", cell: (d) => d.phone ? <a href={`tel:${d.phone}`} className="text-primary hover:underline">{d.phone}</a> : "—" },
            { key: "month", header: "This month", align: "right", mobile: "aside", cell: (d) => {
              const s = byDoctor.get(d.id);
              return s ? <span className="num">{money(s.total)}<span className="block text-xs text-muted-foreground">{s.bills} bill{s.bills === 1 ? "" : "s"}</span></span> : <span className="text-muted-foreground">—</span>;
            } },
            ...(manage ? [{ key: "edit", header: "", mobile: "row" as const, cell: (d: Doctor) => (
              <Button variant="ghost" size="sm" onClick={() => setEditing({ doctor: d, key: d.id + Date.now() })}><PencilIcon /> Edit</Button>) }] : []),
          ]} />
        )}
      </Card>
      {editing && <DoctorDialog key={editing.key} open doctor={editing.doctor} onOpenChange={(v) => !v && setEditing(null)} />}
    </>
  );
}
