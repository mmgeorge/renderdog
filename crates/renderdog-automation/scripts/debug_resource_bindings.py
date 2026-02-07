"""
debug_resource_bindings.py -- Debug script to understand why GetReadWriteResources/GetReadOnlyResources return empty.

This script tests different approaches to extracting bound resource information from a RenderDoc capture.
"""

import json
import traceback

import renderdoc as rd


def write_result(data, path="debug_resource_bindings.result.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def debug_event(controller, eid, debug_data):
    """Debug a single event to understand what's available."""
    controller.SetFrameEvent(eid, False)
    state = controller.GetPipelineState()

    event_debug = {"event_id": eid, "stages": {}, "descriptor_accesses": []}

    # Use the recommended GetDescriptorAccess API
    try:
        desc_access_list = controller.GetDescriptorAccess()
        for access in desc_access_list:
            access_info = {
                "stage": _STAGE_NAMES.get(access.stage, str(access.stage)),
                "type": str(access.type),
                "index": access.index,
                "descriptor_store": str(access.descriptorStore),
                "byte_offset": access.byteOffset,
                "byte_size": access.byteSize,
            }

            # Try to get the actual resource from the descriptor
            try:
                desc_store = access.descriptorStore
                if desc_store is not None and desc_store != rd.ResourceId.Null():
                    try:
                        desc_range = rd.DescriptorRange(access)
                    except Exception:
                        desc_range = rd.DescriptorRange()
                        desc_range.offset = access.byteOffset
                        desc_range.descriptorSize = access.byteSize
                        desc_range.count = 1
                        desc_range.type = access.type

                    descriptors = controller.GetDescriptors(desc_store, [desc_range])
                    access_info["descriptors_count"] = len(descriptors) if descriptors else 0

                    # Also try GetDescriptorLocations
                    try:
                        locations = controller.GetDescriptorLocations(desc_store, [desc_range])
                        if locations and len(locations) > 0:
                            loc = locations[0]
                            loc_info = {}
                            for attr in ['fixedBindNumber', 'logicalBindName', 'category', 'accessedByStages']:
                                try:
                                    val = getattr(loc, attr)
                                    loc_info[attr] = str(val)
                                except Exception:
                                    pass
                            if loc_info:
                                access_info["location"] = loc_info
                    except Exception:
                        pass

                    if descriptors and len(descriptors) > 0:
                        desc = descriptors[0]
                        # Show all descriptor attributes for debugging
                        desc_attrs = {}
                        for attr in ['resource', 'secondary', 'type', 'flags']:
                            try:
                                val = getattr(desc, attr)
                                if attr in ['resource', 'secondary']:
                                    if val is not None and val != rd.ResourceId.Null():
                                        desc_attrs[attr] = get_name(controller, val)
                                    else:
                                        desc_attrs[attr] = "null" if val is None else str(val)
                                else:
                                    desc_attrs[attr] = str(val)
                            except Exception:
                                pass
                        if desc_attrs:
                            access_info["descriptor_details"] = desc_attrs

                        if desc.resource is not None and desc.resource != rd.ResourceId.Null():
                            access_info["resource_name"] = get_name(controller, desc.resource)
            except Exception as e:
                import traceback
                access_info["descriptor_error"] = str(e) + "\n" + traceback.format_exc()

            event_debug["descriptor_accesses"].append(access_info)
    except Exception as e:
        event_debug["descriptor_access_error"] = str(e)

    for stage in _ALL_STAGES:
        stage_name = _STAGE_NAMES.get(stage, str(stage))
        refl = state.GetShaderReflection(stage)

        if refl is None:
            continue

        stage_debug = {
            "shader_name": get_name(controller, refl.resourceId),
            "read_only_reflection": [],
            "read_write_reflection": [],
        }

        # Reflection info
        for i, res in enumerate(refl.readOnlyResources):
            try:
                ro_info = {
                    "index": i,
                    "name": res.name,
                    "set": getattr(res, 'fixedBindSetOrSpace', None),
                    "binding": getattr(res, 'fixedBindNumber', None),
                }
                stage_debug["read_only_reflection"].append(ro_info)
            except Exception as e:
                stage_debug["read_only_reflection"].append({"index": i, "error": str(e)})

        for i, res in enumerate(refl.readWriteResources):
            try:
                rw_info = {
                    "index": i,
                    "name": res.name,
                    "set": getattr(res, 'fixedBindSetOrSpace', None),
                    "binding": getattr(res, 'fixedBindNumber', None),
                }
                stage_debug["read_write_reflection"].append(rw_info)
            except Exception as e:
                stage_debug["read_write_reflection"].append({"index": i, "error": str(e)})

        event_debug["stages"][stage_name] = stage_debug

    # Try Vulkan-specific approach for descriptor buffers
    try:
        vk = controller.GetVulkanPipelineState()
        if vk is not None:
            vk_info = {"vk_found": True}

            # List all attributes of vk.graphics
            if hasattr(vk, 'graphics'):
                gfx_attrs = [a for a in dir(vk.graphics) if not a.startswith('_')]
                vk_info["graphics_attrs"] = gfx_attrs[:30]

            # Check for descriptor buffer bindings (this is what wgpu uses)
            try:
                if hasattr(vk, 'graphics') and hasattr(vk.graphics, 'descriptorBuffers'):
                    db_list = vk.graphics.descriptorBuffers
                    vk_info["descriptor_buffers_found"] = True
                    vk_info["descriptor_buffers_count"] = len(list(db_list)) if db_list else 0
                    if db_list:
                        db_info = []
                        for idx, db in enumerate(db_list):
                            entry = {"index": idx}
                            # Show all attributes of descriptor buffer entry
                            db_attrs = [a for a in dir(db) if not a.startswith('_')]
                            entry["db_attrs"] = db_attrs[:20]
                            # Get the buffer resource
                            for attr in ['bufferResourceId', 'byteOffset', 'byteSize', 'address']:
                                try:
                                    val = getattr(db, attr)
                                    if attr == 'bufferResourceId' and val and val != rd.ResourceId.Null():
                                        entry[attr] = get_name(controller, val)
                                        entry["buffer_id"] = str(val)
                                    elif attr == 'address':
                                        entry[attr] = hex(val) if val else str(val)
                                    else:
                                        entry[attr] = val
                                except Exception as e:
                                    entry[attr + "_error"] = str(e)
                            db_info.append(entry)
                            if idx >= 5:
                                break
                        vk_info["descriptor_buffers"] = db_info
            except Exception as e:
                import traceback
                vk_info["descriptor_buffers_error"] = str(e) + "\n" + traceback.format_exc()

            # Also check compute state
            try:
                if hasattr(vk, 'compute') and hasattr(vk.compute, 'descriptorBuffers'):
                    db_list = vk.compute.descriptorBuffers
                    vk_info["compute_descriptor_buffers_count"] = len(list(db_list)) if db_list else 0
            except Exception:
                pass

            # Also try descriptor sets
            try:
                if hasattr(vk, 'graphics') and hasattr(vk.graphics, 'descriptorSets'):
                    ds_list = vk.graphics.descriptorSets
                    vk_info["descriptor_sets_count"] = len(list(ds_list)) if ds_list else 0
                    if ds_list:
                        ds_info = []
                        for idx, ds in enumerate(ds_list):
                            entry = {"set": idx}
                            # Show all attributes of descriptor set
                            ds_attrs = [a for a in dir(ds) if not a.startswith('_')]
                            entry["ds_attrs"] = ds_attrs[:15]
                            # Get bindings
                            if hasattr(ds, 'bindings'):
                                try:
                                    bindings = ds.bindings
                                    entry["bindings_count"] = len(list(bindings)) if bindings else 0
                                    if bindings:
                                        bind_info = []
                                        for b_idx, bind in enumerate(bindings):
                                            b_entry = {"binding": b_idx}
                                            bind_attrs = [a for a in dir(bind) if not a.startswith('_')]
                                            b_entry["bind_attrs"] = bind_attrs[:10]
                                            if hasattr(bind, 'type'):
                                                b_entry["type"] = str(bind.type)
                                            if hasattr(bind, 'binds'):
                                                try:
                                                    binds = bind.binds
                                                    b_entry["binds_count"] = len(list(binds)) if binds else 0
                                                    if binds:
                                                        resources = []
                                                        for elem in binds[:3]:
                                                            elem_attrs = [a for a in dir(elem) if not a.startswith('_')]
                                                            elem_info = {"elem_attrs": elem_attrs[:10]}
                                                            for r_attr in ['resourceResourceId', 'samplerResourceId', 'resource']:
                                                                try:
                                                                    r_val = getattr(elem, r_attr)
                                                                    if r_val and r_val != rd.ResourceId.Null():
                                                                        elem_info[r_attr] = get_name(controller, r_val)
                                                                except Exception:
                                                                    pass
                                                            resources.append(elem_info)
                                                        if resources:
                                                            b_entry["resources"] = resources
                                                except Exception as e:
                                                    b_entry["binds_error"] = str(e)
                                            bind_info.append(b_entry)
                                            if b_idx >= 10:
                                                break
                                        entry["bindings"] = bind_info
                                except Exception as e:
                                    entry["bindings_error"] = str(e)
                            ds_info.append(entry)
                            if idx >= 5:
                                break
                        vk_info["descriptor_sets"] = ds_info
            except Exception as e:
                import traceback
                vk_info["descriptor_sets_error"] = str(e) + "\n" + traceback.format_exc()

            if vk_info:
                event_debug["vulkan_state"] = vk_info
    except Exception as e:
        event_debug["vulkan_error"] = str(e)

    # Try to find resources used at this event via GetUsage
    try:
        resources_at_event = []
        all_usages_at_event = []
        # Get all resources and check their usage at this event
        for res in controller.GetResources():
            try:
                usages = controller.GetUsage(res.resourceId)
                for usage in usages:
                    if usage.eventId == eid:
                        usage_str = str(usage.usage)
                        # Track all usages for debugging
                        all_usages_at_event.append({
                            "name": res.name,
                            "type": str(res.type),
                            "usage": usage_str,
                        })
                        # Filter to relevant usages (shader reads/writes, excluding render targets)
                        if ('VS' in usage_str or 'PS' in usage_str or 'CS' in usage_str or
                            'Storage' in usage_str or 'Uniform' in usage_str or
                            'Resource' in usage_str):
                            resources_at_event.append({
                                "name": res.name,
                                "type": str(res.type),
                                "usage": usage_str,
                            })
                        break  # Found usage at this event
            except Exception:
                pass

        if resources_at_event:
            event_debug["resources_used"] = resources_at_event[:20]  # Limit output
        # Always show all usages for first few events for debugging
        if len(all_usages_at_event) > 0 and len([e for e in debug_data["events"] if "all_usages" in e]) < 2:
            event_debug["all_usages"] = all_usages_at_event[:30]
    except Exception as e:
        event_debug["resources_used_error"] = str(e)

    debug_data["events"].append(event_debug)


def find_pipeline_events(controller, pipeline_name, actions):
    """Find events where a specific pipeline is active."""
    events = []
    pipeline_id = None

    # Find pipeline
    for res in controller.GetResources():
        if res.name == pipeline_name and res.type == rd.ResourceType.PipelineState:
            pipeline_id = res.resourceId
            break
        if res.type == rd.ResourceType.PipelineState and pipeline_name in res.name:
            pipeline_id = res.resourceId
            break

    if pipeline_id is None:
        return None, []

    for action in actions:
        eid = action.eventId
        controller.SetFrameEvent(eid, False)
        state = controller.GetPipelineState()

        try:
            gfx_pipe = state.GetGraphicsPipelineObject()
            if gfx_pipe == pipeline_id:
                events.append(eid)
                continue
        except Exception:
            pass

        try:
            comp_pipe = state.GetComputePipelineObject()
            if comp_pipe == pipeline_id:
                events.append(eid)
                continue
        except Exception:
            pass

    return pipeline_id, events


def test_name_based_matching(controller, debug_data):
    """Test name-based resource matching as fallback for descriptor buffers."""
    # Collect all textures and buffers
    all_textures = {}
    all_buffers = {}

    try:
        for tex in controller.GetTextures():
            if tex.resourceId and tex.resourceId != rd.ResourceId.Null():
                name = get_name(controller, tex.resourceId)
                all_textures[name] = tex.resourceId
    except Exception as e:
        debug_data["texture_enum_error"] = str(e)

    try:
        for res in controller.GetResources():
            if res.type == rd.ResourceType.Buffer and res.resourceId and res.resourceId != rd.ResourceId.Null():
                all_buffers[res.name] = res.resourceId
    except Exception as e:
        debug_data["buffer_enum_error"] = str(e)

    # Sample texture and buffer names for debugging
    debug_data["sample_textures"] = list(all_textures.keys())[:20]
    debug_data["sample_buffers"] = list(all_buffers.keys())[:20]

    # Test bindings we expect to find (from shader reflection in previous debug runs)
    test_bindings = [
        ("color_texture", True),
        ("normal_texture", True),
        ("roughness_metallic_texture", True),
        ("entity_transforms", False),
        ("primitive_transform", False),
        ("primitive_material", False),
    ]

    def find_resource_by_name(binding_name, is_texture):
        name_lower = binding_name.lower().strip()
        search_dict = all_textures if is_texture else all_buffers

        # Direct match
        if name_lower in {k.lower(): k for k in search_dict}:
            for k, v in search_dict.items():
                if k.lower() == name_lower:
                    return k
            return None

        # Partial match
        for res_name in search_dict.keys():
            res_name_lower = res_name.lower()
            if name_lower in res_name_lower or res_name_lower.endswith(name_lower):
                return res_name

        return None

    match_results = []
    for binding_name, is_texture in test_bindings:
        matched = find_resource_by_name(binding_name, is_texture)
        match_results.append({
            "binding": binding_name,
            "is_texture": is_texture,
            "matched": matched,
        })

    debug_data["name_matching_test"] = match_results


def main():
    # Hardcoded test parameters (sys.argv not available in qrenderdoc embedded Python)
    capture_path = r"C:\Users\mattm\AppData\Local\Temp\RenderDoc\run-game_2026.02.01_16.33_frame395.rdc"
    pipelines_to_test = [
        "model::pbr::pb_render_pipeline::pipeline:17",
        "physics::compute_pipeline::update_particles:18",
    ]

    debug_data = {
        "capture_path": capture_path,
        "pipelines_tested": pipelines_to_test,
        "events": [],
        "errors": [],
    }

    rd.InitialiseReplay(rd.GlobalEnvironment(), [])

    cap = rd.OpenCaptureFile()
    try:
        result = cap.OpenFile(capture_path, "", None)
        if result != rd.ResultCode.Succeeded:
            debug_data["errors"].append("Couldn't open file: " + str(result))
            write_result(debug_data)
            return

        if not cap.LocalReplaySupport():
            debug_data["errors"].append("Capture cannot be replayed")
            write_result(debug_data)
            return

        result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if result != rd.ResultCode.Succeeded:
            debug_data["errors"].append("Couldn't initialise replay: " + str(result))
            write_result(debug_data)
            return

        try:
            actions = list(flatten_actions(controller.GetRootActions()))
            debug_data["total_actions"] = len(actions)

            # Test name-based matching for fallback
            test_name_based_matching(controller, debug_data)

            for pipeline_name in pipelines_to_test:
                pipe_id, events = find_pipeline_events(controller, pipeline_name, actions)

                pipe_info = {
                    "pipeline_name": pipeline_name,
                    "pipeline_id": int(pipe_id) if pipe_id else None,
                    "event_count": len(events),
                    "events": events[:10],  # First 10 events
                }
                debug_data.setdefault("pipelines", []).append(pipe_info)

                # Debug first few events
                for eid in events[:3]:
                    debug_event(controller, eid, debug_data)

            # Also debug a few events regardless of pipeline
            debug_data["sample_event_debug"] = []
            for action in actions[:5]:
                debug_event(controller, action.eventId, debug_data)

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

    write_result(debug_data)
    print("Debug output written to debug_resource_bindings.result.json")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        write_result({"error": traceback.format_exc()})
        print("Error:", traceback.format_exc())
    raise SystemExit(0)
