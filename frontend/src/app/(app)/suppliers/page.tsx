"use client";

import { Suspense } from "react";

import { PartyList } from "@/features/parties/party-list";

export default function Page() {
  return <Suspense><PartyList kind="suppliers" /></Suspense>;
}
