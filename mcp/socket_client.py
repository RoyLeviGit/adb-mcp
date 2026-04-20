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

import socketio
import time
import threading
import json
import os
import socket
import subprocess
from queue import Queue
import logger

# Global configuration variables
proxy_url = None
proxy_timeout = None
application = None

_proxy_process = None
_proxy_lock = threading.Lock()

def _find_node():
    import shutil
    node = shutil.which("node")
    if node:
        return node
    for path in ["/usr/bin/node", "/usr/local/bin/node",
                 "/home/linuxbrew/.linuxbrew/bin/node",
                 os.path.expanduser("~/.nvm/versions/node/*/bin/node")]:
        import glob
        matches = glob.glob(path)
        if matches:
            return matches[0]
        if os.path.exists(path):
            return path
    return None

def _is_proxy_running(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False

def _ensure_proxy_running():
    global _proxy_process

    with _proxy_lock:
        if _is_proxy_running("localhost", 3001):
            return

        proxy_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "adb-proxy-socket", "proxy.js"
        )

        if not os.path.exists(proxy_path):
            logger.log(f"Proxy script not found at {proxy_path}")
            return

        node_bin = _find_node()
        if not node_bin:
            logger.log("node not found in PATH, cannot auto-start proxy")
            return

        logger.log(f"Starting proxy: {proxy_path}")
        try:
            _proxy_process = subprocess.Popen(
                [node_bin, proxy_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.log(f"Failed to start proxy: {e}")
            return

        # Wait up to 5 seconds for the proxy to be ready
        for _ in range(10):
            time.sleep(0.5)
            if _is_proxy_running("localhost", 3001):
                logger.log("Proxy started successfully")
                return

        logger.log("Proxy did not start in time")

def send_message_blocking(command, timeout=None):
    """
    Blocking function that connects to a Socket.IO server, sends a message,
    waits for a response, then disconnects.
    
    Args:
        command: The command to send
        timeout (int): Maximum time to wait for response in seconds
        
    Returns:
        dict: The response received from the server, or None if no response
    """
    # Use global variables
    global application, proxy_url, proxy_timeout
    
    # Check if configuration is set
    if not application or not proxy_url or not proxy_timeout:
        logger.log("Socket client not configured. Call configure() first.")
        return None

    # Use provided timeout or default
    wait_timeout = timeout if timeout is not None else proxy_timeout
    
    # Create a standard (non-async) SocketIO client with WebSocket transport only
    sio = socketio.Client(logger=False)
    
    # Use a queue to get the response from the event handler
    response_queue = Queue()
    
    connection_failed = [False]         

    @sio.event
    def connect():
        logger.log(f"Connected to server with session ID: {sio.sid}")
        
        # Send the command
        logger.log(f"Sending message to {application}: {command}")
        sio.emit('command_packet', {
            'type': "command",
            'application': application,
            'command': command
        })
    
    @sio.event
    def packet_response(data):
        logger.log(f"Received response: {data}")
        response_queue.put(data)
        # Disconnect after receiving the response
        sio.disconnect()
    
    @sio.event
    def disconnect():
        logger.log("Disconnected from server")
        # If we disconnect without response, put None in the queue
        if response_queue.empty():
            response_queue.put(None)
    
    @sio.event
    def connect_error(error):
        logger.log(f"Connection error: {error}")
        connection_failed[0] = True
        response_queue.put(None)
    
    # Connect in a separate thread to avoid blocking the main thread during connection
    def connect_and_wait():
        try:
            sio.connect(proxy_url, transports=['websocket'])
            # Keep the client running until disconnect is called
            sio.wait()
        except Exception as e:
            logger.log(f"Error: {e}")
            connection_failed[0] = True
            if response_queue.empty():
                response_queue.put(None)
            if sio.connected:
                sio.disconnect()
    
    # Start the client in a separate thread
    client_thread = threading.Thread(target=connect_and_wait)
    client_thread.daemon = True
    client_thread.start()
    
    try:
        # Wait for a response or timeout
        logger.log("waiting for response...")
        response = response_queue.get(timeout=wait_timeout)

        if connection_failed[0]:
            raise RuntimeError(f"Error: Could not connect to {application} command proxy server. Make sure that the proxy server is running listening on the correct url {proxy_url}.")

        if response:
            logger.log("response received...")
            try:
                logger.log(json.dumps(response))
            except:
                logger.log(f"Response (not JSON-serializable): {response}")

            if response["status"] == "FAILURE":
                raise AppError(f"Error returned from {application}: {response['message']}")
            
        return response
    except AppError:
        raise
    except Exception as e:
        logger.log(f"Error waiting for response: {e}")
        if sio.connected:
            sio.disconnect()
  
        raise RuntimeError(f"Error: Could not connect to {application}. Connection Timed Out. Make sure that {application} is running and that the MCP Plugin is connected. Original error: {e}")
    finally:
        # Make sure client is disconnected
        if sio.connected:
            sio.disconnect()
        # Wait for the thread to finish (should be quick after disconnect)
        client_thread.join(timeout=1)

class AppError(Exception):
    pass

def configure(app=None, url=None, timeout=None):
    
    global application, proxy_url, proxy_timeout
    
    if app:
        application = app
    if url:
        proxy_url = url
    if timeout:
        proxy_timeout = timeout
    
    logger.log(f"Socket client configured: app={application}, url={proxy_url}, timeout={proxy_timeout}")
    _ensure_proxy_running()