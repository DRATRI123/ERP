"use client";

import { TokenPasswordForm } from "@/components/layout/token-password-form";

export default function AcceptInvitePage() {
  return <TokenPasswordForm endpoint="/auth/accept-invite" title="Join the team"
    description="Choose a password to activate your account." submit="Activate account" />;
}
