import { Fragment } from "react";

/**
 * Plain text with `backtick` spans shown as inline code, for messages that name a command
 * (report criteria, warnings, API problems). Never parses anything else.
 */
export function CodeText({ children }: { children: string }) {
  const parts = children.split(/`([^`]+)`/);
  if (parts.length === 1) return <>{children}</>;
  return (
    <>
      {parts.map((p, i) =>
        i % 2 === 1 ? (
          <code key={i} className="rounded-sm bg-muted px-1 font-mono text-[0.9em]">
            {p}
          </code>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </>
  );
}
