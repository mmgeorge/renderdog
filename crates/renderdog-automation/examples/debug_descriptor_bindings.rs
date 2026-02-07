fn main() {
    let installation = renderdog_automation::RenderDocInstallation::detect()
        .expect("RenderDoc not found");
    let cwd = std::env::current_dir().expect("Failed to get current dir");

    let capture_path = "C:/Users/mattm/AppData/Local/Temp/RenderDoc/run-game_2026.02.01_16.33_frame395.rdc";

    // Set up scripts directory
    let scripts_dir = cwd.join("artifacts").join("renderdoc").join("scripts");
    std::fs::create_dir_all(&scripts_dir).unwrap();

    // Copy the debug script
    let script_content = include_str!("../scripts/debug_descriptor_bindings.py");
    let script_path = scripts_dir.join("debug_descriptor_bindings.py");
    std::fs::write(&script_path, script_content).unwrap();

    // Write the request
    let request = serde_json::json!({
        "capture_path": capture_path,
        "events": [48, 49, 50]
    });
    let request_path = scripts_dir.join("debug_descriptor_bindings.request.json");
    std::fs::write(&request_path, serde_json::to_string_pretty(&request).unwrap()).unwrap();

    // Run qrenderdoc
    let output = std::process::Command::new(&installation.qrenderdoc_exe)
        .arg("--python")
        .arg(&script_path)
        .current_dir(&scripts_dir)
        .output()
        .expect("Failed to run qrenderdoc");

    if !output.stdout.is_empty() {
        println!("stdout: {}", String::from_utf8_lossy(&output.stdout));
    }
    if !output.stderr.is_empty() {
        eprintln!("stderr: {}", String::from_utf8_lossy(&output.stderr));
    }

    // Read the response
    let response_path = scripts_dir.join("debug_descriptor_bindings.response.json");
    if response_path.exists() {
        let response = std::fs::read_to_string(&response_path).unwrap();
        println!("\n=== Descriptor Bindings Debug Output ===\n");
        println!("{}", response);
    }
}
