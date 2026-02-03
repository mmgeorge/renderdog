use std::path::Path;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::resolve_path_string_from_cwd;

/// Helper module for generating a permissive JSON schema for dynamic JSON values.
mod any_json_schema {
    use schemars::Schema;

    pub fn schema(_gen: &mut schemars::SchemaGenerator) -> Schema {
        // Generate a schema that accepts any JSON value (empty object = any)
        Schema::default()
    }
}
use crate::scripting::{QRenderDocJsonEnvelope, create_qrenderdoc_run_dir};
use crate::{
    QRenderDocPythonRequest, RenderDocInstallation, default_scripts_dir, write_script_file,
};

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
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

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
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

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct FindEventsRequest {
    pub capture_path: String,
    pub only_drawcalls: bool,
    pub marker_prefix: Option<String>,
    pub event_id_min: Option<u32>,
    pub event_id_max: Option<u32>,
    pub name_contains: Option<String>,
    pub marker_contains: Option<String>,
    pub case_sensitive: bool,
    pub max_results: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct FoundEvent {
    pub event_id: u32,
    pub parent_event_id: Option<u32>,
    pub depth: u32,
    pub name: String,
    pub flags: u64,
    pub flags_names: Vec<String>,
    pub marker_path: Vec<String>,
    pub marker_path_joined: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct FindEventsResponse {
    pub capture_path: String,
    pub total_matches: u64,
    pub truncated: bool,
    pub first_event_id: Option<u32>,
    pub last_event_id: Option<u32>,
    pub matches: Vec<FoundEvent>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct GetEventsRequest {
    pub capture_path: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct EventInfo {
    pub event_id: u32,
    pub scope: String,
    pub name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct GetEventsResponse {
    pub capture_path: String,
    pub total_events: u64,
    pub events: Vec<EventInfo>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct GetShaderInfoRequest {
    pub capture_path: String,
    pub pipeline_name: String,
    /// Optional list of entry points to filter by. If not provided, returns all entry points.
    #[serde(default)]
    pub entry_points: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ShaderSourceFile {
    pub path: String,
    pub size: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ShaderResource {
    pub name: String,
    #[serde(rename = "type")]
    pub resource_type: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub set: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub binding: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ShaderConstantBlock {
    pub name: String,
    pub byte_size: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub set: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub binding: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ShaderSampler {
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub set: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub binding: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ShaderInputSignature {
    pub name: String,
    pub semantic: String,
    pub index: u32,
    #[serde(rename = "type")]
    pub var_type: String,
    pub components: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ShaderInfo {
    pub entry_point: String,
    pub stage: String,
    pub event_id: u32,
    #[serde(default)]
    pub source_files: Vec<ShaderSourceFile>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub encoding: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub debug_info_error: Option<String>,
    #[serde(default)]
    pub read_write_resources: Vec<ShaderResource>,
    #[serde(default)]
    pub read_only_resources: Vec<ShaderResource>,
    #[serde(default)]
    pub constant_blocks: Vec<ShaderConstantBlock>,
    #[serde(default)]
    pub samplers: Vec<ShaderSampler>,
    #[serde(default)]
    pub input_signature: Vec<ShaderInputSignature>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct GetShaderInfoResponse {
    pub capture_path: String,
    pub pipeline_name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub entry_points_filter: Option<Vec<String>>,
    pub shaders: Vec<ShaderInfo>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct GetBufferChangesDeltaRequest {
    pub capture_path: String,
    pub buffer_name: String,
    #[serde(default = "default_tracked_indices")]
    pub tracked_indices: Vec<u32>,
}

fn default_tracked_indices() -> Vec<u32> {
    vec![0]
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct BufferBinding {
    pub index: u32,
    pub name: String,
    #[serde(rename = "type")]
    pub binding_type: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct BufferUsage {
    pub pipeline: String,
    pub descriptor_set: String,
    pub binding: BufferBinding,
    pub event_ids: Vec<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct BufferElementChange {
    pub event_id: u32,
    #[schemars(schema_with = "any_json_schema::schema")]
    pub delta: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct BufferElement {
    pub buffer_index: u32,
    pub initial_event_id: u32,
    #[schemars(schema_with = "any_json_schema::schema")]
    pub initial_state: serde_json::Value,
    pub changes: Vec<BufferElementChange>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct GetBufferChangesDeltaResponse {
    pub capture_path: String,
    pub buffer_name: String,
    #[schemars(schema_with = "any_json_schema::schema")]
    pub schema: serde_json::Value,
    pub stride: u64,
    pub usages: Vec<BufferUsage>,
    pub tracked_indices: Vec<u32>,
    pub total_changes: u64,
    pub elements: Vec<BufferElement>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct GetEventPipelineStateRequest {
    pub capture_path: String,
    pub event_id: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct PipelineStage {
    pub stage: String,
    pub shader: String,
    #[serde(rename = "entryPoint")]
    pub entry_point: String,
    #[serde(skip_serializing_if = "Option::is_none", rename = "indexBuffer")]
    #[schemars(schema_with = "any_json_schema::schema")]
    pub index_buffer: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none", rename = "vertexBuffers")]
    #[schemars(schema_with = "any_json_schema::schema")]
    pub vertex_buffers: Option<Vec<serde_json::Value>>,
    #[serde(skip_serializing_if = "Option::is_none", rename = "renderTargets")]
    #[schemars(schema_with = "any_json_schema::schema")]
    pub render_targets: Option<Vec<serde_json::Value>>,
    #[serde(skip_serializing_if = "Option::is_none", rename = "depthTarget")]
    #[schemars(schema_with = "any_json_schema::schema")]
    pub depth_target: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none", rename = "depthState")]
    #[schemars(schema_with = "any_json_schema::schema")]
    pub depth_state: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none", rename = "stencilState")]
    #[schemars(schema_with = "any_json_schema::schema")]
    pub stencil_state: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none", rename = "blendState")]
    #[schemars(schema_with = "any_json_schema::schema")]
    pub blend_state: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct PipelineResource {
    pub stage: String,
    pub set: i32,
    pub binding: i32,
    pub name: String,
    pub access: String,
    #[serde(rename = "type")]
    pub resource_type: String,
    pub resource: String,
    #[serde(rename = "resourceId")]
    pub resource_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub contents: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[schemars(schema_with = "any_json_schema::schema")]
    pub layout: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct PipelineUniform {
    pub stage: String,
    pub set: i32,
    pub binding: i32,
    pub name: String,
    pub resource: String,
    #[serde(rename = "resourceId")]
    pub resource_id: String,
    #[serde(rename = "variableCount")]
    pub variable_count: u32,
    #[schemars(schema_with = "any_json_schema::schema")]
    pub variables: Vec<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub contents: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct PipelineSampler {
    pub stage: String,
    pub set: i32,
    pub binding: i32,
    pub name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct GetEventPipelineStateResponse {
    pub capture_path: String,
    pub pipeline: String,
    pub event_id: u32,
    pub is_compute: bool,
    pub stages: Vec<PipelineStage>,
    pub resources: Vec<PipelineResource>,
    pub uniforms: Vec<PipelineUniform>,
    pub samplers: Vec<PipelineSampler>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct GetResourceChangedEventIdsRequest {
    pub capture_path: String,
    pub resource_name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct GetResourceChangedEventIdsResponse {
    pub capture_path: String,
    pub resource_name: String,
    pub resource_id: String,
    pub resource_type: String,
    pub total_actions_scanned: u64,
    pub write_count: u64,
    pub event_ids: Vec<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct SearchResourcesRequest {
    /// Path to the .rdc capture file.
    pub capture_path: String,
    /// Regex pattern to match against resource names.
    ///
    /// Uses Rust-compatible regex syntax. Examples:
    /// - `"particle"` - matches names containing "particle"
    /// - `"^Texture"` - matches names starting with "Texture"
    /// - `"Buffer$"` - matches names ending with "Buffer"
    /// - `"shadow|light"` - matches names containing "shadow" or "light"
    /// - `"gbuffer_\\d+"` - matches "gbuffer_0", "gbuffer_1", etc.
    /// - `".*_diffuse$"` - matches names ending with "_diffuse"
    pub query: String,
    /// If true, treat `query` as a regex pattern (default). If false, treat as literal string.
    #[serde(default = "default_regex_mode")]
    pub regex: bool,
    /// If true, matching is case-sensitive. Default is false (case-insensitive).
    #[serde(default)]
    pub case_sensitive: bool,
    /// Maximum number of results to return. Default is 500.
    #[serde(default = "default_max_search_results")]
    pub max_results: Option<u32>,
    /// Optional list of resource types to filter by.
    ///
    /// Valid values:
    /// - `Unknown` - Unclassified resources
    /// - `Device` - VkDevice / GPU device
    /// - `Queue` - VkQueue
    /// - `CommandBuffer` - VkCommandBuffer
    /// - `Texture` - Images/textures
    /// - `Buffer` - VkBuffer
    /// - `View` - Image/buffer views
    /// - `Sampler` - VkSampler
    /// - `SwapchainImage` - Swapchain images
    /// - `Memory` - VkDeviceMemory
    /// - `Shader` - Shader modules
    /// - `ShaderBinding` - Descriptor set layouts, pipeline layouts
    /// - `PipelineState` - Graphics/compute pipelines
    /// - `StateObject` - Other state objects
    /// - `RenderPass` - VkRenderPass / VkFramebuffer
    /// - `Query` - Query pools
    /// - `Sync` - Fences, semaphores, events
    /// - `Pool` - Command pools, descriptor pools
    /// - `AccelerationStructure` - Ray tracing acceleration structures
    /// - `DescriptorStore` - Descriptor heaps/sets
    #[serde(default)]
    pub resource_types: Option<Vec<String>>,
}

fn default_regex_mode() -> bool {
    true
}

fn default_max_search_results() -> Option<u32> {
    Some(500)
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ResourceMatch {
    pub resource_id: u64,
    pub name: String,
    pub resource_type: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct SearchResourcesResponse {
    pub capture_path: String,
    pub query: String,
    pub regex: bool,
    pub case_sensitive: bool,
    pub total_resources: u64,
    pub total_matches: u64,
    pub truncated: bool,
    pub matches: Vec<ResourceMatch>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct FindResourceUsesRequest {
    /// Path to the .rdc capture file.
    pub capture_path: String,
    /// Resource name or ID to find uses of. Can be an exact name, partial name, or numeric ID.
    pub resource: String,
    /// Maximum number of uses to return. Default is 500.
    #[serde(default = "default_max_search_results")]
    pub max_results: Option<u32>,
    /// Whether to include pipeline info at each event (slower but more detailed). Default is true.
    #[serde(default = "default_include_pipeline_info")]
    pub include_pipeline_info: bool,
}

fn default_include_pipeline_info() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ResourceUse {
    /// The event ID where the resource is used.
    pub event_id: u32,
    /// How the resource is used (e.g., VertexBuffer, ColorTarget, PS_Resource, CS_RWResource).
    pub usage: String,
    /// The view through which the resource is accessed (if applicable).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub view_id: Option<u64>,
    /// Name of the view (if applicable).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub view_name: Option<String>,
    /// Name of the pipeline at this event.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pipeline_name: Option<String>,
    /// Type of pipeline (Graphics or Compute).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pipeline_type: Option<String>,
    /// Shader stage (Vertex, Fragment, Compute, etc.) for stage-specific usages.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stage: Option<String>,
    /// Entry point name for shader resources.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub entry_point: Option<String>,
    /// Additional detail about the usage.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub usage_detail: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct FindResourceUsesResponse {
    pub capture_path: String,
    pub resource_query: String,
    pub resource_id: u64,
    pub resource_name: String,
    pub resource_type: String,
    pub total_uses: u64,
    pub truncated: bool,
    pub uses: Vec<ResourceUse>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
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

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ExportBundleRequest {
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

    pub include_cbuffers: bool,
    pub include_outputs: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ExportBundleResponse {
    pub capture_path: String,

    pub actions_jsonl_path: String,
    pub actions_summary_json_path: String,
    pub total_actions: u64,
    pub drawcall_actions: u64,

    pub bindings_jsonl_path: String,
    pub bindings_summary_json_path: String,
    pub total_drawcalls: u64,
}

#[derive(Debug, Error)]
pub enum TriggerCaptureError {
    #[error("failed to create artifacts dir: {0}")]
    CreateArtifactsDir(std::io::Error),
    #[error("failed to write python script: {0}")]
    WriteScript(std::io::Error),
    #[error("failed to write request JSON: {0}")]
    WriteRequest(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to parse capture JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("failed to read response JSON: {0}")]
    ReadResponse(std::io::Error),
    #[error("qrenderdoc script error: {0}")]
    ScriptError(String),
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
    #[error("failed to write request JSON: {0}")]
    WriteRequest(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to parse export JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("failed to read response JSON: {0}")]
    ReadResponse(std::io::Error),
    #[error("qrenderdoc script error: {0}")]
    ScriptError(String),
}

#[derive(Debug, Error)]
pub enum FindEventsError {
    #[error("failed to create scripts dir: {0}")]
    CreateScriptsDir(std::io::Error),
    #[error("failed to write python script: {0}")]
    WriteScript(std::io::Error),
    #[error("failed to write request JSON: {0}")]
    WriteRequest(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to read response JSON: {0}")]
    ReadResponse(std::io::Error),
    #[error("failed to parse JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("qrenderdoc script error: {0}")]
    ScriptError(String),
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
    #[error("failed to write request JSON: {0}")]
    WriteRequest(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to parse export JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("failed to read response JSON: {0}")]
    ReadResponse(std::io::Error),
    #[error("qrenderdoc script error: {0}")]
    ScriptError(String),
}

#[derive(Debug, Error)]
pub enum ExportBundleError {
    #[error("export actions failed: {0}")]
    Actions(#[from] ExportActionsError),
    #[error("export bindings index failed: {0}")]
    Bindings(#[from] ExportBindingsIndexError),
}

fn remove_if_exists(path: &Path) -> Result<(), std::io::Error> {
    match std::fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e),
    }
}

impl From<crate::QRenderDocPythonError> for ExportBindingsIndexError {
    fn from(value: crate::QRenderDocPythonError) -> Self {
        Self::QRenderDocPython(Box::new(value))
    }
}

impl From<crate::QRenderDocPythonError> for FindEventsError {
    fn from(value: crate::QRenderDocPythonError) -> Self {
        Self::QRenderDocPython(Box::new(value))
    }
}

#[derive(Debug, Error)]
pub enum GetEventsError {
    #[error("failed to create scripts dir: {0}")]
    CreateScriptsDir(std::io::Error),
    #[error("failed to write python script: {0}")]
    WriteScript(std::io::Error),
    #[error("failed to write request JSON: {0}")]
    WriteRequest(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to read response JSON: {0}")]
    ReadResponse(std::io::Error),
    #[error("failed to parse JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("qrenderdoc script error: {0}")]
    ScriptError(String),
}

impl From<crate::QRenderDocPythonError> for GetEventsError {
    fn from(value: crate::QRenderDocPythonError) -> Self {
        Self::QRenderDocPython(Box::new(value))
    }
}

#[derive(Debug, Error)]
pub enum GetShaderInfoError {
    #[error("failed to create scripts dir: {0}")]
    CreateScriptsDir(std::io::Error),
    #[error("failed to write python script: {0}")]
    WriteScript(std::io::Error),
    #[error("failed to write request JSON: {0}")]
    WriteRequest(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to read response JSON: {0}")]
    ReadResponse(std::io::Error),
    #[error("failed to parse JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("qrenderdoc script error: {0}")]
    ScriptError(String),
}

impl From<crate::QRenderDocPythonError> for GetShaderInfoError {
    fn from(value: crate::QRenderDocPythonError) -> Self {
        Self::QRenderDocPython(Box::new(value))
    }
}

#[derive(Debug, Error)]
pub enum GetBufferChangesDeltaError {
    #[error("failed to create scripts dir: {0}")]
    CreateScriptsDir(std::io::Error),
    #[error("failed to write python script: {0}")]
    WriteScript(std::io::Error),
    #[error("failed to write request JSON: {0}")]
    WriteRequest(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to read response JSON: {0}")]
    ReadResponse(std::io::Error),
    #[error("failed to parse JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("qrenderdoc script error: {0}")]
    ScriptError(String),
}

impl From<crate::QRenderDocPythonError> for GetBufferChangesDeltaError {
    fn from(value: crate::QRenderDocPythonError) -> Self {
        Self::QRenderDocPython(Box::new(value))
    }
}

#[derive(Debug, Error)]
pub enum GetEventPipelineStateError {
    #[error("failed to create scripts dir: {0}")]
    CreateScriptsDir(std::io::Error),
    #[error("failed to write python script: {0}")]
    WriteScript(std::io::Error),
    #[error("failed to write request JSON: {0}")]
    WriteRequest(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to read response JSON: {0}")]
    ReadResponse(std::io::Error),
    #[error("failed to parse JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("qrenderdoc script error: {0}")]
    ScriptError(String),
}

impl From<crate::QRenderDocPythonError> for GetEventPipelineStateError {
    fn from(value: crate::QRenderDocPythonError) -> Self {
        Self::QRenderDocPython(Box::new(value))
    }
}

#[derive(Debug, Error)]
pub enum GetResourceChangedEventIdsError {
    #[error("failed to create scripts dir: {0}")]
    CreateScriptsDir(std::io::Error),
    #[error("failed to write python script: {0}")]
    WriteScript(std::io::Error),
    #[error("failed to write request JSON: {0}")]
    WriteRequest(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to read response JSON: {0}")]
    ReadResponse(std::io::Error),
    #[error("failed to parse JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("qrenderdoc script error: {0}")]
    ScriptError(String),
}

impl From<crate::QRenderDocPythonError> for GetResourceChangedEventIdsError {
    fn from(value: crate::QRenderDocPythonError) -> Self {
        Self::QRenderDocPython(Box::new(value))
    }
}

#[derive(Debug, Error)]
pub enum SearchResourcesError {
    #[error("failed to create scripts dir: {0}")]
    CreateScriptsDir(std::io::Error),
    #[error("failed to write python script: {0}")]
    WriteScript(std::io::Error),
    #[error("failed to write request JSON: {0}")]
    WriteRequest(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to read response JSON: {0}")]
    ReadResponse(std::io::Error),
    #[error("failed to parse JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("qrenderdoc script error: {0}")]
    ScriptError(String),
}

impl From<crate::QRenderDocPythonError> for SearchResourcesError {
    fn from(value: crate::QRenderDocPythonError) -> Self {
        Self::QRenderDocPython(Box::new(value))
    }
}

#[derive(Debug, Error)]
pub enum FindResourceUsesError {
    #[error("failed to create scripts dir: {0}")]
    CreateScriptsDir(std::io::Error),
    #[error("failed to write python script: {0}")]
    WriteScript(std::io::Error),
    #[error("failed to write request JSON: {0}")]
    WriteRequest(std::io::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("failed to read response JSON: {0}")]
    ReadResponse(std::io::Error),
    #[error("failed to parse JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("qrenderdoc script error: {0}")]
    ScriptError(String),
}

impl From<crate::QRenderDocPythonError> for FindResourceUsesError {
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

        let run_dir = create_qrenderdoc_run_dir(&scripts_dir, "trigger_capture")
            .map_err(TriggerCaptureError::CreateArtifactsDir)?;
        let request_path = run_dir.join("trigger_capture.request.json");
        let response_path = run_dir.join("trigger_capture.response.json");
        remove_if_exists(&response_path).map_err(TriggerCaptureError::WriteRequest)?;
        std::fs::write(
            &request_path,
            serde_json::to_vec(req).map_err(TriggerCaptureError::ParseJson)?,
        )
        .map_err(TriggerCaptureError::WriteRequest)?;

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args: Vec::new(),
            working_dir: Some(run_dir.clone()),
        })?;
        let _ = result;
        let bytes = std::fs::read(&response_path).map_err(TriggerCaptureError::ReadResponse)?;
        let env: QRenderDocJsonEnvelope<TriggerCaptureResponse> =
            serde_json::from_slice(&bytes).map_err(TriggerCaptureError::ParseJson)?;
        if env.ok {
            env.result
                .ok_or_else(|| TriggerCaptureError::ScriptError("missing result".into()))
        } else {
            Err(TriggerCaptureError::ScriptError(
                env.error.unwrap_or_else(|| "unknown error".into()),
            ))
        }
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

        let run_dir = create_qrenderdoc_run_dir(&scripts_dir, "export_actions_jsonl")
            .map_err(ExportActionsError::CreateOutputDir)?;
        let request_path = run_dir.join("export_actions_jsonl.request.json");
        let response_path = run_dir.join("export_actions_jsonl.response.json");
        remove_if_exists(&response_path).map_err(ExportActionsError::WriteRequest)?;

        let req = ExportActionsRequest {
            capture_path: resolve_path_string_from_cwd(cwd, &req.capture_path),
            output_dir: resolve_path_string_from_cwd(cwd, &req.output_dir),
            ..req.clone()
        };

        std::fs::write(
            &request_path,
            serde_json::to_vec(&req).map_err(ExportActionsError::ParseJson)?,
        )
        .map_err(ExportActionsError::WriteRequest)?;

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args: Vec::new(),
            working_dir: Some(run_dir.clone()),
        })?;
        let _ = result;
        let bytes = std::fs::read(&response_path).map_err(ExportActionsError::ReadResponse)?;
        let env: QRenderDocJsonEnvelope<ExportActionsResponse> =
            serde_json::from_slice(&bytes).map_err(ExportActionsError::ParseJson)?;
        if env.ok {
            env.result
                .ok_or_else(|| ExportActionsError::ScriptError("missing result".into()))
        } else {
            Err(ExportActionsError::ScriptError(
                env.error.unwrap_or_else(|| "unknown error".into()),
            ))
        }
    }

    pub fn find_events(
        &self,
        cwd: &Path,
        req: &FindEventsRequest,
    ) -> Result<FindEventsResponse, FindEventsError> {
        let scripts_dir = default_scripts_dir(cwd);
        std::fs::create_dir_all(&scripts_dir).map_err(FindEventsError::CreateScriptsDir)?;

        let script_path = scripts_dir.join("find_events_json.py");
        write_script_file(&script_path, FIND_EVENTS_JSON_PY)
            .map_err(FindEventsError::WriteScript)?;

        let run_dir = create_qrenderdoc_run_dir(&scripts_dir, "find_events")
            .map_err(FindEventsError::CreateScriptsDir)?;
        let request_path = run_dir.join("find_events_json.request.json");
        let response_path = run_dir.join("find_events_json.response.json");
        remove_if_exists(&response_path).map_err(FindEventsError::WriteRequest)?;

        let req = FindEventsRequest {
            capture_path: resolve_path_string_from_cwd(cwd, &req.capture_path),
            ..req.clone()
        };

        std::fs::write(
            &request_path,
            serde_json::to_vec(&req).map_err(FindEventsError::ParseJson)?,
        )
        .map_err(FindEventsError::WriteRequest)?;

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args: Vec::new(),
            working_dir: Some(run_dir.clone()),
        })?;
        let _ = result;

        let bytes = std::fs::read(&response_path).map_err(FindEventsError::ReadResponse)?;
        let env: QRenderDocJsonEnvelope<FindEventsResponse> =
            serde_json::from_slice(&bytes).map_err(FindEventsError::ParseJson)?;
        if env.ok {
            env.result
                .ok_or_else(|| FindEventsError::ScriptError("missing result".into()))
        } else {
            Err(FindEventsError::ScriptError(
                env.error.unwrap_or_else(|| "unknown error".into()),
            ))
        }
    }

    pub fn get_events(
        &self,
        cwd: &Path,
        req: &GetEventsRequest,
    ) -> Result<GetEventsResponse, GetEventsError> {
        let scripts_dir = default_scripts_dir(cwd);
        std::fs::create_dir_all(&scripts_dir).map_err(GetEventsError::CreateScriptsDir)?;

        let script_path = scripts_dir.join("get_events_json.py");
        write_script_file(&script_path, GET_EVENTS_JSON_PY)
            .map_err(GetEventsError::WriteScript)?;

        let run_dir = create_qrenderdoc_run_dir(&scripts_dir, "get_events")
            .map_err(GetEventsError::CreateScriptsDir)?;
        let request_path = run_dir.join("get_events_json.request.json");
        let response_path = run_dir.join("get_events_json.response.json");
        remove_if_exists(&response_path).map_err(GetEventsError::WriteRequest)?;

        let req = GetEventsRequest {
            capture_path: resolve_path_string_from_cwd(cwd, &req.capture_path),
        };

        std::fs::write(
            &request_path,
            serde_json::to_vec(&req).map_err(GetEventsError::ParseJson)?,
        )
        .map_err(GetEventsError::WriteRequest)?;

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args: Vec::new(),
            working_dir: Some(run_dir.clone()),
        })?;
        let _ = result;

        let bytes = std::fs::read(&response_path).map_err(GetEventsError::ReadResponse)?;
        let env: QRenderDocJsonEnvelope<GetEventsResponse> =
            serde_json::from_slice(&bytes).map_err(GetEventsError::ParseJson)?;
        if env.ok {
            env.result
                .ok_or_else(|| GetEventsError::ScriptError("missing result".into()))
        } else {
            Err(GetEventsError::ScriptError(
                env.error.unwrap_or_else(|| "unknown error".into()),
            ))
        }
    }

    pub fn get_shader_info(
        &self,
        cwd: &Path,
        req: &GetShaderInfoRequest,
    ) -> Result<GetShaderInfoResponse, GetShaderInfoError> {
        let scripts_dir = default_scripts_dir(cwd);
        std::fs::create_dir_all(&scripts_dir).map_err(GetShaderInfoError::CreateScriptsDir)?;

        let script_path = scripts_dir.join("get_shader_info_json.py");
        write_script_file(&script_path, GET_SHADER_INFO_JSON_PY)
            .map_err(GetShaderInfoError::WriteScript)?;

        let run_dir = create_qrenderdoc_run_dir(&scripts_dir, "get_shader_info")
            .map_err(GetShaderInfoError::CreateScriptsDir)?;
        let request_path = run_dir.join("get_shader_info_json.request.json");
        let response_path = run_dir.join("get_shader_info_json.response.json");
        remove_if_exists(&response_path).map_err(GetShaderInfoError::WriteRequest)?;

        let req = GetShaderInfoRequest {
            capture_path: resolve_path_string_from_cwd(cwd, &req.capture_path),
            pipeline_name: req.pipeline_name.clone(),
            entry_points: req.entry_points.clone(),
        };

        std::fs::write(
            &request_path,
            serde_json::to_vec(&req).map_err(GetShaderInfoError::ParseJson)?,
        )
        .map_err(GetShaderInfoError::WriteRequest)?;

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args: Vec::new(),
            working_dir: Some(run_dir.clone()),
        })?;
        let _ = result;

        let bytes = std::fs::read(&response_path).map_err(GetShaderInfoError::ReadResponse)?;
        let env: QRenderDocJsonEnvelope<GetShaderInfoResponse> =
            serde_json::from_slice(&bytes).map_err(GetShaderInfoError::ParseJson)?;
        if env.ok {
            env.result
                .ok_or_else(|| GetShaderInfoError::ScriptError("missing result".into()))
        } else {
            Err(GetShaderInfoError::ScriptError(
                env.error.unwrap_or_else(|| "unknown error".into()),
            ))
        }
    }

    pub fn get_buffer_changes_delta(
        &self,
        cwd: &Path,
        req: &GetBufferChangesDeltaRequest,
    ) -> Result<GetBufferChangesDeltaResponse, GetBufferChangesDeltaError> {
        let scripts_dir = default_scripts_dir(cwd);
        std::fs::create_dir_all(&scripts_dir)
            .map_err(GetBufferChangesDeltaError::CreateScriptsDir)?;

        let script_path = scripts_dir.join("get_buffer_changes_delta_json.py");
        write_script_file(&script_path, GET_BUFFER_CHANGES_DELTA_JSON_PY)
            .map_err(GetBufferChangesDeltaError::WriteScript)?;

        let run_dir = create_qrenderdoc_run_dir(&scripts_dir, "get_buffer_changes_delta")
            .map_err(GetBufferChangesDeltaError::CreateScriptsDir)?;
        let request_path = run_dir.join("get_buffer_changes_delta_json.request.json");
        let response_path = run_dir.join("get_buffer_changes_delta_json.response.json");
        remove_if_exists(&response_path).map_err(GetBufferChangesDeltaError::WriteRequest)?;

        let req = GetBufferChangesDeltaRequest {
            capture_path: resolve_path_string_from_cwd(cwd, &req.capture_path),
            buffer_name: req.buffer_name.clone(),
            tracked_indices: req.tracked_indices.clone(),
        };

        std::fs::write(
            &request_path,
            serde_json::to_vec(&req).map_err(GetBufferChangesDeltaError::ParseJson)?,
        )
        .map_err(GetBufferChangesDeltaError::WriteRequest)?;

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args: Vec::new(),
            working_dir: Some(run_dir.clone()),
        })?;
        let _ = result;

        let bytes =
            std::fs::read(&response_path).map_err(GetBufferChangesDeltaError::ReadResponse)?;
        let env: QRenderDocJsonEnvelope<GetBufferChangesDeltaResponse> =
            serde_json::from_slice(&bytes).map_err(GetBufferChangesDeltaError::ParseJson)?;
        if env.ok {
            env.result
                .ok_or_else(|| GetBufferChangesDeltaError::ScriptError("missing result".into()))
        } else {
            Err(GetBufferChangesDeltaError::ScriptError(
                env.error.unwrap_or_else(|| "unknown error".into()),
            ))
        }
    }

    pub fn get_event_pipeline_state(
        &self,
        cwd: &Path,
        req: &GetEventPipelineStateRequest,
    ) -> Result<GetEventPipelineStateResponse, GetEventPipelineStateError> {
        let scripts_dir = default_scripts_dir(cwd);
        std::fs::create_dir_all(&scripts_dir)
            .map_err(GetEventPipelineStateError::CreateScriptsDir)?;

        let script_path = scripts_dir.join("get_event_pipeline_state_json.py");
        write_script_file(&script_path, GET_EVENT_PIPELINE_STATE_JSON_PY)
            .map_err(GetEventPipelineStateError::WriteScript)?;

        let run_dir = create_qrenderdoc_run_dir(&scripts_dir, "get_event_pipeline_state")
            .map_err(GetEventPipelineStateError::CreateScriptsDir)?;
        let request_path = run_dir.join("get_event_pipeline_state_json.request.json");
        let response_path = run_dir.join("get_event_pipeline_state_json.response.json");
        remove_if_exists(&response_path).map_err(GetEventPipelineStateError::WriteRequest)?;

        let req = GetEventPipelineStateRequest {
            capture_path: resolve_path_string_from_cwd(cwd, &req.capture_path),
            event_id: req.event_id,
        };

        std::fs::write(
            &request_path,
            serde_json::to_vec(&req).map_err(GetEventPipelineStateError::ParseJson)?,
        )
        .map_err(GetEventPipelineStateError::WriteRequest)?;

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args: Vec::new(),
            working_dir: Some(run_dir.clone()),
        })?;
        let _ = result;

        let bytes =
            std::fs::read(&response_path).map_err(GetEventPipelineStateError::ReadResponse)?;
        let env: QRenderDocJsonEnvelope<GetEventPipelineStateResponse> =
            serde_json::from_slice(&bytes).map_err(GetEventPipelineStateError::ParseJson)?;
        if env.ok {
            env.result
                .ok_or_else(|| GetEventPipelineStateError::ScriptError("missing result".into()))
        } else {
            Err(GetEventPipelineStateError::ScriptError(
                env.error.unwrap_or_else(|| "unknown error".into()),
            ))
        }
    }

    pub fn get_resource_changed_event_ids(
        &self,
        cwd: &Path,
        req: &GetResourceChangedEventIdsRequest,
    ) -> Result<GetResourceChangedEventIdsResponse, GetResourceChangedEventIdsError> {
        let scripts_dir = default_scripts_dir(cwd);
        std::fs::create_dir_all(&scripts_dir)
            .map_err(GetResourceChangedEventIdsError::CreateScriptsDir)?;

        let script_path = scripts_dir.join("get_resource_changed_event_ids_json.py");
        write_script_file(&script_path, GET_RESOURCE_CHANGED_EVENT_IDS_JSON_PY)
            .map_err(GetResourceChangedEventIdsError::WriteScript)?;

        let run_dir = create_qrenderdoc_run_dir(&scripts_dir, "get_resource_changed_event_ids")
            .map_err(GetResourceChangedEventIdsError::CreateScriptsDir)?;
        let request_path = run_dir.join("get_resource_changed_event_ids_json.request.json");
        let response_path = run_dir.join("get_resource_changed_event_ids_json.response.json");
        remove_if_exists(&response_path).map_err(GetResourceChangedEventIdsError::WriteRequest)?;

        let req = GetResourceChangedEventIdsRequest {
            capture_path: resolve_path_string_from_cwd(cwd, &req.capture_path),
            resource_name: req.resource_name.clone(),
        };

        std::fs::write(
            &request_path,
            serde_json::to_vec(&req).map_err(GetResourceChangedEventIdsError::ParseJson)?,
        )
        .map_err(GetResourceChangedEventIdsError::WriteRequest)?;

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args: Vec::new(),
            working_dir: Some(run_dir.clone()),
        })?;
        let _ = result;

        let bytes =
            std::fs::read(&response_path).map_err(GetResourceChangedEventIdsError::ReadResponse)?;
        let env: QRenderDocJsonEnvelope<GetResourceChangedEventIdsResponse> =
            serde_json::from_slice(&bytes).map_err(GetResourceChangedEventIdsError::ParseJson)?;
        if env.ok {
            env.result.ok_or_else(|| {
                GetResourceChangedEventIdsError::ScriptError("missing result".into())
            })
        } else {
            Err(GetResourceChangedEventIdsError::ScriptError(
                env.error.unwrap_or_else(|| "unknown error".into()),
            ))
        }
    }

    pub fn search_resources(
        &self,
        cwd: &Path,
        req: &SearchResourcesRequest,
    ) -> Result<SearchResourcesResponse, SearchResourcesError> {
        let scripts_dir = default_scripts_dir(cwd);
        std::fs::create_dir_all(&scripts_dir).map_err(SearchResourcesError::CreateScriptsDir)?;

        let script_path = scripts_dir.join("search_resources_json.py");
        write_script_file(&script_path, SEARCH_RESOURCES_JSON_PY)
            .map_err(SearchResourcesError::WriteScript)?;

        let run_dir = create_qrenderdoc_run_dir(&scripts_dir, "search_resources")
            .map_err(SearchResourcesError::CreateScriptsDir)?;
        let request_path = run_dir.join("search_resources_json.request.json");
        let response_path = run_dir.join("search_resources_json.response.json");
        remove_if_exists(&response_path).map_err(SearchResourcesError::WriteRequest)?;

        let req = SearchResourcesRequest {
            capture_path: resolve_path_string_from_cwd(cwd, &req.capture_path),
            query: req.query.clone(),
            regex: req.regex,
            case_sensitive: req.case_sensitive,
            max_results: req.max_results,
            resource_types: req.resource_types.clone(),
        };

        std::fs::write(
            &request_path,
            serde_json::to_vec(&req).map_err(SearchResourcesError::ParseJson)?,
        )
        .map_err(SearchResourcesError::WriteRequest)?;

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args: Vec::new(),
            working_dir: Some(run_dir.clone()),
        })?;
        let _ = result;

        let bytes = std::fs::read(&response_path).map_err(SearchResourcesError::ReadResponse)?;
        let env: QRenderDocJsonEnvelope<SearchResourcesResponse> =
            serde_json::from_slice(&bytes).map_err(SearchResourcesError::ParseJson)?;
        if env.ok {
            env.result
                .ok_or_else(|| SearchResourcesError::ScriptError("missing result".into()))
        } else {
            Err(SearchResourcesError::ScriptError(
                env.error.unwrap_or_else(|| "unknown error".into()),
            ))
        }
    }

    pub fn find_resource_uses(
        &self,
        cwd: &Path,
        req: &FindResourceUsesRequest,
    ) -> Result<FindResourceUsesResponse, FindResourceUsesError> {
        let scripts_dir = default_scripts_dir(cwd);
        std::fs::create_dir_all(&scripts_dir).map_err(FindResourceUsesError::CreateScriptsDir)?;

        let script_path = scripts_dir.join("find_resource_uses_json.py");
        write_script_file(&script_path, FIND_RESOURCE_USES_JSON_PY)
            .map_err(FindResourceUsesError::WriteScript)?;

        let run_dir = create_qrenderdoc_run_dir(&scripts_dir, "find_resource_uses")
            .map_err(FindResourceUsesError::CreateScriptsDir)?;
        let request_path = run_dir.join("find_resource_uses_json.request.json");
        let response_path = run_dir.join("find_resource_uses_json.response.json");
        remove_if_exists(&response_path).map_err(FindResourceUsesError::WriteRequest)?;

        let req = FindResourceUsesRequest {
            capture_path: resolve_path_string_from_cwd(cwd, &req.capture_path),
            resource: req.resource.clone(),
            max_results: req.max_results,
            include_pipeline_info: req.include_pipeline_info,
        };

        std::fs::write(
            &request_path,
            serde_json::to_vec(&req).map_err(FindResourceUsesError::ParseJson)?,
        )
        .map_err(FindResourceUsesError::WriteRequest)?;

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args: Vec::new(),
            working_dir: Some(run_dir.clone()),
        })?;
        let _ = result;

        let bytes = std::fs::read(&response_path).map_err(FindResourceUsesError::ReadResponse)?;
        let env: QRenderDocJsonEnvelope<FindResourceUsesResponse> =
            serde_json::from_slice(&bytes).map_err(FindResourceUsesError::ParseJson)?;
        if env.ok {
            env.result
                .ok_or_else(|| FindResourceUsesError::ScriptError("missing result".into()))
        } else {
            Err(FindResourceUsesError::ScriptError(
                env.error.unwrap_or_else(|| "unknown error".into()),
            ))
        }
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

        let run_dir = create_qrenderdoc_run_dir(&scripts_dir, "export_bindings_index_jsonl")
            .map_err(ExportBindingsIndexError::CreateOutputDir)?;
        let request_path = run_dir.join("export_bindings_index_jsonl.request.json");
        let response_path = run_dir.join("export_bindings_index_jsonl.response.json");
        remove_if_exists(&response_path).map_err(ExportBindingsIndexError::WriteRequest)?;

        let req = ExportBindingsIndexRequest {
            capture_path: resolve_path_string_from_cwd(cwd, &req.capture_path),
            output_dir: resolve_path_string_from_cwd(cwd, &req.output_dir),
            ..req.clone()
        };

        std::fs::write(
            &request_path,
            serde_json::to_vec(&req).map_err(ExportBindingsIndexError::ParseJson)?,
        )
        .map_err(ExportBindingsIndexError::WriteRequest)?;

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args: Vec::new(),
            working_dir: Some(run_dir.clone()),
        })?;
        let _ = result;
        let bytes =
            std::fs::read(&response_path).map_err(ExportBindingsIndexError::ReadResponse)?;
        let env: QRenderDocJsonEnvelope<ExportBindingsIndexResponse> =
            serde_json::from_slice(&bytes).map_err(ExportBindingsIndexError::ParseJson)?;
        if env.ok {
            env.result
                .ok_or_else(|| ExportBindingsIndexError::ScriptError("missing result".into()))
        } else {
            Err(ExportBindingsIndexError::ScriptError(
                env.error.unwrap_or_else(|| "unknown error".into()),
            ))
        }
    }

    pub fn export_bundle_jsonl(
        &self,
        cwd: &Path,
        req: &ExportBundleRequest,
    ) -> Result<ExportBundleResponse, ExportBundleError> {
        let capture_path = resolve_path_string_from_cwd(cwd, &req.capture_path);
        let output_dir = resolve_path_string_from_cwd(cwd, &req.output_dir);

        let actions = self.export_actions_jsonl(
            cwd,
            &ExportActionsRequest {
                capture_path: capture_path.clone(),
                output_dir: output_dir.clone(),
                basename: req.basename.clone(),
                only_drawcalls: req.only_drawcalls,
                marker_prefix: req.marker_prefix.clone(),
                event_id_min: req.event_id_min,
                event_id_max: req.event_id_max,
                name_contains: req.name_contains.clone(),
                marker_contains: req.marker_contains.clone(),
                case_sensitive: req.case_sensitive,
            },
        )?;

        let bindings = self.export_bindings_index_jsonl(
            cwd,
            &ExportBindingsIndexRequest {
                capture_path: capture_path.clone(),
                output_dir: output_dir.clone(),
                basename: req.basename.clone(),
                marker_prefix: req.marker_prefix.clone(),
                event_id_min: req.event_id_min,
                event_id_max: req.event_id_max,
                name_contains: req.name_contains.clone(),
                marker_contains: req.marker_contains.clone(),
                case_sensitive: req.case_sensitive,
                include_cbuffers: req.include_cbuffers,
                include_outputs: req.include_outputs,
            },
        )?;

        Ok(ExportBundleResponse {
            capture_path,

            actions_jsonl_path: actions.actions_jsonl_path,
            actions_summary_json_path: actions.summary_json_path,
            total_actions: actions.total_actions,
            drawcall_actions: actions.drawcall_actions,

            bindings_jsonl_path: bindings.bindings_jsonl_path,
            bindings_summary_json_path: bindings.summary_json_path,
            total_drawcalls: bindings.total_drawcalls,
        })
    }
}

const TRIGGER_CAPTURE_PY: &str = include_str!("../scripts/trigger_capture.py");

const FIND_EVENTS_JSON_PY: &str = include_str!("../scripts/find_events_json.py");

const EXPORT_ACTIONS_JSONL_PY: &str = include_str!("../scripts/export_actions_jsonl.py");

const EXPORT_BINDINGS_INDEX_JSONL_PY: &str =
    include_str!("../scripts/export_bindings_index_jsonl.py");

const GET_EVENTS_JSON_PY: &str = include_str!("../scripts/get_events_json.py");

const GET_SHADER_INFO_JSON_PY: &str = include_str!("../scripts/get_shader_info_json.py");

const GET_BUFFER_CHANGES_DELTA_JSON_PY: &str =
    include_str!("../scripts/get_buffer_changes_delta_json.py");

const GET_EVENT_PIPELINE_STATE_JSON_PY: &str =
    include_str!("../scripts/get_event_pipeline_state_json.py");

const GET_RESOURCE_CHANGED_EVENT_IDS_JSON_PY: &str =
    include_str!("../scripts/get_resource_changed_event_ids_json.py");

const SEARCH_RESOURCES_JSON_PY: &str = include_str!("../scripts/search_resources_json.py");

const FIND_RESOURCE_USES_JSON_PY: &str = include_str!("../scripts/find_resource_uses_json.py");
