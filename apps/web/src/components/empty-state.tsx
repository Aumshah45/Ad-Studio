import type { ReactNode } from "react";

import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { cn } from "@/lib/utils";

/** Empty state with what is missing and what to do (docs/ux.md "Copy" rules). */
export function EmptyState({
  title,
  body,
  icon,
  children,
  className,
}: {
  title: string;
  body?: ReactNode;
  icon?: ReactNode;
  /** Actions or a command block under the text. */
  children?: ReactNode;
  className?: string;
}) {
  return (
    <Empty className={cn("border", className)}>
      <EmptyHeader>
        {icon ? <EmptyMedia variant="icon">{icon}</EmptyMedia> : null}
        <EmptyTitle>{title}</EmptyTitle>
        {body ? <EmptyDescription>{body}</EmptyDescription> : null}
      </EmptyHeader>
      {children ? <EmptyContent>{children}</EmptyContent> : null}
    </Empty>
  );
}

/** A copyable shell command in mono. */
export function CommandLine({ command, className }: { command: string; className?: string }) {
  return (
    <code
      className={cn(
        "block w-full overflow-x-auto rounded-md border bg-muted px-3 py-2 text-left font-mono text-xs whitespace-pre text-foreground",
        className,
      )}
    >
      {command}
    </code>
  );
}
