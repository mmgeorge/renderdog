# RenderDoc Automation - Claude Development Notes

This document captures important findings and patterns for developing RenderDoc automation scripts.

## Target API: wgpu with Vulkan Backend

**Important**: The only API we need to support is **wgpu with a Vulkan backend**. This simplifies our approach:

1. **No need for D3D11/D3D12/OpenGL/Metal paths** - Focus only on Vulkan-specific APIs when needed
2. **WebGPU invariants apply** - wgpu follows the WebGPU specification, which provides guarantees we can exploit
3. **Predictable binding model** - WebGPU has a well-defined bind group layout system

### WebGPU Specification Reference

The `refs/gpuweb.xml` file contains the WebGPU specification. Use it to understand:
- Bind group layouts and their constraints
- Buffer usage flags and their meanings
- Texture formats and capabilities
- Pipeline layout requirements

```bash
# Search for bind group concepts
grep -n "GPUBindGroup" refs/gpuweb.xml | head -20

# Find buffer usage flags
grep -n "GPUBufferUsage" refs/gpuweb.xml

# Understand texture formats
grep -n "GPUTextureFormat" refs/gpuweb.xml | head -30
```

### WebGPU Invariants We Can Exploit

1. **Bind groups are immutable once created** - Resources bound to a bind group don't change
2. **Pipeline layouts define binding slots** - Each pipeline has a fixed layout
3. **Limited descriptor types** - Only: uniform buffers, storage buffers, samplers, sampled textures, storage textures
4. **No descriptor indexing by default** - Bindings are statically known (unless using binding arrays extension)
5. **Explicit resource usage** - Buffer/texture usages are declared at creation time

## Testing Scripts with the MCP Server

### Checking if Test Captures Exist

Before testing scripts, verify that RenderDoc captures exist in the default temp directory:

```
C:\Users\mattm\AppData\Local\Temp\RenderDoc\*.rdc
```

If no captures exist, prompt the user to create one by:
1. Running a game/application with RenderDoc attached
2. Pressing F12 (or configured capture key) to capture a frame
3. The capture will be saved to the temp directory

### Running and Testing Scripts via Rust Workflow

**IMPORTANT**: Do NOT run scripts directly via `qrenderdoc --python` as this opens a GUI window. Instead, always test scripts through the Rust workflow.

#### Testing Existing Tools

To test an existing tool like `get_pipeline_details`, use the test example:

```bash
# Run the pipeline details test (tests both compute and graphics pipelines)
cargo run --package renderdog-automation --example test_pipeline_details
```

Or use `cargo test` if there are existing tests:
```bash
cargo test --package renderdog-automation
```

The test example at `crates/renderdog-automation/examples/test_pipeline_details.rs` can be modified to test specific scenarios.

#### Verifying Script Output

After running a tool through the Rust workflow, check the response JSON file in the run directory:
```bash
# Response files are written to timestamped directories under .renderdog-scripts/
ls -la .renderdog-scripts/get_pipeline_details_*/
cat .renderdog-scripts/get_pipeline_details_*/get_pipeline_details_json.response.json | python -m json.tool
```

#### Debug Script for Descriptor APIs

A debug script exists at:
```
crates/renderdog-automation/scripts/debug_resource_bindings.py
```

To test it, temporarily add it to a Rust workflow or modify an existing workflow to call it. The script outputs to `debug_resource_bindings.result.json` in the working directory.

## Key API Findings

### GetReadWriteResources / GetReadOnlyResources Return Empty

**Problem**: The `state.GetReadWriteResources(stage)` and `state.GetReadOnlyResources(stage)` APIs often return empty lists, especially when the application uses newer Vulkan features like descriptor buffers.

**Solution**: Use the recommended `GetDescriptorAccess` + `GetDescriptors` pattern instead:

```python
# Get all descriptors accessed at this event
desc_access_list = controller.GetDescriptorAccess()

for access in desc_access_list:
    # Filter by stage
    if access.stage != target_stage:
        continue

    # Get descriptor contents
    desc_store = access.descriptorStore
    if desc_store is None or desc_store == rd.ResourceId.Null():
        continue

    # Create range from access (can be constructed directly)
    try:
        desc_range = rd.DescriptorRange(access)
    except Exception:
        # Fallback: manually construct
        desc_range = rd.DescriptorRange()
        desc_range.offset = access.byteOffset
        desc_range.descriptorSize = access.byteSize
        desc_range.count = 1
        desc_range.type = access.type

    # Query descriptor contents
    descriptors = controller.GetDescriptors(desc_store, [desc_range])

    if descriptors and len(descriptors) > 0:
        desc = descriptors[0]
        resource_id = desc.resource  # The actual bound resource
```

### DescriptorAccess Structure

The `DescriptorAccess` object contains:
- `stage` - ShaderStage (Vertex, Fragment, Compute, etc.)
- `type` - DescriptorType (Image, Buffer, ReadWriteBuffer, etc.)
- `index` - Index in the shader reflection list (matches `refl.readOnlyResources[index]` or `refl.readWriteResources[index]`)
- `descriptorStore` - ResourceId of the descriptor set/store
- `byteOffset` - Offset within the store
- `byteSize` - Size of the descriptor

### Descriptor Types

Read-write types:
- `rd.DescriptorType.ReadWriteBuffer`
- `rd.DescriptorType.ReadWriteImage`
- `rd.DescriptorType.ReadWriteTypedBuffer`

Read-only resource types:
- `rd.DescriptorType.Image`
- `rd.DescriptorType.ImageSampler`
- `rd.DescriptorType.TypedBuffer`
- `rd.DescriptorType.Buffer`

## Reference Documentation

### refs/ Directory

The `refs/` directory contains XML documentation that can be used to understand RenderDoc APIs:

- **`refs/renderdoc.xml`** - Full RenderDoc C++ source/headers (very large, use grep)
- **`refs/renderdoc-docs.xml`** - RenderDoc Python API documentation
- **`refs/wgpu.xml`** - wgpu Vulkan abstraction layer reference

### Useful Searches

Find API usage patterns:
```bash
grep -n "GetDescriptorAccess" refs/renderdoc.xml | head -30
grep -n "DescriptorRange" refs/renderdoc-docs.xml
```

Find struct definitions:
```bash
grep -n "struct DescriptorAccess" refs/renderdoc.xml
```

## Script Development Workflow

1. **Research the API** - Search refs/ for the functionality you need
2. **Create test script** - Use `debug_resource_bindings.py` as a template
3. **Test with captures** - Run against captures in temp directory
4. **Integrate into workflow** - Add to workflows.rs with proper request/response types
5. **Expose via MCP** - Add tool handler in renderdog-mcp/src/main.rs

## Adding a New RenderDoc Tool (Step-by-Step)

### Step 1: Create the Python Script

Create a new script in `crates/renderdog-automation/scripts/`:

```python
"""
get_my_feature_json.py -- Description of what this script does.
"""

import json
import traceback
import renderdoc as rd

REQ_PATH = "get_my_feature_json.request.json"
RESP_PATH = "get_my_feature_json.response.json"

def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)

def flatten_actions(roots):
    """Yield every leaf action in linear order."""
    for action in roots:
        if len(action.children) > 0:
            yield from flatten_actions(action.children)
        else:
            yield action

def main() -> None:
    with open(REQ_PATH, "r", encoding="utf-8") as f:
        req = json.load(f)

    # Your parameters from request
    capture_path = req["capture_path"]
    # ... other params ...

    rd.InitialiseReplay(rd.GlobalEnvironment(), [])

    cap = rd.OpenCaptureFile()
    try:
        result = cap.OpenFile(capture_path, "", None)
        if result != rd.ResultCode.Succeeded:
            raise RuntimeError("Couldn't open file: " + str(result))

        if not cap.LocalReplaySupport():
            raise RuntimeError("Capture cannot be replayed")

        result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if result != rd.ResultCode.Succeeded:
            raise RuntimeError("Couldn't initialise replay: " + str(result))

        try:
            # Your logic here
            actions = list(flatten_actions(controller.GetRootActions()))

            # Build result document
            document = {
                "your_field": "your_value",
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
```

### Step 2: Add Request/Response Types in Rust

In `crates/renderdog-automation/src/workflows.rs`:

```rust
// Request type
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct GetMyFeatureRequest {
    pub capture_path: String,
    // ... other fields ...
}

// Response type
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct GetMyFeatureResponse {
    pub your_field: String,
    // ... other fields ...
}

// Error type
#[derive(Debug, Error)]
pub enum GetMyFeatureError {
    #[error("failed to create scripts dir: {0}")]
    CreateScriptsDir(std::io::Error),
    #[error("failed to write script: {0}")]
    WriteScript(std::io::Error),
    #[error("failed to write request: {0}")]
    WriteRequest(std::io::Error),
    #[error("failed to read response: {0}")]
    ReadResponse(std::io::Error),
    #[error("failed to parse JSON: {0}")]
    ParseJson(serde_json::Error),
    #[error("qrenderdoc python failed: {0}")]
    QRenderDocPython(Box<crate::QRenderDocPythonError>),
    #[error("script error: {0}")]
    ScriptError(String),
}

impl From<crate::QRenderDocPythonError> for GetMyFeatureError {
    fn from(value: crate::QRenderDocPythonError) -> Self {
        Self::QRenderDocPython(Box::new(value))
    }
}
```

### Step 3: Embed Script and Add Workflow Method

In `crates/renderdog-automation/src/workflows.rs`:

```rust
// At the top with other script constants
const GET_MY_FEATURE_JSON_PY: &str = include_str!("../scripts/get_my_feature_json.py");

// In impl RenderDocInstallation
impl RenderDocInstallation {
    pub fn get_my_feature(
        &self,
        cwd: &Path,
        req: &GetMyFeatureRequest,
    ) -> Result<GetMyFeatureResponse, GetMyFeatureError> {
        let scripts_dir = default_scripts_dir(cwd);
        std::fs::create_dir_all(&scripts_dir)
            .map_err(GetMyFeatureError::CreateScriptsDir)?;

        let script_path = scripts_dir.join("get_my_feature_json.py");
        write_script_file(&script_path, GET_MY_FEATURE_JSON_PY)
            .map_err(GetMyFeatureError::WriteScript)?;

        let run_dir = create_qrenderdoc_run_dir(&scripts_dir, "get_my_feature")
            .map_err(GetMyFeatureError::CreateScriptsDir)?;
        let request_path = run_dir.join("get_my_feature_json.request.json");
        let response_path = run_dir.join("get_my_feature_json.response.json");
        remove_if_exists(&response_path).map_err(GetMyFeatureError::WriteRequest)?;

        let req = GetMyFeatureRequest {
            capture_path: resolve_path_string_from_cwd(cwd, &req.capture_path),
            // ... resolve other paths ...
        };

        std::fs::write(
            &request_path,
            serde_json::to_vec(&req).map_err(GetMyFeatureError::ParseJson)?,
        )
        .map_err(GetMyFeatureError::WriteRequest)?;

        let result = self.run_qrenderdoc_python(&QRenderDocPythonRequest {
            script_path: script_path.clone(),
            args: Vec::new(),
            working_dir: Some(run_dir.clone()),
        })?;
        let _ = result;

        let bytes = std::fs::read(&response_path)
            .map_err(GetMyFeatureError::ReadResponse)?;
        let env: QRenderDocJsonEnvelope<GetMyFeatureResponse> =
            serde_json::from_slice(&bytes).map_err(GetMyFeatureError::ParseJson)?;

        if env.ok {
            env.result
                .ok_or_else(|| GetMyFeatureError::ScriptError("missing result".into()))
        } else {
            Err(GetMyFeatureError::ScriptError(
                env.error.unwrap_or_else(|| "unknown error".into()),
            ))
        }
    }
}
```

### Step 4: Expose via MCP Server

In `crates/renderdog-mcp/src/main.rs`:

```rust
// Add request/response wrappers if needed (for cwd handling)
#[derive(Debug, Deserialize, JsonSchema)]
struct GetMyFeatureRequestMcp {
    #[serde(default)]
    cwd: Option<String>,
    capture_path: String,
    // ... other fields ...
}

// Add the tool handler
#[tool(
    name = "renderdoc_get_my_feature",
    description = "Description shown to Claude about what this tool does"
)]
async fn get_my_feature(
    &self,
    #[arg(description = "Path to the .rdc capture file")] capture_path: String,
    // ... other args with descriptions ...
) -> Result<Json<renderdog::GetMyFeatureResponse>, String> {
    let start = Instant::now();
    tracing::info!(
        tool = "renderdoc_get_my_feature",
        "invoked"
    );

    let installation = match renderdog::RenderDocInstallation::find_in_path() {
        Ok(i) => i,
        Err(e) => {
            tracing::error!(tool = "renderdoc_get_my_feature", "failed");
            tracing::debug!(tool = "renderdoc_get_my_feature", err = %e, "details");
            return Err(format!("RenderDoc not found: {e}"));
        }
    };

    let result = installation
        .get_my_feature(
            &self.cwd,
            &renderdog::GetMyFeatureRequest {
                capture_path,
                // ... other fields ...
            },
        )
        .map_err(|e| {
            tracing::error!(tool = "renderdoc_get_my_feature", "failed");
            tracing::debug!(tool = "renderdoc_get_my_feature", err = %e, "details");
            format!("{e}")
        })?;

    tracing::info!(
        tool = "renderdoc_get_my_feature",
        elapsed_ms = start.elapsed().as_millis() as u64,
        "done"
    );
    Ok(Json(result))
}

// Don't forget to add to the tool_router! macro at the bottom
```

### Step 5: Test the Tool

**IMPORTANT**: Never run scripts directly via `qrenderdoc --python` - this opens a GUI window.

1. **Build**: `cargo build --package renderdog-automation --package renderdog-mcp`

2. **Test via Rust workflow**: Create a simple test binary or use an existing example:
   ```bash
   # Check if the code compiles
   cargo check --package renderdog-automation

   # Run any existing tests
   cargo test --package renderdog-automation
   ```

3. **Test via MCP server**: The MCP server runs the scripts headlessly. Call the tool through Claude or test the MCP server directly.

4. **Verify output**: Check the response JSON file:
   ```bash
   # Find the latest run directory
   ls -t .renderdog-scripts/get_my_feature_*/ | head -1

   # Pretty-print the response
   cat .renderdog-scripts/get_my_feature_*/get_my_feature_json.response.json | python -m json.tool
   ```

## Common Pitfalls

1. **Running qrenderdoc directly** - NEVER use `qrenderdoc --python script.py` directly as this opens a GUI window. Always test through the Rust workflow which runs scripts headlessly.
2. **Empty resource lists** - For compute pipelines, `GetReadOnlyResources`/`GetReadWriteResources` work well. For graphics pipelines with descriptor buffers, these may return empty - use `GetDescriptorAccess` as a fallback.
3. **Event timing** - Call `controller.SetFrameEvent(eid, False)` before querying state
4. **Null checks** - Always check `ResourceId.Null()` before using resource IDs
5. **Reflection indexing** - `access.index` matches the index in reflection lists
6. **ResourceId serialization** - When passing ResourceId between Python and JSON, convert to `int()` for serialization but keep the original object for API calls

## Known Limitations

### Descriptor Buffers vs Traditional Descriptor Sets

**Observed Behavior**: The `GetDescriptorAccess()` + `GetDescriptors()` approach works differently depending on how descriptors are bound:

1. **Traditional Descriptor Sets (compute pipelines)**: Resources resolve correctly
   ```json
   {
     "descriptor_store": "ResourceId::54555",  // Normal ID
     "resource_name": "physics::particle_system::particles:109"  // Resolved!
   }
   ```

2. **Descriptor Buffers (graphics pipelines with wgpu)**: Resources do NOT resolve
   ```json
   {
     "descriptor_store": "ResourceId::1000000000000000380",  // Virtual ID
     "resource_name": null  // Cannot be resolved
   }
   ```

The very large descriptor store IDs (like `1000000000000000xxx`) are virtual/internal RenderDoc IDs for descriptor buffer storage. `GetDescriptors()` returns 0 descriptors for these virtual stores.

### Graphics Pipeline resource_bindings Limitation

**Current State**: For graphics pipelines using wgpu with Vulkan descriptor buffers:
- `resource_bindings` entries will NOT have `example_resource` populated
- All other metadata IS available: `name`, `type`, `set`, `binding`, `schema`, `read_write`

**What DOES work for graphics pipelines**:
- Vertex buffer bindings: `example_resource` IS populated (via `GetVBuffers()`)
- Index buffer: `example_resource` IS populated (via `GetIBuffer()`)
- Render targets: `example_resource` IS populated (via `GetOutputTargets()`)
- Constant blocks: Metadata IS available from shader reflection

**Root Cause**:
1. wgpu uses VK_EXT_descriptor_buffer for graphics pipelines, storing descriptors directly in GPU memory buffers
2. Graphics pipelines do NOT appear in `vkCmdBindDescriptorSets` calls at all - they use the descriptor buffer mechanism instead
3. RenderDoc tracks descriptor accesses but uses virtual IDs (like `1000000000000000xxx`) for descriptor buffer storage
4. `GetDescriptors()` cannot resolve resources from these virtual stores
5. `GetReadOnlyResources()` and `GetReadWriteResources()` return empty lists for graphics pipelines
6. `GetUsage()` doesn't return usage records for graphics pipeline draw events

**What was tried**:
- Parsing `vkUpdateDescriptorSets` structured file data → works for compute pipelines
- Parsing `vkCmdBindDescriptorSets` to find bound descriptor sets → graphics pipelines don't use this
- Using `GetDescriptorAccess()` to find resource accesses → returns virtual store IDs that can't be resolved
- Name-based matching between binding names and resource names → names don't match (e.g., "entity_transforms" vs "model::pbr::...")

**Implications for Claude**:
- When analyzing graphics pipeline bindings, focus on the binding metadata (set/binding/type/schema)
- Don't expect to find the exact bound resource instance for resource_bindings
- Vertex buffers, index buffer, and render targets ARE resolved correctly via dedicated APIs
- Compute pipelines work correctly - use them as a model for expected behavior

## Test Captures

Default location: `C:\Users\mattm\AppData\Local\Temp\RenderDoc\`

**IMPORTANT**: Before testing scripts, verify captures exist:
```bash
ls "C:/Users/mattm/AppData/Local/Temp/RenderDoc/"*.rdc
```

If no captures exist, prompt the user:
> "No RenderDoc captures found in C:\Users\mattm\AppData\Local\Temp\RenderDoc\.
> Please create a capture by:
> 1. Running an application with RenderDoc attached
> 2. Pressing F12 to capture a frame
> 3. The .rdc file will be saved to the temp directory"

Example pipelines for testing:
- Graphics: `model::pbr::pb_render_pipeline::pipeline:17`
- Compute: `physics::compute_pipeline::update_particles:18`

## Using Reference Documentation

The `refs/` directory contains valuable XML documentation for understanding RenderDoc APIs:

### Available References

| File | Description | Size |
|------|-------------|------|
| `refs/renderdoc.xml` | Full RenderDoc C++ source/headers | Very large |
| `refs/renderdoc-docs.xml` | RenderDoc Python API documentation | Medium |
| `refs/wgpu.xml` | wgpu Vulkan abstraction layer | Large |
| `refs/gpuweb.xml` | WebGPU specification (W3C) | Large |

### How to Use refs/

1. **Search for API patterns**:
   ```bash
   grep -n "GetDescriptorAccess" refs/renderdoc.xml | head -30
   ```

2. **Find struct definitions**:
   ```bash
   grep -n "struct DescriptorRange" refs/renderdoc.xml
   ```

3. **Read specific sections** (files are too large to read entirely):
   ```bash
   # Find line number first
   grep -n "class PipeState" refs/renderdoc.xml
   # Then read that section
   sed -n '160943,161050p' refs/renderdoc.xml
   ```

4. **Understand wgpu's Vulkan usage**:
   ```bash
   grep -n "descriptor" refs/wgpu.xml | head -50
   ```

### Key Sections in refs/renderdoc-docs.xml

- **Lines 8424-8554**: Descriptor access documentation (GetDescriptorAccess, GetDescriptors)
- **Lines 12760-12770**: DescriptorRange, DescriptorAccess, DescriptorLogicalLocation classes

These references are essential for creating new RenderDoc automation scripts!
