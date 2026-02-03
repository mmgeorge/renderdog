"""
get_events_json.py - RenderDoc Python script that builds a JSON mapping every event ID
to its semantic name and debug-label scope.

Output structure (inside envelope):

    {
        "capture_path": "...",
        "total_events": 123,
        "events": [
            { "event_id": 1, "scope": "physics::solve:392", "name": "vkCmdDispatch(8, 8, 1)" },
            ...
        ]
    }

Where:
    - event_id  is the numeric event ID
    - scope     is the full parent marker hierarchy, e.g.
                "physics::particle_system::compute:solve:392"
    - name      is the API call name with parameters, e.g.
                "vkCmdDispatch(8, 8, 1)"
"""

import json
import traceback

import renderdoc as rd


REQ_PATH = "get_events_json.request.json"
RESP_PATH = "get_events_json.response.json"


def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)


def get_scope(action):
    """
    Walk up the parent chain to build the full debug-marker scope path.
    Marker regions (vkCmdBeginDebugUtilsLabelEXT, etc.) show up as parent
    actions in the tree.  We collect them root -> leaf and join with " > ".
    """
    parts = []
    parent = action.parent
    while parent is not None:
        name = parent.customName if parent.customName else ""
        if name:
            parts.append(name)
        parent = parent.parent

    parts.reverse()
    return " > ".join(parts)


def walk_actions(action, structured_file, rows):
    """
    Recursively walk the action tree.  For every leaf action (and every
    APIEvent within it), emit a row.  Marker regions themselves also get a
    row so you can see where they begin.
    """
    scope = get_scope(action)

    action_name = action.GetName(structured_file)

    if len(action.events) > 0:
        for event in action.events:
            eid = event.eventId
            chunk_name = ""
            if event.chunkIndex < len(structured_file.chunks):
                chunk = structured_file.chunks[event.chunkIndex]
                chunk_name = chunk.name

            if eid == action.eventId:
                display_name = action_name
            else:
                display_name = chunk_name

            rows.append({
                "event_id": int(eid),
                "scope": scope,
                "name": display_name,
            })
    else:
        rows.append({
            "event_id": int(action.eventId),
            "scope": scope,
            "name": action_name,
        })

    for child in action.children:
        walk_actions(child, structured_file, rows)


def main() -> None:
    with open(REQ_PATH, "r", encoding="utf-8") as f:
        req = json.load(f)

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
            structured_file = controller.GetStructuredFile()
            root_actions = controller.GetRootActions()

            rows = []
            for action in root_actions:
                walk_actions(action, structured_file, rows)

            rows.sort(key=lambda r: r["event_id"])

            write_envelope(
                True,
                result={
                    "capture_path": req["capture_path"],
                    "total_events": len(rows),
                    "events": rows,
                },
            )
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
