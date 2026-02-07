fn main() {
    let installation = renderdog_automation::RenderDocInstallation::detect()
        .expect("RenderDoc not found");
    let cwd = std::env::current_dir().expect("Failed to get current dir");

    let capture_path = "C:/Users/mattm/AppData/Local/Temp/RenderDoc/run-game_2026.02.01_16.33_frame395.rdc";

    // Test compute pipeline (has traditional descriptor sets)
    println!("=== Testing Compute Pipeline ===\n");
    let result = installation.get_pipeline_details(
        &cwd,
        &renderdog_automation::GetPipelineDetailsRequest {
            capture_path: capture_path.to_string(),
            pipeline_name: "physics::compute_pipeline::update_particles".to_string(),
        },
    );

    match result {
        Ok(resp) => {
            // Show resource_bindings with example_resource status
            println!("Resource bindings ({} total):", resp.resource_bindings.len());
            for rb in &resp.resource_bindings {
                let has_example = rb.example_resource.is_some();
                println!("  - {} (set={:?}, binding={:?}): example_resource={}",
                    rb.name,
                    rb.set,
                    rb.binding,
                    if has_example { rb.example_resource.as_ref().unwrap() } else { "MISSING" }
                );
            }
        }
        Err(e) => {
            eprintln!("Error: {e}");
        }
    }

    println!("\n\n=== Testing Graphics Pipeline ===\n");

    // Test graphics pipeline
    let result = installation.get_pipeline_details(
        &cwd,
        &renderdog_automation::GetPipelineDetailsRequest {
            capture_path: capture_path.to_string(),
            pipeline_name: "model::pbr::pb_render_pipeline::pipeline".to_string(),
        },
    );

    match result {
        Ok(resp) => {
            // Print full JSON to see debug info
            println!("{}", serde_json::to_string_pretty(&resp).unwrap());
        }
        Err(e) => {
            eprintln!("Error: {e}");
        }
    }
}
