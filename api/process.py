
import os
import json
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler

import requests
import imageio_ffmpeg


class handler(BaseHTTPRequestHandler):

    def respond(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.respond(200, {
            "status": "online",
            "endpoint": "/api/process",
            "method": "POST"
        })

    def do_POST(self):
        token = os.environ.get("BOT_TOKEN")
        secret = os.environ.get("BACKEND_API_KEY")

        if not token or not secret:
            return self.respond(500, {
                "error": "Missing server environment variables"
            })

        if self.headers.get("X-API-Key") != secret:
            return self.respond(401, {"error": "Unauthorized"})

        try:
            length = int(self.headers.get("Content-Length", "0"))

            if length <= 0 or length > 100_000:
                return self.respond(400, {"error": "Invalid request size"})

            data = json.loads(self.rfile.read(length))
            chat_id = data.get("chat_id")
            file_id = data.get("file_id")

            if not isinstance(chat_id, int) or not file_id:
                return self.respond(400, {
                    "error": "chat_id and file_id are required"
                })

            # Get Telegram file path
            info = requests.get(
                f"https://api.telegram.org/bot{token}/getFile",
                params={"file_id": file_id},
                timeout=8
            )
            info.raise_for_status()
            result = info.json()

            if not result.get("ok"):
                return self.respond(400, {
                    "error": "Telegram getFile failed"
                })

            file_path = result["result"]["file_path"]

            if file_path.startswith("/") or ".." in file_path.split("/"):
                return self.respond(400, {"error": "Invalid file path"})

            download_url = (
                f"https://api.telegram.org/file/bot{token}/{file_path}"
            )

            audio = requests.get(download_url, timeout=10)
            audio.raise_for_status()

            if len(audio.content) > 15_000_000:
                return self.respond(413, {"error": "Audio too large"})

            with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
                source = os.path.join(tmp, "input.ogg")
                output = os.path.join(tmp, "output.ogg")

                with open(source, "wb") as f:
                    f.write(audio.content)

                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

                command = [
                    ffmpeg, "-y",
                    "-i", source,
                    "-vn",
                    "-af",
                    "bass=g=7:f=100,volume=6dB,"
                    "aecho=0.8:0.9:100|180:0.25|0.15,"
                    "alimiter=limit=0.95",
                    "-c:a", "libopus",
                    "-b:a", "64k",
                    "-t", "120",
                    output
                ]

                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    timeout=20
                )

                with open(output, "rb") as f:
                    response = requests.post(
                        f"https://api.telegram.org/bot{token}/sendVoice",
                        data={"chat_id": chat_id},
                        files={
                            "voice": ("processed.ogg", f, "audio/ogg")
                        },
                        timeout=15
                    )

                response.raise_for_status()
                telegram_result = response.json()

                if not telegram_result.get("ok"):
                    return self.respond(502, {
                        "error": "Telegram could not send processed audio"
                    })

            return self.respond(200, {
                "ok": True,
                "message": "Processed voice sent"
            })

        except subprocess.TimeoutExpired:
            return self.respond(504, {"error": "FFmpeg timed out"})
        except Exception as e:
            print("Processing error:", repr(e))
            return self.respond(500, {
                "error": "Audio processing failed",
                "detail": str(e)[:300]
            })
