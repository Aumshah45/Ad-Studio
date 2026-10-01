import { BatchGallery } from "@/components/batch/batch-gallery";
import { asGoldenVersion } from "@/lib/evals";

export default async function BatchPage({ searchParams }: PageProps<"/batch">) {
  const { ad, golden_version } = await searchParams;
  // `?ad=<item id>` opens that ad's detail sheet (shareable deep link); `?golden_version=v1|v2`
  // picks the golden dataset version (default: the newest report).
  return (
    <BatchGallery initialAd={typeof ad === "string" ? ad : null} initialVersion={asGoldenVersion(golden_version)} />
  );
}
