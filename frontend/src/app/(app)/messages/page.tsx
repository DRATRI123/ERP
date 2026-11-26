"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { PageHeader } from "@/components/data/page-header";
import { ChatPanel } from "@/features/chat/chat-panel";

function Inner() {
  const sp = useSearchParams();
  const router = useRouter();
  const peer = sp.get("with");
  return (
    <>
      <PageHeader title="Messages" description="Chat with your salespeople and staff. New messages arrive by themselves; turn on alerts to hear them when this tab is in the background." />
      <ChatPanel peer={peer} onPeer={(id) => router.replace(id ? `/messages?with=${id}` : "/messages")} className="h-[calc(100dvh-13rem)] min-h-[28rem]" />
    </>
  );
}

export default function MessagesPage() {
  return <Suspense><Inner /></Suspense>;
}
