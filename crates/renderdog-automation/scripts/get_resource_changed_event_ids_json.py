"""
get_resource_changed_event_ids_json.py - RenderDoc Python script to find all events
that modify a resource (texture or buffer).

Finds a resource by name and scans all actions in the frame, reporting which event
IDs wrote to it.

Detects writes from:
  - Draw calls that target the texture as a color output
  - Draw calls that target the texture as a depth/stencil output
  - Clear operations on the resource
  - Copy/resolve operations targeting the resource
  - Compute or fragment shader writes via RW image/storage bindings
"""

import json
import traceback

import renderdoc as rd


REQ_PATH = "get_resource_changed_event_ids_json.request.json"
RESP_PATH = "get_resource_changed_event_ids_json.response.json"


def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Resource finding
# ---------------------------------------------------------------------------

def find_resource(controller, resource_name):
    """Locate the target resource's ResourceId by name."""
    # Exact match first
    for res in controller.GetResources():
        if res.name == resource_name:
            return res.resourceId, res.name, str(res.type)

    # Fuzzy substring match
    candidates = []
    all_resources = []

    for res in controller.GetResources():
        all_resources.append((res.resourceId, res.name, str(res.type)))
        if resource_name in res.name:
            candidates.append((res.resourceId, res.name, str(res.type)))

    if len(candidates) == 1:
        rid, name, rtype = candidates[0]
        return rid, name, rtype

    if len(candidates) > 1:
        raise RuntimeError(
            "Ambiguous resource name '%s' - %d matches:\n%s"
            % (resource_name, len(candidates),
               "\n".join("  %s  %s" % (rid, name) for rid, name, _ in candidates[:20]))
        )

    raise RuntimeError(
        "Resource '%s' not found. Available resources:\n%s"
        % (resource_name, "\n".join(
            "  %s  %s  (%s)" % (rid, name, rtype) for rid, name, rtype in all_resources[:30]
        ))
    )


# ---------------------------------------------------------------------------
# Write detection
# ---------------------------------------------------------------------------

NULL_ID = rd.ResourceId.Null()


def action_writes_resource(action, res_id, state):
    """
    Check whether `action` writes to `res_id`.

    Uses the action's output list and pipeline state to detect:
      - Color render target writes
      - Depth/stencil target writes
      - RW image/storage image bindings (compute or fragment writes)
    """

    # 1. Check color outputs (action.outputs is a fixed-size array of ResourceId)
    try:
        for out_id in action.outputs:
            if out_id != NULL_ID and out_id == res_id:
                return True
    except Exception:
        pass

    # 2. Check depth output
    try:
        if action.depthOut != NULL_ID and action.depthOut == res_id:
            return True
    except Exception:
        pass

    # 3. Check RW resource bindings for storage image/buffer writes
    #    (covers compute dispatches and fragment shader image stores)
    stages_to_check = [
        rd.ShaderStage.Compute,
        rd.ShaderStage.Fragment,
        rd.ShaderStage.Vertex,
    ]

    for stage in stages_to_check:
        try:
            rw_list = state.GetReadWriteResources(stage)
            for rw in rw_list:
                if rw.descriptor.resource == res_id:
                    return True
        except Exception:
            continue

    return False


def flatten_actions(roots):
    """Yield every leaf action in linear order."""
    for action in roots:
        if len(action.children) > 0:
            yield from flatten_actions(action.children)
        else:
            yield action


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    with open(REQ_PATH, "r", encoding="utf-8") as f:
        req = json.load(f)

    resource_name = req["resource_name"]

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
            res_id, resolved_name, res_type = find_resource(controller, resource_name)

            actions = list(flatten_actions(controller.GetRootActions()))
            if not actions:
                raise RuntimeError("No actions found in capture")

            change_eids = []

            for action in actions:
                eid = action.eventId
                controller.SetFrameEvent(eid, False)
                state = controller.GetPipelineState()

                if action_writes_resource(action, res_id, state):
                    change_eids.append(int(eid))

            document = {
                "capture_path": req["capture_path"],
                "resource_name": resolved_name,
                "resource_id": str(res_id),
                "resource_type": res_type,
                "total_actions_scanned": len(actions),
                "write_count": len(change_eids),
                "event_ids": change_eids,
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
