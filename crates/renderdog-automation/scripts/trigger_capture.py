import json
import os
import time
import traceback

import renderdoc as rd


REQ_PATH = "trigger_capture.request.json"
RESP_PATH = "trigger_capture.response.json"


def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)


def main() -> None:
    with open(REQ_PATH, "r", encoding="utf-8") as f:
        req = json.load(f)

    rd.InitialiseReplay(rd.GlobalEnvironment(), [])

    # Create a target control connection to an already-injected process (started via renderdoccmd capture).
    target = rd.CreateTargetControl(req["host"], int(req["target_ident"]), "renderdog", True)
    if target is None:
        raise RuntimeError(
            f"CreateTargetControl failed for {req['host']}:{int(req['target_ident'])}"
        )

    try:
        target.TriggerCapture(int(req["num_frames"]))

        # Wait for NewCapture message(s)
        deadline = time.time() + float(req["timeout_s"])
        while time.time() < deadline:
            msg = target.ReceiveMessage(None)
            if msg is None:
                continue
            if msg.type == rd.TargetControlMessageType.NewCapture:
                cap = msg.newCapture
                write_envelope(
                    True,
                    result={
                        "capture_path": cap.path,
                        "frame_number": int(cap.frameNumber),
                        "api": str(cap.api),
                    },
                )
                return

        raise RuntimeError("Timed out waiting for NewCapture message")
    finally:
        try:
            target.Shutdown()
        except Exception:
            pass
        rd.ShutdownReplay()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        write_envelope(False, error=traceback.format_exc())
    raise SystemExit(0)