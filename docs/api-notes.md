# OpenRouter API notes

Findings that shaped the pack, and what still needs checking.

## Confirmed against the live API

- Catalog endpoints work without a key.
- `input_references.max` is published per image model, and some models set `min: 1`.
- `supported_frame_images` and `input_modalities` describe video inputs. Reference counts are not published.
- If `frame_images` and `input_references` are both sent, frames win and references are dropped.
- Base64 data URLs work for `frame_images` and for image `input_references`, on image and video models.
- Video and audio `input_references` are refused unless they are HTTPS links ("Only HTTPS URLs are allowed"). That refusal is free.
- The Files API cannot help: it does not accept video, and uploaded files have no public URL.
- A model that does not support references is refused up front and free. Grok Imagine 1.5 Lite does this, even though its model page lists the field.
- Provider image rules are enforced too. ByteDance Seedance 1.5 needs images at least 300px wide.
- TTS `pcm` output from Gemini is 24 kHz.
- `usage.cost` is reported for image, video, chat, STT and audio generation. The `/generation` lookup for TTS occasionally has no data yet, which shows as `cost n/a`.
- The audio generate request works for Lyria (44.1 kHz, about 30 seconds) and gpt-audio (`pcm16`, 24 kHz).
- `provider.options` keyed by `byteplus` is accepted for Seedance 1.5. Whether the option took effect was not checked.

Six Recraft vector models output SVG only, so they are excluded.

## ComfyUI frontend behaviour

- Progress text is cleared when a job ends, so the final cost goes in the node title.
- Links on sockets nested two levels deep are lost when a workflow is reopened (seen on frontend 1.57.6). Video inputs are kept to one level for this reason.
- `DynamicCombo` captures its options when a node is created, so placed nodes need the workflow reopened to see new models.
- ComfyUI 0.8.2 (frontend 1.35.9) runs the nodes but does not render `Autogrow` slots inside a `DynamicCombo`.
- `price_badge` only renders for `api_node` nodes, which also get Comfy's credits treatment, so the pack does not use it.

## Still to verify

1. Uploading to Litterbox and Uguu, and a provider fetching those links. The upload format comes from memory of those services and has only run against a local fake host.
2. A full Seedance job with image, video and audio references together.
3. Reference limits in `data/model_overrides.json` for Seedance and Veo come from third-party sources. Kling, Wan 3.0, Hailuo 3, Gemini Omni and HeyGen limits are unknown. Other models may refuse references like Grok Imagine 1.5 Lite does.
