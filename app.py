"""
HuggingFace Spaces 部署入口
- Gradio 状态面板 + FastAPI 健康检查
- Telegram 机器人后台运行（支持 Webhook/Polling 双模式）
- yt-dlp + GitHub 自动更新
- 反休眠 + 多语言响应
"""

import os
import sys
import time
import json
import threading
import subprocess
import asyncio
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
import gradio as gr

from bot import BOT_TOKEN, SUPPORTED_PLATFORMS

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================
# 环境变量
# ============================================================

UPDATE_INTERVAL = int(os.environ.get("UPDATE_INTERVAL", "86400"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
SPACE_URL = os.environ.get("SPACE_URL", os.environ.get("SPACE_ID", ""))
_start_time = time.time()

if SPACE_URL and not SPACE_URL.startswith("http"):
    SPACE_URL = f"https://{SPACE_URL}.hf.space"


# ============================================================
# 健康检查 HTTP 服务器
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默

    def do_GET(self):
        if self.path in ("/health", "/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            uptime = int(time.time() - _start_time)
            h, r = divmod(uptime, 3600)
            m, s = divmod(r, 60)
            self.wfile.write(json.dumps({
                "status": "healthy" if BOT_TOKEN else "no_token",
                "uptime": f"{h}h {m}m {s}s",
                "uptime_seconds": uptime,
                "platforms": len(SUPPORTED_PLATFORMS),
                "rate_limit": "10/min",
                "engine": "yt-dlp",
                "webhook": bool(WEBHOOK_URL),
            }, ensure_ascii=False).encode())
        else:
            self.send_response(404)
            self.end_headers()


def run_health_server():
    try:
        server = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
        logger.info(f"xdcd 健康检查端口 :{HEALTH_PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"健康检查服务器失败: {e}")


# ============================================================
# 自动更新
# ============================================================

def auto_update_loop():
    logger.info(f"自动更新间隔: {UPDATE_INTERVAL}s ({UPDATE_INTERVAL/3600:.1f}h)")
    while True:
        time.sleep(UPDATE_INTERVAL)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "yt-dlp" in line:
                        logger.info(f"xe285 yt-dlp 更新: {line.strip()}")
                        break
            else:
                logger.warning(f"yt-dlp 更新失败: {result.stderr[:150]}")

            if os.path.isdir(".git"):
                r = subprocess.run(["git", "pull", "--ff-only"],
                                   capture_output=True, text=True, timeout=30)
                if "Already up to date" not in r.stdout:
                    logger.info(f"Git: {r.stdout.strip()[:100]}")
        except Exception as e:
            logger.warning(f"更新异常: {e}")


# ============================================================
# 反休眠
# ============================================================

def keep_alive():
    logger.info(f"反休眠启动 | Space: {SPACE_URL or '仅本地'}")
    while True:
        time.sleep(300)
        try:
            requests.get("http://localhost:7860", timeout=10)
            requests.get(f"http://localhost:{HEALTH_PORT}/health", timeout=10)
        except Exception:
            pass
        if SPACE_URL:
            try:
                requests.get(SPACE_URL, timeout=30)
            except Exception:
                pass


# ============================================================
# Gradio 状态面板
# ============================================================

def get_status():
    uptime = int(time.time() - _start_time)
    h, r = divmod(uptime, 3600)
    m, s = divmod(r, 60)
    if BOT_TOKEN:
        return (
            f"xdad 运行中 (Webhook)" if WEBHOOK_URL else f"xdad 运行中 (Polling)",
            f"{len(SUPPORTED_PLATFORMS)} 直连 + 1000+ yt-dlp",
            f"{h}h {m}m",
            "10条/分钟",
            "xe285 已启用" if WEBHOOK_URL else "未配置",
        )
    return ("xd34 未配置 BOT_TOKEN", "0", "0s", "N/A", "")


def create_ui():
    with gr.Blocks(title="万能链接解析 Bot", theme=gr.themes.Soft()) as demo:
        gr.Markdown("""
        # xdca 万能链接解析 Telegram 机器人

        ### xe285 平台覆盖
        直连 32 平台 + yt-dlp 引擎覆盖 **1000+** 全球网站

        ### xe285 已启用优化
        - 三层回退链（自定义 -> yt-dlp -> 兜底）
        - 重试容错 + 代理支持 + Cookie 注入
        - 多清晰度内联按钮切换
        - 视频智能压缩 (50-200MB)
        - 速率限制 + 并发安全 + 内联模式
        - 24h 自动更新 + 健康检查
        """)

        with gr.Row():
            status = gr.Textbox(label="机器人状态", interactive=False)
            platforms = gr.Textbox(label="支持平台数", interactive=False)
        with gr.Row():
            uptime = gr.Textbox(label="运行时长", interactive=False)
            rate = gr.Textbox(label="速率限制", interactive=False)
            webhook = gr.Textbox(label="Webhook", interactive=False)

        demo.load(get_status, outputs=[status, platforms, uptime, rate, webhook], every=60)

    return demo


# ============================================================
# 主函数
# ============================================================

def run_bot_thread():
    """后台线程：运行 Telegram 机器人"""
    from bot import start_bot
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(start_bot())
    except Exception as e:
        logger.error(f"机器人异常: {e}")
        import traceback; traceback.print_exc()
    finally:
        loop.close()


def main():
    if not BOT_TOKEN:
        logger.error("=" * 50)
        logger.error("xd34 未设置 BOT_TOKEN！请在 Secrets 中添加")
        logger.error("=" * 50)

    # 启动健康检查
    threading.Thread(target=run_health_server, daemon=True, name="HealthCheck").start()

    # 启动自动更新
    threading.Thread(target=auto_update_loop, daemon=True, name="Updater").start()

    # 启动反休眠
    threading.Thread(target=keep_alive, daemon=True, name="KeepAlive").start()

    # 启动机器人
    if BOT_TOKEN:
        threading.Thread(target=run_bot_thread, daemon=True, name="TelegramBot").start()
        mode = "Webhook" if WEBHOOK_URL else "Polling"
        logger.info(f"xdca 机器人启动 ({mode}) | 健康检查 :{HEALTH_PORT}")

    # 启动 Gradio
    demo = create_ui()
    demo.queue()
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)


if __name__ == "__main__":
    main()
