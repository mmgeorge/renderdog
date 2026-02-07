import json
import renderdoc as rd

capture_path = "C:/Users/mattm/AppData/Local/Temp/RenderDoc/run-game_2026.02.01_16.33_frame395.rdc"
layout_id = 297

output = []

rd.InitialiseReplay(rd.GlobalEnvironment(), [])

cap = rd.OpenCaptureFile()
result = cap.OpenFile(capture_path, "", None)
if result != rd.ResultCode.Succeeded:
    output.append("Failed to open file")
    with open("debug_output.txt", "w") as f:
        f.write("\n".join(output))
    raise SystemExit(1)

result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
if result != rd.ResultCode.Succeeded:
    output.append("Failed to open capture")
    with open("debug_output.txt", "w") as f:
        f.write("\n".join(output))
    raise SystemExit(1)

# Find the layout resource
res_desc = None
for res in controller.GetResources():
    if int(res.resourceId) == layout_id:
        res_desc = res
        output.append(f"Found resource: {res.name}")
        output.append(f"Type: {res.type}")
        output.append(f"Initialisation chunks: {list(res.initialisationChunks)}")
        break

if not res_desc:
    output.append(f"Resource {layout_id} not found!")
else:
    sfile = controller.GetStructuredFile()
    output.append(f"\nStructured file has {len(sfile.chunks)} chunks")
    
    for chunk_idx in res_desc.initialisationChunks:
        chunk = sfile.chunks[chunk_idx]
        output.append(f"\nChunk {chunk_idx}: {chunk.name}")
        output.append(f"  Data children: {[c.name for c in chunk.data.children]}")
        
        # Look deeper at CreateInfo if present
        for child in chunk.data.children:
            if "CreateInfo" in child.name or "pCreateInfo" in child.name:
                output.append(f"  CreateInfo children: {[c.name for c in child.children]}")
                for subchild in child.children:
                    if "Binding" in subchild.name or "pBinding" in subchild.name:
                        output.append(f"    {subchild.name} has {len(subchild.children)} items")
                        if subchild.children:
                            output.append(f"    First item children: {[c.name for c in subchild.children[0].children] if subchild.children else 'empty'}")

with open("debug_output.txt", "w") as f:
    f.write("\n".join(output))

controller.Shutdown()
cap.Shutdown()
rd.ShutdownReplay()
