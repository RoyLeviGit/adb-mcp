# MIT License
#
# Copyright (c) 2025 Mike Chambers
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage

from core import init, sendCommand, createCommand
import socket_client
import sys
import tempfile
import os
import io
import shutil


#logger.log(f"Python path: {sys.executable}")
#logger.log(f"PYTHONPATH: {os.environ.get('PYTHONPATH')}")
#logger.log(f"Current working directory: {os.getcwd()}")
#logger.log(f"Sys.path: {sys.path}")


mcp_name = "Adobe Premiere MCP Server"
mcp = FastMCP(mcp_name, log_level="ERROR")
print(f"{mcp_name} running on stdio", file=sys.stderr)

APPLICATION = "premiere"
PROXY_URL = 'http://localhost:3001'
PROXY_TIMEOUT = 20

socket_client.configure(
    app=APPLICATION, 
    url=PROXY_URL,
    timeout=PROXY_TIMEOUT
)

init(APPLICATION, socket_client)

_TYPES_SRC = os.path.join(os.path.dirname(__file__), "..", "types.d.ts")
TYPES_PATH = os.path.join(tempfile.gettempdir(), "premiere-types.d.ts")
shutil.copy2(_TYPES_SRC, TYPES_PATH)

@mcp.tool()
def execute_uxp_script(script_path: str, params: dict = None):
    """
    Executes a UXP JavaScript file inside Premiere Pro.

    The script has access to the following globals:
        - app        : the premierepro module (full Premiere Pro API)
        - constants  : premierepro.Constants
        - fs         : uxp.storage.localFileSystem
        - params     : dict passed in via this call (optional)

    The script should return a JSON-serializable value, which is surfaced as
    the 'response' field in the result. Throwing an Error causes a FAILURE status.

    Args:
        script_path (str): Absolute path to the .js script file to execute.
        params (dict, optional): Key/value pairs made available inside the script
            as the 'params' variable. Defaults to {}.

    Example script (save_and_report.js):
        const project = await app.Project.getActiveProject();
        await project.save();
        return { saved: project.name };
    """
    command = createCommand("executeScript", {
        "scriptPath": script_path,
        "params": params or {}
    })
    return sendCommand(command)


@mcp.tool()
def get_project_info():
    """
    Returns info on the currently active project in Premiere Pro.
    """

    command = createCommand("getProjectInfo", {
    })

    return sendCommand(command)


@mcp.tool()
def get_sequence_frame_image(sequence_id: str, seconds: int):
    """Returns a jpeg of the specified timestamp in the specified sequence in Premiere pro as an MCP Image object that can be displayed."""
    
    temp_dir = tempfile.gettempdir()
    file_path = os.path.join(temp_dir, f"frame_{sequence_id}_{seconds}.png")
    
    command = createCommand("exportFrame", {
        "sequenceId": sequence_id,
        "filePath": file_path,
        "seconds": seconds
    })
    
    result = sendCommand(command)
    
    if not result.get("status") == "SUCCESS":
        return result
    
    file_path = result["response"]["filePath"]
    
    with open(file_path, 'rb') as f:
        png_image = PILImage.open(f)
        
        # Convert to RGB if necessary (removes alpha channel)
        if png_image.mode in ("RGBA", "LA", "P"):
            rgb_image = PILImage.new("RGB", png_image.size, (255, 255, 255))
            rgb_image.paste(png_image, mask=png_image.split()[-1] if png_image.mode == "RGBA" else None)
            png_image = rgb_image
        
        # Save as JPEG to bytes buffer
        jpeg_buffer = io.BytesIO()
        png_image.save(jpeg_buffer, format="JPEG", quality=85, optimize=True)
        jpeg_bytes = jpeg_buffer.getvalue()
    
    image = Image(data=jpeg_bytes, format="jpeg")
    
    del result["response"]
    
    try:
        os.remove(file_path)
    except FileNotFoundError:
        pass
    
    return [result, image]

@mcp.tool()
def export_sequence(sequence_id: str, output_path: str, preset_path: str):
    """
    Exports a Premiere Pro sequence to a video file using specified export settings.

    Args:
        sequence_id (str): The unique identifier of the sequence to export.
        output_path (str): The complete file system path where the exported video will be saved.
        preset_path (str): Path to the export preset file (.epr).

        IMPORTANT: The export may take an extended period of time, so if the call times out,
        it most likely means the export is still in progress.
    """
    command = createCommand("exportSequence", {
        "sequenceId": sequence_id,
        "outputPath": output_path,
        "presetPath": preset_path
    })
    return sendCommand(command)


@mcp.resource("config://get_instructions")
def get_instructions() -> str:
    """Read this first! Returns information and instructions on how to use Premiere Pro and this API"""

    return f"""
    You are a Premiere Pro and video expert who is creative and loves to help other people learn to use Premiere and create.

    Rules to follow:

    1. Think deeply about how to solve the task
    2. Always check your work — call get_project_info first to understand the current project state
    3. Use execute_uxp_script for all editing operations; write the script to a temp file then call it
    4. In general, add clips first, then effects, then transitions
    5. Keep transitions short (no more than 2 seconds), and clips must have no gap between them for transitions to work

    IMPORTANT: To create a new project and add clips:
    1. Create new project via execute_uxp_script
    2. Import media via execute_uxp_script
    3. Create a sequence via execute_uxp_script (add video/image clips before audio)
    4. The first clip determines the sequence dimensions/resolution

    Scripts passed to execute_uxp_script have access to:
        - app        : premierepro module (full Premiere Pro API)
        - constants  : premierepro.Constants
        - fs         : uxp.storage.localFileSystem
        - params     : dict of values passed from the caller

    Scripts should return a JSON-serializable value. Throw an Error to signal failure.

    IMPORTANT: Before writing any script, read the Premiere Pro API type declarations at:
        {TYPES_PATH}
    This file documents every class, method, and property available on `app`, `constants`, etc.

    General Premiere tips:
    - Audio and video clips are on separate tracks accessed by index (0-based)
    - A video clip with audio places the audio on a separate audio track automatically
    - Clips with a higher video track index overlap/hide those with a lower index
    - Images added to a sequence have a default duration of 5 seconds
    """