"use client";

import { BadgePercentIcon, Loader2Icon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field } from "@/components/ui/form-field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useBizMutation, useBizQuery } from "@/hooks/use-business";

/** Settings → Business: the highest discount a biller may give without a manager. */
export function BillingRules({ editable }: { editable: boolean }) {
  const q = useBizQuery<{ settings: Record<string, unknown> }>(["business-profile"], "");
  if (!q.data) return <Skeleton className="mt-4 h-32" />;
  const v = q.data.settings?.discount_limit_percent;
  return <Form key={String(v ?? "")} initial={v === null || v === undefined ? "" : String(v)} editable={editable} />;
}

function Form({ initial, editable }: { initial: string; editable: boolean }) {
  const [limit, setLimit] = useState(initial);
  const save = useBizMutation<Record<string, unknown>>("PATCH", "", { invalidate: [["business-profile"]], success: "Discount limit saved" });
  return (
    <Card className="mt-4">
      <CardHeader><CardTitle className="flex items-center gap-2"><BadgePercentIcon className="size-4" /> Discount limit</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <Field label="Highest line discount without approval (%)" htmlFor="br-limit" hint="Empty = no limit. Scheme discounts don't count. Managers (Approve discounts permission) are not limited." className="sm:max-w-md">
          <Input id="br-limit" inputMode="decimal" className="num" disabled={!editable} value={limit} placeholder="e.g. 10" onChange={(e) => setLimit(e.target.value.replace(/[^\d.]/g, ""))} />
        </Field>
        {editable && <Button disabled={save.isPending || limit === initial || (limit !== "" && !(Number(limit) >= 0 && Number(limit) <= 100))}
          onClick={() => save.mutate({ settings: { discount_limit_percent: limit === "" ? null : limit } })}>{save.isPending && <Loader2Icon className="animate-spin" />} Save</Button>}
      </CardContent>
    </Card>
  );
}
