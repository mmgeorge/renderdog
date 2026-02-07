"""
get_buffer_details_json.py -- RenderDoc Python script for getting buffer metadata.

Finds a buffer by name, infers the struct layout from shader reflection,
and returns schema, stride, and usage information.

Returns:
  - buffer_name: The name of the buffer
  - schema: Type description of the buffer struct fields
  - stride: Byte stride per element
  - usages: List of pipelines/bindings that use this buffer
"""

import struct
import json
import traceback

import renderdoc as rd


REQ_PATH = "get_buffer_details_json.request.json"
RESP_PATH = "get_buffer_details_json.response.json"


def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Struct layout inference from shader reflection
# ---------------------------------------------------------------------------

def vartype_to_struct_char(vartype):
    """Map a renderdoc VarType enum to a Python struct format character."""
    mapping = {
        rd.VarType.Float:  'f',
        rd.VarType.Half:   'e',
        rd.VarType.Double: 'd',
        rd.VarType.SInt:   'i',
        rd.VarType.UInt:   'I',
        rd.VarType.SShort: 'h',
        rd.VarType.UShort: 'H',
        rd.VarType.SByte:  'b',
        rd.VarType.UByte:  'B',
        rd.VarType.SLong:  'q',
        rd.VarType.ULong:  'Q',
        rd.VarType.Bool:   'I',
    }
    return mapping.get(vartype, None)


def vartype_byte_size(vartype):
    """Byte size of a single scalar for the given VarType."""
    sizes = {
        rd.VarType.Float:  4,
        rd.VarType.Half:   2,
        rd.VarType.Double: 8,
        rd.VarType.SInt:   4,
        rd.VarType.UInt:   4,
        rd.VarType.SShort: 2,
        rd.VarType.UShort: 2,
        rd.VarType.SByte:  1,
        rd.VarType.UByte:  1,
        rd.VarType.SLong:  8,
        rd.VarType.ULong:  8,
        rd.VarType.Bool:   4,
    }
    return sizes.get(vartype, 4)


def vartype_to_name(vartype):
    """Map a renderdoc VarType to a human-readable GLSL-style type name."""
    names = {
        rd.VarType.Float:  'float',
        rd.VarType.Half:   'half',
        rd.VarType.Double: 'double',
        rd.VarType.SInt:   'int',
        rd.VarType.UInt:   'uint',
        rd.VarType.SShort: 'short',
        rd.VarType.UShort: 'ushort',
        rd.VarType.SByte:  'sbyte',
        rd.VarType.UByte:  'ubyte',
        rd.VarType.SLong:  'int64',
        rd.VarType.ULong:  'uint64',
        rd.VarType.Bool:   'bool',
    }
    return names.get(vartype, 'unknown')


def build_type_schema(members):
    """Build a concise type-description tree from a list of ShaderConstant members."""
    schema = {}
    for const in members:
        ctype = const.type

        if len(ctype.members) > 0:
            inner = build_type_schema(ctype.members)
            arr_count = max(ctype.elements, 1)
            if arr_count > 1:
                schema[const.name] = {"_array": arr_count, "_element": inner}
            else:
                schema[const.name] = inner
            continue

        base_name = vartype_to_name(ctype.baseType)
        rows = max(ctype.rows, 1)
        cols = max(ctype.columns, 1)
        arr_count = max(ctype.elements, 1)

        if rows > 1 and cols > 1:
            core = "%s[%d][%d]" % (base_name, rows, cols)
        elif rows == 1 and cols > 1:
            core = "%s[%d]" % (base_name, cols)
        elif rows > 1 and cols == 1:
            core = "%s[%d]" % (base_name, rows)
        else:
            core = base_name

        if arr_count > 1:
            type_str = "%s[%d]" % (core, arr_count) if core == base_name else "%s x %d" % (core, arr_count)
        else:
            type_str = core

        schema[const.name] = type_str

    return schema


class FieldDef:
    """A single scalar column we'll read from the buffer."""
    __slots__ = ('name', 'byte_offset', 'struct_char')

    def __init__(self, name, byte_offset, struct_char):
        self.name = name
        self.byte_offset = byte_offset
        self.struct_char = struct_char


def flatten_constant_type(prefix, const, base_offset):
    """Recursively flatten a ShaderConstant into a list of FieldDef."""
    ctype = const.type
    abs_offset = base_offset + const.byteOffset
    field_name = ("%s.%s" % (prefix, const.name)) if prefix else const.name

    if len(ctype.members) > 0:
        fields = []
        arr_count = max(ctype.elements, 1)
        for arr_i in range(arr_count):
            arr_prefix = ("%s[%d]" % (field_name, arr_i)) if arr_count > 1 else field_name
            elem_offset = abs_offset + arr_i * ctype.arrayByteStride if arr_count > 1 else abs_offset
            for member in ctype.members:
                fields.extend(flatten_constant_type(arr_prefix, member, elem_offset))
        return fields

    char = vartype_to_struct_char(ctype.baseType)
    if char is None:
        return []

    scalar_size = vartype_byte_size(ctype.baseType)
    fields = []

    arr_count = max(ctype.elements, 1)
    rows = max(ctype.rows, 1)
    cols = max(ctype.columns, 1)
    total_scalars = rows * cols

    for arr_i in range(arr_count):
        arr_name = ("%s[%d]" % (field_name, arr_i)) if arr_count > 1 else field_name

        if arr_count > 1:
            elem_base = abs_offset + arr_i * ctype.arrayByteStride
        else:
            elem_base = abs_offset

        if total_scalars == 1:
            fields.append(FieldDef(arr_name, elem_base, char))
        else:
            for r in range(rows):
                for c in range(cols):
                    comp_idx = r * cols + c
                    if rows > 1 and cols > 1:
                        comp_name = "%s[%d][%d]" % (arr_name, r, c)
                    else:
                        comp_name = "%s[%d]" % (arr_name, comp_idx)
                    comp_offset = elem_base + comp_idx * scalar_size
                    fields.append(FieldDef(comp_name, comp_offset, char))

    return fields


def leaves(roots):
    for a in roots:
        if len(a.children) > 0:
            yield from leaves(a.children)
        else:
            yield a


def infer_layout_from_reflection(controller, buf_id):
    """Find a shader that references buf_id and extract the struct layout."""
    actions = list(leaves(controller.GetRootActions()))
    stages_to_check = [
        rd.ShaderStage.Compute,
        rd.ShaderStage.Vertex,
        rd.ShaderStage.Fragment,
        rd.ShaderStage.Geometry,
        rd.ShaderStage.Tess_Eval,
        rd.ShaderStage.Tess_Control,
    ]

    for action in actions:
        controller.SetFrameEvent(action.eventId, False)
        state = controller.GetPipelineState()

        for stage in stages_to_check:
            refl = state.GetShaderReflection(stage)
            if refl is None:
                continue

            rw_list = state.GetReadWriteResources(stage)
            for i, used in enumerate(rw_list):
                if used.descriptor.resource == buf_id:
                    if i < len(refl.readWriteResources):
                        res = refl.readWriteResources[i]
                        return extract_fields_from_resource(res)

            ro_list = state.GetReadOnlyResources(stage)
            for i, used in enumerate(ro_list):
                if used.descriptor.resource == buf_id:
                    if i < len(refl.readOnlyResources):
                        res = refl.readOnlyResources[i]
                        return extract_fields_from_resource(res)

    raise RuntimeError(
        "Could not find any shader that references the target buffer. "
        "Make sure the buffer name is correct and the buffer is used "
        "in at least one dispatch or draw in the capture."
    )


def extract_fields_from_resource(shader_res):
    """Given a ShaderResource from reflection, flatten its variableType into FieldDefs."""
    var_type = shader_res.variableType

    if len(var_type.members) == 0:
        raise RuntimeError(
            "Shader resource '%s' has no struct members in its type. "
            "The buffer may not be a structured buffer." % shader_res.name
        )

    members = var_type.members

    if len(members) == 1 and len(members[0].type.members) > 0:
        inner = members[0]
        members = inner.type.members

    fields = []
    for member in members:
        fields.extend(flatten_constant_type("", member, 0))

    if not fields:
        raise RuntimeError("Failed to extract any fields from the struct layout.")

    last = max(fields, key=lambda f: f.byte_offset)
    stride = last.byte_offset + struct.calcsize(last.struct_char)

    if var_type.arrayByteStride > 0:
        stride = var_type.arrayByteStride
    elif (len(shader_res.variableType.members) == 1
          and shader_res.variableType.members[0].type.arrayByteStride > 0):
        stride = shader_res.variableType.members[0].type.arrayByteStride

    schema = build_type_schema(members)

    return fields, stride, schema


# ---------------------------------------------------------------------------
# Buffer finding
# ---------------------------------------------------------------------------

def find_buffer(controller, buffer_name):
    """Locate the target buffer's ResourceId by name."""
    for res in controller.GetResources():
        if res.name == buffer_name:
            return res.resourceId

    available = []
    for r in controller.GetResources():
        if r.type == rd.ResourceType.Buffer:
            available.append("  %s  %s" % (r.resourceId, r.name))

    raise RuntimeError(
        "Buffer '%s' not found. Available buffers:\n%s"
        % (buffer_name, "\n".join(available[:20]))
    )


def flatten_actions(roots):
    """Yield every leaf action in linear order."""
    for action in roots:
        if len(action.children) > 0:
            yield from flatten_actions(action.children)
        else:
            yield action


# ---------------------------------------------------------------------------
# Buffer usage collection
# ---------------------------------------------------------------------------

def collect_buffer_usages(controller, buf_id, actions):
    """Scan every leaf action and record each time the buffer appears in a shader binding."""
    stages = [
        rd.ShaderStage.Compute,
        rd.ShaderStage.Vertex,
        rd.ShaderStage.Fragment,
        rd.ShaderStage.Geometry,
        rd.ShaderStage.Tess_Eval,
        rd.ShaderStage.Tess_Control,
    ]

    def get_name(rid):
        try:
            for r in controller.GetResources():
                if r.resourceId == rid:
                    return r.name
        except Exception:
            pass
        return str(rid)

    def binding_type_str(refl_res, is_rw):
        try:
            vtype = refl_res.variableType
            is_buffer = (vtype.rows == 0 and vtype.columns == 0) or len(vtype.members) > 0
        except Exception:
            is_buffer = True
        prefix = "RW " if is_rw else ""
        return prefix + "Buffer" if is_buffer else prefix + "Resource"

    groups = {}

    for action in actions:
        eid = action.eventId
        controller.SetFrameEvent(eid, False)
        state = controller.GetPipelineState()

        try:
            pipe_id = state.GetGraphicsPipelineObject()
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

        for stage in stages:
            refl = state.GetShaderReflection(stage)
            if refl is None:
                continue

            rw_list = state.GetReadWriteResources(stage)
            for i, used in enumerate(rw_list):
                if used.descriptor.resource == buf_id:
                    record_usage(groups, eid, pipe_id, used, refl, i, True,
                                refl.readWriteResources, get_name, binding_type_str)

            ro_list = state.GetReadOnlyResources(stage)
            for i, used in enumerate(ro_list):
                if used.descriptor.resource == buf_id:
                    record_usage(groups, eid, pipe_id, used, refl, i, False,
                                refl.readOnlyResources, get_name, binding_type_str)

    result = []
    for key, g in sorted(groups.items(), key=lambda kv: kv[1]["event_ids"][0]):
        result.append({
            "pipeline": g["pipeline"],
            "descriptor_set": g["descriptor_set"],
            "binding": {
                "index": g["binding_index"],
                "name": g["binding_name"],
                "type": g["type_str"],
            },
            "event_ids": g["event_ids"],
        })

    return result


def record_usage(groups, eid, pipe_id, used, refl, refl_idx, is_rw,
                 refl_list, get_name, binding_type_str):
    """Record a single buffer usage into the groups accumulator."""
    try:
        ds_id = used.access.descriptorStore
    except Exception:
        ds_id = rd.ResourceId.Null()

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

    type_str = binding_type_str(refl_res, is_rw) if refl_res else ("RW Buffer" if is_rw else "Buffer")

    pipe_name = get_name(pipe_id) if pipe_id != rd.ResourceId.Null() else ""
    ds_name = get_name(ds_id) if ds_id != rd.ResourceId.Null() else ""
    key = (pipe_name, ds_name, bindex, is_rw)

    if key not in groups:
        groups[key] = {
            "pipeline": pipe_name,
            "descriptor_set": ds_name,
            "binding_name": bname,
            "binding_index": bindex,
            "type_str": type_str,
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

    buffer_name = req["buffer_name"]

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
            buf_id = find_buffer(controller, buffer_name)

            # Infer struct layout from shader reflection
            fields, stride, schema = infer_layout_from_reflection(controller, buf_id)

            # Scan all actions
            actions = list(flatten_actions(controller.GetRootActions()))
            if not actions:
                raise RuntimeError("No actions found in capture")

            # Collect buffer usage across all actions
            usages = collect_buffer_usages(controller, buf_id, actions)

            # Build final document
            document = {
                "buffer_name": buffer_name,
                "schema": schema,
                "stride": stride,
                "usages": usages,
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
