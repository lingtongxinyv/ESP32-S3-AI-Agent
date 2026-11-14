# ============================================================
# L6 PC 端：接收设备上传的音频 WAV
# 运行：python lessons/recv_audio.py
# 设备 POST 到 http://172.18.36.101:8888/upload
# ============================================================
from http.server import HTTPServer, BaseHTTPRequestHandler
import os, datetime

SAVE_DIR = os.path.join(os.path.dirname(__file__), "recordings")
os.makedirs(SAVE_DIR, exist_ok=True)
PORT = 8888

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        global self
        path = self.path
        if path == "/upload":
            length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(length)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"mic_{ts}.wav"
            fpath = os.path.join(SAVE_DIR, fname)
            with open(fpath, "wb") as f:
                f.write(data)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK\n")
            print(f"✅ 收到 {len(data)} 字节 → {fpath}")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        print("[server]", fmt % args)

if __name__ == "__main__":
    print("=" * 50)
    print("L6 PC 端：音频接收服务器")
    print(f"  监听: 0.0.0.0:{PORT}")
    print(f"  保存: {SAVE_DIR}")
    print(f"  设备 POST 到 http://<PC-IP>:{PORT}/upload")
    print("=" * 50)
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()
