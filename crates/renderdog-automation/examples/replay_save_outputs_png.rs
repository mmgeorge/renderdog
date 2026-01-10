use std::path::PathBuf;

use renderdog_automation as renderdog;

fn main() -> anyhow::Result<()> {
    let mut args = std::env::args().skip(1);
    let capture_path = args.next().ok_or_else(|| {
        anyhow::anyhow!(
            "usage: replay_save_outputs_png <capture.rdc> [event_id] [out_dir] [basename]"
        )
    })?;

    let event_id = args.next().and_then(|s| s.parse::<u32>().ok());

    let cwd = std::env::current_dir()?;
    let out_dir = args
        .next()
        .map(PathBuf::from)
        .unwrap_or_else(|| renderdog::default_exports_dir(&cwd).join("replay"));
    std::fs::create_dir_all(&out_dir)?;

    let basename = args.next().unwrap_or_else(|| {
        PathBuf::from(&capture_path)
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("capture")
            .to_string()
    });

    let install = renderdog::RenderDocInstallation::detect()?;

    let res = install.replay_save_outputs_png(
        &cwd,
        &renderdog::ReplaySaveOutputsPngRequest {
            capture_path,
            event_id,
            output_dir: out_dir.display().to_string(),
            basename,
            include_depth: false,
        },
    )?;

    println!("{}", serde_json::to_string_pretty(&res)?);
    Ok(())
}
