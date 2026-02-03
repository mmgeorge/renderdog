"""
get_event_pipeline_state_json.py - RenderDoc Python script that dumps JSON metadata
about all bindings for a given pipeline at a given event ID.

Outputs a JSON object with:
  - pipeline: the pipeline name
  - event_id: the event inspected
  - stages: which shader stages are active and their entry points
  - resources: all RO + RW resource bindings (buffers, textures, etc.)
  - uniforms: all constant/uniform buffer bindings with variable contents
  - samplers: all sampler bindings
"""

import json
import traceback
from collections import defaultdict

import renderdoc as rd


REQ_PATH = "get_event_pipeline_state_json.request.json"
RESP_PATH = "get_event_pipeline_state_json.response.json"


def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _resource_name(res_id, controller):
    """Get the debug name for a resource."""
    if res_id == rd.ResourceId.Null():
        return "<null>"
    for r in controller.GetResources():
        if r.resourceId == res_id:
            return r.name if r.name else str(res_id)
    return str(res_id)


def _buf_size_for_resource(res_id, controller):
    """Try to find the buffer's total byte length."""
    for b in controller.GetBuffers():
        if b.resourceId == res_id:
            return b.length
    return None


def _shader_var_to_dict(v):
    """Recursively convert a ShaderVariable to a JSON-friendly dict."""
    if len(v.members) > 0:
        return {
            "name": v.name,
            "members": [_shader_var_to_dict(m) for m in v.members],
        }

    rows = max(v.rows, 1)
    cols = max(v.columns, 1)

    var_type = v.type
    values = []
    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c
            if var_type == rd.VarType.Float or var_type == rd.VarType.Half:
                values.append(v.value.f32v[idx])
            elif var_type == rd.VarType.Double:
                values.append(v.value.f64v[idx])
            elif var_type in (rd.VarType.SInt, rd.VarType.SShort, rd.VarType.SByte):
                values.append(v.value.s32v[idx])
            elif var_type in (rd.VarType.UInt, rd.VarType.UShort, rd.VarType.UByte, rd.VarType.Bool):
                values.append(v.value.u32v[idx])
            elif var_type == rd.VarType.SLong:
                values.append(v.value.s64v[idx])
            elif var_type == rd.VarType.ULong:
                values.append(v.value.u64v[idx])
            else:
                values.append(v.value.f32v[idx])

    if len(values) == 1:
        values = values[0]

    return {
        "name": v.name,
        "type": str(var_type),
        "rows": rows,
        "columns": cols,
        "value": values,
    }


def _vartype_str(vartype):
    """Human-readable name for a VarType enum."""
    _map = {
        rd.VarType.Float:  "float",
        rd.VarType.Half:   "half",
        rd.VarType.Double: "double",
        rd.VarType.SInt:   "int",
        rd.VarType.UInt:   "uint",
        rd.VarType.SShort: "short",
        rd.VarType.UShort: "ushort",
        rd.VarType.SByte:  "sbyte",
        rd.VarType.UByte:  "ubyte",
        rd.VarType.SLong:  "int64",
        rd.VarType.ULong:  "uint64",
        rd.VarType.Bool:   "bool",
    }
    return _map.get(vartype, str(vartype))


def _constant_to_layout(const):
    """Recursively convert a ShaderConstant into a JSON-friendly layout dict."""
    ctype = const.type
    entry = {"name": const.name, "byteOffset": const.byteOffset}

    if len(ctype.members) > 0:
        entry["members"] = [_constant_to_layout(m) for m in ctype.members]
        if ctype.elements > 1:
            entry["arrayCount"] = ctype.elements
        if ctype.arrayByteStride > 0:
            entry["arrayByteStride"] = ctype.arrayByteStride
    else:
        rows = max(ctype.rows, 1)
        cols = max(ctype.columns, 1)
        base = _vartype_str(ctype.baseType)

        if rows == 1 and cols == 1:
            entry["type"] = base
        elif rows == 1:
            entry["type"] = "%s%d" % (base, cols)
        else:
            entry["type"] = "%s%dx%d" % (base, cols, rows)

        if ctype.elements > 1:
            entry["arrayCount"] = ctype.elements
        if ctype.arrayByteStride > 0:
            entry["arrayByteStride"] = ctype.arrayByteStride

    return entry


def _resource_layout(shader_res):
    """Extract the struct layout from a ShaderResource's variableType."""
    var_type = shader_res.variableType
    if var_type is None or len(var_type.members) == 0:
        return None

    layout = {
        "members": [_constant_to_layout(m) for m in var_type.members],
    }
    if var_type.arrayByteStride > 0:
        layout["arrayByteStride"] = var_type.arrayByteStride

    return layout


def _describe_descriptor_type(desc):
    """Turn a Descriptor's type into a human-readable string."""
    try:
        return str(desc.type)
    except Exception:
        return "Unknown"


def _serialize_stencil_face(face):
    """Serialize a StencilFace into a dict."""
    return {
        "function": str(face.function),
        "passOperation": str(face.passOperation),
        "failOperation": str(face.failOperation),
        "depthFailOperation": str(face.depthFailOperation),
        "compareMask": face.compareMask,
        "writeMask": face.writeMask,
        "reference": face.reference,
    }


def _serialize_color_blend(blend):
    """Serialize a ColorBlend into a dict."""
    result = {
        "enabled": blend.enabled,
        "writeMask": blend.writeMask,
    }
    if blend.enabled:
        result["colorBlend"] = {
            "source": str(blend.colorBlend.source),
            "destination": str(blend.colorBlend.destination),
            "operation": str(blend.colorBlend.operation),
        }
        result["alphaBlend"] = {
            "source": str(blend.alphaBlend.source),
            "destination": str(blend.alphaBlend.destination),
            "operation": str(blend.alphaBlend.operation),
        }
    return result


def _get_texture_info(res_id, controller):
    """Look up width/height/format for a texture resource."""
    try:
        for t in controller.GetTextures():
            if t.resourceId == res_id:
                info = {
                    "width": t.width,
                    "height": t.height,
                }
                try:
                    info["format"] = str(t.format.Name())
                except Exception:
                    pass
                return info
    except Exception:
        pass
    return None


def _raise():
    """Helper to raise inside lambdas."""
    raise RuntimeError("skip")


def _safe_str(val):
    """Convert a value to a JSON-friendly representation."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val
    return str(val)


def _try_introspect_depth(state, vk, depth_state):
    """Introspect PipeState and VK objects to discover depth-related attributes."""
    for name in dir(state):
        name_lower = name.lower()
        if "depth" not in name_lower:
            continue
        if name.startswith("_"):
            continue
        try:
            attr = getattr(state, name)
            if callable(attr):
                val = attr()
            else:
                val = attr
            depth_state["pipeState." + name] = _safe_str(val)
        except Exception:
            continue

    if vk is not None:
        for ds_attr in ["depthStencil", "depthState", "depth"]:
            try:
                ds = getattr(vk, ds_attr)
            except Exception:
                continue
            for name in dir(ds):
                if name.startswith("_"):
                    continue
                try:
                    val = getattr(ds, name)
                    if not callable(val):
                        depth_state["vk.%s.%s" % (ds_attr, name)] = _safe_str(val)
                except Exception:
                    continue
            break


def _get_fragment_state(state, controller):
    """Collect render target, depth, stencil, and blend state."""
    frag = {}

    vk = None
    try:
        vk = controller.GetVulkanPipelineState()
    except Exception:
        pass

    # Render Targets
    rt_list = []
    try:
        output_targets = state.GetOutputTargets()
        for i, desc in enumerate(output_targets):
            if desc.resource == rd.ResourceId.Null():
                continue
            res_name = _resource_name(desc.resource, controller)
            rt_entry = {
                "index": i,
                "resource": res_name,
                "resourceId": str(desc.resource),
            }
            tex_info = _get_texture_info(desc.resource, controller)
            if tex_info:
                rt_entry.update(tex_info)
            rt_list.append(rt_entry)
    except Exception:
        pass

    if not rt_list and vk is not None:
        try:
            fb = vk.currentPass.framebuffer
            for i, att in enumerate(fb.attachments):
                if att.imageResourceId == rd.ResourceId.Null():
                    continue
                res_name = _resource_name(att.imageResourceId, controller)
                rt_entry = {
                    "index": i,
                    "resource": res_name,
                    "resourceId": str(att.imageResourceId),
                }
                tex_info = _get_texture_info(att.imageResourceId, controller)
                if tex_info:
                    rt_entry.update(tex_info)
                name_lower = res_name.lower()
                if "swapchain" in name_lower or "wsi" in name_lower:
                    rt_entry["isBackbuffer"] = True
                rt_list.append(rt_entry)
        except Exception:
            pass

    if not rt_list:
        try:
            for t in controller.GetTextures():
                if t.creationFlags & rd.TextureCategory.SwapBuffer:
                    res_name = _resource_name(t.resourceId, controller)
                    rt_list.append({
                        "index": 0,
                        "resource": res_name,
                        "resourceId": str(t.resourceId),
                        "width": t.width,
                        "height": t.height,
                        "format": str(t.format.Name()),
                        "isBackbuffer": True,
                    })
                    break
        except Exception:
            pass

    if rt_list:
        frag["renderTargets"] = rt_list

    # Depth Target
    try:
        depth_desc = state.GetDepthTarget()
        if depth_desc.resource != rd.ResourceId.Null():
            res_name = _resource_name(depth_desc.resource, controller)
            depth_entry = {
                "resource": res_name,
                "resourceId": str(depth_desc.resource),
            }
            tex_info = _get_texture_info(depth_desc.resource, controller)
            if tex_info:
                depth_entry.update(tex_info)
            frag["depthTarget"] = depth_entry
    except Exception:
        pass

    # Depth State
    depth_state = {}

    for getter in [
        lambda: state.IsDepthTestEnabled(),
        lambda: vk.depthStencil.depthTestEnable if vk else _raise(),
        lambda: vk.depthStencil.depthEnable if vk else _raise(),
    ]:
        try:
            depth_state["depthTestEnable"] = bool(getter())
            break
        except Exception:
            continue

    for getter in [
        lambda: state.IsDepthWriteEnabled(),
        lambda: vk.depthStencil.depthWriteEnable if vk else _raise(),
        lambda: vk.depthStencil.writeEnable if vk else _raise(),
    ]:
        try:
            depth_state["depthWriteEnable"] = bool(getter())
            break
        except Exception:
            continue

    for getter in [
        lambda: str(state.GetDepthFunction()),
        lambda: str(vk.depthStencil.depthCompareOp) if vk else _raise(),
        lambda: str(vk.depthStencil.depthFunction) if vk else _raise(),
        lambda: str(vk.depthStencil.func) if vk else _raise(),
    ]:
        try:
            depth_state["depthFunction"] = getter()
            break
        except Exception:
            continue

    for getter in [
        lambda: state.IsDepthBoundsEnabled(),
        lambda: vk.depthStencil.depthBoundsEnable if vk else _raise(),
    ]:
        try:
            depth_state["depthBoundsEnable"] = bool(getter())
            break
        except Exception:
            continue

    if not depth_state:
        _try_introspect_depth(state, vk, depth_state)

    if depth_state:
        frag["depthState"] = depth_state

    # Stencil State
    stencil_state = {}

    for getter in [
        lambda: state.IsStencilTestEnabled(),
        lambda: vk.depthStencil.stencilTestEnable if vk else _raise(),
    ]:
        try:
            stencil_state["stencilTestEnable"] = bool(getter())
            break
        except Exception:
            continue

    if stencil_state.get("stencilTestEnable", False):
        try:
            stencil_state["frontFace"] = _serialize_stencil_face(state.GetStencilFace(True))
            stencil_state["backFace"] = _serialize_stencil_face(state.GetStencilFace(False))
        except Exception:
            if vk is not None:
                try:
                    ds = vk.depthStencil
                    def _vk_stencil_face(face):
                        result = {}
                        for attr in ["failOp", "failOperation", "depthFailOp", "depthFailOperation",
                                     "passOp", "passOperation", "compareOp", "function",
                                     "compareMask", "writeMask", "reference"]:
                            try:
                                result[attr] = str(getattr(face, attr))
                            except Exception:
                                continue
                        return result
                    stencil_state["frontFace"] = _vk_stencil_face(ds.frontFace)
                    stencil_state["backFace"] = _vk_stencil_face(ds.backFace)
                except Exception:
                    pass

    if stencil_state:
        frag["stencilState"] = stencil_state

    # Blend State
    try:
        blends = state.GetColorBlends()
        blend_list = []
        for i, b in enumerate(blends):
            entry = _serialize_color_blend(b)
            entry["index"] = i
            blend_list.append(entry)
        blend_state = {"attachments": blend_list}
        try:
            bf = state.GetBlendFactor()
            blend_state["blendFactor"] = [bf[0], bf[1], bf[2], bf[3]]
        except Exception:
            pass
        try:
            blend_state["logicOpEnabled"] = state.IsLogicOpEnabled()
            if state.IsLogicOpEnabled():
                blend_state["logicOp"] = str(state.GetLogicOp())
        except Exception:
            pass
        frag["blendState"] = blend_state
    except Exception:
        pass

    return frag


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def run_on_controller(controller, event_id):
    """Core logic to extract pipeline state."""
    controller.SetFrameEvent(event_id, False)
    state = controller.GetPipelineState()

    stages_to_check = [
        ("Vertex",    rd.ShaderStage.Vertex),
        ("TCS",       rd.ShaderStage.Tess_Control),
        ("TES",       rd.ShaderStage.Tess_Eval),
        ("Geometry",  rd.ShaderStage.Geometry),
        ("Fragment",  rd.ShaderStage.Fragment),
        ("Compute",   rd.ShaderStage.Compute),
    ]

    is_compute = state.GetShaderReflection(rd.ShaderStage.Compute) is not None
    if is_compute:
        pipe_obj = state.GetComputePipelineObject()
    else:
        pipe_obj = state.GetGraphicsPipelineObject()

    pipe_name = _resource_name(pipe_obj, controller)

    result = {
        "pipeline": pipe_name,
        "event_id": event_id,
        "is_compute": is_compute,
        "stages": [],
        "resources": [],
        "uniforms": [],
        "samplers": [],
    }

    # --- Stages ---
    for stage_name, stage in stages_to_check:
        refl = state.GetShaderReflection(stage)
        if refl is None:
            continue
        entry = state.GetShaderEntryPoint(stage)
        shader_name = _resource_name(refl.resourceId, controller)
        stage_entry = {
            "stage": stage_name,
            "shader": shader_name,
            "entryPoint": entry,
        }

        # Vertex stage: attach vertex/index buffers
        if stage == rd.ShaderStage.Vertex and not is_compute:
            ib = state.GetIBuffer()
            vbs = state.GetVBuffers()
            attrs = state.GetVertexInputs()

            if ib.resourceId != rd.ResourceId.Null():
                ib_name = _resource_name(ib.resourceId, controller)
                ib_entry = {
                    "resource": ib_name,
                    "resourceId": str(ib.resourceId),
                    "byteOffset": ib.byteOffset,
                    "byteStride": ib.byteStride,
                }
                buf_size = _buf_size_for_resource(ib.resourceId, controller)
                if buf_size is not None:
                    ib_entry["contents"] = "%d bytes" % buf_size
                stage_entry["indexBuffer"] = ib_entry

            attrs_by_vb = defaultdict(list)
            for attr in attrs:
                attrs_by_vb[attr.vertexBuffer].append(attr)

            vb_list = []
            for vb_idx, vb in enumerate(vbs):
                if vb.resourceId == rd.ResourceId.Null():
                    continue

                vb_name = _resource_name(vb.resourceId, controller)
                vb_entry = {
                    "bindingIndex": vb_idx,
                    "resource": vb_name,
                    "resourceId": str(vb.resourceId),
                    "byteOffset": vb.byteOffset,
                    "byteStride": vb.byteStride,
                }

                buf_size = _buf_size_for_resource(vb.resourceId, controller)
                if buf_size is not None:
                    vb_entry["contents"] = "%d bytes" % buf_size

                vb_attrs = attrs_by_vb.get(vb_idx, [])
                if vb_attrs:
                    vb_entry["attributes"] = []
                    for attr in vb_attrs:
                        attr_entry = {
                            "name": attr.name,
                            "byteOffset": attr.byteOffset,
                            "perInstance": attr.perInstance,
                            "format": {
                                "compType": str(attr.format.compType),
                                "compCount": attr.format.compCount,
                                "compByteWidth": attr.format.compByteWidth,
                            },
                        }
                        if attr.perInstance and attr.instanceRate > 0:
                            attr_entry["instanceRate"] = attr.instanceRate
                        if attr.genericEnabled:
                            attr_entry["genericEnabled"] = True
                        vb_entry["attributes"].append(attr_entry)

                vb_list.append(vb_entry)

            if vb_list:
                stage_entry["vertexBuffers"] = vb_list

        # Fragment stage: attach render targets and fixed-function state
        if stage == rd.ShaderStage.Fragment and not is_compute:
            stage_entry.update(_get_fragment_state(state, controller))

        result["stages"].append(stage_entry)

    # --- Resources (RO + RW) ---
    for stage_name, stage in stages_to_check:
        refl = state.GetShaderReflection(stage)
        if refl is None:
            continue

        # Read-only resources
        ro_list = state.GetReadOnlyResources(stage)
        for i, used in enumerate(ro_list):
            res_refl = refl.readOnlyResources[i] if i < len(refl.readOnlyResources) else None
            desc = used.descriptor

            binding_name = res_refl.name if res_refl else "unknown"
            set_num = res_refl.fixedBindSetOrSpace if res_refl else -1
            bind_num = res_refl.fixedBindNumber if res_refl else -1
            res_name = _resource_name(desc.resource, controller)
            desc_type = _describe_descriptor_type(desc)

            entry = {
                "stage": stage_name,
                "set": set_num,
                "binding": bind_num,
                "name": binding_name,
                "access": "ReadOnly",
                "type": desc_type,
                "resource": res_name,
                "resourceId": str(desc.resource),
            }

            buf_size = _buf_size_for_resource(desc.resource, controller)
            if buf_size is not None:
                entry["contents"] = "%d bytes" % buf_size

            if res_refl is not None:
                layout = _resource_layout(res_refl)
                if layout is not None:
                    entry["layout"] = layout

            result["resources"].append(entry)

        # Read-write resources
        rw_list = state.GetReadWriteResources(stage)
        for i, used in enumerate(rw_list):
            res_refl = refl.readWriteResources[i] if i < len(refl.readWriteResources) else None
            desc = used.descriptor

            binding_name = res_refl.name if res_refl else "unknown"
            set_num = res_refl.fixedBindSetOrSpace if res_refl else -1
            bind_num = res_refl.fixedBindNumber if res_refl else -1
            res_name = _resource_name(desc.resource, controller)
            desc_type = _describe_descriptor_type(desc)

            entry = {
                "stage": stage_name,
                "set": set_num,
                "binding": bind_num,
                "name": binding_name,
                "access": "ReadWrite",
                "type": desc_type,
                "resource": res_name,
                "resourceId": str(desc.resource),
            }

            buf_size = _buf_size_for_resource(desc.resource, controller)
            if buf_size is not None:
                entry["contents"] = "%d bytes" % buf_size

            if res_refl is not None:
                layout = _resource_layout(res_refl)
                if layout is not None:
                    entry["layout"] = layout

            result["resources"].append(entry)

    # --- Uniforms / Constant Buffers ---
    for stage_name, stage in stages_to_check:
        refl = state.GetShaderReflection(stage)
        if refl is None:
            continue

        entry_point = state.GetShaderEntryPoint(stage)

        for cb_idx, cb_refl in enumerate(refl.constantBlocks):
            try:
                cb = state.GetConstantBlock(stage, cb_idx, 0)
            except Exception:
                continue

            cb_resource = cb.descriptor.resource
            if cb_resource == rd.ResourceId.Null():
                continue

            res_name = _resource_name(cb_resource, controller)

            variables = []
            var_count = 0
            try:
                var_list = controller.GetCBufferVariableContents(
                    pipe_obj, refl.resourceId, stage, entry_point,
                    cb_idx, cb_resource, 0, 0
                )
                var_count = len(var_list)
                variables = [_shader_var_to_dict(v) for v in var_list]
            except Exception as e:
                variables = [{"error": str(e)}]

            buf_size = _buf_size_for_resource(cb_resource, controller)

            uniform_entry = {
                "stage": stage_name,
                "set": cb_refl.fixedBindSetOrSpace,
                "binding": cb_refl.fixedBindNumber,
                "name": cb_refl.name,
                "resource": res_name,
                "resourceId": str(cb_resource),
                "variableCount": var_count,
                "variables": variables,
            }
            if buf_size is not None:
                uniform_entry["contents"] = "%d bytes" % buf_size

            result["uniforms"].append(uniform_entry)

    # --- Samplers ---
    for stage_name, stage in stages_to_check:
        refl = state.GetShaderReflection(stage)
        if refl is None:
            continue

        sampler_list = state.GetSamplers(stage)
        for i, used in enumerate(sampler_list):
            samp_refl = refl.samplers[i] if i < len(refl.samplers) else None
            samp_name = samp_refl.name if samp_refl else "unknown"
            set_num = samp_refl.fixedBindSetOrSpace if samp_refl else -1
            bind_num = samp_refl.fixedBindNumber if samp_refl else -1

            result["samplers"].append({
                "stage": stage_name,
                "set": set_num,
                "binding": bind_num,
                "name": samp_name,
            })

    return result


def main() -> None:
    with open(REQ_PATH, "r", encoding="utf-8") as f:
        req = json.load(f)

    event_id = req["event_id"]

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
            document = run_on_controller(controller, event_id)
            document["capture_path"] = req["capture_path"]
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
