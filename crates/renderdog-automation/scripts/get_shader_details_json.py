"""
get_shader_details_json.py -- RenderDoc Python script that returns JSON info about shaders.

Given a pipeline name and optional entry point filter, finds all matching shader stages
in the capture and returns a JSON array of shader info objects, each containing:

  - entry_point
  - stage
  - event_id
  - source_files (paths + sizes from SPIR-V debug info)
  - encoding
  - read_write_resources
  - read_only_resources
  - constant_blocks
  - samplers
  - input_signature

If entry_points filter is not provided or empty, returns info for all entry points
found in the pipeline.
"""

import json
import traceback

import renderdoc as rd


REQ_PATH = "get_shader_details_json.request.json"
RESP_PATH = "get_shader_details_json.response.json"


def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Resource name lookup
# ---------------------------------------------------------------------------

def build_name_map(controller):
    """Build a dict mapping ResourceId string -> name."""
    names = {}
    try:
        for res in controller.GetResources():
            names[str(res.resourceId)] = res.name
    except Exception:
        pass
    return names


def get_name(names, rid):
    """Look up a resource name, falling back to str(rid)."""
    return names.get(str(rid), str(rid))


# ---------------------------------------------------------------------------
# Find the pipeline + entry points
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


def leaves(roots):
    for a in roots:
        if len(a.children) > 0:
            yield from leaves(a.children)
        else:
            yield a


def find_all_shaders(controller, names, pipeline_name, entry_points_filter):
    """
    Scan actions to find all shader stages that match pipeline_name and
    optionally filter by entry_points.

    Returns list of (action, stage, entry_point_str) tuples.
    """
    if not pipeline_name:
        raise RuntimeError("pipeline_name is required")

    # Convert filter to a set for fast lookup, None means no filter
    filter_set = None
    if entry_points_filter:
        filter_set = set(entry_points_filter)

    results = []
    seen = set()  # Track (stage, entry_point) pairs to avoid duplicates

    for action in leaves(controller.GetRootActions()):
        controller.SetFrameEvent(action.eventId, False)
        state = controller.GetPipelineState()

        # Check if the graphics pipeline name matches
        pipe_match = False
        try:
            pipe_id = state.GetGraphicsPipelineObject()
            if pipe_id != rd.ResourceId.Null():
                if get_name(names, pipe_id) == pipeline_name:
                    pipe_match = True
        except Exception:
            pass

        # Also check compute pipeline
        if not pipe_match:
            try:
                pipe_id = state.GetComputePipelineObject()
                if pipe_id != rd.ResourceId.Null():
                    if get_name(names, pipe_id) == pipeline_name:
                        pipe_match = True
            except Exception:
                pass

        for stage in _ALL_STAGES:
            refl = state.GetShaderReflection(stage)
            if refl is None:
                continue

            # Does the pipeline or shader name match?
            shader_name = get_name(names, refl.resourceId)
            if not pipe_match and shader_name != pipeline_name:
                continue

            # Get entry point
            try:
                ep = state.GetShaderEntryPoint(stage)
            except Exception:
                ep = ""
            if not ep:
                ep = "main"

            # Apply entry point filter if specified
            if filter_set is not None and ep not in filter_set:
                continue

            # Avoid duplicates
            key = (stage, ep)
            if key in seen:
                continue
            seen.add(key)

            results.append((action, stage, ep))

    return results


# ---------------------------------------------------------------------------
# Extract shader info
# ---------------------------------------------------------------------------

def resource_type_str(res):
    """Human-readable type for a ShaderResource."""
    try:
        vtype = res.variableType
        if len(vtype.members) > 0 or (vtype.rows == 0 and vtype.columns == 0):
            return "Buffer"
        return "Texture"
    except Exception:
        return "Unknown"


def extract_info(state, stage, names):
    """Extract info for the matched shader stage.  Returns dict."""
    refl = state.GetShaderReflection(stage)
    if refl is None:
        raise RuntimeError("No reflection available for %s" % _STAGE_NAMES.get(stage, str(stage)))

    info = {}

    # --- Source files from debug info ---
    source_files = []
    try:
        debug = refl.debugInfo
        if debug is not None and debug.files is not None:
            for f in debug.files:
                fname = getattr(f, 'filename', '') or getattr(f, 'Filename', '') or ''
                body = getattr(f, 'contents', '') or getattr(f, 'Contents', '') or ''
                if fname:
                    source_files.append({
                        "path": fname,
                        "size": len(body) if body else 0,
                    })
    except Exception as e:
        info["debug_info_error"] = str(e)
    info["source_files"] = source_files

    # --- Encoding ---
    try:
        if refl.debugInfo is not None:
            info["encoding"] = str(refl.debugInfo.encoding)
    except Exception:
        pass

    # --- Read-write resources (UAV / SSBO) ---
    rw = []
    try:
        for res in refl.readWriteResources:
            entry = {"name": res.name, "type": resource_type_str(res)}
            try:
                entry["set"] = res.fixedBindSetOrSpace
                entry["binding"] = res.fixedBindNumber
            except Exception:
                pass
            rw.append(entry)
    except Exception:
        pass
    info["read_write_resources"] = rw

    # --- Read-only resources (SRV / UBO textures) ---
    ro = []
    try:
        for res in refl.readOnlyResources:
            entry = {"name": res.name, "type": resource_type_str(res)}
            try:
                entry["set"] = res.fixedBindSetOrSpace
                entry["binding"] = res.fixedBindNumber
            except Exception:
                pass
            ro.append(entry)
    except Exception:
        pass
    info["read_only_resources"] = ro

    # --- Constant blocks (UBOs / push constants) ---
    cbs = []
    try:
        for cb in refl.constantBlocks:
            entry = {"name": cb.name, "byte_size": cb.byteSize}
            try:
                entry["set"] = cb.fixedBindSetOrSpace
                entry["binding"] = cb.fixedBindNumber
            except Exception:
                pass
            cbs.append(entry)
    except Exception:
        pass
    info["constant_blocks"] = cbs

    # --- Samplers ---
    samps = []
    try:
        for s in refl.samplers:
            entry = {"name": s.name}
            try:
                entry["set"] = s.fixedBindSetOrSpace
                entry["binding"] = s.fixedBindNumber
            except Exception:
                pass
            samps.append(entry)
    except Exception:
        pass
    info["samplers"] = samps

    # --- Input signature ---
    inputs = []
    try:
        for sig in refl.inputSignature:
            inputs.append({
                "name": sig.varName if sig.varName else sig.semanticName,
                "semantic": sig.semanticName,
                "index": sig.semanticIndex,
                "type": str(sig.varType),
                "components": sig.compCount,
            })
    except Exception:
        pass
    info["input_signature"] = inputs

    return info


def main() -> None:
    with open(REQ_PATH, "r", encoding="utf-8") as f:
        req = json.load(f)

    pipeline_name = req["pipeline_name"]
    entry_points_filter = req.get("entry_points", None)  # Optional array

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
            names = build_name_map(controller)

            # Find all matching shaders
            shader_matches = find_all_shaders(controller, names, pipeline_name, entry_points_filter)

            if not shader_matches:
                filter_msg = ""
                if entry_points_filter:
                    filter_msg = " with entry points %s" % entry_points_filter
                raise RuntimeError(
                    "Could not find pipeline '%s'%s in any action. "
                    "Check the Resource Inspector for available names."
                    % (pipeline_name, filter_msg)
                )

            # Extract info for each shader
            shaders = []
            for action, stage, ep in shader_matches:
                controller.SetFrameEvent(action.eventId, False)
                state = controller.GetPipelineState()

                info = extract_info(state, stage, names)

                shader_entry = {
                    "entry_point": ep,
                    "stage": _STAGE_NAMES.get(stage, str(stage)),
                    "event_id": int(action.eventId),
                }
                shader_entry.update(info)
                shaders.append(shader_entry)

            # Build result
            document = {
                "capture_path": req["capture_path"],
                "pipeline_name": pipeline_name,
                "entry_points_filter": entry_points_filter,
                "shaders": shaders,
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
