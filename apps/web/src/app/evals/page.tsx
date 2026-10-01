import { EvalDashboard } from "@/components/evals/eval-dashboard";
import { asGoldenVersion } from "@/lib/evals";

export default async function EvaluatorPage({ searchParams }: PageProps<"/evals">) {
  const { golden_version } = await searchParams;
  // `?golden_version=v1|v2` picks the golden dataset version (default: the newest report).
  return <EvalDashboard initialVersion={asGoldenVersion(golden_version)} />;
}
