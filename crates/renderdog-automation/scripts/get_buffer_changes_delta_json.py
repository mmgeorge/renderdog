"""
get_buffer_changes_delta_json.py -- RenderDoc Python script for tracking GPU buffer changes.

Finds a buffer by name, automatically infers the struct layout from the shader that
references the buffer, reads struct-formatted data at specified element indices at
every action in the frame, and returns only the snapshots where a value actually changed.

Uses delta encoding: initial_state is the full state, changes contain only diffs.
"""

import struct
import json
import re
import traceback

import renderdoc as rd


REQ_PATH = "get_buffer_changes_delta_json.request.json"
RESP_PATH = "get_buffer_changes_delta_json.response.json"


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
# Structured JSON reconstruction from flat field paths
# ---------------------------------------------------------------------------

PATH_TOKEN_RE = re.compile(
    r'([A-Za-z_]\w*)'
    r'|\[(\d+)\]'
)


def parse_field_path(name):
    """Parse a flat field name into a list of path steps."""
    steps = []
    for part in name.split('.'):
        tokens = PATH_TOKEN_RE.findall(part)
        for tok_name, tok_idx in tokens:
            if tok_name:
                steps.append((tok_name, None))
            else:
                steps.append((None, int(tok_idx)))
    return steps


def build_nested(fields, values):
    """Given a list of FieldDef and values, reconstruct a nested dict/list structure."""
    root = {}

    for field, val in zip(fields, values):
        steps = parse_field_path(field.name)
        insert_at_path(root, steps, val)

    clean(root)
    return root


def insert_at_path(node, steps, value):
    """Walk/create the nested structure described by steps, set the leaf to value."""
    for i, (key, idx) in enumerate(steps):
        is_last = (i == len(steps) - 1)

        if key is not None and idx is None:
            if is_last:
                node[key] = value
            else:
                next_key, next_idx = steps[i + 1]
                if next_key is None and next_idx is not None:
                    if key not in node:
                        node[key] = []
                    node = node[key]
                else:
                    if key not in node:
                        node[key] = {}
                    node = node[key]

        elif key is not None and idx is not None:
            if key not in node:
                node[key] = []
            lst = node[key]
            while len(lst) <= idx:
                lst.append(None)

            if is_last:
                lst[idx] = value
            else:
                next_key, next_idx = steps[i + 1]
                if next_key is None and next_idx is not None:
                    if lst[idx] is None:
                        lst[idx] = []
                    node = lst[idx]
                else:
                    if lst[idx] is None:
                        lst[idx] = {}
                    node = lst[idx]

        elif key is None and idx is not None:
            if not isinstance(node, list):
                break
            while len(node) <= idx:
                node.append(None)

            if is_last:
                node[idx] = value
            else:
                next_key, next_idx = steps[i + 1]
                if next_key is None and next_idx is not None:
                    if node[idx] is None:
                        node[idx] = []
                    node = node[idx]
                else:
                    if node[idx] is None:
                        node[idx] = {}
                    node = node[idx]


def clean(obj):
    """Replace any remaining None holes with 0 and recurse."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if v is None:
                obj[k] = 0
            else:
                clean(v)
    elif isinstance(obj, list):
        for i in range(len(obj)):
            if obj[i] is None:
                obj[i] = 0
            else:
                clean(obj[i])


# ---------------------------------------------------------------------------
# Recursive diff for nested structures
# ---------------------------------------------------------------------------

def diff_nested(old, new):
    """Recursively compare two nested structures and return a sparse patch."""
    if type(old) != type(new):
        return new

    if isinstance(old, dict):
        patch = {}
        all_keys = set(old.keys()) | set(new.keys())
        for k in all_keys:
            if k not in old:
                patch[k] = new[k]
            elif k not in new:
                patch[k] = None
            else:
                sub = diff_nested(old[k], new[k])
                if sub is not None:
                    patch[k] = sub
        return patch if patch else None

    if isinstance(old, list):
        if len(old) != len(new):
            return new
        patch = {}
        for i in range(len(old)):
            sub = diff_nested(old[i], new[i])
            if sub is not None:
                patch[str(i)] = sub
        return patch if patch else None

    if old != new:
        return new
    return None


# ---------------------------------------------------------------------------
# Buffer finding and reading
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


def read_elements(controller, buf_id, indices, fields, stride):
    """Read tracked element indices from the buffer at the current replay state."""
    result = {}
    for idx in indices:
        base = idx * stride
        raw = controller.GetBufferData(buf_id, base, stride)
        if len(raw) < stride:
            continue
        vals = []
        for f in fields:
            val = struct.unpack_from(f.struct_char, raw, f.byte_offset)
            vals.append(val[0])
        result[idx] = tuple(vals)
    return result


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
    tracked_indices = req.get("tracked_indices", [0])

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

            # Track data changes
            element_initial = {}
            element_changes = {idx: [] for idx in tracked_indices}
            last_nested = {}
            last_seen = {}
            total_changes = 0

            for action in actions:
                eid = action.eventId
                controller.SetFrameEvent(eid, False)
                current = read_elements(controller, buf_id, tracked_indices, fields, stride)

                for idx in tracked_indices:
                    vals = current.get(idx)
                    if vals is None:
                        continue
                    prev = last_seen.get(idx)
                    if prev is None or vals != prev:
                        nested = build_nested(fields, vals)

                        if idx not in element_initial:
                            element_initial[idx] = (eid, nested)
                        else:
                            delta = diff_nested(last_nested[idx], nested)
                            if delta is not None:
                                element_changes[idx].append({
                                    "event_id": eid,
                                    "delta": delta,
                                })
                                total_changes += 1

                        last_nested[idx] = nested
                        last_seen[idx] = vals

            # Build elements array
            elements = []
            for idx in tracked_indices:
                if idx not in element_initial:
                    continue
                init_eid, init_state = element_initial[idx]
                elements.append({
                    "buffer_index": idx,
                    "initial_event_id": init_eid,
                    "initial_state": init_state,
                    "changes": element_changes[idx],
                })

            # Build final document
            document = {
                "capture_path": req["capture_path"],
                "buffer_name": buffer_name,
                "schema": schema,
                "stride": stride,
                "usages": usages,
                "tracked_indices": list(tracked_indices),
                "total_changes": total_changes,
                "elements": elements,
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
