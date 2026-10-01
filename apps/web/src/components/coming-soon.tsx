import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";

/** Placeholder for routes that exist in the nav but are built in later slices. */
export function ComingSoon({ title, body }: { title: string; body: string }) {
  return (
    <section className="mx-auto max-w-2xl space-y-4 py-12">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="text-muted-foreground">{body}</p>
      <Link href="/" className={buttonVariants({ variant: "outline" })}>
        Back to Studio
      </Link>
    </section>
  );
}
