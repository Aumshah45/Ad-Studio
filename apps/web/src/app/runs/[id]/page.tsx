import { RunView } from "@/components/run/run-view";

export default async function RunPage({ params }: PageProps<"/runs/[id]">) {
  const { id } = await params;
  return <RunView key={id} runId={id} />;
}
