"""
find_resource_uses_json.py -- RenderDoc Python script that finds all uses of a resource.

Given a resource name or ID, finds all places where the resource is used in the capture
and returns details about each use including:

  - event_id: The event where the resource is used
  - usage: How the resource is used (e.g., VertexBuffer, ColorTarget, PS_Resource, etc.)
  - pipeline_name: The name of the pipeline at this event (if applicable)
  - stage: The shader stage (for shader resources)
  - binding: Binding information (set, binding) if available
  - entry_point: For shaders, the entry point name

ResourceUsage values:
  - Unused, VertexBuffer, IndexBuffer
  - VS_Constants, HS_Constants, DS_Constants, GS_Constants, PS_Constants, CS_Constants (constant buffers)
  - VS_Resource, HS_Resource, DS_Resource, GS_Resource, PS_Resource, CS_Resource (read-only resources)
  - VS_RWResource, HS_RWResource, DS_RWResource, GS_RWResource, PS_RWResource, CS_RWResource (UAV/SSBO)
  - InputTarget, ColorTarget, DepthStencilTarget (render targets)
  - Indirect, Clear, Discard, GenMips, Resolve, ResolveSrc, ResolveDst
  - Copy, CopySrc, CopyDst, Barrier, CPUWrite
"""

import json
import traceback

import renderdoc as rd


REQ_PATH = "find_resource_uses_json.request.json"
RESP_PATH = "find_resource_uses_json.response.json"


def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Resource name lookup
# ---------------------------------------------------------------------------

def build_resource_maps(controller):
    """Build dicts for resource lookup.

    Returns:
        id_to_name: int -> name
        name_to_res: name -> ResourceDescription
        id_to_res: int -> ResourceDescription
    """
    id_to_name = {}
    name_to_res = {}
    id_to_res = {}
    try:
        for res in controller.GetResources():
            rid = int(res.resourceId)
            name = res.name or ""
            id_to_name[rid] = name
            id_to_res[rid] = res
            if name:
                name_to_res[name] = res
    except Exception:
        pass
    return id_to_name, name_to_res, id_to_res


def get_name(id_to_name, rid):
    """Look up a resource name by ID."""
    return id_to_name.get(int(rid), str(rid))


# ---------------------------------------------------------------------------
# Shader stage helpers
# ---------------------------------------------------------------------------

_ALL_STAGES = [
    rd.ShaderStage.Compute,
    rd.ShaderStage.Vertex,
    rd.ShaderStage.Fragment,
    rd.ShaderStage.Geometry,
    rd.ShaderStage.Tess_Eval,
    rd.ShaderStage.Tess_Control,
]

_STAGE_NAMES = {
    rd.ShaderStage.Compute:      "Compute",
    rd.ShaderStage.Vertex:       "Vertex",
    rd.ShaderStage.Fragment:     "Fragment",
    rd.ShaderStage.Geometry:     "Geometry",
    rd.ShaderStage.Tess_Eval:    "TessEval",
    rd.ShaderStage.Tess_Control: "TessControl",
}

# Map ResourceUsage to shader stage (for stage-specific usages)
_USAGE_TO_STAGE = {
    "VS_Constants": "Vertex",
    "HS_Constants": "TessControl",
    "DS_Constants": "TessEval",
    "GS_Constants": "Geometry",
    "PS_Constants": "Fragment",
    "CS_Constants": "Compute",
    "VS_Resource": "Vertex",
    "HS_Resource": "TessControl",
    "DS_Resource": "TessEval",
    "GS_Resource": "Geometry",
    "PS_Resource": "Fragment",
    "CS_Resource": "Compute",
    "VS_RWResource": "Vertex",
    "HS_RWResource": "TessControl",
    "DS_RWResource": "TessEval",
    "GS_RWResource": "Geometry",
    "PS_RWResource": "Fragment",
    "CS_RWResource": "Compute",
}


def usage_to_str(usage):
    """Convert ResourceUsage enum to string."""
    try:
        return str(usage).replace("ResourceUsage.", "")
    except Exception:
        return str(usage)


def find_resource(controller, id_to_name, name_to_res, id_to_res, resource_query):
    """
    Find a resource by name or ID.
    Returns (ResourceDescription, resource_name, resource_type) or raises RuntimeError.
    """
    # Try as integer ID first
    try:
        rid = int(resource_query)
        if rid in id_to_res:
            res = id_to_res[rid]
            rtype = str(res.type).replace("ResourceType.", "")
            return res, id_to_name.get(rid, str(rid)), rtype
    except ValueError:
        pass

    # Try as exact name
    if resource_query in name_to_res:
        res = name_to_res[resource_query]
        rtype = str(res.type).replace("ResourceType.", "")
        return res, resource_query, rtype

    # Try partial match
    matches = []
    for res in controller.GetResources():
        name = res.name or ""
        if resource_query in name:
            matches.append((res, name, str(res.type).replace("ResourceType.", "")))

    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        match_names = [m[1] for m in matches[:10]]
        raise RuntimeError(
            "Multiple resources match '%s': %s%s. Please be more specific."
            % (resource_query, match_names, "..." if len(matches) > 10 else "")
        )

    raise RuntimeError(
        "Resource '%s' not found. Use renderdoc_search_resources to find available resources."
        % resource_query
    )


def get_pipeline_info_at_event(controller, id_to_name, event_id, resource_id, usage_str):
    """
    Get pipeline and binding info at a specific event for a resource.
    Returns dict with pipeline_name, stage, entry_point, binding info.
    """
    info = {}

    controller.SetFrameEvent(event_id, False)
    state = controller.GetPipelineState()

    # Get pipeline name
    try:
        pipe_id = state.GetGraphicsPipelineObject()
        if pipe_id != rd.ResourceId.Null():
            info["pipeline_name"] = get_name(id_to_name, pipe_id)
            info["pipeline_type"] = "Graphics"
    except Exception:
        pass

    if "pipeline_name" not in info:
        try:
            pipe_id = state.GetComputePipelineObject()
            if pipe_id != rd.ResourceId.Null():
                info["pipeline_name"] = get_name(id_to_name, pipe_id)
                info["pipeline_type"] = "Compute"
        except Exception:
            pass

    # Determine stage from usage
    stage_name = _USAGE_TO_STAGE.get(usage_str)
    if stage_name:
        info["stage"] = stage_name

        # Try to get entry point for this stage
        for stage in _ALL_STAGES:
            if _STAGE_NAMES.get(stage) == stage_name:
                try:
                    ep = state.GetShaderEntryPoint(stage)
                    if ep:
                        info["entry_point"] = ep
                except Exception:
                    pass
                break

    # For shader resources, try to find binding information
    if stage_name:
        for stage in _ALL_STAGES:
            if _STAGE_NAMES.get(stage) != stage_name:
                continue

            refl = state.GetShaderReflection(stage)
            if refl is None:
                continue

            # Check if this is the shader itself
            if int(refl.resourceId) == resource_id:
                info["usage_detail"] = "shader_module"
                try:
                    ep = state.GetShaderEntryPoint(stage)
                    if ep:
                        info["entry_point"] = ep
                except Exception:
                    pass
                break

            # Check constant blocks
            try:
                for cb in refl.constantBlocks:
                    # We can't directly check if this CB uses our resource without
                    # more API calls, but we can report the bindings
                    pass
            except Exception:
                pass

            # Check read-only resources
            try:
                for i, res in enumerate(refl.readOnlyResources):
                    try:
                        info["binding_set"] = res.fixedBindSetOrSpace
                        info["binding_slot"] = res.fixedBindNumber
                    except Exception:
                        pass
            except Exception:
                pass

            break

    return info


def main() -> None:
    with open(REQ_PATH, "r", encoding="utf-8") as f:
        req = json.load(f)

    resource_query = req["resource"]
    max_results = req.get("max_results", 500)
    include_pipeline_info = req.get("include_pipeline_info", True)

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
            id_to_name, name_to_res, id_to_res = build_resource_maps(controller)

            # Find the resource - returns ResourceDescription object
            resource_desc, resource_name, resource_type = find_resource(
                controller, id_to_name, name_to_res, id_to_res, resource_query
            )
            resource_id = int(resource_desc.resourceId)

            # Get all usages - pass the actual ResourceId object
            usages = controller.GetUsage(resource_desc.resourceId)

            uses = []
            seen_events = set()

            for usage in usages:
                event_id = int(usage.eventId)
                usage_str = usage_to_str(usage.usage)

                # Skip if we've seen this event+usage combination
                key = (event_id, usage_str)
                if key in seen_events:
                    continue
                seen_events.add(key)

                use_entry = {
                    "event_id": event_id,
                    "usage": usage_str,
                }

                # Add view info if available
                if usage.view != rd.ResourceId.Null():
                    use_entry["view_id"] = int(usage.view)
                    use_entry["view_name"] = get_name(id_to_name, usage.view)

                # Get pipeline info for this event
                if include_pipeline_info:
                    try:
                        pipeline_info = get_pipeline_info_at_event(
                            controller, id_to_name, event_id, resource_id, usage_str
                        )
                        use_entry.update(pipeline_info)
                    except Exception:
                        pass

                uses.append(use_entry)

                if max_results and len(uses) >= max_results:
                    break

            # Build result
            document = {
                "capture_path": req["capture_path"],
                "resource_query": resource_query,
                "resource_id": resource_id,
                "resource_name": resource_name,
                "resource_type": resource_type,
                "total_uses": len(uses),
                "truncated": max_results and len(uses) >= max_results,
                "uses": uses,
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
