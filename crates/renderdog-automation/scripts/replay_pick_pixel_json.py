import json
import os
import traceback

import renderdoc as rd


REQ_PATH = "replay_pick_pixel_json.request.json"
RESP_PATH = "replay_pick_pixel_json.response.json"


def write_response(obj) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


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
            event_id = req.get("event_id", None)
            if event_id is not None:
                controller.SetFrameEvent(int(event_id), True)

            textures = controller.GetTextures()
            idx = int(req["texture_index"])
            if idx < 0 or idx >= len(textures):
                raise RuntimeError("texture_index out of range")

            t = textures[idx]
            pv = controller.PickPixel(
                t.resourceId,
                int(req["x"]),
                int(req["y"]),
                rd.Subresource(0, 0, 0),
                rd.CompType.Typeless,
            )

            rgba = [
                float(pv.floatValue[0]),
                float(pv.floatValue[1]),
                float(pv.floatValue[2]),
                float(pv.floatValue[3]),
            ]

            write_response(
                {
                    "capture_path": req["capture_path"],
                    "event_id": event_id,
                    "texture_index": int(req["texture_index"]),
                    "x": int(req["x"]),
                    "y": int(req["y"]),
                    "rgba": rgba,
                }
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
        write_response({"ok": False, "error": traceback.format_exc()})
    else:
        with open(RESP_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        write_response({"ok": True, "result": payload})
    raise SystemExit(0)