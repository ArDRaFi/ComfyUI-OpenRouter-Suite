from __future__ import annotations

from .. import capabilities as caps
from .. import catalog, client, usage, widgets
from ..host import IO
from ..media import image as image_media


class ORSImage(IO.ComfyNode):
    @classmethod
    def define_schema(cls):
        return IO.Schema(
            node_id="ORS_Image",
            display_name="ORS Image",
            category="OpenRouter/Image",
            description="Generate or edit images with any OpenRouter image model, using your own key.",
            inputs=[
                widgets.prompt_input(tooltip="The image to generate, or the edit to make."),
                IO.DynamicCombo.Input("model", options=widgets.image_options(), tooltip="OpenRouter image model."),
                widgets.seed_input(),
            ],
            outputs=[IO.Image.Output(), IO.Mask.Output()],
            hidden=[IO.Hidden.unique_id],
        )

    @classmethod
    async def execute(cls, prompt: str, model: dict, seed: int) -> IO.NodeOutput:
        slug = model["model"]
        entry = catalog.entry("image", slug)
        capability = caps.image_caps(slug, entry)
        if not prompt.strip():
            raise client.OpenRouterError("Enter a prompt.")

        references = image_media.collect_slots(model.get("images"))
        if len(references) > capability.max_refs:
            raise client.OpenRouterError(f"{slug} accepts up to {capability.max_refs} reference images, but {len(references)} were connected.")
        if len(references) < capability.min_refs:
            raise client.OpenRouterError(f"{slug} needs at least {capability.min_refs} reference image(s).")

        body: dict = {"model": slug, "prompt": prompt}
        for param in capability.params:
            if param.name in model:
                body[param.name] = model[param.name]
        if body.get("output_format") not in ("jpeg", "webp"):
            body.pop("output_compression", None)  # only valid for lossy formats
        if capability.seed:
            body["seed"] = seed
        if references:
            body["input_references"] = [
                {"type": "image_url", "image_url": {"url": image_media.to_data_url(frame)}} for frame in references
            ]

        response = await client.post_json("/images", body)
        images, masks = image_media.decode_results(response.get("data") or [])
        usage.report(cls.hidden.unique_id, (response.get("usage") or {}).get("cost"))
        return IO.NodeOutput(images, masks)
