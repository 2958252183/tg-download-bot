"""
Telegram Bot 入口
- Health endpoint (port 8080)
- Bot polling
- yt-dlp auto-update
"""

import os, sys, time, json, threading, subprocess, asyncio, logging, traceback
from http.server import HTTPServer, BaseHTTPRequestHandler

from bot import BOT_TOKEN, SUPPORTED_PLATFORMS

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_start_time = time.time()
_bot_status = "idle"
_bot_error_msg = ""

# ============================================================
# yt-dlp auto-update
# ============================================================
def auto_update():
    interval = int(os.environ.get("UPDATE_INTERVAL", "86400"))
    logger.info(f"yt-dlp auto-update every {interval}s")
    while True:
        time.sleep(interval)
        try:
            r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                             capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                for line in r.stdout.split("\n"):
                    if "yt-dlp" in line:
                        logger.info(f"yt-dlp updated: {line.strip()}")
                        break
        except Exception as e:
            logger.warning(f"yt-dlp update failed: {e}")

# ============================================================
# Health server (Docker HEALTHCHECK uses this)
# ============================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            global _bot_status
            code = 200 if _bot_status == "running" else 503
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = json.dumps({"status": _bot_status, "platforms": len(SUPPORTED_PLATFORMS)})
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *args):
        pass

def start_health():
    HTTPServer(("0.0.0.0", 8080), HealthHandler).serve_forever()

# ============================================================
# Bot runner
# ============================================================
def run_bot():
    global _bot_status, _bot_error_msg
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        _bot_status = "error"
        _bot_error_msg = "BOT_TOKEN missing"
        return

    _bot_status = "starting"
    logger.info("Starting bot...")

    try:
        from bot import start_bot
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _bot_status = "running"
        logger.info("Bot polling started")
        loop.run_until_complete(start_bot(webhook_url=None))
    except Exception as e:
        _bot_status = "error"
        _bot_error_msg = str(e)
        logger.error(f"Bot failed: {e}")
        logger.error(traceback.format_exc())

# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    threading.Thread(target=auto_update, daemon=True).start()
    threading.Thread(target=start_health, daemon=True).start()
    threading.Thread(target=run_bot, daemon=True).start()

    logger.info("All services started. Bot polling...")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Shutting down")
