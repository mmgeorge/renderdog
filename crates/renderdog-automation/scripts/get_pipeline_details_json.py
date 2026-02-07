"""
get_pipeline_details_json.py -- RenderDoc Python script for getting pipeline metadata.

Finds a pipeline by name and returns detailed information including:
  - pipeline_type: "Graphics" or "Compute"
  - stages: List of shader stages with entry points
  - resource_bindings: Read-only and read-write resource bindings per stage
  - constant_blocks: Uniform/constant buffer bindings per stage
  - samplers: Sampler bindings per stage
  - vertex_inputs: For graphics pipelines, the vertex input layout
  - render_targets: For graphics pipelines, output targets info
  - event_ids: Events where this pipeline is used

Request parameters:
  - pipeline_name: Name of the pipeline to inspect
  - capture_path: Path to the .rdc capture file

Returns:
  - pipeline_name, pipeline_id, pipeline_type
  - stages, resource_bindings, constant_blocks, samplers
  - event_ids where the pipeline is active
"""

import json
import traceback

import renderdoc as rd


REQ_PATH = "get_pipeline_details_json.request.json"
RESP_PATH = "get_pipeline_details_json.response.json"


def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Type schema helpers (for buffer layout inference)
# ---------------------------------------------------------------------------

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


def extract_buffer_schema(res_info):
    """Extract schema from a shader resource's variableType."""
    try:
        var_type = res_info.variableType
        if len(var_type.members) == 0:
            return None

        members = var_type.members

        # Unwrap single-element wrapper structs
        if len(members) == 1 and len(members[0].type.members) > 0:
            members = members[0].type.members

        return build_type_schema(members)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALL_STAGES = [
    rd.ShaderStage.Vertex,
    rd.ShaderStage.Tess_Control,
    rd.ShaderStage.Tess_Eval,
    rd.ShaderStage.Geometry,
    rd.ShaderStage.Fragment,
    rd.ShaderStage.Compute,
]

_STAGE_NAMES = {
    rd.ShaderStage.Vertex: "Vertex",
    rd.ShaderStage.Tess_Control: "TessControl",
    rd.ShaderStage.Tess_Eval: "TessEval",
    rd.ShaderStage.Geometry: "Geometry",
    rd.ShaderStage.Fragment: "Fragment",
    rd.ShaderStage.Compute: "Compute",
}

# Vulkan shader stage flag bits
_VK_SHADER_STAGE_FLAGS = {
    0x00000001: "Vertex",
    0x00000002: "TessControl",
    0x00000004: "TessEval",
    0x00000008: "Geometry",
    0x00000010: "Fragment",
    0x00000020: "Compute",
    0x0000001F: "AllGraphics",
    0x7FFFFFFF: "All",
}

# Vulkan descriptor type names
_VK_DESCRIPTOR_TYPES = {
    0: "Sampler",
    1: "CombinedImageSampler",
    2: "SampledImage",
    3: "StorageImage",
    4: "UniformTexelBuffer",
    5: "StorageTexelBuffer",
    6: "UniformBuffer",
    7: "StorageBuffer",
    8: "UniformBufferDynamic",
    9: "StorageBufferDynamic",
    10: "InputAttachment",
}

# Vulkan pipeline creation flags
_VK_PIPELINE_CREATE_FLAGS = {
    0x00000001: "DisableOptimization",
    0x00000002: "AllowDerivatives",
    0x00000004: "Derivative",
    0x00000008: "ViewIndexFromDeviceIndex",
    0x00000010: "DispatchBase",
    0x00000020: "DeferCompile",
    0x00000040: "CaptureStatistics",
    0x00000080: "CaptureInternalRepresentations",
    0x00000100: "FailOnPipelineCompileRequired",
    0x00000200: "EarlyReturnOnFailure",
    0x00000400: "LinkTimeOptimization",
    0x00000800: "Library",
    0x00001000: "RayTracingSkipTriangles",
    0x00002000: "RayTracingSkipAABBs",
    0x00004000: "RayTracingNoNullAnyHitShaders",
    0x00008000: "RayTracingNoNullClosestHitShaders",
    0x00010000: "RayTracingNoNullMissShaders",
    0x00020000: "RayTracingNoNullIntersectionShaders",
    0x00040000: "IndirectBindable",
    0x00080000: "RayTracingShaderGroupHandleCaptureReplay",
    0x00100000: "RayTracingAllowMotion",
    0x00200000: "RenderingFragmentShadingRateAttachment",
    0x00400000: "RenderingFragmentDensityMapAttachment",
    0x00800000: "RetainLinkTimeOptimizationInfo",
    0x01000000: "RayTracingOpacityMicromap",
    0x02000000: "ColorAttachmentFeedbackLoop",
    0x04000000: "DepthStencilAttachmentFeedbackLoop",
    0x08000000: "NoProtectedAccess",
    0x10000000: "RayTracingDisplacementMicromap",
    0x20000000: "DescriptorBuffer",
    0x40000000: "ProtectedAccessOnly",
}


def stage_flags_to_list(flags):
    """Convert Vulkan shader stage flags to list of stage names."""
    if flags == 0x7FFFFFFF:
        return ["All"]
    if flags == 0x0000001F:
        return ["AllGraphics"]

    stages = []
    for bit, name in _VK_SHADER_STAGE_FLAGS.items():
        if bit < 0x0000001F and (flags & bit):
            stages.append(name)
    return stages if stages else ["Unknown"]


def pipeline_flags_to_list(flags):
    """Convert Vulkan pipeline creation flags to list of semantic names."""
    if flags == 0:
        return []

    result = []
    for bit, name in _VK_PIPELINE_CREATE_FLAGS.items():
        if flags & bit:
            result.append(name)
    return result


def descriptor_type_to_string(dtype):
    """Convert Vulkan descriptor type enum to string."""
    return _VK_DESCRIPTOR_TYPES.get(dtype, "Unknown(%d)" % dtype)


# ---------------------------------------------------------------------------
# Structured File Helpers
# ---------------------------------------------------------------------------

def get_resource_by_id(controller, resource_id):
    """Find a resource description by ID."""
    for res in controller.GetResources():
        if res.resourceId == resource_id:
            return res
    return None


def get_sd_child_value(obj, name, default=None):
    """Get a child's value by name from an SDObject."""
    try:
        for i in range(obj.NumChildren()):
            child = obj.GetChild(i)
            if child.name == name:
                basetype = child.type.basetype if hasattr(child.type, 'basetype') else None
                if basetype == rd.SDBasic.UnsignedInteger:
                    return child.AsInt()
                elif basetype == rd.SDBasic.SignedInteger:
                    return child.AsInt()
                elif basetype == rd.SDBasic.Float:
                    return child.AsFloat()
                elif basetype == rd.SDBasic.Boolean:
                    return child.AsBool()
                elif basetype == rd.SDBasic.String:
                    return child.AsString()
                elif basetype == rd.SDBasic.Enum:
                    s = child.AsString()
                    return s if s else child.AsInt()
                elif basetype == rd.SDBasic.Resource:
                    rid = child.AsResourceId()
                    return rid if rid != rd.ResourceId.Null() else None
                return child
    except Exception:
        pass
    return default


def get_sd_child(obj, name):
    """Get a child SDObject by name."""
    try:
        for i in range(obj.NumChildren()):
            child = obj.GetChild(i)
            if child.name == name:
                return child
    except Exception:
        pass
    return None


def sd_children_list(obj):
    """Get all children of an SDObject as a list."""
    result = []
    try:
        for i in range(obj.NumChildren()):
            result.append(obj.GetChild(i))
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Descriptor Set Content Parsing from Structured File
# ---------------------------------------------------------------------------

def build_descriptor_set_contents_map(controller):
    """Build a map of descriptor set contents from vkUpdateDescriptorSets calls.

    Returns: dict mapping (descriptor_set_id, binding_index) -> {
        "type": descriptor_type,
        "buffer": buffer_resource_id (optional),
        "image_view": image_view_resource_id (optional),
    }
    """
    sfile = controller.GetStructuredFile()
    if sfile is None:
        return {}

    # Build a lookup dict for resource names
    resource_names = {}
    for res in controller.GetResources():
        resource_names[int(res.resourceId)] = res.name

    # (descriptor_set_id, binding) -> binding_info
    result = {}

    for chunk in sfile.chunks:
        if "UpdateDescriptorSets" not in chunk.name:
            continue

        # Get pDescriptorWrites array
        writes_obj = get_sd_child(chunk, "pDescriptorWrites")
        if writes_obj is None:
            continue

        for write_obj in sd_children_list(writes_obj):
            dst_set = get_sd_child_value(write_obj, "dstSet")
            if dst_set is None:
                continue
            dst_set_id = int(dst_set) if hasattr(dst_set, '__int__') else dst_set

            dst_binding = get_sd_child_value(write_obj, "dstBinding", 0)
            desc_type = get_sd_child_value(write_obj, "descriptorType")

            binding_info = {"type": str(desc_type) if desc_type else None}

            # Check for buffer info
            buffer_infos_obj = get_sd_child(write_obj, "pBufferInfo")
            if buffer_infos_obj is not None:
                for buf_info_obj in sd_children_list(buffer_infos_obj):
                    buffer_rid = get_sd_child_value(buf_info_obj, "buffer")
                    if buffer_rid is not None:
                        buffer_id = int(buffer_rid) if hasattr(buffer_rid, '__int__') else buffer_rid
                        binding_info["buffer"] = buffer_id
                        binding_info["buffer_name"] = resource_names.get(buffer_id)
                    break  # Only need first buffer

            # Check for image info
            image_infos_obj = get_sd_child(write_obj, "pImageInfo")
            if image_infos_obj is not None:
                for img_info_obj in sd_children_list(image_infos_obj):
                    image_view_rid = get_sd_child_value(img_info_obj, "imageView")
                    if image_view_rid is not None:
                        iv_id = int(image_view_rid) if hasattr(image_view_rid, '__int__') else image_view_rid
                        binding_info["image_view"] = iv_id
                        binding_info["image_view_name"] = resource_names.get(iv_id)
                    sampler_rid = get_sd_child_value(img_info_obj, "sampler")
                    if sampler_rid is not None:
                        sampler_id = int(sampler_rid) if hasattr(sampler_rid, '__int__') else sampler_rid
                        binding_info["sampler"] = sampler_id
                    break  # Only need first image

            key = (dst_set_id, dst_binding)
            result[key] = binding_info

    return result


def find_bound_descriptor_sets_at_event(controller, event_id, pipeline_layout_id=None):
    """Find descriptor sets bound at or before a specific event.

    Returns: dict mapping set_index -> descriptor_set_resource_id
    """
    sfile = controller.GetStructuredFile()
    if sfile is None:
        return {}

    # Find all vkCmdBindDescriptorSets calls and their associated event IDs
    # We need to find the most recent one before our target event
    bind_calls = []  # [(chunk_event_id, bind_point, first_set, descriptor_set_ids)]

    for chunk in sfile.chunks:
        if "BindDescriptorSets" not in chunk.name:
            continue

        # Get the event ID for this chunk (it's on the chunk itself)
        chunk_eid = None
        try:
            chunk_eid = chunk.metadata.chunkID
        except Exception:
            pass

        layout = get_sd_child_value(chunk, "layout")
        first_set = get_sd_child_value(chunk, "firstSet", 0)
        bind_point = get_sd_child_value(chunk, "pipelineBindPoint")

        # Get descriptor set IDs
        sets_obj = get_sd_child(chunk, "pDescriptorSets")
        if sets_obj is None:
            continue

        set_ids = []
        for set_obj in sd_children_list(sets_obj):
            try:
                rid = set_obj.AsResourceId()
                if rid is not None and rid != rd.ResourceId.Null():
                    set_ids.append(int(rid))
                else:
                    set_ids.append(None)
            except Exception:
                set_ids.append(None)

        if set_ids:
            bind_calls.append({
                "chunk_eid": chunk_eid,
                "layout": int(layout) if layout else None,
                "bind_point": str(bind_point) if bind_point else None,
                "first_set": first_set,
                "set_ids": set_ids,
            })

    # For now, return all bound sets from all bind calls
    # A more sophisticated implementation would track state per-event
    result = {}
    for call in bind_calls:
        first_set = call["first_set"]
        for i, set_id in enumerate(call["set_ids"]):
            if set_id is not None:
                result[first_set + i] = set_id

    return result


def get_resource_from_descriptor_sets(desc_set_contents, bound_sets, set_index, binding_index):
    """Look up the actual resource bound at a specific set/binding.

    Args:
        desc_set_contents: Map from (set_id, binding) -> binding_info
        bound_sets: Map from set_index -> descriptor_set_id
        set_index: The bind group / descriptor set index
        binding_index: The binding within the set

    Returns: (resource_id, resource_name) or (None, None)
    """
    desc_set_id = bound_sets.get(set_index)
    if desc_set_id is None:
        return None, None

    binding_info = desc_set_contents.get((desc_set_id, binding_index))
    if binding_info is None:
        return None, None

    # Return buffer or image_view, whichever is present
    if "buffer" in binding_info:
        return binding_info["buffer"], binding_info.get("buffer_name")
    if "image_view" in binding_info:
        return binding_info["image_view"], binding_info.get("image_view_name")

    return None, None


# ---------------------------------------------------------------------------
# VkGraphicsPipelineCreateInfo Extraction from Structured File
# ---------------------------------------------------------------------------

def extract_graphics_pipeline_create_info(controller, pipeline_resource_id):
    """Extract VkGraphicsPipelineCreateInfo from structured file for a graphics pipeline."""
    if pipeline_resource_id is None or pipeline_resource_id == rd.ResourceId.Null():
        return None

    try:
        res_desc = get_resource_by_id(controller, pipeline_resource_id)
        if res_desc is None or not res_desc.initialisationChunks:
            return None

        sfile = controller.GetStructuredFile()
        if sfile is None:
            return None

        # Find the vkCreateGraphicsPipelines chunk
        for chunk_idx in res_desc.initialisationChunks:
            try:
                chunk = sfile.chunks[chunk_idx]
                if "CreateGraphicsPipelines" not in chunk.name:
                    continue

                # Find CreateInfo parameter
                create_info = None
                for child in sd_children_list(chunk):
                    if child.name == "CreateInfo":
                        create_info = child
                        break

                if create_info is None:
                    continue

                return parse_graphics_pipeline_create_info(controller, create_info)

            except Exception:
                continue

        return None

    except Exception:
        return None


def parse_graphics_pipeline_create_info(controller, create_info):
    """Parse VkGraphicsPipelineCreateInfo SDObject into a dictionary."""
    result = {}

    # flags
    flags = get_sd_child_value(create_info, "flags")
    if flags:
        result["flags"] = str(flags)

    # Shader stages
    stages_obj = get_sd_child(create_info, "pStages")
    if stages_obj:
        stages = []
        for stage_child in sd_children_list(stages_obj):
            stage_info = {}
            stage_val = get_sd_child_value(stage_child, "stage")
            if stage_val:
                stage_info["stage"] = str(stage_val).replace("VK_SHADER_STAGE_", "").replace("_BIT", "")
            module_id = get_sd_child_value(stage_child, "module")
            if module_id:
                stage_info["module"] = get_name(controller, module_id)
            entry = get_sd_child_value(stage_child, "pName")
            if entry:
                stage_info["entry_point"] = entry
            if stage_info:
                stages.append(stage_info)
        if stages:
            result["shader_stages"] = stages

    # Vertex input state
    vertex_input = get_sd_child(create_info, "pVertexInputState")
    if vertex_input:
        vi_info = parse_vertex_input_state(vertex_input)
        if vi_info:
            result["vertex_input"] = vi_info

    # Input assembly state
    input_assembly = get_sd_child(create_info, "pInputAssemblyState")
    if input_assembly:
        ia_info = {}
        topology = get_sd_child_value(input_assembly, "topology")
        if topology:
            ia_info["topology"] = str(topology).replace("VK_PRIMITIVE_TOPOLOGY_", "")
        prim_restart = get_sd_child_value(input_assembly, "primitiveRestartEnable")
        if prim_restart:
            ia_info["primitive_restart_enable"] = bool(prim_restart)
        if ia_info:
            result["input_assembly"] = ia_info

    # Tessellation state
    tess_state = get_sd_child(create_info, "pTessellationState")
    if tess_state and tess_state.type.basetype != rd.SDBasic.Null:
        patch_points = get_sd_child_value(tess_state, "patchControlPoints")
        if patch_points:
            result["tessellation"] = {"patch_control_points": patch_points}

    # Viewport state
    viewport_state = get_sd_child(create_info, "pViewportState")
    if viewport_state:
        vp_info = {}
        vp_count = get_sd_child_value(viewport_state, "viewportCount")
        if vp_count:
            vp_info["viewport_count"] = vp_count
        scissor_count = get_sd_child_value(viewport_state, "scissorCount")
        if scissor_count:
            vp_info["scissor_count"] = scissor_count
        if vp_info:
            result["viewport"] = vp_info

    # Rasterization state
    raster_state = get_sd_child(create_info, "pRasterizationState")
    if raster_state:
        rs_info = parse_rasterization_state(raster_state)
        if rs_info:
            result["rasterization"] = rs_info

    # Multisample state
    ms_state = get_sd_child(create_info, "pMultisampleState")
    if ms_state:
        ms_info = {}
        samples = get_sd_child_value(ms_state, "rasterizationSamples")
        if samples:
            ms_info["samples"] = str(samples).replace("VK_SAMPLE_COUNT_", "").replace("_BIT", "")
        sample_shading = get_sd_child_value(ms_state, "sampleShadingEnable")
        if sample_shading:
            ms_info["sample_shading_enable"] = bool(sample_shading)
        alpha_to_coverage = get_sd_child_value(ms_state, "alphaToCoverageEnable")
        if alpha_to_coverage:
            ms_info["alpha_to_coverage_enable"] = bool(alpha_to_coverage)
        if ms_info:
            result["multisample"] = ms_info

    # Depth stencil state
    ds_state = get_sd_child(create_info, "pDepthStencilState")
    if ds_state:
        ds_info = parse_depth_stencil_state_from_create_info(ds_state)
        if ds_info:
            result["depth_stencil"] = ds_info

    # Color blend state
    cb_state = get_sd_child(create_info, "pColorBlendState")
    if cb_state:
        cb_info = parse_color_blend_state(cb_state)
        if cb_info:
            result["color_blend"] = cb_info

    # Dynamic state
    dyn_state = get_sd_child(create_info, "pDynamicState")
    if dyn_state:
        dyn_info = parse_dynamic_state(dyn_state)
        if dyn_info:
            result["dynamic_states"] = dyn_info

    # Layout
    layout_id = get_sd_child_value(create_info, "layout")
    if layout_id:
        result["layout"] = get_name(controller, layout_id)

    # Render pass
    render_pass_id = get_sd_child_value(create_info, "renderPass")
    if render_pass_id:
        result["render_pass"] = get_name(controller, render_pass_id)

    # Subpass
    subpass = get_sd_child_value(create_info, "subpass")
    if subpass is not None:
        result["subpass"] = subpass

    return result if result else None


def parse_vertex_input_state(vertex_input):
    """Parse VkPipelineVertexInputStateCreateInfo."""
    result = {}

    # Vertex bindings
    bindings_obj = get_sd_child(vertex_input, "pVertexBindingDescriptions")
    if bindings_obj:
        bindings = []
        for binding_child in sd_children_list(bindings_obj):
            b_info = {}
            b_info["binding"] = get_sd_child_value(binding_child, "binding", 0)
            b_info["stride"] = get_sd_child_value(binding_child, "stride", 0)
            input_rate = get_sd_child_value(binding_child, "inputRate")
            if input_rate:
                b_info["input_rate"] = str(input_rate).replace("VK_VERTEX_INPUT_RATE_", "")
            bindings.append(b_info)
        if bindings:
            result["bindings"] = bindings

    # Vertex attributes
    attrs_obj = get_sd_child(vertex_input, "pVertexAttributeDescriptions")
    if attrs_obj:
        attrs = []
        for attr_child in sd_children_list(attrs_obj):
            a_info = {}
            a_info["location"] = get_sd_child_value(attr_child, "location", 0)
            a_info["binding"] = get_sd_child_value(attr_child, "binding", 0)
            fmt = get_sd_child_value(attr_child, "format")
            if fmt:
                a_info["format"] = str(fmt).replace("VK_FORMAT_", "")
            a_info["offset"] = get_sd_child_value(attr_child, "offset", 0)
            attrs.append(a_info)
        if attrs:
            result["attributes"] = attrs

    return result if result else None


def parse_rasterization_state(raster_state):
    """Parse VkPipelineRasterizationStateCreateInfo."""
    result = {}

    depth_clamp = get_sd_child_value(raster_state, "depthClampEnable")
    if depth_clamp:
        result["depth_clamp_enable"] = bool(depth_clamp)

    rasterizer_discard = get_sd_child_value(raster_state, "rasterizerDiscardEnable")
    if rasterizer_discard:
        result["rasterizer_discard_enable"] = bool(rasterizer_discard)

    polygon_mode = get_sd_child_value(raster_state, "polygonMode")
    if polygon_mode:
        result["polygon_mode"] = str(polygon_mode).replace("VK_POLYGON_MODE_", "")

    cull_mode = get_sd_child_value(raster_state, "cullMode")
    if cull_mode:
        result["cull_mode"] = str(cull_mode).replace("VK_CULL_MODE_", "").replace("_BIT", "")

    front_face = get_sd_child_value(raster_state, "frontFace")
    if front_face:
        result["front_face"] = str(front_face).replace("VK_FRONT_FACE_", "")

    depth_bias = get_sd_child_value(raster_state, "depthBiasEnable")
    if depth_bias:
        result["depth_bias_enable"] = bool(depth_bias)

    line_width = get_sd_child_value(raster_state, "lineWidth")
    if line_width is not None and line_width != 1.0:
        result["line_width"] = line_width

    return result if result else None


def parse_depth_stencil_state_from_create_info(ds_state):
    """Parse VkPipelineDepthStencilStateCreateInfo."""
    result = {}

    depth_test = get_sd_child_value(ds_state, "depthTestEnable")
    if depth_test is not None:
        result["depth_test_enable"] = bool(depth_test)

    depth_write = get_sd_child_value(ds_state, "depthWriteEnable")
    if depth_write is not None:
        result["depth_write_enable"] = bool(depth_write)

    depth_compare = get_sd_child_value(ds_state, "depthCompareOp")
    if depth_compare:
        result["depth_compare_op"] = str(depth_compare).replace("VK_COMPARE_OP_", "")

    depth_bounds = get_sd_child_value(ds_state, "depthBoundsTestEnable")
    if depth_bounds:
        result["depth_bounds_test_enable"] = bool(depth_bounds)

    stencil_test = get_sd_child_value(ds_state, "stencilTestEnable")
    if stencil_test:
        result["stencil_test_enable"] = bool(stencil_test)

    # Front stencil op
    front = get_sd_child(ds_state, "front")
    if front:
        front_info = parse_stencil_op_state(front)
        if front_info:
            result["front_stencil"] = front_info

    # Back stencil op
    back = get_sd_child(ds_state, "back")
    if back:
        back_info = parse_stencil_op_state(back)
        if back_info:
            result["back_stencil"] = back_info

    return result if result else None


def parse_stencil_op_state(stencil_op):
    """Parse VkStencilOpState."""
    result = {}

    fail_op = get_sd_child_value(stencil_op, "failOp")
    if fail_op:
        result["fail_op"] = str(fail_op).replace("VK_STENCIL_OP_", "")

    pass_op = get_sd_child_value(stencil_op, "passOp")
    if pass_op:
        result["pass_op"] = str(pass_op).replace("VK_STENCIL_OP_", "")

    depth_fail_op = get_sd_child_value(stencil_op, "depthFailOp")
    if depth_fail_op:
        result["depth_fail_op"] = str(depth_fail_op).replace("VK_STENCIL_OP_", "")

    compare_op = get_sd_child_value(stencil_op, "compareOp")
    if compare_op:
        result["compare_op"] = str(compare_op).replace("VK_COMPARE_OP_", "")

    return result if result else None


def parse_color_blend_state(cb_state):
    """Parse VkPipelineColorBlendStateCreateInfo."""
    result = {}

    logic_op_enable = get_sd_child_value(cb_state, "logicOpEnable")
    if logic_op_enable:
        result["logic_op_enable"] = bool(logic_op_enable)
        logic_op = get_sd_child_value(cb_state, "logicOp")
        if logic_op:
            result["logic_op"] = str(logic_op).replace("VK_LOGIC_OP_", "")

    # Attachments
    attachments_obj = get_sd_child(cb_state, "pAttachments")
    if attachments_obj:
        attachments = []
        for att_child in sd_children_list(attachments_obj):
            att_info = {}
            blend_enable = get_sd_child_value(att_child, "blendEnable")
            att_info["blend_enable"] = bool(blend_enable) if blend_enable else False

            if att_info["blend_enable"]:
                src_color = get_sd_child_value(att_child, "srcColorBlendFactor")
                if src_color:
                    att_info["src_color_blend"] = str(src_color).replace("VK_BLEND_FACTOR_", "")

                dst_color = get_sd_child_value(att_child, "dstColorBlendFactor")
                if dst_color:
                    att_info["dst_color_blend"] = str(dst_color).replace("VK_BLEND_FACTOR_", "")

                color_op = get_sd_child_value(att_child, "colorBlendOp")
                if color_op:
                    att_info["color_blend_op"] = str(color_op).replace("VK_BLEND_OP_", "")

                src_alpha = get_sd_child_value(att_child, "srcAlphaBlendFactor")
                if src_alpha:
                    att_info["src_alpha_blend"] = str(src_alpha).replace("VK_BLEND_FACTOR_", "")

                dst_alpha = get_sd_child_value(att_child, "dstAlphaBlendFactor")
                if dst_alpha:
                    att_info["dst_alpha_blend"] = str(dst_alpha).replace("VK_BLEND_FACTOR_", "")

                alpha_op = get_sd_child_value(att_child, "alphaBlendOp")
                if alpha_op:
                    att_info["alpha_blend_op"] = str(alpha_op).replace("VK_BLEND_OP_", "")

            color_write = get_sd_child_value(att_child, "colorWriteMask")
            if color_write:
                att_info["color_write_mask"] = str(color_write).replace("VK_COLOR_COMPONENT_", "").replace("_BIT", "")

            attachments.append(att_info)
        if attachments:
            result["attachments"] = attachments

    return result if result else None


def parse_dynamic_state(dyn_state):
    """Parse VkPipelineDynamicStateCreateInfo."""
    states_obj = get_sd_child(dyn_state, "pDynamicStates")
    if not states_obj:
        return None

    states = []
    for state_child in sd_children_list(states_obj):
        try:
            val = state_child.AsString()
            if val:
                states.append(str(val).replace("VK_DYNAMIC_STATE_", ""))
        except Exception:
            try:
                val = state_child.AsInt()
                states.append(str(val))
            except Exception:
                pass

    return states if states else None


# ---------------------------------------------------------------------------
# Pipeline Layout Extraction from Structured File
# ---------------------------------------------------------------------------


def extract_pipeline_layout_from_structured_file(controller, layout_resource_id):
    """Extract full pipeline layout info from the structured file (Resource Initialization Parameters)."""
    if layout_resource_id is None or layout_resource_id == rd.ResourceId.Null():
        return None

    try:
        res_desc = get_resource_by_id(controller, layout_resource_id)
        if res_desc is None or not res_desc.initialisationChunks:
            return None

        sfile = controller.GetStructuredFile()
        if sfile is None:
            return None

        layout_info = {}

        # Find the vkCreatePipelineLayout chunk
        for chunk_idx in res_desc.initialisationChunks:
            try:
                chunk = sfile.chunks[chunk_idx]
                if "CreatePipelineLayout" not in chunk.name:
                    continue

                # Find CreateInfo
                create_info = None
                for child in chunk.data.children:
                    if child.name in ["CreateInfo", "pCreateInfo"]:
                        create_info = child
                        break

                if create_info is None:
                    continue

                # Extract set layouts
                set_layout_ids = []
                push_constant_ranges = []

                for child in create_info.children:
                    if child.name in ["pSetLayouts", "setLayouts"]:
                        # Each child is a VkDescriptorSetLayout handle
                        for i, set_layout_child in enumerate(child.children):
                            # The child should have the resource ID
                            try:
                                # Try to get the resource ID from the child
                                if hasattr(set_layout_child.data, 'basic') and hasattr(set_layout_child.data.basic, 'id'):
                                    set_layout_ids.append((i, set_layout_child.data.basic.id))
                                elif hasattr(set_layout_child, 'children') and len(set_layout_child.children) > 0:
                                    # Look for resourceId child
                                    for prop in set_layout_child.children:
                                        if prop.name == "resourceId" or prop.name == "id":
                                            set_layout_ids.append((i, prop.data.basic.id))
                                            break
                            except Exception:
                                pass

                    elif child.name in ["pPushConstantRanges", "pushConstantRanges"]:
                        # Extract push constant ranges
                        for range_child in child.children:
                            pc_range = {}
                            for prop in range_child.children:
                                if prop.name == "stageFlags":
                                    flags = prop.data.basic.u
                                    pc_range["stages"] = stage_flags_to_list(flags)
                                elif prop.name == "offset":
                                    pc_range["offset"] = prop.data.basic.u
                                elif prop.name == "size":
                                    pc_range["size"] = prop.data.basic.u
                            if pc_range:
                                push_constant_ranges.append(pc_range)

                if push_constant_ranges:
                    layout_info["push_constants"] = push_constant_ranges

                # Now extract bindings from each descriptor set layout
                descriptor_sets = []
                for set_idx, set_layout_id in set_layout_ids:
                    set_info = {"set": set_idx}

                    # Get set layout resource name
                    set_layout_name = get_name(controller, set_layout_id)
                    if set_layout_name:
                        # Extract meaningful part: "shader:16 [group0]" -> "group0"
                        if "[" in set_layout_name and "]" in set_layout_name:
                            group_name = set_layout_name[set_layout_name.rfind("[")+1:set_layout_name.rfind("]")]
                            set_info["name"] = group_name
                        else:
                            set_info["name"] = set_layout_name

                    # Get bindings from the set layout's structured data
                    bindings = extract_descriptor_set_layout_bindings(controller, set_layout_id)
                    if bindings:
                        set_info["bindings"] = bindings

                    descriptor_sets.append(set_info)

                if descriptor_sets:
                    layout_info["descriptor_sets"] = descriptor_sets

                break  # Found the create info

            except Exception:
                continue

        return layout_info if layout_info else None

    except Exception:
        return None


def extract_pipeline_layout(controller, pipeline_type, active_events):
    """Extract pipeline layout information including descriptor set layouts and push constants."""
    if not active_events:
        return None

    layout_info = {}
    layout_resource_id = None

    # Get the first active event to access Vulkan state
    for eid in active_events[:5]:
        try:
            controller.SetFrameEvent(eid, False)
            vk = controller.GetVulkanPipelineState()
            if vk is None:
                continue

            # Get the pipeline object
            pipe = None
            if pipeline_type == "Graphics":
                pipe = vk.graphics
            else:
                pipe = vk.compute

            if pipe is None:
                continue

            # Get pipeline flags and convert to semantic names
            try:
                flags_int = int(pipe.flags)
                flags_list = pipeline_flags_to_list(flags_int)
                if flags_list:
                    layout_info["flags"] = flags_list
            except Exception:
                pass

            # Get pipeline layout resource ID
            if pipeline_type == "Graphics":
                try:
                    layout_resource_id = pipe.pipelinePreRastLayoutResourceId
                except Exception:
                    pass
                if layout_resource_id is None or layout_resource_id == rd.ResourceId.Null():
                    try:
                        layout_resource_id = pipe.pipelineFragmentLayoutResourceId
                    except Exception:
                        pass
            else:
                try:
                    layout_resource_id = pipe.pipelineComputeLayoutResourceId
                except Exception:
                    pass

            if layout_resource_id is not None and layout_resource_id != rd.ResourceId.Null():
                layout_name = get_name(controller, layout_resource_id)
                if layout_name:
                    layout_info["name"] = layout_name.replace(" [implicit]", "")

            # Check if using descriptor buffers (VK_EXT_descriptor_buffer)
            uses_descriptor_buffers = False
            try:
                if pipe.descriptorBuffers and len(pipe.descriptorBuffers) > 0:
                    for db in pipe.descriptorBuffers:
                        if db.buffer is not None and db.buffer != rd.ResourceId.Null():
                            uses_descriptor_buffers = True
                            break
            except Exception:
                pass

            if uses_descriptor_buffers:
                layout_info["uses_descriptor_buffers"] = True

            # If we got useful info, break
            if layout_resource_id:
                break

        except Exception:
            continue

    # Extract full layout info from structured file
    if layout_resource_id:
        structured_info = extract_pipeline_layout_from_structured_file(controller, layout_resource_id)
        if structured_info:
            # Merge structured info into layout_info
            if "push_constants" in structured_info:
                layout_info["push_constants"] = structured_info["push_constants"]
            if "descriptor_sets" in structured_info:
                layout_info["descriptor_sets"] = structured_info["descriptor_sets"]

    # Fallback: get descriptor sets from runtime state if not found in structured file
    if "descriptor_sets" not in layout_info:
        set_layouts = []
        for eid in active_events[:1]:
            try:
                controller.SetFrameEvent(eid, False)
                vk = controller.GetVulkanPipelineState()
                if vk is None:
                    continue

                pipe = vk.graphics if pipeline_type == "Graphics" else vk.compute
                if pipe is None:
                    continue

                for i, ds in enumerate(pipe.descriptorSets):
                    layout_rid = ds.layoutResourceId if ds.layoutResourceId != rd.ResourceId.Null() else None
                    if layout_rid is None:
                        continue

                    set_info = {"set": i}
                    layout_name = get_name(controller, layout_rid)
                    if layout_name:
                        if "[" in layout_name and "]" in layout_name:
                            group_name = layout_name[layout_name.rfind("[")+1:layout_name.rfind("]")]
                            set_info["name"] = group_name
                        else:
                            set_info["name"] = layout_name

                    # Get bindings
                    bindings = extract_descriptor_set_layout_bindings(controller, layout_rid)
                    if bindings:
                        set_info["bindings"] = bindings

                    set_layouts.append(set_info)

                if set_layouts:
                    layout_info["descriptor_sets"] = set_layouts
                break
            except Exception:
                continue

    # Get descriptor buffer bindings if present (for VK_EXT_descriptor_buffer)
    desc_buffers = []
    for eid in active_events[:1]:
        try:
            controller.SetFrameEvent(eid, False)
            vk = controller.GetVulkanPipelineState()
            if vk is None:
                continue

            pipe = vk.graphics if pipeline_type == "Graphics" else vk.compute
            if pipe is None:
                continue

            for i, db in enumerate(pipe.descriptorBuffers):
                if db.buffer is not None and db.buffer != rd.ResourceId.Null():
                    buf_info = {
                        "index": i,
                        "buffer": get_name(controller, db.buffer),
                    }
                    if db.offset > 0:
                        buf_info["offset"] = db.offset

                    # Indicate buffer type
                    types = []
                    if db.resourceBuffer:
                        types.append("resource")
                    if db.samplerBuffer:
                        types.append("sampler")
                    if db.pushDescriptor:
                        types.append("push")
                    if types:
                        buf_info["types"] = types

                    desc_buffers.append(buf_info)

            if desc_buffers:
                layout_info["descriptor_buffers"] = desc_buffers
            break
        except Exception:
            continue

    return layout_info if layout_info else None


def extract_descriptor_set_layout_bindings(controller, layout_resource_id):
    """Extract binding info from a descriptor set layout using structured file."""
    if layout_resource_id is None or layout_resource_id == rd.ResourceId.Null():
        return None

    try:
        # Find the resource description to get initialization chunks
        res_desc = None
        for res in controller.GetResources():
            if res.resourceId == layout_resource_id:
                res_desc = res
                break

        if res_desc is None or not res_desc.initialisationChunks:
            return None

        # Get the structured file
        sfile = controller.GetStructuredFile()
        if sfile is None:
            return None

        # Look for vkCreateDescriptorSetLayout chunk
        bindings = []
        for chunk_idx in res_desc.initialisationChunks:
            try:
                chunk = sfile.chunks[chunk_idx]
                if "CreateDescriptorSetLayout" not in chunk.name:
                    continue

                # Navigate to CreateInfo -> pBindings
                create_info = None
                for child in chunk.data.children:
                    if child.name in ["CreateInfo", "pCreateInfo"]:
                        create_info = child
                        break

                if create_info is None:
                    continue

                # Find bindings array
                bindings_obj = None
                for child in create_info.children:
                    if child.name in ["pBindings", "bindings"]:
                        bindings_obj = child
                        break

                if bindings_obj is None:
                    continue

                # Extract each binding
                for binding_child in bindings_obj.children:
                    binding_info = {}
                    for prop in binding_child.children:
                        name = prop.name
                        if name == "binding":
                            binding_info["binding"] = prop.data.basic.u
                        elif name == "descriptorType":
                            dtype = prop.data.basic.u
                            binding_info["descriptor_type"] = descriptor_type_to_string(dtype)
                            binding_info["descriptor_type_raw"] = dtype
                        elif name == "descriptorCount":
                            binding_info["descriptor_count"] = prop.data.basic.u
                        elif name == "stageFlags":
                            flags = prop.data.basic.u
                            binding_info["stage_flags"] = flags
                            binding_info["stages"] = stage_flags_to_list(flags)
                        elif name == "pImmutableSamplers":
                            # Check if there are immutable samplers
                            if len(prop.children) > 0:
                                binding_info["has_immutable_samplers"] = True
                                binding_info["immutable_sampler_count"] = len(prop.children)
                            else:
                                binding_info["has_immutable_samplers"] = False

                    if binding_info:
                        bindings.append(binding_info)

            except Exception:
                continue

        return bindings if bindings else None

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pipeline finding
# ---------------------------------------------------------------------------

def find_pipeline(controller, pipeline_name):
    """Locate the target pipeline's resource description by name."""
    for res in controller.GetResources():
        if res.name == pipeline_name and res.type == rd.ResourceType.PipelineState:
            return res

    # Also check if any name matches (could be partial)
    for res in controller.GetResources():
        if res.type == rd.ResourceType.PipelineState and pipeline_name in res.name:
            return res

    available = []
    for r in controller.GetResources():
        if r.type == rd.ResourceType.PipelineState:
            available.append("  %s  %s" % (r.resourceId, r.name))

    raise RuntimeError(
        "Pipeline '%s' not found. Available pipelines:\n%s"
        % (pipeline_name, "\n".join(available[:30]))
    )


def flatten_actions(roots):
    """Yield every leaf action in linear order."""
    for action in roots:
        if len(action.children) > 0:
            yield from flatten_actions(action.children)
        else:
            yield action


def get_name(controller, rid):
    """Get resource name by ID."""
    try:
        for r in controller.GetResources():
            if r.resourceId == rid:
                return r.name
    except Exception:
        pass
    return str(rid)


# ---------------------------------------------------------------------------
# Pipeline details extraction
# ---------------------------------------------------------------------------

def extract_pipeline_details(controller, pipeline_id, pipeline_name, actions):
    """Extract detailed information about a pipeline."""
    details = {
        "pipeline_name": pipeline_name,
        "pipeline_id": int(pipeline_id),
        "pipeline_type": None,
        "stages": [],
        "resource_bindings": [],
        "constant_blocks": [],
        "samplers": [],
        "render_targets": [],
        "depth_state": None,
        "stencil_state": None,
        "blend_state": None,
        "event_ids": [],
    }

    # First pass: find all events where this pipeline is active
    active_events = []
    for action in actions:
        eid = action.eventId
        controller.SetFrameEvent(eid, False)
        state = controller.GetPipelineState()

        # Check graphics pipeline
        try:
            gfx_pipe = state.GetGraphicsPipelineObject()
            if gfx_pipe == pipeline_id:
                details["pipeline_type"] = "Graphics"
                active_events.append(eid)
                details["event_ids"].append(eid)
                continue
        except Exception:
            pass

        # Check compute pipeline
        try:
            comp_pipe = state.GetComputePipelineObject()
            if comp_pipe == pipeline_id:
                details["pipeline_type"] = "Compute"
                active_events.append(eid)
                details["event_ids"].append(eid)
                continue
        except Exception:
            pass

    if not active_events:
        raise RuntimeError(
            "Pipeline '%s' is not used in any action in the capture." % pipeline_name
        )

    # Build descriptor set contents map once (from vkUpdateDescriptorSets)
    # This maps (descriptor_set_id, binding_index) -> binding_info
    desc_set_contents = build_descriptor_set_contents_map(controller)

    # Find bound descriptor sets (from vkCmdBindDescriptorSets)
    # This maps set_index -> descriptor_set_id
    bound_desc_sets = find_bound_descriptor_sets_at_event(controller, active_events[0] if active_events else 0)

    # Try multiple events to extract shader reflection info
    # (some events might have better reflection data than others)
    for event_idx, eid in enumerate(active_events[:10]):  # Try up to 10 events
        controller.SetFrameEvent(eid, True)  # Force replay
        state = controller.GetPipelineState()

        # Check which stages have reflection at this event
        stages_with_refl = []
        for stage in _ALL_STAGES:
            refl = state.GetShaderReflection(stage)
            if refl is not None:
                stages_with_refl.append((stage, refl))

        # If we found stages, extract from this event
        if stages_with_refl:
            for stage, refl in stages_with_refl:
                stage_name = _STAGE_NAMES.get(stage, str(stage))
                shader_name = get_name(controller, refl.resourceId)

                try:
                    entry_point = state.GetShaderEntryPoint(stage)
                except Exception:
                    entry_point = "main"

                # Only add stage if not already added
                if not any(s["stage"] == stage_name for s in details["stages"]):
                    stage_info = {
                        "stage": stage_name,
                        "shader": shader_name,
                        "entry_point": entry_point or "main",
                    }
                    details["stages"].append(stage_info)

                    # Extract resource bindings for this stage (from reflection)
                    extract_stage_resources(controller, state, stage, stage_name, refl, details, active_events,
                                          desc_set_contents, bound_desc_sets)

            # If we got stages, we're done
            if details["stages"]:
                break

    # For graphics pipelines, scan all active events to collect layout info
    if details["pipeline_type"] == "Graphics":
        # Scan vertex/index buffer layouts and add to Vertex stage
        vertex_buffers, index_buffer = scan_vertex_index_buffer_layouts(controller, active_events)
        for stage in details["stages"]:
            if stage["stage"] == "Vertex":
                if vertex_buffers:
                    stage["vertex_buffers"] = vertex_buffers
                if index_buffer:
                    stage["index_buffer"] = index_buffer
                break

        # Scan render target and state layouts
        scan_render_target_layouts(controller, active_events, details)
        scan_depth_stencil_blend_state(controller, active_events, details)
    else:
        # Remove graphics-specific fields for compute pipelines
        del details["render_targets"]
        del details["depth_state"]
        del details["stencil_state"]
        del details["blend_state"]

    # Remove empty samplers list if empty
    if not details.get("samplers"):
        del details["samplers"]

    # Extract pipeline layout information
    pipeline_layout = extract_pipeline_layout(controller, details["pipeline_type"], active_events)
    if pipeline_layout:
        details["pipeline_layout"] = pipeline_layout

        # Try to extract detailed binding info for each descriptor set layout
        for ds in pipeline_layout.get("descriptor_sets", []):
            layout_rid = ds.pop("_layout_resource_id", None)  # Get and remove from output
            if layout_rid:
                try:
                    bindings = extract_descriptor_set_layout_bindings(controller, layout_rid)
                    if bindings:
                        ds["bindings"] = bindings
                except Exception:
                    pass

    # For graphics pipelines, extract VkGraphicsPipelineCreateInfo from structured file
    if details["pipeline_type"] == "Graphics":
        create_info = extract_graphics_pipeline_create_info(controller, pipeline_id)
        if create_info:
            details["vulkan_create_info"] = create_info

    return details


def get_texture_by_id(controller, tex_id):
    """Find a texture description by resource ID."""
    for t in controller.GetTextures():
        if t.resourceId == tex_id:
            return t
    return None


def scan_render_target_layouts(controller, active_events, details):
    """Scan all active events to discover render target layouts used with this pipeline."""
    # Track unique render target configurations by slot index
    color_targets_seen = {}  # index -> {format, sample_count, first_resource}
    depth_target_seen = None

    for eid in active_events:
        controller.SetFrameEvent(eid, True)  # Force replay
        state = controller.GetPipelineState()

        # Check color render targets
        try:
            outputs = state.GetOutputTargets()
            for i, out in enumerate(outputs):
                # Check if resource is valid (not null)
                res_id = out.resource
                if res_id is None:
                    continue
                # Try multiple ways to check for null
                is_null = False
                try:
                    is_null = (res_id == rd.ResourceId.Null())
                except Exception:
                    try:
                        is_null = (int(res_id) == 0)
                    except Exception:
                        pass

                if not is_null and i not in color_targets_seen:
                    # Get texture info for format
                    fmt = "Unknown"
                    sample_count = 1
                    tex_desc = get_texture_by_id(controller, res_id)
                    if tex_desc is not None:
                        try:
                            fmt = str(tex_desc.format.Name())
                            sample_count = tex_desc.msSamp
                        except Exception:
                            pass

                    color_targets_seen[i] = {
                        "index": i,
                        "format": fmt,
                        "sample_count": sample_count,
                        "example_resource": get_name(controller, res_id),
                    }
        except Exception:
            pass

        # Check depth target
        try:
            depth = state.GetDepthTarget()
            res_id = depth.resource
            if res_id is not None and depth_target_seen is None:
                is_null = False
                try:
                    is_null = (res_id == rd.ResourceId.Null())
                except Exception:
                    try:
                        is_null = (int(res_id) == 0)
                    except Exception:
                        pass

                if not is_null:
                    fmt = "Unknown"
                    sample_count = 1
                    tex_desc = get_texture_by_id(controller, res_id)
                    if tex_desc is not None:
                        try:
                            fmt = str(tex_desc.format.Name())
                            sample_count = tex_desc.msSamp
                        except Exception:
                            pass

                    depth_target_seen = {
                        "index": 0,
                        "format": fmt,
                        "sample_count": sample_count,
                        "example_resource": get_name(controller, res_id),
                    }
        except Exception:
            pass

    # Fallback: try Vulkan-specific framebuffer attachments
    if not color_targets_seen and not depth_target_seen:
        for eid in active_events:
            controller.SetFrameEvent(eid, True)
            try:
                vk = controller.GetVulkanPipelineState()
                if vk is not None:
                    fb = vk.currentPass.framebuffer
                    for i, att in enumerate(fb.attachments):
                        res_id = att.imageResourceId
                        if res_id is None:
                            continue
                        is_null = False
                        try:
                            is_null = (res_id == rd.ResourceId.Null())
                        except Exception:
                            try:
                                is_null = (int(res_id) == 0)
                            except Exception:
                                pass

                        if not is_null:
                            fmt = "Unknown"
                            sample_count = 1
                            tex_desc = get_texture_by_id(controller, res_id)
                            if tex_desc is not None:
                                try:
                                    fmt = str(tex_desc.format.Name())
                                    sample_count = tex_desc.msSamp
                                except Exception:
                                    pass

                            # Determine if depth or color based on format
                            is_depth = "depth" in fmt.lower() or "d32" in fmt.lower() or "d24" in fmt.lower() or "d16" in fmt.lower()

                            if is_depth and depth_target_seen is None:
                                depth_target_seen = {
                                    "index": 0,
                                    "format": fmt,
                                    "sample_count": sample_count,
                                    "example_resource": get_name(controller, res_id),
                                }
                            elif not is_depth and i not in color_targets_seen:
                                color_targets_seen[i] = {
                                    "index": i,
                                    "format": fmt,
                                    "sample_count": sample_count,
                                    "example_resource": get_name(controller, res_id),
                                }
                    if color_targets_seen or depth_target_seen:
                        break
            except Exception:
                pass

    # Fallback: check for swapchain buffer if no color targets found
    if not color_targets_seen:
        try:
            for t in controller.GetTextures():
                if t.creationFlags & rd.TextureCategory.SwapBuffer:
                    color_targets_seen[0] = {
                        "index": 0,
                        "format": str(t.format.Name()),
                        "sample_count": t.msSamp,
                        "example_resource": get_name(controller, t.resourceId),
                        "is_swapchain": True,
                    }
                    break
        except Exception:
            pass

    # Build render_targets list
    for i in sorted(color_targets_seen.keys()):
        entry = color_targets_seen[i]
        details["render_targets"].append({
            "index": entry["index"],
            "type": "ColorTarget",
            "format": entry["format"],
            "sample_count": entry["sample_count"],
            "example_resource": entry["example_resource"],
        })

    if depth_target_seen:
        details["render_targets"].append({
            "index": depth_target_seen["index"],
            "type": "DepthStencilTarget",
            "format": depth_target_seen["format"],
            "sample_count": depth_target_seen["sample_count"],
            "example_resource": depth_target_seen["example_resource"],
        })


def scan_depth_stencil_blend_state(controller, active_events, details):
    """Scan active events to find depth/stencil/blend state configuration."""
    # Try to find an event with meaningful state
    for eid in active_events:
        controller.SetFrameEvent(eid, False)
        state = controller.GetPipelineState()

        # Extract depth state if not already found
        if details["depth_state"] is None:
            extract_depth_state(controller, state, details)

        # Extract stencil state if not already found
        if details["stencil_state"] is None:
            extract_stencil_state(controller, state, details)

        # Extract blend state if not already found
        if details["blend_state"] is None:
            extract_blend_state(state, details)

        # If we have all state, we're done
        if (details["depth_state"] is not None and
            details["stencil_state"] is not None and
            details["blend_state"] is not None):
            break


def extract_stage_resources(controller, state, stage, stage_name, refl, details, active_events=None,
                            desc_set_contents=None, bound_desc_sets=None):
    """Extract resource bindings, constant blocks, and samplers for a stage from shader reflection.

    Args:
        desc_set_contents: Map from (descriptor_set_id, binding) -> binding_info (from vkUpdateDescriptorSets)
        bound_desc_sets: Map from set_index -> descriptor_set_id (from vkCmdBindDescriptorSets)
    """

    # Build a map of bound resources for this stage by scanning active events
    # Key: (is_rw, refl_index) -> resource_id
    bound_resource_map = {}

    # Also build a map by (set, binding) for descriptor set lookup
    # Key: (set_index, binding_index) -> resource_id
    set_binding_resource_map = {}

    events_to_scan = active_events[:10] if active_events else []

    # Pre-cache all resources by type for fallback name-based matching
    all_textures = {}  # name -> resourceId
    all_buffers = {}   # name -> resourceId
    try:
        for tex in controller.GetTextures():
            if tex.resourceId and tex.resourceId != rd.ResourceId.Null():
                # Store by full name and also by last component
                name = get_name(controller, tex.resourceId)
                all_textures[name] = tex.resourceId
                # Also index by last component (after last ::)
                if '::' in name:
                    short_name = name.rsplit('::', 1)[-1]
                    # Remove trailing ID suffix like ":123"
                    if ':' in short_name:
                        short_name = short_name.rsplit(':', 1)[0]
                    all_textures[short_name.lower()] = tex.resourceId
    except Exception:
        pass

    try:
        for res in controller.GetResources():
            if res.type == rd.ResourceType.Buffer and res.resourceId and res.resourceId != rd.ResourceId.Null():
                name = res.name
                all_buffers[name] = res.resourceId
                # Also index by last component
                if '::' in name:
                    short_name = name.rsplit('::', 1)[-1]
                    if ':' in short_name:
                        short_name = short_name.rsplit(':', 1)[0]
                    all_buffers[short_name.lower()] = res.resourceId
    except Exception:
        pass

    # Scan active events to find bound resources using GetReadOnlyResources/GetReadWriteResources
    for eid in events_to_scan:
        try:
            controller.SetFrameEvent(eid, False)
            event_state = controller.GetPipelineState()

            # Get read-only resources for this stage
            try:
                ro_list = event_state.GetReadOnlyResources(stage)
                for i, binding in enumerate(ro_list):
                    key = (False, i)  # (is_rw=False, index)
                    if key not in bound_resource_map:
                        try:
                            res_id = binding.descriptor.resource
                            if res_id is not None and res_id != rd.ResourceId.Null():
                                bound_resource_map[key] = res_id
                        except Exception:
                            pass
            except Exception:
                pass

            # Get read-write resources for this stage
            try:
                rw_list = event_state.GetReadWriteResources(stage)
                for i, binding in enumerate(rw_list):
                    key = (True, i)  # (is_rw=True, index)
                    if key not in bound_resource_map:
                        try:
                            res_id = binding.descriptor.resource
                            if res_id is not None and res_id != rd.ResourceId.Null():
                                bound_resource_map[key] = res_id
                        except Exception:
                            pass
            except Exception:
                pass

        except Exception:
            pass

    # Build set_binding_resource_map from descriptor set data (structured file parsing)
    # This works when GetReadOnlyResources/GetReadWriteResources return empty (compute pipelines)
    # but not for graphics pipelines using VK_EXT_descriptor_buffer
    if desc_set_contents and bound_desc_sets:
        for set_index, desc_set_id in bound_desc_sets.items():
            for (ds_id, binding_idx), binding_info in desc_set_contents.items():
                if ds_id == desc_set_id:
                    res_id = binding_info.get("buffer") or binding_info.get("image_view")
                    if res_id is not None:
                        set_binding_resource_map[(set_index, binding_idx)] = res_id

    def find_resource_by_name(binding_name, is_texture):
        """Try to find a resource by matching the binding name against known resources."""
        # Normalize the binding name (lowercase, remove common prefixes/suffixes)
        name_lower = binding_name.lower().strip()

        # Direct match
        if is_texture:
            if name_lower in all_textures:
                return all_textures[name_lower]
        else:
            if name_lower in all_buffers:
                return all_buffers[name_lower]

        # Search for partial matches (binding name contained in resource name)
        search_dict = all_textures if is_texture else all_buffers
        for res_name, res_id in search_dict.items():
            res_name_lower = res_name.lower()
            # Check if binding name is contained in resource name
            if name_lower in res_name_lower:
                return res_id
            # Check if resource name ends with binding name (common wgpu pattern)
            if res_name_lower.endswith(name_lower):
                return res_id

        return None

    # Build a map of resources used at our events by stage and type
    # This uses GetUsage() which works even when GetDescriptors() fails for virtual stores
    usage_map = {}  # (stage, is_rw, is_texture) -> list of resource_ids

    # Map RenderDoc ResourceUsage enum to our stage names
    usage_stage_map = {
        rd.ResourceUsage.VS_Resource: ("Vertex", False),
        rd.ResourceUsage.VS_RWResource: ("Vertex", True),
        rd.ResourceUsage.PS_Resource: ("Fragment", False),
        rd.ResourceUsage.PS_RWResource: ("Fragment", True),
        rd.ResourceUsage.CS_Resource: ("Compute", False),
        rd.ResourceUsage.CS_RWResource: ("Compute", True),
    }

    event_set = set(events_to_scan)

    # Check all textures
    for tex in controller.GetTextures():
        if tex.resourceId is None or tex.resourceId == rd.ResourceId.Null():
            continue
        try:
            usages = controller.GetUsage(tex.resourceId)
            for u in usages:
                if u.eventId in event_set and u.usage in usage_stage_map:
                    stage_name, is_rw = usage_stage_map[u.usage]
                    if stage_name == stage_name:  # Match our current stage
                        key = (stage_name, is_rw, True)  # True = texture
                        if key not in usage_map:
                            usage_map[key] = []
                        if tex.resourceId not in usage_map[key]:
                            usage_map[key].append(tex.resourceId)
        except Exception:
            pass

    # Check all buffers
    for res in controller.GetResources():
        if res.type != rd.ResourceType.Buffer:
            continue
        if res.resourceId is None or res.resourceId == rd.ResourceId.Null():
            continue
        try:
            usages = controller.GetUsage(res.resourceId)
            for u in usages:
                if u.eventId in event_set and u.usage in usage_stage_map:
                    u_stage, is_rw = usage_stage_map[u.usage]
                    key = (u_stage, is_rw, False)  # False = buffer
                    if key not in usage_map:
                        usage_map[key] = []
                    if res.resourceId not in usage_map[key]:
                        usage_map[key].append(res.resourceId)
        except Exception:
            pass

    def find_resource_by_usage(stage_name, is_rw, is_texture):
        """Find resources used at our events with matching usage type."""
        key = (stage_name, is_rw, is_texture)
        return usage_map.get(key, [])

    def get_example_resource(is_rw, refl_idx, binding_name, is_texture, set_index=None, binding_index=None):
        """Get example resource from pre-built map, with fallback to usage-based and name-based search."""
        # First try the direct descriptor path (works for compute pipelines)
        res_id = bound_resource_map.get((is_rw, refl_idx))
        if res_id is not None:
            return res_id

        # Second: try set/binding lookup from structured file parsing (works for graphics pipelines)
        if set_index is not None and binding_index is not None:
            res_id = set_binding_resource_map.get((set_index, binding_index))
            if res_id is not None:
                return res_id

        # Third: try usage-based matching (works when GetDescriptors fails)
        usage_candidates = find_resource_by_usage(stage_name, is_rw, is_texture)
        if len(usage_candidates) == 1:
            # Exactly one resource of this type used at this stage - must be it!
            return usage_candidates[0]
        elif len(usage_candidates) > 1:
            # Multiple candidates - try to narrow down by name
            for cand_id in usage_candidates:
                cand_name = get_name(controller, cand_id).lower()
                if binding_name.lower() in cand_name:
                    return cand_id
            # Still ambiguous - return first one as best guess
            return usage_candidates[0]

        # Last resort: try name-based matching
        return find_resource_by_name(binding_name, is_texture)

    # Read-only resources (textures, buffers) - extract from reflection directly
    try:
        for i, res_info in enumerate(refl.readOnlyResources):
            res_type = get_resource_type_str(res_info)
            is_texture = (res_type == "Texture")
            entry = {
                "stage": stage_name,
                "name": res_info.name,
                "type": res_type,
                "read_write": False,
            }
            # Get set/binding for descriptor set lookup
            set_idx = None
            bind_idx = None
            try:
                set_idx = res_info.fixedBindSetOrSpace
                bind_idx = res_info.fixedBindNumber
                entry["set"] = set_idx
                entry["binding"] = bind_idx
            except Exception:
                entry["binding"] = i

            # Find bound resource for example_resource and format
            # Pass set/binding for descriptor set lookup, binding name and type for fallback
            res_id = get_example_resource(False, i, res_info.name, is_texture, set_idx, bind_idx)

            if res_id is not None:
                entry["example_resource"] = get_name(controller, res_id)
                # For textures, get format
                if is_texture:
                    tex_desc = get_texture_by_id(controller, res_id)
                    if tex_desc is not None:
                        try:
                            entry["format"] = str(tex_desc.format.Name())
                        except Exception:
                            pass

            # For buffers, extract schema from reflection
            if res_type == "Buffer":
                schema = extract_buffer_schema(res_info)
                if schema:
                    entry["schema"] = schema

            details["resource_bindings"].append(entry)
    except Exception:
        pass

    # Read-write resources (UAV, SSBO, storage images) - extract from reflection directly
    try:
        for i, res_info in enumerate(refl.readWriteResources):
            res_type = get_resource_type_str(res_info)
            is_texture = (res_type == "Texture")
            entry = {
                "stage": stage_name,
                "name": res_info.name,
                "type": res_type,
                "read_write": True,
            }
            # Get set/binding for descriptor set lookup
            set_idx = None
            bind_idx = None
            try:
                set_idx = res_info.fixedBindSetOrSpace
                bind_idx = res_info.fixedBindNumber
                entry["set"] = set_idx
                entry["binding"] = bind_idx
            except Exception:
                entry["binding"] = i

            # Find bound resource for example_resource and format
            # Pass set/binding for descriptor set lookup, binding name and type for fallback
            res_id = get_example_resource(True, i, res_info.name, is_texture, set_idx, bind_idx)

            if res_id is not None:
                entry["example_resource"] = get_name(controller, res_id)
                # For textures, get format
                if is_texture:
                    tex_desc = get_texture_by_id(controller, res_id)
                    if tex_desc is not None:
                        try:
                            entry["format"] = str(tex_desc.format.Name())
                        except Exception:
                            pass

            # For buffers, extract schema from reflection
            if res_type == "Buffer":
                schema = extract_buffer_schema(res_info)
                if schema:
                    entry["schema"] = schema

            details["resource_bindings"].append(entry)
    except Exception:
        pass

    # Constant blocks (UBOs, push constants)
    try:
        for i, cb in enumerate(refl.constantBlocks):
            entry = {
                "stage": stage_name,
                "name": cb.name,
                "byte_size": cb.byteSize,
            }
            try:
                entry["set"] = cb.fixedBindSetOrSpace
                entry["binding"] = cb.fixedBindNumber
            except Exception:
                entry["binding"] = i
            details["constant_blocks"].append(entry)
    except Exception:
        pass

    # Samplers
    try:
        for i, sampler in enumerate(refl.samplers):
            entry = {
                "stage": stage_name,
                "name": sampler.name,
            }
            try:
                entry["set"] = sampler.fixedBindSetOrSpace
                entry["binding"] = sampler.fixedBindNumber
            except Exception:
                entry["binding"] = i
            details["samplers"].append(entry)
    except Exception:
        pass


def get_resource_type_str(res_info):
    """Determine if resource is buffer or texture."""
    try:
        vtype = res_info.variableType
        if len(vtype.members) > 0 or (vtype.rows == 0 and vtype.columns == 0):
            return "Buffer"
        return "Texture"
    except Exception:
        return "Unknown"


def scan_vertex_index_buffer_layouts(controller, active_events):
    """Scan all active events to discover vertex/index buffer layouts used with this pipeline."""
    # Track unique vertex buffer bindings by slot index
    vb_layouts_seen = {}  # slot_index -> {stride, attributes}
    index_buffer_seen = None

    for eid in active_events:
        controller.SetFrameEvent(eid, True)  # Force replay
        state = controller.GetPipelineState()

        try:
            attrs = state.GetVertexInputs()
            vbs = state.GetVBuffers()

            # Group attributes by vertex buffer slot
            attrs_by_vb = {}
            for attr in attrs:
                vb_idx = attr.vertexBuffer
                if vb_idx not in attrs_by_vb:
                    attrs_by_vb[vb_idx] = []
                attrs_by_vb[vb_idx].append(attr)

            # Process each vertex buffer
            for vb_idx, vb in enumerate(vbs):
                if vb.resourceId == rd.ResourceId.Null():
                    continue

                if vb_idx not in vb_layouts_seen:
                    vb_attrs = attrs_by_vb.get(vb_idx, [])
                    attr_list = []
                    for attr in vb_attrs:
                        fmt_str = "Unknown"
                        try:
                            fmt_str = str(attr.format.Name()) if attr.format else "Unknown"
                        except Exception:
                            try:
                                # Fallback: construct from components
                                fmt = attr.format
                                comp_type = str(fmt.compType).replace("CompType.", "")
                                fmt_str = "%s%dx%d" % (comp_type, fmt.compCount, fmt.compByteWidth * 8)
                            except Exception:
                                pass

                        attr_list.append({
                            "name": attr.name or "attr_%d" % len(attr_list),
                            "offset": attr.byteOffset,
                            "format": fmt_str,
                            "per_instance": attr.perInstance,
                        })

                    vb_layouts_seen[vb_idx] = {
                        "binding": vb_idx,
                        "stride": vb.byteStride,
                        "attributes": attr_list,
                        "example_resource": get_name(controller, vb.resourceId),
                    }

            # Check for index buffer
            if index_buffer_seen is None:
                try:
                    ib = state.GetIBuffer()
                    if ib.resourceId != rd.ResourceId.Null():
                        # Determine index format from stride
                        index_format = "Unknown"
                        if ib.byteStride == 2:
                            index_format = "Uint16"
                        elif ib.byteStride == 4:
                            index_format = "Uint32"

                        index_buffer_seen = {
                            "format": index_format,
                            "stride": ib.byteStride,
                            "example_resource": get_name(controller, ib.resourceId),
                        }
                except Exception:
                    pass

        except Exception:
            pass

    # Build vertex_buffers list sorted by binding index
    vertex_buffers = []
    for idx in sorted(vb_layouts_seen.keys()):
        vertex_buffers.append(vb_layouts_seen[idx])

    return vertex_buffers, index_buffer_seen


def extract_depth_state(controller, state, details):
    """Extract depth testing state for graphics pipelines."""
    depth_state = {}

    # Try to get Vulkan-specific state for more detail
    vk = None
    ds = None  # depthStencil state object
    try:
        vk = controller.GetVulkanPipelineState()
        if vk is not None:
            # Try different attribute names for depth/stencil state
            for attr in ["depthStencil", "depthState", "ds"]:
                try:
                    ds = getattr(vk, attr)
                    if ds is not None:
                        break
                except Exception:
                    continue
    except Exception:
        pass

    # Depth test enable
    val = None
    try:
        val = state.IsDepthTestEnabled()
    except Exception:
        pass
    if val is None and ds is not None:
        for attr in ["depthTestEnable", "depthEnable"]:
            try:
                val = getattr(ds, attr)
                break
            except Exception:
                continue
    if val is not None:
        depth_state["depth_test_enable"] = bool(val)

    # Depth write enable
    val = None
    try:
        val = state.IsDepthWriteEnabled()
    except Exception:
        pass
    if val is None and ds is not None:
        for attr in ["depthWriteEnable", "writeEnable"]:
            try:
                val = getattr(ds, attr)
                break
            except Exception:
                continue
    if val is not None:
        depth_state["depth_write_enable"] = bool(val)

    # Depth function/compare op
    val = None
    try:
        val = state.GetDepthFunction()
    except Exception:
        pass
    if val is None and ds is not None:
        for attr in ["depthCompareOp", "depthFunction", "compareOp", "func"]:
            try:
                val = getattr(ds, attr)
                break
            except Exception:
                continue
    if val is not None:
        val_str = str(val)
        # Clean up enum prefixes
        for prefix in ["CompareFunction.", "CompareOp.", "VK_COMPARE_OP_"]:
            val_str = val_str.replace(prefix, "")
        depth_state["depth_function"] = val_str

    # Depth bounds
    val = None
    try:
        val = state.IsDepthBoundsEnabled()
    except Exception:
        pass
    if val is None and ds is not None:
        try:
            val = ds.depthBoundsEnable
        except Exception:
            pass
    if val is not None:
        depth_state["depth_bounds_enable"] = bool(val)

    # Try to get min/max depth bounds
    if ds is not None:
        try:
            depth_state["min_depth_bounds"] = float(ds.minDepthBounds)
            depth_state["max_depth_bounds"] = float(ds.maxDepthBounds)
        except Exception:
            pass

    if depth_state:
        details["depth_state"] = depth_state


def extract_stencil_state(controller, state, details):
    """Extract stencil testing state for graphics pipelines."""
    stencil_state = {}

    vk = None
    ds = None
    try:
        vk = controller.GetVulkanPipelineState()
        if vk is not None:
            for attr in ["depthStencil", "depthState", "ds"]:
                try:
                    ds = getattr(vk, attr)
                    if ds is not None:
                        break
                except Exception:
                    continue
    except Exception:
        pass

    def clean_enum_str(val):
        """Clean up enum string representation."""
        val_str = str(val)
        for prefix in ["StencilOp.", "CompareOp.", "CompareFunction.", "StencilOperation.",
                       "VK_STENCIL_OP_", "VK_COMPARE_OP_"]:
            val_str = val_str.replace(prefix, "")
        return val_str

    # Get stencil face details - try both API and Vulkan state
    def get_stencil_face_from_api(is_front):
        try:
            face = state.GetStencilFace(is_front)
            result = {}
            # Try different attribute names for the comparison function
            for attr in ["function", "compareOp", "compare", "func"]:
                try:
                    result["compare"] = clean_enum_str(getattr(face, attr))
                    break
                except Exception:
                    continue
            # Fail op
            for attr in ["failOperation", "failOp", "stencilFailOp"]:
                try:
                    result["fail_op"] = clean_enum_str(getattr(face, attr))
                    break
                except Exception:
                    continue
            # Depth fail op
            for attr in ["depthFailOperation", "depthFailOp"]:
                try:
                    result["depth_fail_op"] = clean_enum_str(getattr(face, attr))
                    break
                except Exception:
                    continue
            # Pass op
            for attr in ["passOperation", "passOp", "depthPassOp"]:
                try:
                    result["pass_op"] = clean_enum_str(getattr(face, attr))
                    break
                except Exception:
                    continue

            # Get masks
            read_mask = None
            write_mask = None
            for attr in ["compareMask", "readMask", "stencilReadMask"]:
                try:
                    read_mask = int(getattr(face, attr))
                    break
                except Exception:
                    continue
            for attr in ["writeMask", "stencilWriteMask"]:
                try:
                    write_mask = int(getattr(face, attr))
                    break
                except Exception:
                    continue

            return result if result else None, read_mask, write_mask
        except Exception:
            return None, None, None

    def get_stencil_face_from_vk(face_obj):
        result = {}
        read_mask = None
        write_mask = None

        if face_obj is None:
            return None, None, None

        for attr, key in [
            ("failOp", "fail_op"), ("passOp", "pass_op"), ("depthFailOp", "depth_fail_op"),
            ("compareOp", "compare"), ("func", "compare"),
        ]:
            try:
                val = getattr(face_obj, attr)
                result[key] = clean_enum_str(val)
            except Exception:
                continue

        for attr in ["compareMask", "reference", "readMask"]:
            try:
                read_mask = int(getattr(face_obj, attr))
                break
            except Exception:
                continue

        for attr in ["writeMask"]:
            try:
                write_mask = int(getattr(face_obj, attr))
                break
            except Exception:
                continue

        return result if result else None, read_mask, write_mask

    # Try to get stencil enable state first (Vulkan-specific)
    stencil_enabled = None
    if ds is not None:
        for attr in ["stencilTestEnable", "stencilEnable"]:
            try:
                stencil_enabled = bool(getattr(ds, attr))
                break
            except Exception:
                continue

    # Front face
    front, front_read_mask, front_write_mask = get_stencil_face_from_api(True)
    if front is None and ds is not None:
        try:
            front, front_read_mask, front_write_mask = get_stencil_face_from_vk(ds.frontFace)
        except Exception:
            # Try alternative Vulkan paths
            try:
                front, front_read_mask, front_write_mask = get_stencil_face_from_vk(ds.front)
            except Exception:
                pass
    if front:
        stencil_state["front"] = front

    # Back face
    back, back_read_mask, back_write_mask = get_stencil_face_from_api(False)
    if back is None and ds is not None:
        try:
            back, back_read_mask, back_write_mask = get_stencil_face_from_vk(ds.backFace)
        except Exception:
            # Try alternative Vulkan paths
            try:
                back, back_read_mask, back_write_mask = get_stencil_face_from_vk(ds.back)
            except Exception:
                pass
    if back:
        stencil_state["back"] = back

    # Use front face masks for top-level (they're typically the same)
    # Only include if we actually got face state, or if stencil is enabled
    if front_read_mask is not None:
        stencil_state["read_mask"] = front_read_mask
    elif back_read_mask is not None:
        stencil_state["read_mask"] = back_read_mask
    elif ds is not None:
        # Try to get masks from depthStencil state directly
        for attr in ["stencilReadMask", "compareMask"]:
            try:
                stencil_state["read_mask"] = int(getattr(ds, attr))
                break
            except Exception:
                continue

    if front_write_mask is not None:
        stencil_state["write_mask"] = front_write_mask
    elif back_write_mask is not None:
        stencil_state["write_mask"] = back_write_mask
    elif ds is not None:
        # Try to get masks from depthStencil state directly
        for attr in ["stencilWriteMask", "writeMask"]:
            try:
                stencil_state["write_mask"] = int(getattr(ds, attr))
                break
            except Exception:
                continue

    # Only output stencil_state if we have meaningful content
    # (front/back face ops, or non-trivial masks)
    if stencil_state:
        has_face_ops = "front" in stencil_state or "back" in stencil_state
        has_nonzero_masks = (stencil_state.get("read_mask", 0) != 0 or
                            stencil_state.get("write_mask", 0) != 0)
        if has_face_ops or has_nonzero_masks or stencil_enabled:
            details["stencil_state"] = stencil_state


def _serialize_blend_op(blend):
    """Serialize a blend operation."""
    return {
        "source": str(blend.source).replace("BlendMultiplier.", "").replace("VK_BLEND_FACTOR_", ""),
        "destination": str(blend.destination).replace("BlendMultiplier.", "").replace("VK_BLEND_FACTOR_", ""),
        "operation": str(blend.operation).replace("BlendOperation.", "").replace("VK_BLEND_OP_", ""),
    }


def extract_blend_state(state, details):
    """Extract blend state for graphics pipelines."""
    blend_state = {}

    try:
        blends = state.GetColorBlends()
        attachments = []
        for i, b in enumerate(blends):
            entry = {
                "index": i,
                "enabled": b.enabled,
                "write_mask": b.writeMask,
            }
            if b.enabled:
                try:
                    entry["color_blend"] = _serialize_blend_op(b.colorBlend)
                except Exception:
                    pass
                try:
                    entry["alpha_blend"] = _serialize_blend_op(b.alphaBlend)
                except Exception:
                    pass
            attachments.append(entry)

        if attachments:
            blend_state["attachments"] = attachments
    except Exception:
        pass

    try:
        bf = state.GetBlendFactor()
        blend_state["blend_factor"] = [float(bf[0]), float(bf[1]), float(bf[2]), float(bf[3])]
    except Exception:
        pass

    try:
        logic_enabled = state.IsLogicOpEnabled()
        blend_state["logic_op_enabled"] = bool(logic_enabled)
        if logic_enabled:
            blend_state["logic_op"] = str(state.GetLogicOp()).replace("LogicOperation.", "")
    except Exception:
        pass

    if blend_state:
        details["blend_state"] = blend_state


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    with open(REQ_PATH, "r", encoding="utf-8") as f:
        req = json.load(f)

    pipeline_name = req["pipeline_name"]

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
            # Find the pipeline
            pipe_res = find_pipeline(controller, pipeline_name)
            pipe_id = pipe_res.resourceId

            # Scan all actions
            actions = list(flatten_actions(controller.GetRootActions()))
            if not actions:
                raise RuntimeError("No actions found in capture")

            # Extract pipeline details
            details = extract_pipeline_details(controller, pipe_id, pipe_res.name, actions)

            write_envelope(True, result=details)
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
