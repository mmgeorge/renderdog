import json
import os
import traceback

import renderdoc as rd


REQ_PATH = "replay_list_textures_json.request.json"
RESP_PATH = "replay_list_textures_json.response.json"


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

            name_by_id = {}
            try:
                for r in controller.GetResources():
                    rrid = int(r.resourceId)
                    n = getattr(r, "name", None)
                    if n is None:
                        n = getattr(r, "resourceName", None)
                    if n is None:
                        continue
                    name_by_id[rrid] = str(n or "")
            except Exception:
                name_by_id = {}

            textures = controller.GetTextures()
            out = []
            for i, t in enumerate(textures):
                rid = t.resourceId
                name = name_by_id.get(int(rid), "") or ""
                try:
                    desc = controller.GetResourceDescription(rid)
                    if desc is not None:
                        if not name:
                            name = str(desc.name or "")
                except Exception:
                    pass

                arraysize = getattr(t, "arraysize", getattr(t, "arraySize", 1))
                ms_samp = getattr(t, "msSamp", getattr(t, "msSamples", 1))
                byte_size = getattr(t, "byteSize", getattr(t, "bytesize", getattr(t, "byte_size", 0)))
                out.append(
                    {
                        "index": int(i),
                        "resource_id": int(rid),
                        "name": name,
                        "width": int(t.width),
                        "height": int(t.height),
                        "depth": int(t.depth),
                        "mips": int(t.mips),
                        "arraysize": int(arraysize),
                        "ms_samp": int(ms_samp),
                        "byte_size": int(byte_size),
                    }
                )

            write_response(
                {
                    "capture_path": req["capture_path"],
                    "event_id": event_id,
                    "textures": out,
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
        # Wrap successful output written by main().
        with open(RESP_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        write_response({"ok": True, "result": payload})
    raise SystemExit(0)