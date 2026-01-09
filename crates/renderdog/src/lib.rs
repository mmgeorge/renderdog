//! RenderDoc in-application API wrapper.
//!
//! This crate provides a Rust wrapper around RenderDoc's *in-app capture API* (`renderdoc_app.h`).
//! It supports:
//! - connecting to an injected RenderDoc (Windows),
//! - dynamically loading the RenderDoc library (Windows/Linux),
//! - runtime API version negotiation (tries 1.6.0 down to 1.0.0).
//!
//! For automation workflows (renderdoccmd/qrenderdoc), see the `renderdog-automation` crate.

mod in_app;
mod renderdog;
mod settings;

pub use in_app::*;
pub use renderdog::*;
pub use settings::*;

pub type SysCaptureOption = RENDERDOC_CaptureOption;
pub type SysInputButton = RENDERDOC_InputButton;
pub type SysDevicePointer = RENDERDOC_DevicePointer;
pub type SysWindowHandle = RENDERDOC_WindowHandle;
