"use client";

import { Checkbox as CheckboxPrimitive, Switch as SwitchPrimitive, Tooltip as TooltipPrimitive } from "radix-ui";
import { CheckIcon } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

function Switch({ className, ...props }: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      className={cn("peer inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-transparent shadow-xs transition-all outline-none data-[state=checked]:bg-primary data-[state=unchecked]:bg-input disabled:opacity-50", className)}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block size-4 rounded-full bg-white shadow ring-0 transition-transform data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0.5" />
    </SwitchPrimitive.Root>
  );
}

function Checkbox({ className, ...props }: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      className={cn("peer size-4 shrink-0 rounded-[4px] border border-input shadow-xs outline-none data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground", className)}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="flex items-center justify-center"><CheckIcon className="size-3.5" /></CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}

const TooltipProvider = TooltipPrimitive.Provider;
function Tooltip({ content, children, side = "right" }: { content: React.ReactNode; children: React.ReactNode; side?: "top" | "right" | "bottom" | "left" }) {
  return (
    <TooltipPrimitive.Root delayDuration={200}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content side={side} sideOffset={6}
          className="z-50 rounded-md bg-foreground px-2 py-1 text-xs text-background shadow-md data-[state=delayed-open]:animate-in">
          {content}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

export { Checkbox, Switch, Tooltip, TooltipProvider };
