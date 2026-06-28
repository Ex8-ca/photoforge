#!/usr/bin/env python3
"""photoforge frontend + ComfyUI reverse proxy + Cloudinary history on a single port."""
import http.server
import urllib.request
import urllib.error
import os
import json
import time
import io
import cloudinary
import cloudinary.uploader
import cloudinary.api

COMFYUI = "http://127.0.0.1:8188"
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".")

# MiniMax config
def _load_minimax_key():
    """Load MiniMax API key from minimax-speech .env file."""
    env_path = os.path.expanduser("~/.hermes/skills/minimax-speech/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("MINIMAX_API_KEY=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("MINIMAX_API_KEY", "")

MINIMAX_API_KEY = _load_minimax_key()
MINIMAX_API_URL = "https://api.minimax.io/v1/image_generation"

# Cloudinary config — loads from .env in project root
def _load_cloudinary():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                key, val = line.split("=", 1)
                # Keys already have CLOUDINARY_ prefix in the .env file
                os.environ[key.strip().upper()] = val.strip()
    cloudinary.config(
        cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
        api_key=os.environ.get("CLOUDINARY_API_KEY", ""),
        api_secret=os.environ.get("CLOUDINARY_API_SECRET", ""),
        secure=True,
    )

_load_cloudinary()

class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self.proxy("GET")
        elif self.path == "/cloudinary/history":
            self.serve_cloudinary_history()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self.proxy("POST")
        elif self.path == "/cloudinary/upload":
            self.handle_cloudinary_upload()
        elif self.path == "/minimax/generate":
            self.handle_minimax_generate()
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/cloudinary/delete/"):
            public_id = self.path[len("/cloudinary/delete/"):]
            self.handle_cloudinary_delete(public_id)
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def proxy(self, method):
        comfyui_path = self.path[4:]  # strip "/api"
        url = COMFYUI + comfyui_path

        try:
            if method == "POST":
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len) if content_len > 0 else None
                req = urllib.request.Request(url, data=body, method="POST")
                req.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))
            else:
                req = urllib.request.Request(url)

            with urllib.request.urlopen(req, timeout=300) as resp:
                data = resp.read()
                self.send_response(resp.status)
                ct = resp.headers.get("Content-Type", "application/octet-stream")
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", len(data))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            body = e.read() if e.fp else b''
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Proxy error: {e}".encode())

    # ================================================================
    # Cloudinary: upload image from ComfyUI
    # ================================================================
    def handle_cloudinary_upload(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len))
            comfyui_url = body.get("url", "")
            base64_data = body.get("base64", "")

            if not comfyui_url and not base64_data:
                self.send_json_error(400, "Missing url or base64")
                return

            if base64_data:
                # Direct base64 upload (for MiniMax images)
                import base64
                img_data = base64.b64decode(base64_data)
            else:
                # Fetch from ComfyUI
                if comfyui_url.startswith("/api/"):
                    fetch_url = COMFYUI + comfyui_url[4:]
                elif comfyui_url.startswith("http"):
                    fetch_url = comfyui_url
                else:
                    fetch_url = COMFYUI + comfyui_url

                req = urllib.request.Request(fetch_url)
                with urllib.request.urlopen(req, timeout=60) as img_resp:
                    img_data = img_resp.read()

            # Upload to Cloudinary via SDK
            import cloudinary.uploader as uploader
            result = uploader.upload(
                io.BytesIO(img_data),
                folder="photoforge",
                resource_type="image",
            )

            self.send_json_ok({
                "success": True,
                "url": result.get("secure_url", ""),
                "public_id": result.get("public_id", ""),
                "width": result.get("width", 0),
                "height": result.get("height", 0),
                "format": result.get("format", ""),
                "created_at": result.get("created_at", ""),
            })

        except Exception as e:
            self.log_message("Cloudinary upload error: %s", str(e))
            self.send_json_error(500, str(e))

    # ================================================================
    # Cloudinary: list recent images
    # ================================================================
    def serve_cloudinary_history(self):
        try:
            import cloudinary.api as api
            result = api.resources(
                type="upload",
                prefix="photoforge/",
                max_results=30,
                sort_by=[("created_at", "desc")],
            )

            images = []
            for res in result.get("resources", []):
                images.append({
                    "url": res.get("secure_url", ""),
                    "public_id": res.get("public_id", ""),
                    "width": res.get("width", 0),
                    "height": res.get("height", 0),
                    "created_at": res.get("created_at", ""),
                    "format": res.get("format", ""),
                })

            self.send_json_ok({"images": images})

        except Exception as e:
            self.log_message("Cloudinary history error: %s", str(e))
            self.send_json_ok({"images": [], "error": str(e)})

    # ================================================================
    # MiniMax: generate image from text prompt
    # ================================================================
    def handle_minimax_generate(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len))
            prompt = body.get("prompt", "")
            aspect_ratio = body.get("aspect_ratio", "1:1")

            if not prompt:
                self.send_json_error(400, "Missing prompt")
                return

            if not MINIMAX_API_KEY:
                self.send_json_error(500, "MiniMax API key not configured")
                return

            payload = {
                "model": "image-01",
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "response_format": "base64",
            }

            req_data = json.dumps(payload).encode()
            req = urllib.request.Request(
                MINIMAX_API_URL,
                data=req_data,
                method="POST",
            )
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {MINIMAX_API_KEY}")

            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())

            data = result.get("data") or {}
            images = data.get("image_base64", [])
            if not images:
                self.send_json_error(500, f"No images returned from MiniMax: {result.get('base_resp', 'unknown response')}")
                return

            # Decode first image and return as data URL
            import base64
            img_bytes = base64.b64decode(images[0])

            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(img_bytes))
            self.end_headers()
            self.wfile.write(img_bytes)

            self.log_message("MiniMax image generated (%d bytes)", len(img_bytes))

        except urllib.error.HTTPError as e:
            err_body = e.read() if e.fp else b""
            self.log_message("MiniMax HTTP %d: %s", e.code, err_body.decode()[:200])
            self.send_json_error(e.code, f"MiniMax API error: {err_body.decode()[:200]}")
        except Exception as e:
            self.log_message("MiniMax generation error: %s", str(e))
            self.send_json_error(500, str(e))

    # ================================================================
    # Cloudinary: delete an image
    # ================================================================
    def handle_cloudinary_delete(self, public_id):
        try:
            import cloudinary.uploader as uploader
            decoded_id = urllib.parse.unquote(public_id)
            result = uploader.destroy(decoded_id)
            self.send_json_ok({"success": result.get("result") == "ok"})
        except Exception as e:
            self.send_json_error(500, str(e))

    # ================================================================
    # Helpers
    # ================================================================
    def send_json_ok(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_json_error(self, code, msg):
        body = json.dumps({"error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        if not self.path.startswith("/api/") and not self.path.startswith("/cloudinary/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass

if __name__ == "__main__":
    import sys
    import urllib.parse
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
    server = http.server.HTTPServer(("0.0.0.0", port), ProxyHandler)
    print(f"photoforge running at http://192.168.1.3:{port}")
    print(f"Proxying /api/* -> {COMFYUI}")
    print(f"MiniMax: {'configured' if MINIMAX_API_KEY else 'NOT configured'}")
    print(f"Cloudinary uploads -> dol2t3l5x")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
        server.shutdown()
