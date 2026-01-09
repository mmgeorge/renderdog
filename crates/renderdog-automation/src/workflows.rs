use std::{ffi::OsString, path::Path};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::{
    QRenderDocPythonRequest, RenderDocInstallation, default_scripts_dir, write_script_file,
};

#[derive(Debug, Clone)]
pub struct TriggerCaptureRequest {
    pub host: String,
    pub target_ident: u32,
    pub num_frames: u32,
    pub timeout_s: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct TriggerCaptureResponse {
    pub capture_path: String,
    pub frame_number: u32,
    pub api: String,
}

#[derive(Debug, Clone)]
pub struct ExportActionsRequest {
    pub capture_path: String,
    pub output_dir: String,
    pub basename: String,
    pub only_drawcalls: bool,
    pub marker_prefix: Option<String>,
    pub event_id_min: Option<u32>,
    pub event_id_max: Option<u32>,
    pub name_contains: Option<String>,
    pub marker_contains: Option<String>,
    pub case_sensitive: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ExportActionsResponse {
    pub capture_path: String,
    pub actions_jsonl_path: String,
    pub summary_json_path: String,
    pub total_actions: u64,
    pub drawcall_actions: u64,
}

#[derive(Debug, Clone)]
pub struct ExportBindingsIndexRequest {
    pub capture_path: String,
    pub output_dir: String,
    pub basename: String,
    pub marker_prefix: Option<String>,
    pub event_id_min: Option<u32>,
    pub event_id_max: Option<u32>,
    pub name_contains: Option<String>,
    pub marker_contains: Option<String>,
    pub case_sensitive: bool,
    pub include_cbuffers: bool,
    pub include_outputs: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ExportBindingsIndexResponse {
    pub capture_path: String,
    pub bindings_jsonl_path: String,
    pub summary_json_path: String,
    pub total_drawcalls: u64,
}

#[derive(Debug, Error)]
pub enum TriggerCaptureError {
    #[error("failed to create artifacts dir: {0}")]
    CreateArtifactsDir(std::io::Error),
    #[error("failed to write python script: {0}")]
    WriteScript(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to parse capture JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("missing capture_path in script output")]
    MissingCapturePath,
}

impl From<crate::QRenderDocPythonError> for TriggerCaptureError {
    fn from(value: crate::QRenderDocPythonError) -> Self {
        Self::QRenderDocPython(Box::new(value))
    }
}

#[derive(Debug, Error)]
pub enum ExportActionsError {
    #[error("failed to create output dir: {0}")]
    CreateOutputDir(std::io::Error),
    #[error("failed to write python script: {0}")]
    WriteScript(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to parse export JSON: {0}")]
    ParseJson(serde_json::Error),
}

impl From<crate::QRenderDocPythonError> for ExportActionsError {
    fn from(value: crate::QRenderDocPythonError) -> Self {
        Self::QRenderDocPython(Box::new(value))
    }
}

#[derive(Debug, Error)]
pub enum ExportBindingsIndexError {
    #[error("failed to create output dir: {0}")]
    CreateOutputDir(std::io::Error),
    #[error("failed to write python script: {0}")]
    WriteScript(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to parse export JSON: {0}")]
    ParseJson(serde_json::Error),
}

impl From<crate::QRenderDocPythonError> for ExportBindingsIndexError {
    fn from(value: crate::QRenderDocPythonError) -> Self {
        Self::QRenderDocPython(Box::new(value))
    }
}

impl RenderDocInstallation {
    pub fn trigger_capture_via_target_control(
        &self,
        cwd: &Path,
        req: &TriggerCaptureRequest,
    ) -> Result<TriggerCaptureResponse, TriggerCaptureError> {
        let scripts_dir = default_scripts_dir(cwd);
        std::fs::create_dir_all(&scripts_dir).map_err(TriggerCaptureError::CreateArtifactsDir)?;

        let script_path = scripts_dir.join("trigger_capture.py");
        write_script_file(&script_path, TRIGGER_CAPTURE_PY)
            .map_err(TriggerCaptureError::WriteScript)?;

        let args: Vec<OsString> = vec![
            OsString::from("--host"),
            OsString::from(req.host.clone()),
            OsString::from("--ident"),
            OsString::from(req.target_ident.to_string()),
            OsString::from("--frames"),
            OsString::from(req.num_frames.to_string()),
            OsString::from("--timeout-s"),
            OsString::from(req.timeout_s.to_string()),
        ];

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args,
            working_dir: Some(cwd.to_path_buf()),
        })?;

        let line = result
            .stdout
            .lines()
            .rev()
            .find(|l| !l.trim().is_empty())
            .unwrap_or("");

        let parsed: TriggerCaptureResponse =
            serde_json::from_str(line).map_err(TriggerCaptureError::ParseJson)?;

        if parsed.capture_path.trim().is_empty() {
            return Err(TriggerCaptureError::MissingCapturePath);
        }

        Ok(parsed)
    }

    pub fn export_actions_jsonl(
        &self,
        cwd: &Path,
        req: &ExportActionsRequest,
    ) -> Result<ExportActionsResponse, ExportActionsError> {
        let scripts_dir = default_scripts_dir(cwd);
        std::fs::create_dir_all(&scripts_dir).map_err(ExportActionsError::CreateOutputDir)?;

        let script_path = scripts_dir.join("export_actions_jsonl.py");
        write_script_file(&script_path, EXPORT_ACTIONS_JSONL_PY)
            .map_err(ExportActionsError::WriteScript)?;

        let mut args: Vec<OsString> = vec![
            OsString::from("--capture"),
            OsString::from(req.capture_path.clone()),
            OsString::from("--out-dir"),
            OsString::from(req.output_dir.clone()),
            OsString::from("--basename"),
            OsString::from(req.basename.clone()),
        ];
        if req.only_drawcalls {
            args.push(OsString::from("--only-drawcalls"));
        }
        if let Some(prefix) = &req.marker_prefix {
            args.push(OsString::from("--marker-prefix"));
            args.push(OsString::from(prefix.clone()));
        }
        if let Some(v) = req.event_id_min {
            args.push(OsString::from("--event-min"));
            args.push(OsString::from(v.to_string()));
        }
        if let Some(v) = req.event_id_max {
            args.push(OsString::from("--event-max"));
            args.push(OsString::from(v.to_string()));
        }
        if let Some(q) = &req.name_contains {
            args.push(OsString::from("--name-contains"));
            args.push(OsString::from(q.clone()));
        }
        if let Some(q) = &req.marker_contains {
            args.push(OsString::from("--marker-contains"));
            args.push(OsString::from(q.clone()));
        }
        if req.case_sensitive {
            args.push(OsString::from("--case-sensitive"));
        }

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args,
            working_dir: Some(cwd.to_path_buf()),
        })?;

        let line = result
            .stdout
            .lines()
            .rev()
            .find(|l| !l.trim().is_empty())
            .unwrap_or("");

        serde_json::from_str(line).map_err(ExportActionsError::ParseJson)
    }

    pub fn export_bindings_index_jsonl(
        &self,
        cwd: &Path,
        req: &ExportBindingsIndexRequest,
    ) -> Result<ExportBindingsIndexResponse, ExportBindingsIndexError> {
        let scripts_dir = default_scripts_dir(cwd);
        std::fs::create_dir_all(&scripts_dir).map_err(ExportBindingsIndexError::CreateOutputDir)?;

        let script_path = scripts_dir.join("export_bindings_index_jsonl.py");
        write_script_file(&script_path, EXPORT_BINDINGS_INDEX_JSONL_PY)
            .map_err(ExportBindingsIndexError::WriteScript)?;

        let mut args: Vec<OsString> = vec![
            OsString::from("--capture"),
            OsString::from(req.capture_path.clone()),
            OsString::from("--out-dir"),
            OsString::from(req.output_dir.clone()),
            OsString::from("--basename"),
            OsString::from(req.basename.clone()),
        ];
        if let Some(prefix) = &req.marker_prefix {
            args.push(OsString::from("--marker-prefix"));
            args.push(OsString::from(prefix.clone()));
        }
        if let Some(v) = req.event_id_min {
            args.push(OsString::from("--event-min"));
            args.push(OsString::from(v.to_string()));
        }
        if let Some(v) = req.event_id_max {
            args.push(OsString::from("--event-max"));
            args.push(OsString::from(v.to_string()));
        }
        if let Some(q) = &req.name_contains {
            args.push(OsString::from("--name-contains"));
            args.push(OsString::from(q.clone()));
        }
        if let Some(q) = &req.marker_contains {
            args.push(OsString::from("--marker-contains"));
            args.push(OsString::from(q.clone()));
        }
        if req.case_sensitive {
            args.push(OsString::from("--case-sensitive"));
        }
        if req.include_cbuffers {
            args.push(OsString::from("--include-cbuffers"));
        }
        if req.include_outputs {
            args.push(OsString::from("--include-outputs"));
        }

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args,
            working_dir: Some(cwd.to_path_buf()),
        })?;

        let line = result
            .stdout
            .lines()
            .rev()
            .find(|l| !l.trim().is_empty())
            .unwrap_or("");

        serde_json::from_str(line).map_err(ExportBindingsIndexError::ParseJson)
    }
}

const TRIGGER_CAPTURE_PY: &str = r#"
import argparse
import json
import time

import renderdoc as rd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--ident", required=True, type=int)
    parser.add_argument("--frames", required=True, type=int)
    parser.add_argument("--timeout-s", required=True, type=int)
    args = parser.parse_args()

    rd.InitialiseReplay(rd.GlobalEnvironment(), [])

    # Create a target control connection to an already-injected process (started via renderdoccmd capture).
    target = rd.CreateTargetControl(args.host, args.ident, "renderdog", True)
    if target is None:
        raise RuntimeError(f\"CreateTargetControl failed for {args.host}:{args.ident}\")

    try:
        target.TriggerCapture(args.frames)

        # Wait for NewCapture message(s)
        msg = None
        deadline = time.time() + float(args.timeout_s)
        while time.time() < deadline:
            msg = target.ReceiveMessage(None)
            if msg is None:
                continue
            if msg.type == rd.TargetControlMessageType.NewCapture:
                cap = msg.newCapture
                out = {
                    "capture_path": cap.path,
                    "frame_number": int(cap.frameNumber),
                    "api": str(cap.api),
                }
                print(json.dumps(out))
                return 0

        raise RuntimeError("Timed out waiting for NewCapture message")
    finally:
        try:
            target.Shutdown()
        except Exception:
            pass
        rd.ShutdownReplay()


if __name__ == "__main__":
    raise SystemExit(main())
"#;

const EXPORT_ACTIONS_JSONL_PY: &str = r#"
import argparse
import json
import os

import renderdoc as rd


FLAG_NAMES = [
    ("Clear", rd.ActionFlags.Clear),
    ("Drawcall", rd.ActionFlags.Drawcall),
    ("Dispatch", rd.ActionFlags.Dispatch),
    ("MeshDispatch", rd.ActionFlags.MeshDispatch),
    ("CmdList", rd.ActionFlags.CmdList),
    ("SetMarker", rd.ActionFlags.SetMarker),
    ("PushMarker", rd.ActionFlags.PushMarker),
    ("PopMarker", rd.ActionFlags.PopMarker),
    ("Present", rd.ActionFlags.Present),
    ("MultiAction", rd.ActionFlags.MultiAction),
    ("Copy", rd.ActionFlags.Copy),
    ("Resolve", rd.ActionFlags.Resolve),
    ("GenMips", rd.ActionFlags.GenMips),
    ("PassBoundary", rd.ActionFlags.PassBoundary),
    ("DispatchRay", rd.ActionFlags.DispatchRay),
    ("BuildAccStruct", rd.ActionFlags.BuildAccStruct),
    ("Indexed", rd.ActionFlags.Indexed),
    ("Instanced", rd.ActionFlags.Instanced),
    ("Auto", rd.ActionFlags.Auto),
    ("Indirect", rd.ActionFlags.Indirect),
    ("ClearColor", rd.ActionFlags.ClearColor),
    ("ClearDepthStencil", rd.ActionFlags.ClearDepthStencil),
    ("BeginPass", rd.ActionFlags.BeginPass),
    ("EndPass", rd.ActionFlags.EndPass),
    ("CommandBufferBoundary", rd.ActionFlags.CommandBufferBoundary),
]


def flags_to_names(flags):
    names = []
    for name, bit in FLAG_NAMES:
        if flags & bit:
            names.append(name)
    return names


def is_drawcall_like(flags: int) -> bool:
    return bool(
        (flags & rd.ActionFlags.Drawcall)
        or (flags & rd.ActionFlags.Dispatch)
        or (flags & rd.ActionFlags.MeshDispatch)
        or (flags & rd.ActionFlags.DispatchRay)
    )


def marker_path_join(marker_path) -> str:
    if not marker_path:
        return ""
    return "/".join([str(x) for x in marker_path])

def normalize(s: str, case_sensitive: bool) -> str:
    if s is None:
        return ""
    if case_sensitive:
        return str(s)
    return str(s).lower()


def iter_actions(structured_file, actions, marker_stack, parent_event_id, depth, out_fp, counters,
                 only_drawcalls: bool, marker_prefix: str,
                 event_min, event_max,
                 name_contains: str, marker_contains: str,
                 case_sensitive: bool):
    for a in actions:
        name = a.GetName(structured_file)
        flags = a.flags

        effective_marker_path = list(marker_stack)
        if flags & rd.ActionFlags.PushMarker:
            effective_marker_path.append(str(name))

        joined_marker_path = marker_path_join(effective_marker_path)
        name_str = str(name)

        def recurse():
            if flags & rd.ActionFlags.PushMarker:
                marker_stack.append(str(name))
                iter_actions(structured_file, a.children, marker_stack, a.eventId, depth + 1, out_fp, counters,
                             only_drawcalls, marker_prefix,
                             event_min, event_max,
                             name_contains, marker_contains,
                             case_sensitive)
                marker_stack.pop()
            else:
                iter_actions(structured_file, a.children, marker_stack, a.eventId, depth + 1, out_fp, counters,
                             only_drawcalls, marker_prefix,
                             event_min, event_max,
                             name_contains, marker_contains,
                             case_sensitive)

        if marker_prefix:
            if not (joined_marker_path == marker_prefix or joined_marker_path.startswith(marker_prefix + "/")):
                recurse()
                continue

        eid = int(a.eventId)

        should_emit = True
        if only_drawcalls and not is_drawcall_like(flags):
            should_emit = False
        if event_min is not None and eid < int(event_min):
            should_emit = False
        if event_max is not None and eid > int(event_max):
            should_emit = False

        if name_contains:
            if name_contains not in normalize(name_str, case_sensitive):
                should_emit = False
        if marker_contains:
            if marker_contains not in normalize(joined_marker_path, case_sensitive):
                should_emit = False

        if should_emit:
            rec = {
                "event_id": eid,
            "parent_event_id": int(parent_event_id) if parent_event_id is not None else None,
            "depth": int(depth),
            "name": name_str,
            "flags": int(flags),
            "flags_names": flags_to_names(flags),
            "marker_path": effective_marker_path,
            "num_children": int(len(a.children)),
            }

            out_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")

            counters["total_actions"] += 1
            if is_drawcall_like(flags):
                counters["drawcall_actions"] += 1

        recurse()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--basename", required=True)
    parser.add_argument("--only-drawcalls", action="store_true")
    parser.add_argument("--marker-prefix", default="")
    parser.add_argument("--event-min", type=int, default=None)
    parser.add_argument("--event-max", type=int, default=None)
    parser.add_argument("--name-contains", default="")
    parser.add_argument("--marker-contains", default="")
    parser.add_argument("--case-sensitive", action="store_true")
    args = parser.parse_args()

    rd.InitialiseReplay(rd.GlobalEnvironment(), [])

    os.makedirs(args.out_dir, exist_ok=True)

    actions_path = os.path.join(args.out_dir, f"{args.basename}.actions.jsonl")
    summary_path = os.path.join(args.out_dir, f"{args.basename}.summary.json")

    cap = rd.OpenCaptureFile()
    try:
        result = cap.OpenFile(args.capture, "", None)
        if result != rd.ResultCode.Succeeded:
            raise RuntimeError("Couldn't open file: " + str(result))

        if not cap.LocalReplaySupport():
            raise RuntimeError("Capture cannot be replayed")

        result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if result != rd.ResultCode.Succeeded:
            raise RuntimeError("Couldn't initialise replay: " + str(result))

        try:
            structured_file = controller.GetStructuredFile()
            roots = controller.GetRootActions()

            counters = {"total_actions": 0, "drawcall_actions": 0}
            with open(actions_path, "w", encoding="utf-8") as fp:
                iter_actions(structured_file, roots, [], None, 0, fp, counters,
                             args.only_drawcalls, args.marker_prefix,
                             args.event_min, args.event_max,
                             normalize(args.name_contains, args.case_sensitive),
                             normalize(args.marker_contains, args.case_sensitive),
                             args.case_sensitive)

            api = str(controller.GetAPIProperties().pipelineType)

            summary = {
                "capture_path": args.capture,
                "api": api,
                "total_actions": int(counters["total_actions"]),
                "drawcall_actions": int(counters["drawcall_actions"]),
                "actions_jsonl_path": actions_path,
            }

            with open(summary_path, "w", encoding="utf-8") as fp:
                json.dump(summary, fp, ensure_ascii=False, indent=2)

            print(json.dumps({
                "capture_path": args.capture,
                "actions_jsonl_path": actions_path,
                "summary_json_path": summary_path,
                "total_actions": int(counters["total_actions"]),
                "drawcall_actions": int(counters["drawcall_actions"]),
            }, ensure_ascii=False))
            return 0
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
    raise SystemExit(main())
"#;

const EXPORT_BINDINGS_INDEX_JSONL_PY: &str = r#"
import argparse
import json
import os

import renderdoc as rd


def normalize(s: str, case_sensitive: bool) -> str:
    if s is None:
        return ""
    if case_sensitive:
        return str(s)
    return str(s).lower()


def is_drawcall_like(flags: int) -> bool:
    return bool(
        (flags & rd.ActionFlags.Drawcall)
        or (flags & rd.ActionFlags.Dispatch)
        or (flags & rd.ActionFlags.MeshDispatch)
        or (flags & rd.ActionFlags.DispatchRay)
    )


def marker_path_join(marker_path) -> str:
    if not marker_path:
        return ""
    return "/".join([str(x) for x in marker_path])


def try_res_name(controller, rid) -> str:
    try:
        desc = controller.GetResourceDescription(rid)
        if desc is None:
            return ""
        return str(desc.name or "")
    except Exception:
        return ""


def stage_name(stage) -> str:
    try:
        return str(stage)
    except Exception:
        return "Unknown"


def build_reflection_name_map(reflection, access: str):
    m = {}
    if reflection is None:
        return m
    try:
        if access == "ro":
            for res in reflection.readOnlyResources:
                m[int(res.fixedBindNumber)] = str(res.name)
        elif access == "rw":
            for res in reflection.readWriteResources:
                m[int(res.fixedBindNumber)] = str(res.name)
    except Exception:
        pass
    return m


def serialize_bindings_for_stage(controller, pipe, stage, include_cbuffers: bool):
    shader = pipe.GetShader(stage)
    if shader == rd.ResourceId.Null():
        return None

    info = {
        "shader": {
            "resource_id": str(shader),
            "name": try_res_name(controller, shader),
            "entry_point": str(pipe.GetShaderEntryPoint(stage) or ""),
        },
        "srvs": [],
        "uavs": [],
        "cbuffers": [],
    }

    reflection = None
    try:
        reflection = pipe.GetShaderReflection(stage)
    except Exception:
        reflection = None

    ro_name_map = build_reflection_name_map(reflection, "ro")
    rw_name_map = build_reflection_name_map(reflection, "rw")

    # SRVs
    try:
        srvs = pipe.GetReadOnlyResources(stage, False)
        for srv in srvs:
            rid = srv.descriptor.resource
            if rid == rd.ResourceId.Null():
                continue
            slot = int(srv.access.index)
            info["srvs"].append(
                {
                    "slot": slot,
                    "name": ro_name_map.get(slot, ""),
                    "resource_id": str(rid),
                    "resource_name": try_res_name(controller, rid),
                }
            )
    except Exception:
        pass

    # UAVs
    try:
        uavs = pipe.GetReadWriteResources(stage, False)
        for uav in uavs:
            rid = uav.descriptor.resource
            if rid == rd.ResourceId.Null():
                continue
            slot = int(uav.access.index)
            info["uavs"].append(
                {
                    "slot": slot,
                    "name": rw_name_map.get(slot, ""),
                    "resource_id": str(rid),
                    "resource_name": try_res_name(controller, rid),
                }
            )
    except Exception:
        pass

    # Constant buffers (metadata only; no variable dumping)
    if include_cbuffers and reflection is not None:
        try:
            for i, cb in enumerate(reflection.constantBlocks):
                entry = {
                    "slot": int(i),
                    "name": str(cb.name),
                    "size": int(cb.byteSize),
                    "resource_id": None,
                    "resource_name": "",
                }
                try:
                    bind = pipe.GetConstantBuffer(stage, i, 0)
                    if bind.resourceId != rd.ResourceId.Null():
                        entry["resource_id"] = str(bind.resourceId)
                        entry["resource_name"] = try_res_name(controller, bind.resourceId)
                except Exception:
                    pass
                info["cbuffers"].append(entry)
        except Exception:
            pass

    return info


def serialize_outputs(controller, pipe):
    out = {"render_targets": [], "depth_target": None}
    try:
        om = pipe.GetOutputMerger()
        if om is None:
            return out

        rts = []
        for i, rt in enumerate(om.renderTargets):
            rid = rt.resourceId
            if rid == rd.ResourceId.Null():
                continue
            rts.append(
                {
                    "index": int(i),
                    "resource_id": str(rid),
                    "resource_name": try_res_name(controller, rid),
                }
            )
        out["render_targets"] = rts

        dt = om.depthTarget.resourceId
        if dt != rd.ResourceId.Null():
            out["depth_target"] = {
                "resource_id": str(dt),
                "resource_name": try_res_name(controller, dt),
            }
    except Exception:
        pass
    return out


def iter_actions(structured_file, controller, actions, marker_stack, depth,
                 out_fp, counters,
                 marker_prefix: str,
                 event_min, event_max,
                 name_contains: str, marker_contains: str,
                 case_sensitive: bool,
                 include_cbuffers: bool,
                 include_outputs: bool):
    for a in actions:
        name = a.GetName(structured_file)
        flags = a.flags

        effective_marker_path = list(marker_stack)
        if flags & rd.ActionFlags.PushMarker:
            effective_marker_path.append(str(name))

        joined_marker_path = marker_path_join(effective_marker_path)
        name_str = str(name)

        def recurse():
            if flags & rd.ActionFlags.PushMarker:
                marker_stack.append(str(name))
                iter_actions(structured_file, controller, a.children, marker_stack, depth + 1,
                             out_fp, counters,
                             marker_prefix,
                             event_min, event_max,
                             name_contains, marker_contains,
                             case_sensitive,
                             include_cbuffers, include_outputs)
                marker_stack.pop()
            else:
                iter_actions(structured_file, controller, a.children, marker_stack, depth + 1,
                             out_fp, counters,
                             marker_prefix,
                             event_min, event_max,
                             name_contains, marker_contains,
                             case_sensitive,
                             include_cbuffers, include_outputs)

        if marker_prefix:
            if not (joined_marker_path == marker_prefix or joined_marker_path.startswith(marker_prefix + "/")):
                recurse()
                continue

        eid = int(a.eventId)

        should_emit = is_drawcall_like(flags)
        if event_min is not None and eid < int(event_min):
            should_emit = False
        if event_max is not None and eid > int(event_max):
            should_emit = False

        if name_contains:
            if name_contains not in normalize(name_str, case_sensitive):
                should_emit = False
        if marker_contains:
            if marker_contains not in normalize(joined_marker_path, case_sensitive):
                should_emit = False

        if should_emit:
            controller.SetFrameEvent(eid, False)
            pipe = controller.GetPipelineState()

            stages = [
                rd.ShaderStage.Vertex,
                rd.ShaderStage.Hull,
                rd.ShaderStage.Domain,
                rd.ShaderStage.Geometry,
                rd.ShaderStage.Pixel,
                rd.ShaderStage.Compute,
            ]

            stage_map = {}
            shader_names = []
            resource_names = []

            for st in stages:
                st_info = serialize_bindings_for_stage(controller, pipe, st, include_cbuffers)
                if st_info is None:
                    continue

                st_key = stage_name(st)
                stage_map[st_key] = st_info

                sh = st_info.get("shader") or {}
                if sh.get("name"):
                    shader_names.append(sh.get("name"))
                if sh.get("entry_point"):
                    shader_names.append(sh.get("entry_point"))

                for srv in st_info.get("srvs") or []:
                    if srv.get("name"):
                        resource_names.append(srv.get("name"))
                    if srv.get("resource_name"):
                        resource_names.append(srv.get("resource_name"))
                for uav in st_info.get("uavs") or []:
                    if uav.get("name"):
                        resource_names.append(uav.get("name"))
                    if uav.get("resource_name"):
                        resource_names.append(uav.get("resource_name"))
                for cb in st_info.get("cbuffers") or []:
                    if cb.get("name"):
                        resource_names.append(cb.get("name"))
                    if cb.get("resource_name"):
                        resource_names.append(cb.get("resource_name"))

            rec = {
                "event_id": eid,
                "depth": int(depth),
                "name": name_str,
                "marker_path": effective_marker_path,
                "marker_path_joined": joined_marker_path,
                "stages": stage_map,
                "shader_names": shader_names,
                "resource_names": resource_names,
            }

            if include_outputs:
                rec["outputs"] = serialize_outputs(controller, pipe)

            out_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            counters["total_drawcalls"] += 1

        recurse()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--basename", required=True)
    parser.add_argument("--marker-prefix", default="")
    parser.add_argument("--event-min", type=int, default=None)
    parser.add_argument("--event-max", type=int, default=None)
    parser.add_argument("--name-contains", default="")
    parser.add_argument("--marker-contains", default="")
    parser.add_argument("--case-sensitive", action="store_true")
    parser.add_argument("--include-cbuffers", action="store_true")
    parser.add_argument("--include-outputs", action="store_true")
    args = parser.parse_args()

    rd.InitialiseReplay(rd.GlobalEnvironment(), [])

    os.makedirs(args.out_dir, exist_ok=True)

    bindings_path = os.path.join(args.out_dir, f"{args.basename}.bindings.jsonl")
    summary_path = os.path.join(args.out_dir, f"{args.basename}.bindings_summary.json")

    cap = rd.OpenCaptureFile()
    try:
        result = cap.OpenFile(args.capture, "", None)
        if result != rd.ResultCode.Succeeded:
            raise RuntimeError("Couldn't open file: " + str(result))

        if not cap.LocalReplaySupport():
            raise RuntimeError("Capture cannot be replayed")

        result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if result != rd.ResultCode.Succeeded:
            raise RuntimeError("Couldn't initialise replay: " + str(result))

        try:
            structured_file = controller.GetStructuredFile()
            roots = controller.GetRootActions()

            counters = {"total_drawcalls": 0}
            with open(bindings_path, "w", encoding="utf-8") as fp:
                iter_actions(
                    structured_file,
                    controller,
                    roots,
                    [],
                    0,
                    fp,
                    counters,
                    args.marker_prefix,
                    args.event_min,
                    args.event_max,
                    normalize(args.name_contains, args.case_sensitive),
                    normalize(args.marker_contains, args.case_sensitive),
                    args.case_sensitive,
                    args.include_cbuffers,
                    args.include_outputs,
                )

            api = str(controller.GetAPIProperties().pipelineType)

            summary = {
                "capture_path": args.capture,
                "api": api,
                "total_drawcalls": int(counters["total_drawcalls"]),
                "bindings_jsonl_path": bindings_path,
            }

            with open(summary_path, "w", encoding="utf-8") as fp:
                json.dump(summary, fp, ensure_ascii=False, indent=2)

            print(
                json.dumps(
                    {
                        "capture_path": args.capture,
                        "bindings_jsonl_path": bindings_path,
                        "summary_json_path": summary_path,
                        "total_drawcalls": int(counters["total_drawcalls"]),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
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
    raise SystemExit(main())
"#;
