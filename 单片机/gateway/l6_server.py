#!/usr/bin/env python3
"""
L6 服务器：接收 ESP32-S3 POST 上来的裸 PCM（s16le 单声道），封 WAV 头存盘
零依赖，Python 3 自带模块
启动: python3 l6_server.py [port]   (默认 8765)
停止: Ctrl+C
"""
import sys, os, json, struct, asyncio, base64, subprocess, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime

from providers import LocalASR, LocalTTS, build_llm

ROOT = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(ROOT, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

HOST = "0.0.0.0"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765

ASR = LocalASR({"model": os.path.expanduser("~/.cache/esp32-agent/whisper-small")})
TTS = LocalTTS({"model_path": os.path.join(ROOT, "voices/zh_CN-huayan-medium.onnx"),
                "sample_rate": 8000})
LLM = build_llm({"provider": "xiaoling", "to": "小綾"})
TTS_RATE = 8000


def shorten(text, limit=24):
    """控制下行体积：优先在第一句结束处截断，保证至少说完整一句。"""
    text = text.strip()
    if len(text) <= limit:
        return text
    for p in "。！？!?":
        i = text.find(p)
        if 0 < i < limit:
            return text[:i + 1]
    for p in "，,、":
        i = text.rfind(p, 0, limit)
        if i >= 10:
            return text[:i + 1]
    return text[:limit]


def _resample(pcm, src, dst=16000):
    """设备上行只有 8k，ASR 要 16k：服务端重采样。"""
    if src == dst:
        return pcm
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le",
         "-ar", str(src), "-ac", "1", "-i", "pipe:0",
         "-f", "s16le", "-ar", str(dst), "-ac", "1", "pipe:1"],
        input=pcm, capture_output=True)
    return p.stdout


def ask(pcm, rate):
    """录音 → 文字 → 小綾的回复"""
    async def run():
        text = await ASR.transcribe(_resample(pcm, rate), 16000)
        if not text:
            return text, ""
        parts = []
        async for piece in LLM.stream(text):
            parts.append(piece)
        return text, "".join(parts)
    return asyncio.run(run())

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"        # keep-alive，省掉每次按键的 TCP 握手

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")

    def _send_json(self, obj, code=200):
        resp = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def _send_audio(self, pcm, text, reply):
        self.send_response(200)
        self.send_header("Content-Type", "audio/pcm")
        self.send_header("Content-Length", str(len(pcm)))
        self.send_header("X-Rate", str(TTS_RATE))
        self.send_header("X-Text-B64", base64.b64encode(text.encode()).decode())
        self.send_header("X-Reply-B64", base64.b64encode(reply.encode()).decode())
        self.end_headers()
        self.wfile.write(pcm)

    def do_POST(self):
        if self.path not in ("/upload", "/voice"):
            self.send_error(404, "not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            self._send_json({"ok": False, "error": "Content-Length required"}, 411)
            return

        ts = int(datetime.now().timestamp() * 1000)
        fname = f"mic_{ts}.wav"
        fpath = os.path.join(UPLOAD_DIR, fname)
        rate = int(self.headers.get("X-Rate", 16000))
        ch = int(self.headers.get("X-Channels", 1))
        try:
            with open(fpath, "wb") as f:
                f.write(b"\x00" * 44)          # 先占位，收完回填头
                remain = length
                while remain > 0:               # 分块落盘，不整包进内存
                    chunk = self.rfile.read(min(8192, remain))
                    if not chunk:
                        raise IOError("body truncated")
                    f.write(chunk)
                    remain -= len(chunk)
                align = ch * 2
                f.seek(0)
                f.write(b"RIFF" + struct.pack("<I", 36 + length) + b"WAVE")
                f.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, ch, rate,
                                              rate * align, align, 16))
                f.write(b"data" + struct.pack("<I", length))

            print(f"  ✅ saved {fname} ({length/1024:.1f} KB, {rate} Hz)")
            text, reply = "", ""
            t0 = time.perf_counter()
            try:
                text, reply = ask(open(fpath, "rb").read()[44:], rate)
                print(f"  🗣  {text}  ({time.perf_counter()-t0:.2f}s)")
                print(f"  💬 {reply}")
            except Exception as e:
                print(f"  ⚠️ ASR/LLM: {e}")

            if self.path == "/voice":
                pcm = b""
                t1 = time.perf_counter()
                try:
                    pcm = asyncio.run(TTS.synth(shorten(reply) or "我在。"))
                    print(f"  🔊 {len(pcm)/1024:.0f} KB @ {TTS_RATE} Hz "
                          f"({time.perf_counter()-t1:.2f}s) 总 {time.perf_counter()-t0:.2f}s")
                except Exception as e:
                    print(f"  ⚠️ TTS: {e}")
                self._send_audio(pcm, text, reply)
            else:
                self._send_json({"ok": True, "file": fname, "size": length + 44,
                                 "text": text, "reply": reply})
        except Exception as e:
            print(f"  ❌ {e}")
            try: os.remove(fpath)
            except OSError: pass
            self.close_connection = True        # 半截 body，连接已不可复用
            self._send_json({"ok": False, "error": str(e)})

    def do_GET(self):
        """GET / → 列出所有上传的 WAV 文件"""
        if self.path == "/":
            files = sorted(os.listdir(UPLOAD_DIR), reverse=True)
            if self.headers.get("Accept", "").startswith("text/html"):
                body = "<html><body><h1>L6 Uploads</h1><ul>"
                for f in files:
                    size = os.path.getsize(os.path.join(UPLOAD_DIR, f)) / 1024
                    body += f'<li><a href="/uploads/{f}">{f}</a> ({size:.1f} KB)</li>'
                body += "</ul></body></html>"
            else:
                body = json.dumps({"files": files})

            body = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # GET /uploads/filename.wav → 直接下载 WAV
        if self.path.startswith("/uploads/"):
            fname = self.path[len("/uploads/"):]
            fpath = os.path.join(UPLOAD_DIR, fname)
            if os.path.exists(fpath):
                with open(fpath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404, "file not found")
            return

        self.send_error(404)

if __name__ == "__main__":
    print(f"L6 server on {HOST}:{PORT}")
    print(f"uploads -> {UPLOAD_DIR}")
    print(f"list at  http://agent.lingtong.asia:{PORT}/")
    print("Ctrl+C to stop\n")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
