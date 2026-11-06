import os
import json
import http.server
import socketserver
import traceback
from datetime import datetime

from config import PREDICTIONS_FILE, MOSCOW_TZ
from stats import get_full_stats

def load_predictions_from_file():
    print(f"📂 [WEB] Попытка прочитать файл: {PREDICTIONS_FILE}", flush=True)
    try:
        if not os.path.exists(PREDICTIONS_FILE):
            print(f"⚠️ [WEB] Файл не найден по пути {PREDICTIONS_FILE}!", flush=True)
            return []
        with open(PREDICTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            print(f"✅ [WEB] Успешно прочитано {len(data)} прогнозов!", flush=True)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"🔥 [WEB ОШИБКА ЧТЕНИЯ] {e}", flush=True)
        return []

class StatsHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            for path in ("index.html", "stats.html", "templates/index.html"):
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK - HTML not found")
            return

        if self.path.startswith("/api/stats"):
            print("🌐 [WEB] Получен запрос к /api/stats", flush=True)
            predictions = load_predictions_from_file()
            try:
                data = get_full_stats(predictions)
                print(f"✅ [WEB] Статистика собрана. Последних: {len(data.get('last', []))}", flush=True)
            except Exception as e:
                print(f"🔥 [WEB ОШИБКА API /api/stats] {e}", flush=True)
                print(traceback.format_exc(), flush=True)
                data = {"error": str(e), "summary": {}, "dogons": [], "suits": [], "daily": [], "last": []}
            
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        return super().do_GET()

    def log_message(self, *args):
        pass

class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

def start_web_server():
    port = int(os.getenv("PORT", 8000))
    try:
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass
    with ReusableTCPServer(("0.0.0.0", port), StatsHandler) as httpd:
        print(f"🌐 Веб-сервер запущен на порту {port}", flush=True)
        httpd.serve_forever()