"use client";

import { useRouter } from "next/navigation";
import { useCallback, useId, useRef, useState, type FormEvent } from "react";

import { CodeText } from "@/components/code-text";
import { GeographyCombobox, countryByCode, hemisphereHint } from "@/components/studio/geography-combobox";
import { ProductDropzone } from "@/components/studio/product-dropzone";
import { RequiredTextField, requiredTextError } from "@/components/studio/required-text-field";
import { SeasonCombobox } from "@/components/studio/season-combobox";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { runsCreateRunRoute, type Product, type RunCreate } from "@/lib/api";
import { DEFAULT_BRIEF, type BriefDraft } from "@/lib/brief";
import type { Country } from "@/lib/countries";
import { briefErrorsFromProblem, isProblem, type BriefErrors } from "@/lib/problem";

type Aspect = BriefDraft["aspect"];

export { DEFAULT_BRIEF, type BriefDraft } from "@/lib/brief";

function Kbd() {
  return (
    <kbd
      aria-hidden
      className="ml-1 rounded border border-primary-foreground/30 px-1 font-sans text-[11px] leading-4 opacity-80"
    >
      Ctrl/⌘ ↵
    </kbd>
  );
}

function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function FieldBlock({
  label,
  htmlFor,
  help,
  hint,
  error,
  errorId,
  helpId,
  children,
}: {
  label: string;
  htmlFor?: string;
  help?: string;
  hint?: string | null;
  error?: string;
  errorId: string;
  helpId: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5" data-invalid={error ? true : undefined}>
      <label htmlFor={htmlFor} className="text-sm font-medium">
        {label}
      </label>
      {help ? (
        <p id={helpId} className="text-xs text-muted-foreground">
          {help}
        </p>
      ) : null}
      {children}
      {hint && !error ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      {error ? (
        <p id={errorId} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function BriefForm({ initial = DEFAULT_BRIEF }: { initial?: BriefDraft }) {
  const router = useRouter();
  const ids = {
    geo: useId(),
    season: useId(),
    text: useId(),
    productErr: useId(),
    geoErr: useId(),
    seasonErr: useId(),
    textErr: useId(),
    geoHelp: useId(),
    seasonHelp: useId(),
    textHelp: useId(),
    productHelp: useId(),
    aspectHelp: useId(),
  };
  const [product, setProduct] = useState<Product | null>(null);
  const [country, setCountry] = useState<Country | null>(countryByCode(initial.geographyCode));
  const [season, setSeason] = useState(initial.season);
  const [requiredText, setRequiredText] = useState(initial.requiredText);
  const [aspect, setAspect] = useState<Aspect>(initial.aspect);
  const [errors, setErrors] = useState<BriefErrors>({});
  const [submitting, setSubmitting] = useState(false);
  // One key per brief: a retry of the same body replays the same run instead of starting another.
  const idempotency = useRef<{ body: string; key: string } | null>(null);

  const clearError = (field: keyof BriefErrors) =>
    setErrors((e) => (e[field] || e.form ? { ...e, [field]: undefined, form: undefined } : e));

  const initialProductId = initial.productId ?? null;
  const onProductsLoaded = useCallback(
    (items: Product[]) => {
      setProduct(
        (current) => current ?? items.find((p) => p.id === initialProductId) ?? items[0] ?? null,
      );
    },
    [initialProductId],
  );

  const validate = (): BriefErrors => {
    const out: BriefErrors = {};
    if (!product) out.product_id = "Start with a product photo. Drop a PNG or JPEG, or pick a stored product.";
    if (!country) out.geography_code = "Pick a country from the list.";
    if (!season.trim()) out.season = "Enter a month, season or holiday.";
    const textErr = requiredTextError(requiredText);
    if (textErr) out.required_text = textErr;
    return out;
  };

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (submitting) return;
    const clientErrors = validate();
    setErrors(clientErrors);
    if (Object.keys(clientErrors).length > 0 || !product || !country) return;

    const body: RunCreate = {
      product_id: product.id,
      geography_code: country.code,
      season: season.trim(),
      required_text: requiredText,
      aspect_ratio: aspect,
    };
    const serialized = JSON.stringify(body);
    if (idempotency.current?.body !== serialized) {
      idempotency.current = { body: serialized, key: newIdempotencyKey() };
    }

    setSubmitting(true);
    try {
      const res = await runsCreateRunRoute({
        body,
        headers: { "Idempotency-Key": idempotency.current.key },
      });
      if (res.data) {
        router.push(`/runs/${res.data.run_id}`);
        return;
      }
      setErrors(
        isProblem(res.error)
          ? briefErrorsFromProblem(res.error)
          : { form: `The API answered ${res.response?.status ?? "with an error"}. Try again.` },
      );
    } catch {
      setErrors({ form: "Can't reach Ad Studio's API. Is `make dev` running?" });
    }
    setSubmitting(false);
  }

  const describedBy = (help: string, err: string, hasErr: boolean) =>
    [help, hasErr ? err : null].filter(Boolean).join(" ");

  return (
    <form
      noValidate
      onSubmit={submit}
      onKeyDown={(e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          void submit();
        }
      }}
      className="space-y-5"
      aria-label="Ad brief"
    >
      <FieldBlock
        label="Product photo"
        help="Used as the reference in every generation. Plain background works best."
        helpId={ids.productHelp}
        error={errors.product_id}
        errorId={ids.productErr}
      >
        <ProductDropzone
          value={product}
          onChange={(p) => {
            setProduct(p);
            clearError("product_id");
          }}
          onLoaded={onProductsLoaded}
          disabled={submitting}
        />
      </FieldBlock>

      <FieldBlock
        label="Target market"
        htmlFor={ids.geo}
        hint={hemisphereHint(country)}
        error={errors.geography_code}
        errorId={ids.geoErr}
        helpId={ids.geoHelp}
      >
        <GeographyCombobox
          id={ids.geo}
          value={country}
          onChange={(c) => {
            setCountry(c);
            clearError("geography_code");
          }}
          invalid={Boolean(errors.geography_code)}
          describedBy={errors.geography_code ? ids.geoErr : undefined}
          disabled={submitting}
        />
      </FieldBlock>

      <FieldBlock
        label="Season, month or holiday"
        htmlFor={ids.season}
        hint="Resolved against the market's hemisphere: December is summer in Australia."
        error={errors.season}
        errorId={ids.seasonErr}
        helpId={ids.seasonHelp}
      >
        <SeasonCombobox
          id={ids.season}
          value={season}
          onChange={(v) => {
            setSeason(v);
            clearError("season");
          }}
          invalid={Boolean(errors.season)}
          describedBy={errors.season ? ids.seasonErr : undefined}
          disabled={submitting}
        />
      </FieldBlock>

      <FieldBlock
        label="Text that must appear"
        htmlFor={ids.text}
        help="Printed exactly as written, never translated or corrected."
        helpId={ids.textHelp}
        error={errors.required_text}
        errorId={ids.textErr}
      >
        <RequiredTextField
          id={ids.text}
          value={requiredText}
          onChange={(v) => {
            setRequiredText(v);
            clearError("required_text");
          }}
          invalid={Boolean(errors.required_text)}
          describedBy={describedBy(ids.textHelp, ids.textErr, Boolean(errors.required_text))}
          disabled={submitting}
          onSubmitShortcut={() => void submit()}
        />
      </FieldBlock>

      <FieldBlock
        label="Placement"
        help="Output long edge ≤ 1024 px"
        helpId={ids.aspectHelp}
        error={errors.aspect_ratio}
        errorId={ids.aspectHelp + "-err"}
      >
        <ToggleGroup
          variant="outline"
          spacing={0}
          value={[aspect]}
          onValueChange={(v: string[]) => {
            const next = v[0];
            if (next === "4:5" || next === "1:1") setAspect(next);
          }}
          aria-label="Placement"
          disabled={submitting}
        >
          <ToggleGroupItem value="4:5" aria-label="4:5 feed">
            4:5 feed
          </ToggleGroupItem>
          <ToggleGroupItem value="1:1" aria-label="1:1 square">
            1:1 square
          </ToggleGroupItem>
        </ToggleGroup>
      </FieldBlock>

      {errors.form ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn&apos;t start the run</AlertTitle>
          <AlertDescription>
            <CodeText>{errors.form}</CodeText>
          </AlertDescription>
        </Alert>
      ) : null}

      <Button type="submit" size="lg" className="w-full" disabled={submitting}>
        {submitting ? (
          <>
            <Spinner /> Generating…
          </>
        ) : (
          <>
            Generate ad <Kbd />
          </>
        )}
      </Button>
    </form>
  );
}
