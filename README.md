# OpenRouter Suite for ComfyUI

Run [OpenRouter](https://openrouter.ai)'s image, video, LLM and audio models from ComfyUI with your own API key.

Each node has one model dropdown and shows only the inputs that model accepts. New OpenRouter models appear on their own, with no update or restart.

## Status

Early release (0.1.0), developed and tested on ComfyUI 0.44.2.

Tested against the live OpenRouter API: image generation and references, first and last frame video, image references for video, LLM input (image, video, audio, PDF), JSON output, text to speech, transcription and music.

Not yet tested with a real provider: video and audio references, which depend on the temporary upload hosts described below. See [docs/api-notes.md](docs/api-notes.md) for what is confirmed and what is still open.

## Install

Requires ComfyUI 0.37 or newer. There are no extra Python dependencies.

```
cd ComfyUI/custom_nodes
git clone https://github.com/ArDRaFi/ComfyUI-OpenRouter-Suite
```

Then restart ComfyUI. Older versions of ComfyUI run the nodes but don't show the reference sockets.

## Add your API key

Open **Settings > OpenRouter** and paste a key from [openrouter.ai/keys](https://openrouter.ai/keys). You can set the `OPENROUTER_API_KEY` environment variable instead.

The key is stored on the ComfyUI server. It is never saved in workflows or images.

If your ComfyUI is reachable over a network, anyone who can open it can spend through your key.

## Nodes

| Node | What it does |
|---|---|
| **ORS Image** | Generate or edit images, with as many references as the model allows |
| **ORS Video** | Text to video, first frame, first and last frame, references, editing and upscaling |
| **ORS Video Job** | Fetch a video job you already paid for, after a crash or interrupt |
| **ORS LLM** | Chat with any model, with optional images, video, audio or a PDF |
| **ORS Text to Speech** | Speech, with each model's own voices |
| **ORS Speech to Text** | Transcription, with optional SRT subtitles |
| **ORS Audio Generate** | Music and spoken replies |

### Inputs follow the model

Reference slots grow as you connect them, up to that model's limit. GPT Image takes 16 references, FLUX.2 Pro takes 8 and Krea 2 takes 1.

ORS Video works the same way. Veo shows first frame, last frame and image references, Seedance 2.0 adds video and audio references, and FLUX Video Edit asks for a source video. What you connect decides the kind of generation. Connect either frames or references, not both, because OpenRouter ignores references when frames are sent.

OpenRouter does not publish how many references each video model accepts. `data/model_overrides.json` holds known limits. Models not listed there get 4 images, 1 video and 1 audio slot, and any provider rejection is shown as a clear error.

### New models

The model list refreshes hourly in the background. A notification appears when new models arrive. New nodes can use them straight away. For an ORS Image, Video or Text to Speech node already on the canvas, reopen the workflow to see them.

### What it costs

After each run, the node title shows that run's cost and your session total. If a provider does not report a cost, it shows `cost n/a`.

Seeds start on `fixed`, so re-queuing an unchanged node does not run or bill it again. Change the seed to generate a new result.

Interrupting a video stops the wait, but the job keeps running at the provider and may still be billed. Use ORS Video Job to fetch it.

### Video and audio references are uploaded publicly

OpenRouter only accepts video and audio references as public HTTPS links. When you connect a video or audio file as a reference, or a source video to edit, ORS Video uploads it to a third-party temporary host, [Litterbox](https://litterbox.catbox.moe) or [Uguu](https://uguu.se) as a fallback. Anyone with the link can open the file until the host removes it.

Don't connect private or sensitive files to those inputs. Images are sent directly to OpenRouter and are not uploaded this way. Your OpenRouter key is never sent to these hosts.

## Limitations

- SVG-only models are not listed, because they cannot produce an IMAGE output.
- Animated GIF output is not supported.
- Video generation is not eligible for OpenRouter's Zero Data Retention.

## Example workflows

Ready-made workflows are in [example_workflows](example_workflows): image references, first and last frame video, mixed video references, describing an image with an LLM, and speech with transcription. Load your own files into the input nodes before running them.

## Development

Run the unit tests from the repository root:

```
python -m unittest discover tests
```

`tests/e2e_fake.py` runs real ComfyUI workflows against a local fake of OpenRouter at no cost. `scripts/update_snapshot.py` refreshes the bundled model list.

## License

GPL-3.0. This is an independent project, not affiliated with OpenRouter or Comfy Org. Generations are billed to your own OpenRouter account.
