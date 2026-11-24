"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { PageHeader } from "@/components/data/page-header";
import { DocumentEditor } from "@/features/billing/document-editor";

function Inner() {
  const draft = useSearchParams().get("draft") ?? undefined;
  return (
    <>
      <PageHeader title={draft ? "Edit draft" : "New purchase"} description="Record a supplier's bill; stock and books update when you finalize." />
      <DocumentEditor side="purchase" draftId={draft} key={draft ?? "new"} />
    </>
  );
}

export default function NewPurchasePage() {
  return <Suspense><Inner /></Suspense>;
}
