# Guide: GPU pass markers/labels for RenderDoc

RenderDoc inspection is dramatically easier if your renderer emits **stable GPU markers** for
every pass. These markers show up in RenderDoc's **Event Browser**, and `renderdog` workflows
depend on them for fast searching (both in UI and in exported `*.actions.jsonl`).

`renderdog` does **not** create markers for you. Markers come from your graphics API.

## Naming recommendations

- Use **human-searchable** labels: `fret clip mask pass`, `fret composite premul masked pass`, ...
- Prefer a stable prefix per project/module (e.g. `fret ...`), so grep/search is easy.
- Keep the label stable across runs; avoid including frame counters or random IDs.
- If you have variants, encode them as suffixes: `... (msaa)`, `... (tier2)`, `... (premul)`.

## API-specific pointers

### Vulkan

- Use `VK_EXT_debug_utils` labels:
  - `vkCmdBeginDebugUtilsLabelEXT` / `vkCmdEndDebugUtilsLabelEXT`
  - Optionally `vkCmdInsertDebugUtilsLabelEXT` for single events
- RenderDoc will group drawcalls under these labels and make them searchable.

### D3D12

- Use `ID3D12GraphicsCommandList::BeginEvent` / `EndEvent` (or PIX helpers that wrap these APIs).
- Keep events at a pass granularity (one event per pass is usually the sweet spot).

### D3D11

- Use `ID3DUserDefinedAnnotation::BeginEvent` / `EndEvent`.

### OpenGL

- Use `glPushDebugGroup` / `glPopDebugGroup` (requires `KHR_debug`).

## How this connects to renderdog workflows

Once markers exist, your workflow can be:

1) Capture (`renderdog` in-app hotkey capture, or `renderdoccmd capture` via `renderdog-automation`)
2) Open in UI and search labels (Event Browser)
3) Export searchable artifacts for AI:
   - `renderdoc_export_bundle_jsonl` / `renderdoc_capture_and_export_bundle_jsonl`
   - or the `renderdog-automation` examples in the workspace `README.md`

If Vulkan capture fails, run the built-in diagnostics and follow the suggested `renderdoccmd vulkanlayer`
commands to (re)register the Vulkan layer.

