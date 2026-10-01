"use client";

import { Activity, ClipboardCheck, LayoutGrid, WandSparkles } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Studio", icon: WandSparkles, match: (p: string) => p === "/" || p.startsWith("/runs") },
  { href: "/batch", label: "Batch", icon: LayoutGrid, match: (p: string) => p.startsWith("/batch") },
  { href: "/evals", label: "Evaluator", icon: ClipboardCheck, match: (p: string) => p.startsWith("/evals") },
  { href: "/status", label: "Status", icon: Activity, match: (p: string) => p.startsWith("/status") },
] as const;

export function MainNav({ orientation = "vertical" }: { orientation?: "vertical" | "horizontal" }) {
  const pathname = usePathname() ?? "/";
  return (
    <nav
      aria-label="Main"
      className={cn("flex gap-1", orientation === "vertical" ? "flex-col" : "flex-row overflow-x-auto")}
    >
      {NAV.map(({ href, label, icon: Icon, match }) => {
        const active = match(pathname);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring",
              active
                ? "bg-primary/10 font-medium text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {/* On phones the horizontal bar drops the icons so all four fit at 375 px. */}
            <Icon className={cn("size-4", orientation === "horizontal" && "hidden sm:block")} aria-hidden />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
