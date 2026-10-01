"use client";

import { ImageUp, RotateCw } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { productsCreateProduct, productsListProducts, type Product } from "@/lib/api";
import { isProblem, problemMessage } from "@/lib/problem";
import { apiUrl } from "@/lib/run-events";
import { cn } from "@/lib/utils";

const ACCEPT = "image/png,image/jpeg,image/webp";
const MAX_BYTES = 10 * 1024 * 1024;

type LoadState = "loading" | "ready" | "error";

interface ProductDropzoneProps {
  value: Product | null;
  onChange: (product: Product) => void;
  /** API error for the product field, shown under the control. */
  error?: string;
  disabled?: boolean;
  /** Called once with the product list so the form can preselect one. */
  onLoaded?: (products: Product[]) => void;
}

function nameFromFile(file: File): string {
  const base = file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim();
  return (base || "Product").slice(0, 80);
}

/** Pick a stored product or upload a new photo (`POST /v1/products`, idempotent by content). */
export function ProductDropzone({ value, onChange, error, disabled, onLoaded }: ProductDropzoneProps) {
  const inputId = useId();
  const errorId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const onLoadedRef = useRef(onLoaded);
  const [products, setProducts] = useState<Product[]>([]);
  const [load, setLoad] = useState<LoadState>("loading");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [lastFile, setLastFile] = useState<File | null>(null);

  useEffect(() => {
    onLoadedRef.current = onLoaded;
  }, [onLoaded]);

  const refresh = useCallback(async () => {
    try {
      const res = await productsListProducts({ query: { limit: 50 } });
      if (!res.data) throw new Error("no data");
      setProducts(res.data.items);
      setLoad("ready");
      onLoadedRef.current?.(res.data.items);
    } catch {
      setLoad("error");
    }
  }, []);

  useEffect(() => {
    const id = setTimeout(refresh, 0);
    return () => clearTimeout(id);
  }, [refresh]);

  const upload = useCallback(
    async (file: File) => {
      setUploadError(null);
      setLastFile(file);
      if (!ACCEPT.split(",").includes(file.type)) {
        setUploadError("That file isn't a supported image. Use PNG, JPEG or WebP up to 10 MB.");
        return;
      }
      if (file.size > MAX_BYTES) {
        setUploadError("That photo is over 10 MB. Use PNG, JPEG or WebP up to 10 MB.");
        return;
      }
      setUploading(true);
      try {
        const res = await productsCreateProduct({ body: { image: file, name: nameFromFile(file) } });
        if (res.data) {
          const product = res.data;
          setProducts((list) => [product, ...list.filter((p) => p.id !== product.id)]);
          setLastFile(null);
          onChange(product);
        } else {
          setUploadError(
            isProblem(res.error) ? problemMessage(res.error) : "Upload failed. Your brief is kept.",
          );
        }
      } catch {
        setUploadError("Upload failed (network). Your brief is kept.");
      } finally {
        setUploading(false);
      }
    },
    [onChange],
  );

  const shownError = uploadError ?? error;

  return (
    <div className="space-y-3">
      <label
        htmlFor={inputId}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files[0];
          if (file && !disabled) void upload(file);
        }}
        className={cn(
          "flex cursor-pointer items-center gap-3 rounded-lg border border-dashed p-3 text-sm transition-colors has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-ring",
          dragging ? "border-primary bg-primary/5" : "hover:bg-muted/50",
          shownError && "border-destructive",
          disabled && "pointer-events-none opacity-60",
        )}
      >
        {value ? (
          // eslint-disable-next-line @next/next/no-img-element -- API-served, already size-capped
          <img
            src={apiUrl(value.image_url)}
            alt=""
            className="size-16 shrink-0 rounded-md border bg-muted object-contain"
          />
        ) : (
          <div className="flex size-16 shrink-0 items-center justify-center rounded-md border bg-muted">
            <ImageUp className="size-6 text-muted-foreground" aria-hidden />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">
            {uploading ? "Checking photo…" : value ? value.name : "Drop a product photo"}
          </div>
          <div className="text-xs text-muted-foreground">
            {uploading ? (
              <span className="inline-flex items-center gap-1">
                <Spinner className="size-3" /> Uploading and normalising
              </span>
            ) : value ? (
              <span className="tabular-nums">
                {value.width}×{value.height} · click or drop to replace
              </span>
            ) : (
              "PNG, JPEG or WebP up to 10 MB, or pick one below"
            )}
          </div>
        </div>
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          disabled={disabled || uploading}
          aria-invalid={shownError ? true : undefined}
          aria-describedby={shownError ? errorId : undefined}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
            e.target.value = "";
          }}
        />
      </label>

      {shownError ? (
        <div id={errorId} role="alert" className="flex items-start gap-2 text-sm text-destructive">
          <span className="flex-1">{shownError}</span>
          {uploadError && lastFile ? (
            <Button type="button" size="xs" variant="outline" onClick={() => void upload(lastFile)}>
              <RotateCw /> Retry upload
            </Button>
          ) : null}
        </div>
      ) : null}

      {load === "loading" ? (
        <div className="flex gap-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="size-14 rounded-md" />
          ))}
        </div>
      ) : load === "error" ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          Couldn&apos;t load products.
          <Button type="button" size="xs" variant="ghost" onClick={() => void refresh()}>
            Retry
          </Button>
        </div>
      ) : products.length > 0 ? (
        <div role="group" aria-label="Stored products" className="flex flex-wrap gap-2">
          {products.map((p) => {
            const selected = value?.id === p.id;
            return (
              <button
                key={p.id}
                type="button"
                disabled={disabled}
                aria-pressed={selected}
                title={p.name}
                onClick={() => onChange(p)}
                className={cn(
                  "size-14 overflow-hidden rounded-md border bg-muted outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-60",
                  selected ? "ring-2 ring-primary" : "hover:border-foreground/30",
                )}
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- API-served thumbnails */}
                <img src={apiUrl(p.image_url)} alt={p.name} className="size-full object-contain" />
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
