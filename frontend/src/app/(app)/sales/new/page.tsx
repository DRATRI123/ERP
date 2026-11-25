"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { PageHeader } from "@/components/data/page-header";
import { DocumentEditor } from "@/features/billing/document-editor";

function Inner() {
  const sp = useSearchParams();
  const draft = sp.get("draft") ?? undefined;
  const party = sp.get("party");
  const preset = { docType: sp.get("type") ?? undefined, party: party ? { id: party, name: sp.get("party_name") ?? "" } : undefined };
  return (
    <>
      <PageHeader title={draft ? "Edit draft" : "New sale"} description="Scan or search items, choose the customer, take payment — done." />
      <DocumentEditor side="sales" draftId={draft} preset={preset} key={draft ?? `new-${preset.docType ?? ""}-${party ?? ""}`} />
    </>
  );
}

export default function NewSalePage() {
  return <Suspense><Inner /></Suspense>;
}
