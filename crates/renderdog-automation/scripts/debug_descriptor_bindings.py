"""Debug script to explore vkCmdBindDescriptorSets in the structured file."""

import json
import renderdoc as rd

REQ_PATH = "debug_descriptor_bindings.request.json"
RESP_PATH = "debug_descriptor_bindings.response.json"


def write_result(data):
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_name(controller, rid):
    """Get resource name by ID (accepts ResourceId or int)."""
    try:
        # Convert to int for comparison
        target_id = int(rid)
        for r in controller.GetResources():
            if int(r.resourceId) == target_id:
                return r.name
    except Exception:
        pass
    return str(rid)


def explore_sd_object(obj, depth=0, max_depth=5):
    """Recursively explore an SDObject and return its structure."""
    if depth > max_depth:
        return "..."

    result = {}

    try:
        if hasattr(obj, 'name') and obj.name:
            result["name"] = obj.name
    except Exception:
        pass

    try:
        if hasattr(obj, 'type'):
            result["type"] = str(obj.type.basetype) if hasattr(obj.type, 'basetype') else str(obj.type)
    except Exception:
        pass

    # Try to get value
    try:
        basetype = obj.type.basetype if hasattr(obj.type, 'basetype') else None
        if basetype == rd.SDBasic.UnsignedInteger:
            result["value"] = obj.AsInt()
        elif basetype == rd.SDBasic.SignedInteger:
            result["value"] = obj.AsInt()
        elif basetype == rd.SDBasic.Resource:
            rid = obj.AsResourceId()
            if rid != rd.ResourceId.Null():
                result["value"] = int(rid)
    except Exception:
        pass

    # Explore children
    try:
        num_children = obj.NumChildren() if hasattr(obj, 'NumChildren') else 0
        if num_children > 0:
            result["children"] = []
            for i in range(num_children):
                child = obj.GetChild(i)
                result["children"].append(explore_sd_object(child, depth + 1, max_depth))
    except Exception:
        pass

    return result


def get_sd_child(obj, name):
    """Get child by name."""
    try:
        for i in range(obj.NumChildren()):
            child = obj.GetChild(i)
            if child.name == name:
                return child
    except Exception:
        pass
    return None


def get_sd_value(obj, name, default=None):
    """Get child value by name."""
    child = get_sd_child(obj, name)
    if child is None:
        return default
    try:
        basetype = child.type.basetype
        if basetype == rd.SDBasic.UnsignedInteger:
            return child.AsInt()
        elif basetype == rd.SDBasic.SignedInteger:
            return child.AsInt()
        elif basetype == rd.SDBasic.Resource:
            rid = child.AsResourceId()
            return int(rid) if rid != rd.ResourceId.Null() else None
        elif basetype == rd.SDBasic.Enum:
            return child.AsString() or child.AsInt()
    except Exception:
        pass
    return default


def get_sd_array(obj, name):
    """Get array children."""
    child = get_sd_child(obj, name)
    if child is None:
        return []
    result = []
    try:
        for i in range(child.NumChildren()):
            result.append(child.GetChild(i))
    except Exception:
        pass
    return result


def main():
    with open(REQ_PATH, "r", encoding="utf-8") as f:
        req = json.load(f)

    capture_path = req["capture_path"]
    pipeline_layout_id = req.get("pipeline_layout_id")  # Optional: filter by pipeline layout

    rd.InitialiseReplay(rd.GlobalEnvironment(), [])

    cap = rd.OpenCaptureFile()
    result = cap.OpenFile(capture_path, "", None)
    if result != rd.ResultCode.Succeeded:
        write_result({"error": f"Couldn't open file: {result}"})
        return

    result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
    if result != rd.ResultCode.Succeeded:
        write_result({"error": f"Couldn't open capture: {result}"})
        return

    try:
        sfile = controller.GetStructuredFile()

        # Step 1: Build a map of descriptor set contents from vkUpdateDescriptorSets
        # descriptor_set_id -> {binding -> {buffer: id, image: id, sampler: id}}
        descriptor_set_contents = {}

        for chunk in sfile.chunks:
            if "UpdateDescriptorSets" not in chunk.name:
                continue

            writes = get_sd_array(chunk, "pDescriptorWrites")
            for write in writes:
                dst_set = get_sd_value(write, "dstSet")
                dst_binding = get_sd_value(write, "dstBinding", 0)
                desc_type = get_sd_value(write, "descriptorType")

                if dst_set is None:
                    continue

                if dst_set not in descriptor_set_contents:
                    descriptor_set_contents[dst_set] = {}

                binding_info = {"type": desc_type}

                # Check for buffer info
                buffer_infos = get_sd_array(write, "pBufferInfo")
                if buffer_infos:
                    buf_info = buffer_infos[0]
                    buffer_id = get_sd_value(buf_info, "buffer")
                    if buffer_id:
                        binding_info["buffer"] = buffer_id
                        binding_info["buffer_name"] = get_name(controller, buffer_id)

                # Check for image info
                image_infos = get_sd_array(write, "pImageInfo")
                if image_infos:
                    img_info = image_infos[0]
                    image_view = get_sd_value(img_info, "imageView")
                    sampler = get_sd_value(img_info, "sampler")
                    if image_view:
                        binding_info["image_view"] = image_view
                        binding_info["image_view_name"] = get_name(controller, image_view)
                    if sampler:
                        binding_info["sampler"] = sampler

                descriptor_set_contents[dst_set][dst_binding] = binding_info

        # Step 2: Find vkCmdBindDescriptorSets calls for our pipeline layout
        bound_sets = []  # {layout, first_set, sets: [{set_id, bindings}]}

        for chunk in sfile.chunks:
            if "BindDescriptorSets" not in chunk.name:
                continue

            layout = get_sd_value(chunk, "layout")
            first_set = get_sd_value(chunk, "firstSet", 0)
            bind_point = get_sd_value(chunk, "pipelineBindPoint")

            # If filtering by layout, skip non-matching
            if pipeline_layout_id and layout != pipeline_layout_id:
                continue

            sets_arr = get_sd_array(chunk, "pDescriptorSets")
            sets_info = []
            for i, set_obj in enumerate(sets_arr):
                set_id = None
                try:
                    set_id = int(set_obj.AsResourceId())
                except Exception:
                    pass

                if set_id:
                    bindings = descriptor_set_contents.get(set_id, {})
                    sets_info.append({
                        "set_index": first_set + i,
                        "set_id": set_id,
                        "set_name": get_name(controller, set_id),
                        "bindings": bindings,
                    })

            if sets_info:
                bound_sets.append({
                    "layout": layout,
                    "layout_name": get_name(controller, layout) if layout else None,
                    "bind_point": bind_point,
                    "sets": sets_info,
                })

            if len(bound_sets) >= 20:  # Limit output
                break

        # Step 3: Find vkCmdPushDescriptorSetKHR calls (push descriptors)
        push_descriptor_calls = []
        for chunk in sfile.chunks:
            if "PushDescriptorSet" not in chunk.name:
                continue

            layout = get_sd_value(chunk, "layout")
            set_index = get_sd_value(chunk, "set", 0)
            bind_point = get_sd_value(chunk, "pipelineBindPoint")

            writes = get_sd_array(chunk, "pDescriptorWrites")
            bindings = {}
            for write in writes:
                dst_binding = get_sd_value(write, "dstBinding", 0)
                desc_type = get_sd_value(write, "descriptorType")
                binding_info = {"type": desc_type}

                # Check for buffer info
                buffer_infos = get_sd_array(write, "pBufferInfo")
                if buffer_infos:
                    buf_info = buffer_infos[0]
                    buffer_id = get_sd_value(buf_info, "buffer")
                    if buffer_id:
                        binding_info["buffer"] = buffer_id
                        binding_info["buffer_name"] = get_name(controller, buffer_id)

                # Check for image info
                image_infos = get_sd_array(write, "pImageInfo")
                if image_infos:
                    img_info = image_infos[0]
                    image_view = get_sd_value(img_info, "imageView")
                    sampler = get_sd_value(img_info, "sampler")
                    if image_view:
                        binding_info["image_view"] = image_view
                        binding_info["image_view_name"] = get_name(controller, image_view)
                    if sampler:
                        binding_info["sampler"] = sampler

                bindings[dst_binding] = binding_info

            if bindings:
                push_descriptor_calls.append({
                    "layout": layout,
                    "layout_name": get_name(controller, layout) if layout else None,
                    "bind_point": bind_point,
                    "set": set_index,
                    "bindings": bindings,
                })

            if len(push_descriptor_calls) >= 10:  # Limit output
                break

        # Step 4: Look for ALL graphics BindDescriptorSets calls
        graphics_bound_sets = []
        all_graphics_layouts = set()
        for chunk in sfile.chunks:
            if "BindDescriptorSets" not in chunk.name:
                continue

            bind_point = get_sd_value(chunk, "pipelineBindPoint")
            if bind_point and "GRAPHICS" in str(bind_point):
                layout = get_sd_value(chunk, "layout")
                layout_name = get_name(controller, layout) if layout else None
                all_graphics_layouts.add((layout, layout_name))
                first_set = get_sd_value(chunk, "firstSet", 0)

                sets_arr = get_sd_array(chunk, "pDescriptorSets")
                sets_info = []
                for i, set_obj in enumerate(sets_arr):
                    set_id = None
                    try:
                        set_id = int(set_obj.AsResourceId())
                    except Exception:
                        pass

                    if set_id:
                        bindings = descriptor_set_contents.get(set_id, {})
                        sets_info.append({
                            "set_index": first_set + i,
                            "set_id": set_id,
                            "set_name": get_name(controller, set_id),
                            "bindings": bindings,
                        })

                if sets_info:
                    graphics_bound_sets.append({
                        "layout": layout,
                        "layout_name": layout_name,
                        "bind_point": bind_point,
                        "sets": sets_info,
                    })

        # Filter to only show the first 5 graphics bound sets plus any with "pbr" in name
        pbr_bound_sets = [b for b in graphics_bound_sets if b.get("layout_name") and "pbr" in b["layout_name"].lower()]
        sample_graphics_bound_sets = graphics_bound_sets[:5]

        output = {
            "total_descriptor_sets_with_content": len(descriptor_set_contents),
            "bound_sets_sample": bound_sets[:5],  # Limit to 5
            "push_descriptor_calls": push_descriptor_calls,
            "all_graphics_layouts": [{"id": l, "name": n} for l, n in sorted(all_graphics_layouts)],
            "graphics_bound_sets_sample": sample_graphics_bound_sets,
            "pbr_bound_sets": pbr_bound_sets,
        }

        write_result(output)

    finally:
        controller.Shutdown()
        cap.Shutdown()
        rd.ShutdownReplay()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        write_result({"error": traceback.format_exc()})
    raise SystemExit(0)
