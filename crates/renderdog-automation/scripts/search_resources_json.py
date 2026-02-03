"""
search_resources_json.py -- RenderDoc Python script that searches for resources by name.

Given a regex pattern, finds all resources (buffers, textures, shaders, pipelines,
samplers, etc.) whose names match the pattern and returns a JSON list with:

  - resource_id
  - name
  - resource_type

Supports Rust-compatible regex syntax (Python's `re` module).

Example regex patterns:
  - "particle"           -> matches names containing "particle"
  - "^Texture"           -> matches names starting with "Texture"
  - "Buffer$"            -> matches names ending with "Buffer"
  - "shadow|light"       -> matches names containing "shadow" or "light"
  - "gbuffer_\\d+"       -> matches "gbuffer_0", "gbuffer_1", etc.
  - ".*_diffuse$"        -> matches names ending with "_diffuse"

Valid resource_types filter values:
  - Unknown              -> Unclassified resources
  - Device               -> VkDevice / GPU device
  - Queue                -> VkQueue
  - CommandBuffer        -> VkCommandBuffer
  - Texture              -> Images/textures
  - Buffer               -> VkBuffer
  - View                 -> Image/buffer views
  - Sampler              -> VkSampler
  - SwapchainImage       -> Swapchain images
  - Memory               -> VkDeviceMemory
  - Shader               -> Shader modules
  - ShaderBinding        -> Descriptor set layouts, pipeline layouts
  - PipelineState        -> Graphics/compute pipelines
  - StateObject          -> Other state objects
  - RenderPass           -> VkRenderPass / VkFramebuffer
  - Query                -> Query pools
  - Sync                 -> Fences, semaphores, events
  - Pool                 -> Command pools, descriptor pools
  - AccelerationStructure -> Ray tracing acceleration structures
  - DescriptorStore      -> Descriptor heaps/sets
"""

import json
import re
import traceback

import renderdoc as rd


REQ_PATH = "search_resources_json.request.json"
RESP_PATH = "search_resources_json.response.json"


def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)


def resource_type_name(rtype) -> str:
    """Convert ResourceType enum to a human-readable string."""
    try:
        return str(rtype).replace("ResourceType.", "")
    except Exception:
        return "Unknown"


def main() -> None:
    with open(REQ_PATH, "r", encoding="utf-8") as f:
        req = json.load(f)

    query = req.get("query", "")
    case_sensitive = req.get("case_sensitive", False)
    regex_mode = req.get("regex", True)  # default to regex mode
    max_results = req.get("max_results", 500)
    resource_types = req.get("resource_types", None)  # optional list like ["Buffer", "Texture"]

    rd.InitialiseReplay(rd.GlobalEnvironment(), [])

    cap = rd.OpenCaptureFile()
    try:
        result = cap.OpenFile(req["capture_path"], "", None)
        if result != rd.ResultCode.Succeeded:
            raise RuntimeError("Couldn't open file: " + str(result))

        if not cap.LocalReplaySupport():
            raise RuntimeError("Capture cannot be replayed")

        result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if result != rd.ResultCode.Succeeded:
            raise RuntimeError("Couldn't initialise replay: " + str(result))

        try:
            resources = controller.GetResources()

            # Build the search pattern
            pattern_str = query if regex_mode else re.escape(query)
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(pattern_str, flags)
            except re.error as e:
                raise RuntimeError(f"Invalid regex pattern '{query}': {e}")

            matches = []
            for res in resources:
                name = res.name or ""
                type_name = resource_type_name(res.type)

                # Filter by resource type if specified
                if resource_types is not None:
                    if type_name not in resource_types:
                        continue

                # Match against query
                if query and not pattern.search(name):
                    continue

                matches.append({
                    "resource_id": int(res.resourceId),
                    "name": name,
                    "resource_type": type_name,
                })

                if max_results and len(matches) >= max_results:
                    break

            document = {
                "capture_path": req["capture_path"],
                "query": query,
                "regex": regex_mode,
                "case_sensitive": case_sensitive,
                "total_resources": len(resources),
                "total_matches": len(matches),
                "truncated": max_results and len(matches) >= max_results,
                "matches": matches,
            }

            write_envelope(True, result=document)
        finally:
            try:
                controller.Shutdown()
            except Exception:
                pass
    finally:
        try:
            cap.Shutdown()
        except Exception:
            pass
        rd.ShutdownReplay()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        write_envelope(False, error=traceback.format_exc())
    raise SystemExit(0)
