import { Badge } from "@/components/ui/badge";
import { titleCase } from "@/lib/format";

export function DocStatusBadge({ status, payment }: { status: string; payment?: string }) {
  if (status === "draft") return <Badge variant="secondary">Draft</Badge>;
  if (status === "cancelled") return <Badge variant="destructive">Cancelled</Badge>;
  if (payment === "paid") return <Badge>Paid</Badge>;
  if (payment === "partial") return <Badge variant="blue">Partly paid</Badge>;
  if (payment === "unpaid") return <Badge variant="warning">Unpaid</Badge>;
  return <Badge variant="outline">{titleCase(status)}</Badge>;
}
