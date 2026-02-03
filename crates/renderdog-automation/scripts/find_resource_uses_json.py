"""
find_resource_uses_json.py -- RenderDoc Python script that finds all uses of a resource.

Given a resource name or ID, finds all places where the resource is used in the capture
and returns details about each use including:

  - event_id: The event where the resource is used
  - usage: How the resource is used (e.g., VertexBuffer, ColorTarget, PS_Resource, etc.)
  - is_write: Whether the resource was modified at this event (see below)
  - pipeline_name: The name of the pipeline at this event (if applicable)
  - stage: The shader stage (for shader resources)
  - binding: Binding information (set, binding) if available
  - entry_point: For shaders, the entry point name

Request parameters:
  - resource: Resource name or ID to find (required)
  - capture_path: Path to the RenderDoc capture file (required)
  - max_results: Maximum number of uses to return (default 500)
  - data_sample_bytes: Max bytes to read when comparing data (default 64KB).
                       Set to 0 to read entire resource.
  - max_changed_elements: Max number of changed buffer elements to report (default 3).

is_write field:
  - Compares actual binary data at each event with the previous state
  - is_write=true only when bytes actually differ from previous read
  - is_write=null for the first event (no previous state to compare)
  - For buffers with shader reflection, shows semantic field-level diffs

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
import re
import struct
import traceback

import renderdoc as rd


REQ_PATH = "find_resource_uses_json.request.json"
RESP_PATH = "find_resource_uses_json.response.json"


# ---------------------------------------------------------------------------
# Struct layout inference from shader reflection
# ---------------------------------------------------------------------------

def _vartype_to_struct_char(vartype):
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


def _vartype_byte_size(vartype):
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


class FieldDef:
    """A single scalar field we'll read from the buffer."""
    __slots__ = ('name', 'byte_offset', 'struct_char')

    def __init__(self, name, byte_offset, struct_char):
        self.name = name
        self.byte_offset = byte_offset
        self.struct_char = struct_char


def _flatten_constant_type(prefix, const, base_offset):
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
                fields.extend(_flatten_constant_type(arr_prefix, member, elem_offset))
        return fields

    char = _vartype_to_struct_char(ctype.baseType)
    if char is None:
        return []

    scalar_size = _vartype_byte_size(ctype.baseType)
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


def _extract_fields_from_resource(shader_res):
    """
    Given a ShaderResource from reflection, flatten its variableType into
    FieldDefs and compute the struct stride.
    Returns (fields, stride) or (None, None) if no struct layout.
    """
    var_type = shader_res.variableType

    if len(var_type.members) == 0:
        return None, None

    members = var_type.members

    # Unwrap single wrapper struct if present
    if len(members) == 1 and len(members[0].type.members) > 0:
        members = members[0].type.members

    fields = []
    for member in members:
        fields.extend(_flatten_constant_type("", member, 0))

    if not fields:
        return None, None

    # Compute stride
    last = max(fields, key=lambda f: f.byte_offset)
    stride = last.byte_offset + struct.calcsize(last.struct_char)

    if var_type.arrayByteStride > 0:
        stride = var_type.arrayByteStride
    elif (len(shader_res.variableType.members) == 1
          and shader_res.variableType.members[0].type.arrayByteStride > 0):
        stride = shader_res.variableType.members[0].type.arrayByteStride

    return fields, stride


def infer_buffer_layout(controller, buf_id):
    """
    Find a shader that references the buffer and extract struct layout.
    Returns (fields, stride) or (None, None) if not found.
    """
    def _leaves(roots):
        for a in roots:
            if len(a.children) > 0:
                yield from _leaves(a.children)
            else:
                yield a

    stages_to_check = [
        rd.ShaderStage.Compute,
        rd.ShaderStage.Vertex,
        rd.ShaderStage.Fragment,
        rd.ShaderStage.Geometry,
        rd.ShaderStage.Tess_Eval,
        rd.ShaderStage.Tess_Control,
    ]

    for action in _leaves(controller.GetRootActions()):
        controller.SetFrameEvent(action.eventId, False)
        state = controller.GetPipelineState()

        for stage in stages_to_check:
            refl = state.GetShaderReflection(stage)
            if refl is None:
                continue

            # Check RW resources
            rw_list = state.GetReadWriteResources(stage)
            for i, used in enumerate(rw_list):
                if used.descriptor.resource == buf_id:
                    if i < len(refl.readWriteResources):
                        fields, stride = _extract_fields_from_resource(refl.readWriteResources[i])
                        if fields:
                            return fields, stride

            # Check RO resources
            ro_list = state.GetReadOnlyResources(stage)
            for i, used in enumerate(ro_list):
                if used.descriptor.resource == buf_id:
                    if i < len(refl.readOnlyResources):
                        fields, stride = _extract_fields_from_resource(refl.readOnlyResources[i])
                        if fields:
                            return fields, stride

    return None, None


# ---------------------------------------------------------------------------
# Nested structure building from flat field paths
# ---------------------------------------------------------------------------

_PATH_TOKEN_RE = re.compile(r'([A-Za-z_]\w*)|\[(\d+)\]')


def _parse_field_path(name):
    """Parse "field.sub[2].x" into [(key, index), ...] steps."""
    steps = []
    for part in name.split('.'):
        tokens = _PATH_TOKEN_RE.findall(part)
        for tok_name, tok_idx in tokens:
            if tok_name:
                steps.append((tok_name, None))
            else:
                steps.append((None, int(tok_idx)))
    return steps


def _insert_at_path(node, steps, value):
    """Walk/create nested structure, set leaf to value."""
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


def _clean(obj):
    """Replace None holes with 0."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if v is None:
                obj[k] = 0
            else:
                _clean(v)
    elif isinstance(obj, list):
        for i in range(len(obj)):
            if obj[i] is None:
                obj[i] = 0
            else:
                _clean(obj[i])


def build_nested_from_fields(fields, raw_data, stride, element_index=0):
    """
    Read one element from raw buffer data and build nested dict structure.
    Returns the nested dict or None if data is too small.
    """
    base = element_index * stride
    if len(raw_data) < base + stride:
        return None

    root = {}
    for field in fields:
        offset = base + field.byte_offset
        if offset + struct.calcsize(field.struct_char) > len(raw_data):
            continue
        val = struct.unpack_from(field.struct_char, raw_data, offset)[0]
        steps = _parse_field_path(field.name)
        _insert_at_path(root, steps, val)

    _clean(root)
    return root


# ---------------------------------------------------------------------------
# Recursive diff for nested structures
# ---------------------------------------------------------------------------

def diff_nested(old, new):
    """
    Compare two nested structures and return sparse patch of changes.
    Returns None if identical.
    """
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

    # Scalar comparison
    if old != new:
        return new
    return None


def find_changed_buffer_elements(fields, stride, old_data, new_data, max_elements=3):
    """
    Scan buffer elements to find which ones changed.
    Returns list of {"element": index, "delta": semantic_diff} for changed elements.
    """
    if not fields or stride <= 0:
        return None

    num_elements = min(len(old_data), len(new_data)) // stride
    changed = []

    for elem_idx in range(num_elements):
        try:
            old_nested = build_nested_from_fields(fields, old_data, stride, elem_idx)
            new_nested = build_nested_from_fields(fields, new_data, stride, elem_idx)

            if old_nested and new_nested:
                delta = diff_nested(old_nested, new_nested)
                if delta:
                    changed.append({
                        "element": elem_idx,
                        "delta": delta,
                    })
                    if len(changed) >= max_elements:
                        break
        except Exception:
            continue

    return changed if changed else None


def find_changed_bytes_region(old_data, new_data, max_regions=3):
    """
    Find byte regions that changed between old and new data.
    Returns list of {"offset": byte_offset, "length": num_bytes, "old": hex, "new": hex}.
    """
    if old_data is None or new_data is None:
        return None

    min_len = min(len(old_data), len(new_data))
    regions = []
    i = 0

    while i < min_len and len(regions) < max_regions:
        if old_data[i] != new_data[i]:
            # Found start of changed region
            start = i
            while i < min_len and old_data[i] != new_data[i]:
                i += 1
            end = i

            # Limit region size for display
            display_len = min(end - start, 16)
            regions.append({
                "offset": start,
                "length": end - start,
                "old_hex": old_data[start:start + display_len].hex(),
                "new_hex": new_data[start:start + display_len].hex(),
            })
        else:
            i += 1

    # Check for length difference
    if len(old_data) != len(new_data) and len(regions) < max_regions:
        regions.append({
            "note": "size_changed",
            "old_size": len(old_data),
            "new_size": len(new_data),
        })

    return regions if regions else None


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

# Usages that write/modify the resource
_WRITE_USAGES = {
    # Render targets
    "ColorTarget",
    "DepthStencilTarget",
    # RW resources (storage buffers/images) - these CAN write
    "VS_RWResource",
    "HS_RWResource",
    "DS_RWResource",
    "GS_RWResource",
    "PS_RWResource",
    "CS_RWResource",
    "TS_RWResource",
    "MS_RWResource",
    "All_RWResource",
    # Operations that modify
    "Clear",
    "CopyDst",
    "ResolveDst",
    "GenMips",
    "Discard",
    # Stream output
    "StreamOut",
    # CPU writes
    "CPUWrite",
}

# Usages that only read the resource
_READ_USAGES = {
    "VertexBuffer",
    "IndexBuffer",
    # Constant buffers (read-only)
    "VS_Constants",
    "HS_Constants",
    "DS_Constants",
    "GS_Constants",
    "PS_Constants",
    "CS_Constants",
    "TS_Constants",
    "MS_Constants",
    "All_Constants",
    # Shader resources (textures, samplers - read-only)
    "VS_Resource",
    "HS_Resource",
    "DS_Resource",
    "GS_Resource",
    "PS_Resource",
    "CS_Resource",
    "TS_Resource",
    "MS_Resource",
    "All_Resource",
    # Copy/resolve source
    "CopySrc",
    "ResolveSrc",
    # Input attachment
    "InputTarget",
    # Indirect args (read)
    "Indirect",
}


def is_write_usage(usage_str):
    """Determine if a usage type could potentially modify the resource (heuristic only)."""
    if usage_str in _WRITE_USAGES:
        return True
    if usage_str in _READ_USAGES:
        return False
    # For unknown usages, check if it contains "RW" or known write patterns
    if "RWResource" in usage_str:
        return True
    if usage_str in ("Copy", "Resolve"):
        # Generic copy/resolve could be either - mark as potential write
        return True
    # Default to None (unknown) for things like Barrier, Unused
    return None


NULL_ID = rd.ResourceId.Null()


def read_buffer_data(controller, resource_id, max_bytes=64*1024):
    """
    Read buffer data for comparison.
    Returns (bytes, None) on success, (None, error_string) on failure.
    max_bytes limits how much data to read (0 = all).
    """
    try:
        # Ensure max_bytes is a proper integer (RenderDoc expects uint64_t)
        if max_bytes is None or max_bytes <= 0:
            max_bytes = 0  # 0 means read all
        max_bytes = int(max_bytes)

        data = controller.GetBufferData(resource_id, int(0), max_bytes)
        if data and len(data) > 0:
            return bytes(data), None
        return None, "GetBufferData returned empty"
    except Exception as e:
        return None, "GetBufferData error: %s" % str(e)


def read_texture_data(controller, resource_id, max_bytes=64*1024):
    """
    Read texture data for comparison.
    Returns (bytes, None) on success, (None, error_string) on failure.
    """
    try:
        # Read first mip, first slice
        sub = rd.Subresource(0, 0, 0)  # mip 0, slice 0, sample 0
        data = controller.GetTextureData(resource_id, sub)
        if data and len(data) > 0:
            # Limit data size for comparison
            if max_bytes and len(data) > max_bytes:
                return bytes(data[:max_bytes]), None
            return bytes(data), None
        return None, "GetTextureData returned empty"
    except Exception as e:
        return None, "GetTextureData error: %s" % str(e)


def read_resource_data(controller, resource_desc, max_bytes=64*1024):
    """
    Read resource data (buffer or texture) for comparison.
    Returns (bytes, None) on success, (None, error_string) on failure.
    """
    rtype = str(resource_desc.type).replace("ResourceType.", "")
    if rtype == "Buffer":
        data, err = read_buffer_data(controller, resource_desc.resourceId, max_bytes)
        if err:
            return None, "Buffer read failed: %s (resource_id=%s, max_bytes=%s)" % (err, resource_desc.resourceId, max_bytes)
        return data, None
    elif rtype == "Texture":
        data, err = read_texture_data(controller, resource_desc.resourceId, max_bytes)
        if err:
            return None, "Texture read failed: %s" % err
        return data, None
    else:
        return None, "unsupported resource type: %s" % rtype


def compute_byte_delta(old_data, new_data, max_diffs=8):
    """
    Compare two byte sequences and return a delta showing what changed.

    Returns a dict with:
      - total_bytes_compared: how many bytes were compared
      - bytes_changed: count of bytes that differ
      - first_diffs: list of first N differences, each with {offset, old, new}
      - identical: True if data is identical

    Values are shown as hex strings for readability.
    """
    if old_data is None or new_data is None:
        return None

    min_len = min(len(old_data), len(new_data))
    max_len = max(len(old_data), len(new_data))

    diffs = []
    bytes_changed = 0

    # Compare overlapping portion
    for i in range(min_len):
        if old_data[i] != new_data[i]:
            bytes_changed += 1
            if len(diffs) < max_diffs:
                diffs.append({
                    "offset": i,
                    "old": "0x%02x" % old_data[i],
                    "new": "0x%02x" % new_data[i],
                })

    # Account for length difference
    if len(old_data) != len(new_data):
        bytes_changed += abs(len(old_data) - len(new_data))
        if len(diffs) < max_diffs:
            if len(new_data) > len(old_data):
                # New data has extra bytes
                for i in range(len(old_data), min(len(new_data), len(old_data) + max_diffs - len(diffs))):
                    diffs.append({
                        "offset": i,
                        "old": "N/A",
                        "new": "0x%02x" % new_data[i],
                    })
            else:
                # Old data had extra bytes (truncated)
                for i in range(len(new_data), min(len(old_data), len(new_data) + max_diffs - len(diffs))):
                    diffs.append({
                        "offset": i,
                        "old": "0x%02x" % old_data[i],
                        "new": "N/A",
                    })

    return {
        "total_bytes_compared": min_len,
        "old_size": len(old_data),
        "new_size": len(new_data),
        "bytes_changed": bytes_changed,
        "identical": bytes_changed == 0,
        "first_diffs": diffs,
    }


def compute_u32_delta(old_data, new_data, max_diffs=8):
    """
    Compare two byte sequences as arrays of u32 values and return a delta.
    More useful for structured buffer data where 4-byte alignment is common.

    Returns a dict with:
      - total_u32s_compared: how many u32 values were compared
      - u32s_changed: count of u32s that differ
      - first_diffs: list of first N differences with {index, offset, old, new}
      - identical: True if data is identical
    """
    import struct

    if old_data is None or new_data is None:
        return None

    # Pad to 4-byte alignment
    def pad_to_4(data):
        remainder = len(data) % 4
        if remainder:
            return data + b'\x00' * (4 - remainder)
        return data

    old_padded = pad_to_4(old_data)
    new_padded = pad_to_4(new_data)

    old_u32s = len(old_padded) // 4
    new_u32s = len(new_padded) // 4
    min_u32s = min(old_u32s, new_u32s)

    diffs = []
    u32s_changed = 0

    for i in range(min_u32s):
        offset = i * 4
        old_val = struct.unpack_from('<I', old_padded, offset)[0]
        new_val = struct.unpack_from('<I', new_padded, offset)[0]

        if old_val != new_val:
            u32s_changed += 1
            if len(diffs) < max_diffs:
                # Also try to interpret as float for debugging
                old_float = struct.unpack_from('<f', old_padded, offset)[0]
                new_float = struct.unpack_from('<f', new_padded, offset)[0]

                diff_entry = {
                    "index": i,
                    "offset": offset,
                    "old_u32": old_val,
                    "new_u32": new_val,
                }

                # Add float interpretation if it looks like a reasonable float
                def is_reasonable_float(f):
                    import math
                    if math.isnan(f) or math.isinf(f):
                        return False
                    return abs(f) < 1e10 and abs(f) > 1e-10 or f == 0.0

                if is_reasonable_float(old_float) or is_reasonable_float(new_float):
                    diff_entry["old_f32"] = round(old_float, 6) if is_reasonable_float(old_float) else None
                    diff_entry["new_f32"] = round(new_float, 6) if is_reasonable_float(new_float) else None

                diffs.append(diff_entry)

    # Account for length difference
    u32s_changed += abs(old_u32s - new_u32s)

    return {
        "total_u32s_compared": min_u32s,
        "old_u32_count": old_u32s,
        "new_u32_count": new_u32s,
        "u32s_changed": u32s_changed,
        "identical": u32s_changed == 0,
        "first_diffs": diffs,
    }


def is_bound_as_write_target(controller, resource_id, event_id):
    """
    Check if a resource is bound as a write target at a specific event by examining
    the action and pipeline state bindings.

    This is a heuristic check - it tells you if the resource COULD be written to,
    not whether actual bytes changed.

    Returns True if bound as write target, False if not, None if unknown.
    """
    controller.SetFrameEvent(event_id, False)

    # Get the action at this event
    actions = controller.GetRootActions()

    def find_action(action_list, target_eid):
        for a in action_list:
            if a.eventId == target_eid:
                return a
            if a.children:
                found = find_action(a.children, target_eid)
                if found:
                    return found
        return None

    action = find_action(actions, event_id)
    if action is None:
        return None

    # Convert resource_id to ResourceId if it's an int
    if isinstance(resource_id, int):
        for res in controller.GetResources():
            if int(res.resourceId) == resource_id:
                res_id = res.resourceId
                break
        else:
            return None
    else:
        res_id = resource_id

    # Check 1: Color render target outputs
    try:
        for out_id in action.outputs:
            if out_id != NULL_ID and out_id == res_id:
                return True
    except Exception:
        pass

    # Check 2: Depth/stencil output
    try:
        if action.depthOut != NULL_ID and action.depthOut == res_id:
            return True
    except Exception:
        pass

    # Check 3: RW resource bindings (storage buffers/images)
    state = controller.GetPipelineState()
    stages_to_check = [
        rd.ShaderStage.Compute,
        rd.ShaderStage.Fragment,
        rd.ShaderStage.Vertex,
        rd.ShaderStage.Geometry,
        rd.ShaderStage.Tess_Control,
        rd.ShaderStage.Tess_Eval,
    ]

    for stage in stages_to_check:
        try:
            rw_list = state.GetReadWriteResources(stage)
            for rw in rw_list:
                if rw.descriptor.resource == res_id:
                    return True
        except Exception:
            continue

    # If we checked everything and didn't find a write target, it's read-only
    return False


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
    data_sample_bytes = req.get("data_sample_bytes") or (64 * 1024)  # 64KB default
    max_changed_elements = req.get("max_changed_elements") or 3  # How many changed elements to report

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

            # Try to infer buffer struct layout from shader reflection
            buffer_fields = None
            buffer_stride = None
            if resource_type == "Buffer":
                try:
                    buffer_fields, buffer_stride = infer_buffer_layout(
                        controller, resource_desc.resourceId
                    )
                except Exception:
                    pass

            # Get all usages - pass the actual ResourceId object
            usages = controller.GetUsage(resource_desc.resourceId)

            # Sort usages by event_id for data change tracking
            usages_list = list(usages)
            usages_list.sort(key=lambda u: u.eventId)

            uses = []
            seen_events = set()

            # For data change tracking: store the last known data state
            last_data = None
            last_data_event = None

            for usage in usages_list:
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

                # Determine is_write by comparing actual data
                # For read-only usages, skip data comparison
                if usage_str in ("CopySrc", "ResolveSrc", "Indirect", "VertexBuffer", "IndexBuffer"):
                    use_entry["is_write"] = False
                    use_entry["write_check"] = "read_only_usage"
                elif usage_str.endswith("_Constants") or (usage_str.endswith("_Resource") and "RW" not in usage_str):
                    use_entry["is_write"] = False
                    use_entry["write_check"] = "read_only_usage"
                else:
                    # For potential write usages, compare actual data
                    controller.SetFrameEvent(event_id, True)  # replay TO this event
                    current_data, read_error = read_resource_data(controller, resource_desc, data_sample_bytes)

                    if current_data is not None:
                        if last_data is None:
                            # First time reading data - can't determine if changed
                            # Check if it's bound as a write target
                            is_write_target = is_bound_as_write_target(
                                controller, resource_desc.resourceId, event_id
                            )
                            if is_write_target:
                                # Could have been written, but we don't know for sure
                                use_entry["is_write"] = None
                                use_entry["write_check"] = "first_read_no_baseline"
                                use_entry["data_size"] = len(current_data)
                            else:
                                use_entry["is_write"] = False
                                use_entry["write_check"] = "first_read_not_bound"
                                use_entry["data_size"] = len(current_data)
                        else:
                            # Compare with previous data
                            if current_data != last_data:
                                use_entry["is_write"] = True
                                use_entry["write_check"] = "data_changed"
                                use_entry["previous_event_id"] = last_data_event
                                use_entry["data_size"] = len(current_data)

                                # Compute delta showing which elements changed
                                if buffer_fields and buffer_stride:
                                    # Use semantic diff with element indices
                                    changed_elements = find_changed_buffer_elements(
                                        buffer_fields, buffer_stride,
                                        last_data, current_data,
                                        max_elements=max_changed_elements
                                    )
                                    if changed_elements:
                                        num_elements = len(current_data) // buffer_stride
                                        use_entry["delta"] = {
                                            "type": "buffer_elements",
                                            "total_elements": num_elements,
                                            "changed": changed_elements,
                                        }
                                    else:
                                        # Changed but couldn't parse elements - show byte regions
                                        byte_regions = find_changed_bytes_region(last_data, current_data)
                                        if byte_regions:
                                            use_entry["delta"] = {
                                                "type": "byte_regions",
                                                "changed": byte_regions,
                                            }
                                else:
                                    # No buffer layout - show byte regions
                                    byte_regions = find_changed_bytes_region(last_data, current_data)
                                    if byte_regions:
                                        use_entry["delta"] = {
                                            "type": "byte_regions",
                                            "total_bytes": len(current_data),
                                            "changed": byte_regions,
                                        }
                            else:
                                use_entry["is_write"] = False
                                use_entry["write_check"] = "data_unchanged"
                                use_entry["data_size"] = len(current_data)

                        last_data = current_data
                        last_data_event = event_id
                    else:
                        # Couldn't read data - fall back to binding check
                        use_entry["write_check"] = "data_read_failed"
                        use_entry["read_error"] = read_error
                        is_write_target = is_bound_as_write_target(
                            controller, resource_desc.resourceId, event_id
                        )
                        if is_write_target is not None:
                            use_entry["is_write"] = is_write_target

                # Add view info if available
                if usage.view != rd.ResourceId.Null():
                    use_entry["view_id"] = int(usage.view)
                    use_entry["view_name"] = get_name(id_to_name, usage.view)

                # Get pipeline info for this event
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

            # Add buffer layout info if available
            if buffer_fields and buffer_stride:
                document["buffer_layout"] = {
                    "stride": buffer_stride,
                    "field_count": len(buffer_fields),
                    "fields": [f.name for f in buffer_fields[:20]],  # First 20 field names
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
