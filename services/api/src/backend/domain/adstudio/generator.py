"""Generator: one image call through `guarded_call`, then decode -> cap -> downscale -> blob.

The call is timed, rate-limited, recorded in the ledger (with the run id) and cached. The cache
value is a pointer to the stored blob, so a cache hit returns the same processed image without a
model call. Cache keys include the model, the prompt version and text, the input image shas, the
candidate slot and an optional salt (`fresh=true` on a run makes new images).
"""

from dataclasses import dataclass
from typing import Any

from backend.domain.adstudio.image_clients import ImageClient, ImageRequest
from backend.domain.adstudio.imaging import postprocess_generated
from backend.llm.cache import cache_key
from backend.llm.calls import CallRuntime, Units, guarded_call
from backend.storage.blobs import BlobStore, sha256_hex


@dataclass(frozen=True)
class GeneratedImage:
    sha256: str
    size: int
    width: int
    height: int
    native_width: int
    native_height: int
    mime: str
    requested_model: str
    served_model: str
    prompt_name: str
    prompt_version: str
    cached: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "size": self.size,
            "width": self.width,
            "height": self.height,
            "native_width": self.native_width,
            "native_height": self.native_height,
            "mime": self.mime,
            "requested_model": self.requested_model,
            "served_model": self.served_model,
            "prompt_name": self.prompt_name,
            "prompt_version": self.prompt_version,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any], *, cached: bool) -> "GeneratedImage":
        return cls(
            sha256=str(data["sha256"]),
            size=int(data["size"]),
            width=int(data["width"]),
            height=int(data["height"]),
            native_width=int(data["native_width"]),
            native_height=int(data["native_height"]),
            mime=str(data["mime"]),
            requested_model=str(data["requested_model"]),
            served_model=str(data["served_model"]),
            prompt_name=str(data["prompt_name"]),
            prompt_version=str(data["prompt_version"]),
            cached=cached,
        )


class Generator:
    def __init__(
        self,
        client: ImageClient,
        blobs: BlobStore,
        runtime: CallRuntime,
        *,
        timeout_s: float = 60.0,
    ) -> None:
        self.client = client
        self.blobs = blobs
        self.runtime = runtime
        self.timeout_s = timeout_s

    async def generate(
        self,
        request: ImageRequest,
        *,
        model: str,
        prompt_name: str,
        prompt_version: str,
        slot: int = 0,
        salt: str = "",
    ) -> GeneratedImage:
        requested = self.client.model_for(model)
        parts: list[object] = [
            "image",
            requested,
            prompt_name,
            prompt_version,
            sha256_hex(request.prompt.encode()),
            [sha256_hex(img) for img in request.images],
            request.aspect_ratio,
            request.image_size,
            slot,
            salt,
        ]
        tag = str(getattr(self.client, "cache_tag", "") or "")
        if tag:  # the fake's failure script: scripted and clean images never share a key
            parts.append(tag)
        key = cache_key(*parts)

        async def call() -> GeneratedImage:
            result = await self.client.generate(request, model=requested)
            processed = postprocess_generated(result.data)
            stored = self.blobs.put_generated(processed.data)
            return GeneratedImage(
                sha256=stored.sha256,
                size=stored.size,
                width=processed.width,
                height=processed.height,
                native_width=processed.native_width,
                native_height=processed.native_height,
                mime=processed.mime,
                requested_model=requested,
                served_model=result.served_model,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
            )

        def units(img: GeneratedImage) -> Units:
            return Units(
                output=1,
                unit_type="images",
                served_model=img.served_model,
                meta={
                    "native_size": [img.native_width, img.native_height],
                    "size": [img.width, img.height],
                    "sha256": img.sha256,
                },
            )

        image, cached = await guarded_call(
            "image",
            f"image.{prompt_name}",
            requested,
            call,
            version=prompt_version,
            prompt_name=prompt_name,
            cache_key=key,
            units=units,
            encode=lambda img: img.to_json(),
            decode=lambda data: GeneratedImage.from_json(data, cached=True),
            runtime=self.runtime,
            timeout_s=self.timeout_s,
            retry_on_timeout=False,
        )
        if cached and not self.blobs.exists(image.sha256):
            # The cache points at a blob that is gone (e.g. a wiped var/): regenerate once.
            return await self.generate(
                request,
                model=model,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
                slot=slot,
                salt=f"{salt}|regen",
            )
        return image
