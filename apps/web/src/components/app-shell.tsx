import type { ReactNode } from "react";

import { MainNav } from "@/components/main-nav";
import { ModelHealthDot } from "@/components/model-health-dot";
import { Separator } from "@/components/ui/separator";
import { Toaster } from "@/components/ui/sonner";
import { PROJECT_NAME } from "@/lib/project";
import { cn } from "@/lib/utils";

export function AppShell({ children, header }: { children: ReactNode; header?: ReactNode }) {
  return (
    <div className="flex min-h-screen w-full">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:font-medium"
      >
        Skip to content
      </a>
      <aside className="hidden w-56 shrink-0 flex-col border-r bg-muted/30 p-4 md:flex">
        <div className="text-sm font-semibold tracking-tight">{PROJECT_NAME}</div>
        <Separator className="my-4" />
        <MainNav />
        <div className="mt-auto">
          <ModelHealthDot />
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        {/* The sidebar carries the nav from md up, so the bar is only there on phones (or with a header). */}
        <header className={cn("flex h-14 items-center gap-4 border-b px-4 md:px-6", !header && "md:hidden")}>
          <span className="hidden font-semibold whitespace-nowrap sm:inline md:hidden">{PROJECT_NAME}</span>
          <div className="min-w-0 md:hidden">
            <MainNav orientation="horizontal" />
          </div>
          {header}
        </header>
        <main id="main" className="min-w-0 flex-1 p-4 md:p-6">{children}</main>
      </div>
      <Toaster />
    </div>
  );
}
