import { WandSparkles } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { BriefForm } from "@/components/studio/brief-form";
import { briefFromParams } from "@/lib/brief";

export default async function StudioPage({ searchParams }: PageProps<"/">) {
  const params = await searchParams;
  const initial = briefFromParams(params);
  // A new query (e.g. "Rerun for another market") remounts the form with that brief.
  const formKey = JSON.stringify(initial);
  return (
    <div className="mx-auto grid max-w-6xl gap-8 lg:grid-cols-[360px_minmax(0,1fr)]">
      <section aria-labelledby="studio-title" className="space-y-5">
        <div className="space-y-1">
          <h1 id="studio-title" className="text-2xl font-semibold tracking-tight">
            Studio
          </h1>
          <p className="text-sm text-muted-foreground">
            Turn one product photo into a localised display ad. Released only after it passes the quality
            gate.
          </p>
        </div>
        <BriefForm key={formKey} initial={initial} />
      </section>
      <section aria-label="Run canvas" className="hidden lg:block">
        <EmptyState
          className="min-h-[420px] border-dashed"
          icon={<WandSparkles aria-hidden />}
          title="Your ad will build here, step by step"
          body="Plan, candidates, checks, repairs and the approval card stream in as the run goes. Press Generate ad (⌘↵ or Ctrl+Enter)."
        />
      </section>
    </div>
  );
}
