"""
HuggingFace Spaces Gradio 部署入口
- Gradio 状态面板
- Telegram 机器人后台运行
- yt-dlp 自动更新
- 反休眠保护
- 健康检查端点 (port 8080)
"""

import os
import sys
import time
import json
import threading
import subprocess
import asyncio
import logging
import traceback
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

import gradio as gr

from bot import BOT_TOKEN, SUPPORTED_PLATFORMS

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

UPDATE_INTERVAL = int(os.environ.get("UPDATE_INTERVAL", "86400"))
SPACE_URL = os.environ.get("SPACE_URL", "")
_start_time = time.time()

# Bot 运行状态追踪
_bot_status = "idle"       # idle / starting / running / error / stopped
_bot_error_msg = ""
_bot_start_time = 0.0


# ============================================================
# 自动更新 yt-dlp
# ============================================================

def auto_update():
    logger.info(f"自动更新间隔: {UPDATE_INTERVAL}s")
    while True:
        time.sleep(UPDATE_INTERVAL)
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                capture_output=True, text=True, timeout=120
            )
            if r.returncode == 0:
                for line in r.stdout.split("\n"):
                    if "yt-dlp" in line:
                        logger.info(f"xe285 yt-dlp: {line.strip()}")
                        break
        except Exception as e:
            logger.warning(f"更新失败: {e}")


# ============================================================
# 反休眠
# ============================================================

def keep_alive():
    if SPACE_URL:
        logger.info(f"反休眠: {SPACE_URL}")
    while True:
        time.sleep(300)
        try:
            requests.get("http://localhost:7860", timeout=10)
        except Exception:
            pass
        if SPACE_URL:
            try:
                requests.get(SPACE_URL, timeout=30)
            except Exception:
                pass


# ============================================================
# 健康检查端点 (Docker HEALTHCHECK 用)
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            global _bot_status, _bot_error_msg
            self.send_response(200 if _bot_status == "running" else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            body = json.dumps({
                "status": _bot_status,
                "error": _bot_error_msg if _bot_status == "error" else None,
                "platforms": len(SUPPORTED_PLATFORMS),
            })
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # 静默 HTTP 日志


def start_health_server():
    try:
        server = HTTPServer(("0.0.0.0", 8080), HealthHandler)
        logger.info("健康检查端点: http://0.0.0.0:8080/health")
        server.serve_forever()
    except Exception as e:
        logger.warning(f"健康检查启动失败 (端口可能被占用): {e}")


# ============================================================
# Gradio 界面
# ============================================================

def get_status():
    global _bot_status, _bot_error_msg, _bot_start_time
    uptime = int(time.time() - _start_time)
    h, r = divmod(uptime, 3600)
    m, s = divmod(r, 60)

    if BOT_TOKEN:
        if _bot_status == "running":
            bot_uptime = int(time.time() - _bot_start_time)
            bh, br = divmod(bot_uptime, 3600)
            bm, bs = divmod(br, 60)
            status_text = f"xdad 运行中 (bot 已运行 {bh}h {bm}m)"
        elif _bot_status == "error":
            short_err = _bot_error_msg[:60].replace("\n", " ")
            status_text = f"xd34 错误: {short_err}"
        elif _bot_status == "starting":
            status_text = "xd4a 启动中..."
        else:
            status_text = f"xd34 状态异常: {_bot_status}"

        return (
            status_text,
            f"{len(SUPPORTED_PLATFORMS)} 直连 + 1000+ yt-dlp",
            f"{h}h {m}m",
            "10条/分钟",
        )

    return ("xd34 未配置 BOT_TOKEN — 请在 HF Settings → Secrets 设置 BOT_TOKEN", "0", "0s", "N/A")


def create_ui():
    with gr.Blocks(title="万能链接解析 Bot") as demo:
        gr.Markdown("""
        # xdca 万能链接解析 Telegram 机器人

        ### xe285 平台覆盖
        直连 32 平台 + yt-dlp 引擎覆盖 **1000+** 全球网站

        ### xe285 功能
        - 三层回退链（自定义 xf8fx yt-dlp xf8fx 兜底）
        - 重试容错 + 代理支持 + Cookie 注入
        - 多清晰度内联按钮切换
        - 视频智能压缩 (50-200MB)
        - 速率限制 + 并发安全 + 内联模式
        - 24h 自动更新 + 反休眠

        ---
        ### Telegram 中使用
        1. 搜索 @yxyzyhbot
        2. 发送 /start
        3. 发送链接即可解析
        """)

        with gr.Row():
            status = gr.Textbox(label="机器人状态", interactive=False, lines=2)
            platforms = gr.Textbox(label="支持平台", interactive=False, lines=2)
        with gr.Row():
            uptime = gr.Textbox(label="运行时长", interactive=False)
            rate = gr.Textbox(label="速率限制", interactive=False)

        # 页面加载 + 每 30 秒自动刷新状态
        demo.load(get_status, outputs=[status, platforms, uptime, rate], every=30)
    return demo


# ============================================================
# 主函数
# ============================================================

def _run_bot_in_thread():
    """在独立线程中运行机器人，带完整状态追踪和错误诊断"""
    global _bot_status, _bot_error_msg, _bot_start_time

    _bot_status = "starting"
    _bot_start_time = time.time()

    logger.info("机器人线程启动...")

    try:
        from bot import start_bot

        # 创建独立事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        _bot_status = "running"
        logger.info("xdad 机器人事件循环启动，开始轮询 Telegram...")

        loop.run_until_complete(start_bot())

    except Exception as e:
        _bot_status = "error"
        _bot_error_msg = f"{type(e).__name__}: {e}"
        logger.error("xd34 机器人启动失败!")
        logger.error(f"   错误类型: {type(e).__name__}")
        logger.error(f"   错误信息: {e}")
        logger.error(f"   完整堆栈:\n{traceback.format_exc()}")

        # 常见问题诊断
        err_str = str(e).lower()
        if "unauthorized" in err_str or "401" in err_str or "token" in err_str:
            logger.error("xd34 xe285 诊断: BOT_TOKEN 无效！请检查 Settings → Secrets → BOT_TOKEN")
        elif "connection" in err_str or "timeout" in err_str or "network" in err_str:
            logger.error("xd34 xe285 诊断: 网络连接失败，检查 api.telegram.org 是否可达")
        elif "conflict" in err_str or "409" in err_str:
            logger.error("xd34 xe285 诊断: 可能存在另一个实例正在使用同一 token (409 Conflict)")
    finally:
        if _bot_status not in ("error", "stopped"):
            _bot_status = "stopped"
        logger.info(f"机器人线程结束，最终状态: {_bot_status}")


def main():
    if not BOT_TOKEN:
        logger.error("xd34 未设置 BOT_TOKEN！请在 HF Space Settings → Secrets → BOT_TOKEN 添加")
        logger.error("   获取 Token: @BotFather → /newbot → 复制 token")
        logger.error("   HF Secrets: Space Settings → Repository Secrets → New Secret")

    threading.Thread(target=auto_update, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    threading.Thread(target=start_health_server, daemon=True).start()

    if BOT_TOKEN:
        threading.Thread(target=_run_bot_in_thread, daemon=True).start()
        logger.info("xdca 机器人后台线程已创建")

    demo = create_ui()
    demo.queue()
    demo.launch(server_name="0.0.0.0", server_port=7860, show_error=True)


if __name__ == "__main__":
    main()
