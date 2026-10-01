"""Slice-1 live spike: confirm the Gemini image models, the working API surface and the native size.

Makes one call per (model, api) until one succeeds per model (worst case 4 calls, ~$0.20).
Prints model id, api, status, native size, latency and bytes, and writes the images to
var/spike/. Never prints the API key.

    cd services/api && uv run python scripts/spike_image.py [--aspect 4:5]
"""

import argparse
import asyncio
import io
import time
from pathlib import Path
from typing import get_args

from PIL import Image

from backend.core.settings import Settings
from backend.domain.adstudio.image_clients import (
    AspectRatio,
    GeminiImageClient,
    ImageApi,
    ImageRequest,
)

ROOT = Path(__file__).resolve().parents[3]
PROMPT = (
    "Create a display advertisement photo. Keep the product from the reference image exactly "
    "as it is (shape, colours, logo and label). Place it as the hero on a sunny Australian "
    'summer beach table. At the top, render the headline text exactly: "Summer Sale — 30% OFF".'
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--aspect", default="4:5", choices=["4:5", "1:1"])
    args = parser.parse_args()
    aspect: AspectRatio = args.aspect

    settings = Settings()
    key = settings.google_api_key.get_secret_value() if settings.google_api_key else ""
    if not key:
        raise SystemExit("GOOGLE_API_KEY is empty in .env")
    reference = (ROOT / "data/golden/products/P1.jpg").read_bytes()
    out = Path("var/spike")
    out.mkdir(parents=True, exist_ok=True)

    models = [settings.image_model_candidate, settings.image_model_repair]
    apis: tuple[ImageApi, ...] = get_args(ImageApi)
    for model in models:
        for api in apis:
            client = GeminiImageClient(key, api=api)
            request = ImageRequest(prompt=PROMPT, images=(reference,), aspect_ratio=aspect)
            t0 = time.perf_counter()
            try:
                result = await asyncio.wait_for(client.generate(request, model=model), timeout=90)
            except Exception as exc:  # report and try the next surface
                msg = str(exc).replace(key, "<key>")[:300]
                print(f"FAIL  {model:40} {api:17} {type(exc).__name__}: {msg}")
                continue
            ms = int((time.perf_counter() - t0) * 1000)
            img = Image.open(io.BytesIO(result.data))
            name = f"{model.split(':')[1]}_{api}_{aspect.replace(':', 'x')}.{img.format.lower()}"
            (out / name).write_bytes(result.data)
            print(
                f"OK    {model:40} {api:17} native={img.size[0]}x{img.size[1]} "
                f"long_edge={max(img.size)} fmt={img.format} {len(result.data) // 1024} KB "
                f"{ms} ms meta={result.meta} -> var/spike/{name}"
            )
            break


if __name__ == "__main__":
    asyncio.run(main())
