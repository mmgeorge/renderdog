"""
get_texture_details_json.py -- RenderDoc Python script for getting texture metadata.

Finds a texture by name and returns detailed information including:
  - format, width, height, depth, mip_levels, array_size, sample_count
  - usages: list of pipelines/bindings that use this texture, including render targets

Returns:
  - texture_name: The name of the texture
  - texture_id: The resource ID
  - format: Texture format (e.g., R8G8B8A8_UNORM)
  - width, height, depth: Dimensions
  - mip_levels: Number of mip levels
  - array_size: Array size (1 for non-array textures)
  - sample_count: MSAA sample count (1 for non-MSAA)
  - usages: List of pipeline usages with binding info
"""

import json
import traceback

import renderdoc as rd


REQ_PATH = "get_texture_details_json.request.json"
RESP_PATH = "get_texture_details_json.response.json"


def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Texture finding
# ---------------------------------------------------------------------------

def find_texture(controller, texture_name):
    """Locate the target texture's resource description by name."""
    for res in controller.GetResources():
        if res.name == texture_name:
            return res

    available = []
    for r in controller.GetResources():
        if r.type == rd.ResourceType.Texture:
            available.append("  %s  %s" % (r.resourceId, r.name))

    raise RuntimeError(
        "Texture '%s' not found. Available textures:\n%s"
        % (texture_name, "\n".join(available[:30]))
    )


def get_texture_by_id(controller, tex_id):
    """Find a texture description by resource ID."""
    for t in controller.GetTextures():
        if t.resourceId == tex_id:
            return t
    return None


def get_texture_info(controller, tex_id):
    """Get texture metadata from the replay controller."""
    tex_desc = get_texture_by_id(controller, tex_id)
    if tex_desc is None:
        raise RuntimeError("Texture with ID %s not found in GetTextures()" % tex_id)

    # Get format name, handling potential issues with depth formats
    try:
        format_name = str(tex_desc.format.Name())
    except Exception:
        # Fallback: construct format info manually
        format_name = "Unknown"
        try:
            fmt = tex_desc.format
            comp_type = str(fmt.compType).replace("CompType.", "")
            format_name = "%s_%dch_%dbpc" % (comp_type, fmt.compCount, fmt.compByteWidth * 8)
        except Exception:
            pass

    return {
        "format": format_name,
        "width": tex_desc.width,
        "height": tex_desc.height,
        "depth": tex_desc.depth,
        "mip_levels": tex_desc.mips,
        "array_size": tex_desc.arraysize,
        "sample_count": tex_desc.msSamp,
        "cube_map": tex_desc.cubemap,
    }


def flatten_actions(roots):
    """Yield every leaf action in linear order."""
    for action in roots:
        if len(action.children) > 0:
            yield from flatten_actions(action.children)
        else:
            yield action


# ---------------------------------------------------------------------------
# Texture usage collection
# ---------------------------------------------------------------------------

def collect_texture_usages(controller, tex_id, actions):
    """Scan every leaf action and record texture usages in shader bindings and render targets."""
    stages = [
        rd.ShaderStage.Compute,
        rd.ShaderStage.Vertex,
        rd.ShaderStage.Fragment,
        rd.ShaderStage.Geometry,
        rd.ShaderStage.Tess_Eval,
        rd.ShaderStage.Tess_Control,
    ]

    stage_names = {
        rd.ShaderStage.Compute: "Compute",
        rd.ShaderStage.Vertex: "Vertex",
        rd.ShaderStage.Fragment: "Fragment",
        rd.ShaderStage.Geometry: "Geometry",
        rd.ShaderStage.Tess_Eval: "TessEval",
        rd.ShaderStage.Tess_Control: "TessControl",
    }

    def get_name(rid):
        try:
            for r in controller.GetResources():
                if r.resourceId == rid:
                    return r.name
        except Exception:
            pass
        return str(rid)

    def binding_type_str(refl_res, is_rw):
        """Determine if resource is a texture or buffer from reflection."""
        try:
            vtype = refl_res.variableType
            # If it has rows/cols but no members, it's likely a texture
            is_buffer = (vtype.rows == 0 and vtype.columns == 0) or len(vtype.members) > 0
        except Exception:
            is_buffer = False
        prefix = "RW " if is_rw else ""
        return prefix + "Buffer" if is_buffer else prefix + "Texture"

    groups = {}

    for action in actions:
        eid = action.eventId
        controller.SetFrameEvent(eid, False)
        state = controller.GetPipelineState()

        # Get pipeline name
        try:
            pipe_id = state.GetGraphicsPipelineObject()
            pipe_type = "Graphics"
        except Exception:
            pipe_id = rd.ResourceId.Null()
            pipe_type = None

        if pipe_id == rd.ResourceId.Null():
            try:
                pipe_id = state.GetComputePipelineObject()
                pipe_type = "Compute"
            except Exception:
                pipe_id = rd.ResourceId.Null()

        if pipe_id == rd.ResourceId.Null():
            for s in stages:
                try:
                    r = state.GetShaderReflection(s)
                    if r is not None:
                        pipe_id = r.resourceId
                        break
                except Exception:
                    continue

        pipe_name = get_name(pipe_id) if pipe_id != rd.ResourceId.Null() else ""

        # Check render targets (color attachments)
        try:
            outputs = state.GetOutputTargets()
            for i, out in enumerate(outputs):
                if out.resource == tex_id:
                    key = (pipe_name, "RenderTarget", i, "ColorTarget")
                    if key not in groups:
                        groups[key] = {
                            "pipeline": pipe_name,
                            "usage_type": "ColorTarget",
                            "binding": {
                                "index": i,
                                "name": "ColorAttachment%d" % i,
                                "type": "RenderTarget",
                            },
                            "event_ids": [],
                        }
                    eids = groups[key]["event_ids"]
                    if not eids or eids[-1] != eid:
                        eids.append(eid)
        except Exception:
            pass

        # Check depth target
        try:
            depth = state.GetDepthTarget()
            if depth.resource == tex_id:
                key = (pipe_name, "DepthTarget", 0, "DepthStencilTarget")
                if key not in groups:
                    groups[key] = {
                        "pipeline": pipe_name,
                        "usage_type": "DepthStencilTarget",
                        "binding": {
                            "index": 0,
                            "name": "DepthStencil",
                            "type": "DepthStencilTarget",
                        },
                        "event_ids": [],
                    }
                eids = groups[key]["event_ids"]
                if not eids or eids[-1] != eid:
                    eids.append(eid)
        except Exception:
            pass

        # Check shader bindings
        for stage in stages:
            refl = state.GetShaderReflection(stage)
            if refl is None:
                continue

            stage_name = stage_names.get(stage, str(stage))

            # Read-write resources (UAV / storage images)
            try:
                rw_list = state.GetReadWriteResources(stage)
                for i, used in enumerate(rw_list):
                    res_id = used.descriptor.resource
                    # Check if it's our texture or a view of our texture
                    if res_id == tex_id:
                        record_shader_usage(
                            groups, eid, pipe_name, stage_name, refl, i, True,
                            refl.readWriteResources, get_name, binding_type_str
                        )
                    else:
                        # Check if it's a view of our texture
                        try:
                            view_res = controller.GetTexture(res_id)
                            # This would fail if res_id is not a texture
                        except Exception:
                            pass
            except Exception:
                pass

            # Read-only resources (SRV / sampled textures)
            try:
                ro_list = state.GetReadOnlyResources(stage)
                for i, used in enumerate(ro_list):
                    res_id = used.descriptor.resource
                    if res_id == tex_id:
                        record_shader_usage(
                            groups, eid, pipe_name, stage_name, refl, i, False,
                            refl.readOnlyResources, get_name, binding_type_str
                        )
            except Exception:
                pass

    # Convert groups to sorted list
    result = []
    for key, g in sorted(groups.items(), key=lambda kv: kv[1]["event_ids"][0] if kv[1]["event_ids"] else 0):
        result.append({
            "pipeline": g["pipeline"],
            "usage_type": g["usage_type"],
            "binding": g["binding"],
            "event_ids": g["event_ids"],
        })

    return result


def record_shader_usage(groups, eid, pipe_name, stage_name, refl, refl_idx, is_rw,
                        refl_list, get_name, binding_type_str):
    """Record a single texture shader usage into the groups accumulator."""
    bname = ""
    bindex = refl_idx
    refl_res = None
    if refl_idx < len(refl_list):
        refl_res = refl_list[refl_idx]
        bname = refl_res.name
        try:
            bindex = refl_res.fixedBindNumber
        except Exception:
            bindex = refl_idx

    type_str = binding_type_str(refl_res, is_rw) if refl_res else ("RW Texture" if is_rw else "Texture")
    usage_type = "%s_%s" % (stage_name, "RWResource" if is_rw else "Resource")

    key = (pipe_name, usage_type, bindex, type_str)

    if key not in groups:
        groups[key] = {
            "pipeline": pipe_name,
            "usage_type": usage_type,
            "binding": {
                "index": bindex,
                "name": bname,
                "type": type_str,
            },
            "event_ids": [],
        }

    eids = groups[key]["event_ids"]
    if not eids or eids[-1] != eid:
        eids.append(eid)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    with open(REQ_PATH, "r", encoding="utf-8") as f:
        req = json.load(f)

    texture_name = req["texture_name"]

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
            # Find the texture
            tex_res = find_texture(controller, texture_name)
            tex_id = tex_res.resourceId

            # Get texture metadata
            tex_info = get_texture_info(controller, tex_id)

            # Scan all actions
            actions = list(flatten_actions(controller.GetRootActions()))
            if not actions:
                raise RuntimeError("No actions found in capture")

            # Collect texture usages
            usages = collect_texture_usages(controller, tex_id, actions)

            # Build final document
            document = {
                "texture_name": texture_name,
                "texture_id": int(tex_id),
            }
            document.update(tex_info)
            document["usages"] = usages

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
