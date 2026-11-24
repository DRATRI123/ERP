"use client";

import { TokenPasswordForm } from "@/components/layout/token-password-form";

export default function ResetPasswordPage() {
  return <TokenPasswordForm endpoint="/auth/reset-password" title="Set a new password"
    description="Choose a new password for your account." submit="Save password" />;
}
