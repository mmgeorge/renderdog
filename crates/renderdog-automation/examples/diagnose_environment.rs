use anyhow::Context;

fn main() -> anyhow::Result<()> {
    let install = renderdog_automation::RenderDocInstallation::detect()
        .context("failed to detect RenderDoc installation; set RENDERDOG_RENDERDOC_DIR")?;

    let diag = install
        .diagnose_environment()
        .context("failed to diagnose RenderDoc environment")?;

    let json = serde_json::to_string_pretty(&diag).context("failed to serialize JSON")?;
    println!("{json}");
    Ok(())
}
