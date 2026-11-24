"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { PageHeader } from "@/components/data/page-header";
import { DocumentEditor } from "@/features/billing/document-editor";

function Inner() {
  const draft = useSearchParams().get("draft") ?? undefined;
  return (
    <>
      <PageHeader title={draft ? "Edit draft" : "New sale"} description="Scan or search items, choose the customer, take payment — done." />
      <DocumentEditor side="sales" draftId={draft} key={draft ?? "new"} />
    </>
  );
}

export default function NewSalePage() {
  return <Suspense><Inner /></Suspense>;
}
