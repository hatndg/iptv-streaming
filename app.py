from flask import Flask, Response, request
import subprocess
import threading
import os

app = Flask(__name__)

# Thay bằng logo của bạn
LOGO_PATH = "logo.png"

# Các kênh mẫu
CHANNELS = {
    "btv": "https://livehoster-dungsclive-sg01.onrender.com/hls/btv/index.m3u8",
    "btv2": "https://livehoster-dungsclive-sg01.onrender.com/hls/btv/index.m3u8"
}

def generate_stream(channel_url):
    """Khởi chạy FFmpeg để xử lý M3U8 + overlay logo."""
    ffmpeg_cmd = [
        "ffmpeg",
        "-i", channel_url,
        "-i", LOGO_PATH,
        "-filter_complex", "overlay=10:10",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-c:a", "aac",
        "-f", "flv",
        "pipe:1"
    ]
    return subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

@app.route("/stream/<channel>")
def stream_channel(channel):
    """Endpoint truyền phát kênh với overlay logo."""
    if channel not in CHANNELS:
        return "Kênh không tồn tại", 404

    proc = generate_stream(CHANNELS[channel])

    def generate():
        try:
            while True:
                data = proc.stdout.read(1024)
                if not data:
                    break
                yield data
        finally:
            proc.kill()

    return Response(generate(), content_type="video/x-flv")

@app.route("/")
def index():
    return """
    <h2>dungtv</h2>
    """

@app.route("/healthz")
def health_check():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
