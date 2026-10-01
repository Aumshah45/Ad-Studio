/** RFC 9457 problem+json bodies from the API (`core.errors.AppError`) and their mapping onto form fields. */

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  /** FastAPI request validation: one entry per failing field. */
  errors?: { loc: string[]; msg: string }[];
  [key: string]: unknown;
}

export function isProblem(value: unknown): value is Problem {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Problem).type === "string" &&
    typeof (value as Problem).title === "string"
  );
}

/** Short problem code: `unknown-season` whether the API sends a bare code or a URI. */
export function problemCode(p: Problem): string {
  const parts = p.type.split("/");
  return parts[parts.length - 1] ?? p.type;
}

export function problemMessage(p: Problem): string {
  return p.detail || p.title;
}

export type BriefField = "product_id" | "geography_code" | "season" | "required_text" | "aspect_ratio";
export type BriefErrors = Partial<Record<BriefField | "form", string>>;

const CODE_TO_FIELD: Record<string, BriefField> = {
  "unknown-season": "season",
  "invalid-text": "required_text",
  "unsupported-script": "required_text",
  "unknown-geography": "geography_code",
};

const BRIEF_FIELDS = new Set<string>([
  "product_id",
  "geography_code",
  "geography_detail",
  "season",
  "required_text",
  "aspect_ratio",
]);

/** Place each API error next to the field it is about; anything else goes to `form`. */
export function briefErrorsFromProblem(p: Problem): BriefErrors {
  const code = problemCode(p);
  const field = CODE_TO_FIELD[code];
  if (field) return { [field]: problemMessage(p) };
  if (code === "not-found" && /product/i.test(p.title)) {
    return { product_id: "That product no longer exists. Pick or upload another photo." };
  }
  if (code === "validation-error" && p.errors?.length) {
    const out: BriefErrors = {};
    for (const err of p.errors) {
      const name = [...err.loc].reverse().find((part) => BRIEF_FIELDS.has(part));
      const key: BriefField | "form" =
        name === "geography_detail" ? "geography_code" : ((name as BriefField | undefined) ?? "form");
      out[key] = out[key] ? `${out[key]} ${err.msg}` : err.msg;
    }
    return out;
  }
  return { form: problemMessage(p) };
}
