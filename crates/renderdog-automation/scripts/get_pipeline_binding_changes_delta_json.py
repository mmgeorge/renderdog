"""
get_pipeline_binding_changes_delta_json.py -- Track pipeline binding changes across a frame.

Finds a pipeline by name and tracks which resources are bound at each binding point
across all events where the pipeline is active. Returns delta-encoded changes showing
when bindings change.

Request parameters:
  - pipeline_name: Name of the pipeline to track
  - capture_path: Path to the .rdc capture file

Returns:
  - pipeline_name, pipeline_type
  - total_changes: Number of binding changes detected
  - bindings: Array of tracked bindings with initial state and changes
"""

import json
import traceback

import renderdoc as rd


REQ_PATH = "get_pipeline_binding_changes_delta_json.request.json"
RESP_PATH = "get_pipeline_binding_changes_delta_json.response.json"


def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)


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


# ---------------------------------------------------------------------------
# Pipeline finding
# ---------------------------------------------------------------------------

def find_pipeline(controller, pipeline_name):
    """Locate the target pipeline's resource description by name."""
    for res in controller.GetResources():
        if res.name == pipeline_name and res.type == rd.ResourceType.PipelineState:
            return res

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
    if rid == rd.ResourceId.Null():
        return None
    try:
        for r in controller.GetResources():
            if r.resourceId == rid:
                return r.name
    except Exception:
        pass
    return str(rid)


# ---------------------------------------------------------------------------
# Binding tracking
# ---------------------------------------------------------------------------

def get_binding_key(stage_name, binding_type, set_num, binding_num, name):
    """Create a unique key for a binding point."""
    return (stage_name, binding_type, set_num, binding_num, name)


def extract_current_bindings(controller, state, pipeline_type):
    """Extract all current resource bindings from pipeline state."""
    bindings = {}

    stages = _ALL_STAGES if pipeline_type == "Graphics" else [rd.ShaderStage.Compute]

    for stage in stages:
        refl = state.GetShaderReflection(stage)
        if refl is None:
            continue

        stage_name = _STAGE_NAMES.get(stage, str(stage))

        # Read-only resources
        try:
            ro_list = state.GetReadOnlyResources(stage)
            for i, binding in enumerate(ro_list):
                if i < len(refl.readOnlyResources):
                    res_info = refl.readOnlyResources[i]
                    try:
                        set_num = res_info.fixedBindSetOrSpace
                        binding_num = res_info.fixedBindNumber
                    except Exception:
                        set_num = 0
                        binding_num = i

                    key = get_binding_key(stage_name, "Resource", set_num, binding_num, res_info.name)
                    bound_res = binding.descriptor.resource
                    bindings[key] = {
                        "resource_id": int(bound_res) if bound_res != rd.ResourceId.Null() else None,
                        "resource_name": get_name(controller, bound_res),
                    }
        except Exception:
            pass

        # Read-write resources
        try:
            rw_list = state.GetReadWriteResources(stage)
            for i, binding in enumerate(rw_list):
                if i < len(refl.readWriteResources):
                    res_info = refl.readWriteResources[i]
                    try:
                        set_num = res_info.fixedBindSetOrSpace
                        binding_num = res_info.fixedBindNumber
                    except Exception:
                        set_num = 0
                        binding_num = i

                    key = get_binding_key(stage_name, "RWResource", set_num, binding_num, res_info.name)
                    bound_res = binding.descriptor.resource
                    bindings[key] = {
                        "resource_id": int(bound_res) if bound_res != rd.ResourceId.Null() else None,
                        "resource_name": get_name(controller, bound_res),
                    }
        except Exception:
            pass

        # Samplers
        try:
            sampler_list = state.GetSamplers(stage)
            for i, binding in enumerate(sampler_list):
                if i < len(refl.samplers):
                    sampler_info = refl.samplers[i]
                    try:
                        set_num = sampler_info.fixedBindSetOrSpace
                        binding_num = sampler_info.fixedBindNumber
                    except Exception:
                        set_num = 0
                        binding_num = i

                    key = get_binding_key(stage_name, "Sampler", set_num, binding_num, sampler_info.name)
                    bound_res = binding.descriptor.resource
                    bindings[key] = {
                        "resource_id": int(bound_res) if bound_res != rd.ResourceId.Null() else None,
                        "resource_name": get_name(controller, bound_res),
                    }
        except Exception:
            pass

    # For graphics pipelines, also track render targets
    if pipeline_type == "Graphics":
        try:
            outputs = state.GetOutputTargets()
            for i, out in enumerate(outputs):
                key = get_binding_key("Output", "ColorTarget", 0, i, "ColorAttachment%d" % i)
                bindings[key] = {
                    "resource_id": int(out.resource) if out.resource != rd.ResourceId.Null() else None,
                    "resource_name": get_name(controller, out.resource),
                }
        except Exception:
            pass

        try:
            depth = state.GetDepthTarget()
            key = get_binding_key("Output", "DepthStencilTarget", 0, 0, "DepthStencil")
            bindings[key] = {
                "resource_id": int(depth.resource) if depth.resource != rd.ResourceId.Null() else None,
                "resource_name": get_name(controller, depth.resource),
            }
        except Exception:
            pass

    return bindings


def track_binding_changes(controller, pipeline_id, pipeline_name, actions):
    """Track binding changes across all events where the pipeline is active."""

    # First pass: determine pipeline type
    pipeline_type = None
    for action in actions:
        controller.SetFrameEvent(action.eventId, False)
        state = controller.GetPipelineState()

        try:
            if state.GetGraphicsPipelineObject() == pipeline_id:
                pipeline_type = "Graphics"
                break
        except Exception:
            pass

        try:
            if state.GetComputePipelineObject() == pipeline_id:
                pipeline_type = "Compute"
                break
        except Exception:
            pass

    if pipeline_type is None:
        raise RuntimeError("Pipeline '%s' is not used in any action." % pipeline_name)

    # Track bindings
    binding_initial = {}  # key -> (event_id, value)
    binding_changes = {}  # key -> list of changes
    last_values = {}      # key -> last value
    total_changes = 0

    for action in actions:
        eid = action.eventId
        controller.SetFrameEvent(eid, False)
        state = controller.GetPipelineState()

        # Check if our pipeline is active
        is_active = False
        try:
            if pipeline_type == "Graphics":
                is_active = state.GetGraphicsPipelineObject() == pipeline_id
            else:
                is_active = state.GetComputePipelineObject() == pipeline_id
        except Exception:
            continue

        if not is_active:
            continue

        # Extract current bindings
        current_bindings = extract_current_bindings(controller, state, pipeline_type)

        # Compare with previous values
        for key, value in current_bindings.items():
            if key not in binding_initial:
                # First time seeing this binding
                binding_initial[key] = (eid, value)
                binding_changes[key] = []
                last_values[key] = value
            elif value != last_values.get(key):
                # Value changed
                binding_changes[key].append({
                    "event_id": eid,
                    "new_value": value,
                })
                total_changes += 1
                last_values[key] = value

    # Build result
    bindings = []
    for key, (init_eid, init_value) in sorted(binding_initial.items()):
        stage_name, binding_type, set_num, binding_num, name = key
        bindings.append({
            "stage": stage_name,
            "binding_type": binding_type,
            "set": set_num,
            "binding": binding_num,
            "name": name,
            "initial_event_id": init_eid,
            "initial_value": init_value,
            "changes": binding_changes[key],
        })

    return {
        "pipeline_name": pipeline_name,
        "pipeline_type": pipeline_type,
        "total_changes": total_changes,
        "bindings": bindings,
    }


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

            # Track binding changes
            result_doc = track_binding_changes(controller, pipe_id, pipe_res.name, actions)

            write_envelope(True, result=result_doc)
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
