"""
get_texture_changes_delta_json.py -- RenderDoc Python script for tracking texture texel changes.

Finds a texture by name, reads texel data at specified coordinates at every action
in the frame, and returns only the snapshots where a texel value actually changed.

Uses delta encoding: initial_state contains the first seen values, changes contain only diffs.

Request parameters:
  - texture_name: Name of the texture to track
  - capture_path: Path to the .rdc capture file
  - tracked_texels: List of {x, y, z?, mip?, slice?} coordinates (default: [{x:0, y:0}])

Returns:
  - tracked_texels: The texel coordinates that were tracked
  - format: Texture format info
  - total_changes: Total number of changes detected
  - texels: Array of {coord, initial_event_id, initial_state, changes}
"""

import struct
import json
import traceback

import renderdoc as rd


REQ_PATH = "get_texture_changes_delta_json.request.json"
RESP_PATH = "get_texture_changes_delta_json.response.json"


def write_envelope(ok: bool, result=None, error: str = None) -> None:
    with open(RESP_PATH, "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "result": result, "error": error}, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Texture format handling
# ---------------------------------------------------------------------------

def get_format_info(fmt):
    """Extract channel count and data type info from a ResourceFormat."""
    fmt_name = str(fmt.Name())

    # Determine component count and type from format
    comp_count = fmt.compCount
    comp_byte_width = fmt.compByteWidth
    comp_type = fmt.compType

    type_name = str(comp_type).replace("CompType.", "")

    return {
        "name": fmt_name,
        "channels": comp_count,
        "bytes_per_channel": comp_byte_width,
        "component_type": type_name,
    }


def unpack_texel(raw_bytes, fmt):
    """Unpack raw bytes into channel values based on format."""
    comp_count = fmt.compCount
    comp_byte_width = fmt.compByteWidth
    comp_type = fmt.compType

    values = []
    offset = 0

    for i in range(comp_count):
        if offset + comp_byte_width > len(raw_bytes):
            break

        chunk = raw_bytes[offset:offset + comp_byte_width]
        offset += comp_byte_width

        # Unpack based on component type and width
        if comp_type == rd.CompType.Float:
            if comp_byte_width == 4:
                val = struct.unpack('f', chunk)[0]
            elif comp_byte_width == 2:
                # Half float - Python 3.6+ supports 'e' format
                val = struct.unpack('e', chunk)[0]
            elif comp_byte_width == 8:
                val = struct.unpack('d', chunk)[0]
            else:
                val = 0.0
            values.append(round(val, 6))  # Round for cleaner output
        elif comp_type == rd.CompType.UInt:
            if comp_byte_width == 1:
                val = struct.unpack('B', chunk)[0]
            elif comp_byte_width == 2:
                val = struct.unpack('H', chunk)[0]
            elif comp_byte_width == 4:
                val = struct.unpack('I', chunk)[0]
            else:
                val = 0
            values.append(val)
        elif comp_type == rd.CompType.SInt:
            if comp_byte_width == 1:
                val = struct.unpack('b', chunk)[0]
            elif comp_byte_width == 2:
                val = struct.unpack('h', chunk)[0]
            elif comp_byte_width == 4:
                val = struct.unpack('i', chunk)[0]
            else:
                val = 0
            values.append(val)
        elif comp_type == rd.CompType.UNorm:
            # Unsigned normalized: 0-255 -> 0.0-1.0
            if comp_byte_width == 1:
                val = struct.unpack('B', chunk)[0] / 255.0
            elif comp_byte_width == 2:
                val = struct.unpack('H', chunk)[0] / 65535.0
            else:
                val = 0.0
            values.append(round(val, 6))
        elif comp_type == rd.CompType.SNorm:
            # Signed normalized: -128-127 -> -1.0-1.0
            if comp_byte_width == 1:
                val = struct.unpack('b', chunk)[0] / 127.0
            elif comp_byte_width == 2:
                val = struct.unpack('h', chunk)[0] / 32767.0
            else:
                val = 0.0
            values.append(round(val, 6))
        elif comp_type == rd.CompType.UNormSRGB:
            # sRGB normalized
            if comp_byte_width == 1:
                val = struct.unpack('B', chunk)[0] / 255.0
            else:
                val = 0.0
            values.append(round(val, 6))
        else:
            # Fallback: treat as unsigned int
            if comp_byte_width == 1:
                val = struct.unpack('B', chunk)[0]
            elif comp_byte_width == 2:
                val = struct.unpack('H', chunk)[0]
            elif comp_byte_width == 4:
                val = struct.unpack('I', chunk)[0]
            else:
                val = 0
            values.append(val)

    # Name the channels
    channel_names = ['r', 'g', 'b', 'a'][:len(values)]
    return dict(zip(channel_names, values))


# ---------------------------------------------------------------------------
# Texture finding and reading
# ---------------------------------------------------------------------------

def find_texture(controller, texture_name):
    """Locate the target texture's resource description by name."""
    for res in controller.GetResources():
        if res.name == texture_name:
            return res

    available = []
    for r in controller.GetResources():
        if r.type == rd.ResourceType.Texture:
            available.append("  %s  %s" % (r.resourceId, r.name))

    raise RuntimeError(
        "Texture '%s' not found. Available textures:\n%s"
        % (texture_name, "\n".join(available[:30]))
    )


def read_texel(controller, tex_id, tex_desc, x, y, z=0, mip=0, slice_idx=0):
    """Read a single texel from a texture at the current replay state."""
    try:
        # Calculate the subresource index
        # subresource = mip + (slice * mipLevels)
        subresource = mip + (slice_idx * tex_desc.mips)

        # Get the texture data for this subresource
        # We need to read the whole subresource and extract our texel
        data = controller.GetTextureData(tex_id, rd.Subresource(mip, slice_idx, 0))

        if data is None or len(data) == 0:
            return None

        # Calculate texel offset
        # Width at this mip level
        mip_width = max(1, tex_desc.width >> mip)
        mip_height = max(1, tex_desc.height >> mip)

        # Clamp coordinates
        x = min(x, mip_width - 1)
        y = min(y, mip_height - 1)

        # Bytes per texel
        fmt = tex_desc.format
        bytes_per_texel = fmt.compCount * fmt.compByteWidth

        # Handle block-compressed formats
        if fmt.type == rd.ResourceFormatType.BC1 or \
           fmt.type == rd.ResourceFormatType.BC2 or \
           fmt.type == rd.ResourceFormatType.BC3 or \
           fmt.type == rd.ResourceFormatType.BC4 or \
           fmt.type == rd.ResourceFormatType.BC5 or \
           fmt.type == rd.ResourceFormatType.BC6 or \
           fmt.type == rd.ResourceFormatType.BC7:
            # For compressed formats, we can't easily read individual texels
            # Return a note about this
            return {"compressed": True, "format": str(fmt.Name())}

        # Row pitch (bytes per row)
        row_pitch = mip_width * bytes_per_texel

        # Calculate byte offset
        offset = y * row_pitch + x * bytes_per_texel

        if offset + bytes_per_texel > len(data):
            return None

        texel_bytes = data[offset:offset + bytes_per_texel]
        return unpack_texel(bytes(texel_bytes), fmt)

    except Exception as e:
        return {"error": str(e)}


def flatten_actions(roots):
    """Yield every leaf action in linear order."""
    for action in roots:
        if len(action.children) > 0:
            yield from flatten_actions(action.children)
        else:
            yield action


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------

def diff_texel(old, new):
    """Compare two texel values and return a diff if different."""
    if old == new:
        return None

    # If either has an error or is compressed, just return the new value
    if isinstance(old, dict) and ("error" in old or "compressed" in old):
        return new
    if isinstance(new, dict) and ("error" in new or "compressed" in new):
        return new

    # Compare channel by channel
    if isinstance(old, dict) and isinstance(new, dict):
        diff = {}
        all_keys = set(old.keys()) | set(new.keys())
        for k in all_keys:
            old_val = old.get(k)
            new_val = new.get(k)
            if old_val != new_val:
                diff[k] = new_val
        return diff if diff else None

    return new


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    with open(REQ_PATH, "r", encoding="utf-8") as f:
        req = json.load(f)

    texture_name = req["texture_name"]
    tracked_texels = req.get("tracked_texels", [{"x": 0, "y": 0}])

    rd.InitialiseReplay(rd.GlobalEnvironment(), [])

    cap = rd.OpenCaptureFile()
    try:
        result = cap.OpenFile(req["capture_path"], "", None)
        if result != rd.ResultCode.Succeeded:
            raise RuntimeError("Couldn't open file: " + str(result))

        if not cap.LocalReplaySupport():
            raise RuntimeError("Capture cannot be replayed")

        result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if result != rd.ResultCode.Succeeded:
            raise RuntimeError("Couldn't initialise replay: " + str(result))

        try:
            # Find the texture
            tex_res = find_texture(controller, texture_name)
            tex_id = tex_res.resourceId

            # Get texture description from GetTextures() list
            tex_desc = None
            for t in controller.GetTextures():
                if t.resourceId == tex_id:
                    tex_desc = t
                    break
            if tex_desc is None:
                raise RuntimeError("Texture '%s' not found in GetTextures()" % texture_name)

            # Get format info
            fmt_info = get_format_info(tex_desc.format)

            # Scan all actions
            actions = list(flatten_actions(controller.GetRootActions()))
            if not actions:
                raise RuntimeError("No actions found in capture")

            # Track texel changes
            texel_initial = {}  # coord_key -> (event_id, value)
            texel_changes = {}  # coord_key -> list of changes
            last_values = {}    # coord_key -> last seen value
            total_changes = 0

            # Normalize tracked_texels
            normalized_texels = []
            for t in tracked_texels:
                coord = {
                    "x": t.get("x", 0),
                    "y": t.get("y", 0),
                    "z": t.get("z", 0),
                    "mip": t.get("mip", 0),
                    "slice": t.get("slice", 0),
                }
                normalized_texels.append(coord)
                coord_key = (coord["x"], coord["y"], coord["z"], coord["mip"], coord["slice"])
                texel_changes[coord_key] = []

            for action in actions:
                eid = action.eventId
                controller.SetFrameEvent(eid, False)

                for coord in normalized_texels:
                    coord_key = (coord["x"], coord["y"], coord["z"], coord["mip"], coord["slice"])

                    current = read_texel(
                        controller, tex_id, tex_desc,
                        coord["x"], coord["y"], coord["z"],
                        coord["mip"], coord["slice"]
                    )

                    if current is None:
                        continue

                    prev = last_values.get(coord_key)

                    if prev is None:
                        # First time seeing this texel
                        texel_initial[coord_key] = (eid, current)
                        last_values[coord_key] = current
                    elif current != prev:
                        # Value changed
                        delta = diff_texel(prev, current)
                        if delta is not None:
                            texel_changes[coord_key].append({
                                "event_id": eid,
                                "delta": delta,
                            })
                            total_changes += 1
                        last_values[coord_key] = current

            # Build texels array
            texels = []
            for coord in normalized_texels:
                coord_key = (coord["x"], coord["y"], coord["z"], coord["mip"], coord["slice"])

                if coord_key not in texel_initial:
                    continue

                init_eid, init_state = texel_initial[coord_key]

                texels.append({
                    "coord": coord,
                    "initial_event_id": init_eid,
                    "initial_state": init_state,
                    "changes": texel_changes[coord_key],
                })

            # Build final document
            document = {
                "tracked_texels": normalized_texels,
                "format": fmt_info,
                "total_changes": total_changes,
                "texels": texels,
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
