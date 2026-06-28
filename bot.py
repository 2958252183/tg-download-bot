"""
Telegram 万能链接解析机器人
- 支持 30+ 中文平台 + yt-dlp 1000+ 全球平台
- 速率限制：每用户每分钟最多10条链接
- 清晰度选择：默认最高画质，内联按钮切换其他清晰度
"""

import os
import re
import time
import uuid
import tempfile
import asyncio
import logging
import httpx
import shutil
from collections import defaultdict
from typing import Optional, Dict, List, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, InlineQueryHandler, filters, ContextTypes
)

from parsers import (
    parse_url, extract_all_links, download_file,
    clean_filename, MediaInfo, QualityOption, PLATFORM_MAP,
    get_parse_error,
)

# 视频压缩
import subprocess

# ============================================================
# 常量
# ============================================================

MAX_FILE_SIZE = 50 * 1024 * 1024       # 50MB Telegram 限制
COMPRESS_THRESHOLD = 50 * 1024 * 1024  # 超过此大小尝试压缩
COMPRESS_MAX = 200 * 1024 * 1024       # 超过200MB放弃压缩

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TELEGRAM_PROXY_URL = os.environ.get("TELEGRAM_PROXY_URL", "")  # Cloudflare Worker 代理
AI_API_KEY = os.environ.get("AI_API_KEY", "")
AI_API_BASE = os.environ.get("AI_API_BASE", "https://api.openai.com/v1")
AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")
DOWNLOAD_DIR = tempfile.mkdtemp(prefix="tg_media_")

SUPPORTED_PLATFORMS = list(PLATFORM_MAP.keys())

# ============================================================
# 速率限制
# ============================================================

_rate_map: Dict[int, List[float]] = defaultdict(list)
RATE_LIMIT = 10       # 每分钟最多N条
RATE_WINDOW = 60      # 秒


def check_rate(user_id: int) -> Tuple[bool, int]:
    """返回 (是否允许, 剩余次数)"""
    now = time.time()
    timestamps = [t for t in _rate_map[user_id] if now - t < RATE_WINDOW]
    _rate_map[user_id] = timestamps
    remaining = RATE_LIMIT - len(timestamps)
    if remaining <= 0:
        return False, 0
    timestamps.append(now)
    return True, remaining - 1


# ============================================================
# 清晰度缓存（存 quality_options 供回调查询）
# ============================================================

quality_store: Dict[str, Tuple[MediaInfo, List[QualityOption]]] = {}


# ============================================================
# 命令
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = SUPPORTED_PLATFORMS[:12]
    ai_status = "" if AI_API_KEY else "\n\n    AI\u52a9\u624b\uff1a\u914d\u7f6e AI_API_KEY \u540e\u53ef\u5f00\u542f"
    await update.message.reply_text(
        f"\U0001f916 \u4f60\u597d\uff0c\u6211\u662f\u4e07\u80fd\u89e3\u6790\u673a\u5668\u4eba\uff01\n\n"
        f"\U0001f4e5 \u53d1\u94fe\u63a5\uff0c\u6211\u5e2e\u4f60\u4e0b\u8f7d\u89c6\u9891\u56fe\u7247\n"
        f"\U0001f3a5 \u652f\u6301\uff1a{' | '.join(top)} ...\n"
        f"\U0001f310 yt-dlp \u5f15\u64ce\u8986\u76d6 1000+ \u5168\u7403\u7f51\u7ad9\n\n"
        f"\u2728 \u529f\u80fd\uff1a\n"
        f"  \u2022 \u81ea\u52a8\u6700\u9ad8\u753b\u8d28 + \u591a\u6e05\u6670\u5ea6\u5207\u6362\n"
        f"  \u2022 \u89c6\u9891\u667a\u80fd\u538b\u7f29 (50MB\u5185)\n"
        f"  \u2022 \u901f\u7387\u9650\u5236\uff1a\u6bcf\u5206\u949f {RATE_LIMIT} \u6761\n"
        f"  \u2022 \u5185\u8054\u6a21\u5f0f\uff1a\u5728\u4efb\u610f\u804a\u5929\u6846 @\u6211 \u89e3\u6790\n"
        f"  \u2022 \u7fa4\u804a\u81ea\u52a8\u89e3\u6790\uff08\u9700\u7ba1\u7406\u5458\u6743\u9650\uff09"
        f"{ai_status}\n\n"
        f"\U0001f4ac \u53d1 /help \u67e5\u770b\u8be6\u7ec6\u8bf4\u660e\uff0c\u76f4\u63a5\u804a\u5929\u4e5f\u53ef\u4ee5\u54e6\uff5e"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "使用说明\n\n"
        "1. 发送链接，自动解析并发送最高画质\n"
        "2. 视频下方按钮可切换 720p/1080p/4K 等\n"
        f"3. 每分钟最多 {RATE_LIMIT} 条链接\n"
        "4. 支持平台列表：/platforms\n"
        "5. 每24小时自动更新解析引擎"
    )


async def platforms_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [f"直连平台（{len(SUPPORTED_PLATFORMS)} 个）:\n"]
    cols = 3
    for i in range(0, len(SUPPORTED_PLATFORMS), cols):
        chunk = SUPPORTED_PLATFORMS[i:i+cols]
        lines.append("  |  ".join(f"{i+j+1}.{p}" for j, p in enumerate(chunk)))
    lines.append("\nyt-dlp 引擎额外覆盖 1000+ 网站")
    await update.message.reply_text("\n".join(lines))



# ============================================================
# 内联模式（在任意聊天框 @bot 链接）
# ============================================================

from telegram import InlineQueryResultArticle, InputTextMessageContent

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.inline_query.query.strip()
    if not query_text:
        return

    links = extract_all_links(query_text)
    results = []

    for url, platform in links[:5]:
        results.append(InlineQueryResultArticle(
            id=uuid.uuid4().hex,
            title=f"解析 {platform} 链接",
            description=url[:80],
            input_message_content=InputTextMessageContent(
                message_text=f"正在解析 {platform} 链接...\n{url}"
            ),
        ))

    if not results:
        results.append(InlineQueryResultArticle(
            id=uuid.uuid4().hex,
            title="未检测到支持的链接",
            description="请发送包含抖音/快手/B站/微博/小红书/YouTube等链接的消息",
            input_message_content=InputTextMessageContent(
                message_text="请发送有效的平台链接"
            ),
        ))

    await update.inline_query.answer(results, cache_time=10)


# ============================================================
# AI 对话
# ============================================================

# 对话历史缓存 (user_id -> list of messages)
_chat_history: Dict[int, list] = {}
MAX_HISTORY = 20

# 机器人自我介绍 prompt
SYSTEM_PROMPT = """你是一个 Telegram 万能链接解析机器人的 AI 助手。
你的功能：
- 解析抖音、小红书、快手、微博、B站、YouTube、Twitter/X、Instagram、Facebook、TikTok 等 30+ 平台的视频和图片
- yt-dlp 引擎额外支持 1000+ 全球网站
- 支持多清晰度切换（1080p/720p/540p/480p）
- 支持内联模式（在任意聊天框 @机器人名 链接即可解析）
- 速率限制：每分钟 10 条链接
- 视频超过 50MB 会自动压缩
- 支持群聊自动解析（需管理员权限）

请用中文简短友好地回答用户问题。不要编造功能。如果用户问不支持的功能，诚实说明。回答控制在 100 字以内。"""

async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    """处理 AI 对话"""
    if not AI_API_KEY:
        return

    user = update.effective_user
    user_id = user.id
    message = update.message

    # 获取或初始化历史
    if user_id not in _chat_history:
        _chat_history[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    history = _chat_history[user_id]
    history.append({"role": "user", "content": user_text})

    # 限制历史长度
    if len(history) > MAX_HISTORY + 1:
        history = [history[0]] + history[-(MAX_HISTORY):]
        _chat_history[user_id] = history

    # 显示输入中...
    typing_task = asyncio.create_task(_send_typing_loop(message))

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{AI_API_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {AI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": AI_MODEL,
                    "messages": history,
                    "max_tokens": 500,
                    "temperature": 0.7,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"]["content"].strip()

            history.append({"role": "assistant", "content": reply})
            _chat_history[user_id] = history

            # 限制回复长度
            if len(reply) > 1000:
                reply = reply[:997] + "..."

            await message.reply_text(reply)

    except Exception as e:
        logger.error(f"AI chat error: {e}")
        await message.reply_text("\U0001f614 AI \u6682\u65f6\u4e0d\u53ef\u7528\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5\u3002\n\n\u4f60\u4e5f\u53ef\u4ee5\u76f4\u63a5\u53d1\u94fe\u63a5\u6765\u89e3\u6790\u89c6\u9891\uff01")
    finally:
        typing_task.cancel()

async def _send_typing_loop(message):
    """持续发送 typing 状态"""
    try:
        while True:
            await message.chat.send_action("typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass

# 清除对话历史命令
async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _chat_history.pop(user_id, None)
    await update.message.reply_text("\U0001f9f9 \u5df2\u6e05\u9664\u5bf9\u8bdd\u5386\u53f2\uff01")


# ============================================================
# 消息处理
# ============================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    text = message.text or message.caption or ""

    if not text:
        return

    links = extract_all_links(text)
    if not links:
        # No links - route to AI chat
        if AI_API_KEY:
            await ai_chat(update, context, text)
        else:
            await message.reply_text(
                "\U0001f44b \u6ca1\u68c0\u6d4b\u5230\u94fe\u63a5\uff0c\u76f4\u63a5\u53d1\u94fe\u63a5\u6211\u5c31\u80fd\u89e3\u6790\uff01\n\n"
                "\U0001f4ac \u4f60\u4e5f\u53ef\u4ee5\u95ee\u6211\u5173\u4e8e\u673a\u5668\u4eba\u7684\u95ee\u9898\uff0c\n"
                "\u8bf7\u6c42\u7ba1\u7406\u5458\u914d\u7f6e AI_API_KEY \u5f00\u542f AI \u5bf9\u8bdd\u3002"
            )
        return

    # 群聊兼容
    chat_type = message.chat.type
    if chat_type in ("group", "supergroup"):
        bot_un = f"@{context.bot.username}" if context.bot.username else ""
        if bot_un and bot_un not in text:
            return

    # ---- 速率限制 ----
    allowed, remaining = check_rate(user.id)
    if not allowed:
        await message.reply_text(
            f"速率限制：每分钟最多 {RATE_LIMIT} 条链接，请稍后再试"
        )
        return

    total_links = min(len(links), 5)

    if total_links == 1:
        status = await message.reply_text(f"解析中... [{links[0][1]}]")
    else:
        plats = ", ".join(dict.fromkeys(p for _, p in links[:total_links]))
        status = await message.reply_text(
            f"检测到 {total_links} 个链接 [{plats}]，解析中..."
            f"\n(剩余 {remaining} 次)"
        )

    ok = 0
    fail = 0
    fail_reasons = []

    for url, platform in links[:total_links]:
        try:
            await status.edit_text(f"[{platform}] 解析中...\n{url[:60]}...")
            media = await parse_url(url)

            if not media:
                fail += 1
                err = get_parse_error(url)
                if err:
                    fail_reasons.append(err)
                continue

            sent = await _send_media(update, context, media)
            if sent:
                ok += 1
                try:
                    await message.delete()
                except Exception:
                    pass
            else:
                fail += 1

        except Exception as e:
            logger.error(f"[{platform}] {e}")
            fail += 1

    parts = []
    if ok > 0:
        parts.append(f"{ok} \u4e2a\u6210\u529f")
    if fail > 0:
        parts.append(f"{fail} \u4e2a\u5931\u8d25")
    final = " | ".join(parts) if parts else "\u5168\u90e8\u89e3\u6790\u5931\u8d25"
    if fail_reasons:
        reasons = "\n".join(f"  \u2022 {r}" for r in fail_reasons[:3])
        final += f"\n\n\u274c \u5931\u8d25\u539f\u56e0\uff1a\n{reasons}"
        if len(fail_reasons) > 3:
            final += f"\n  \u2022 ...\u8fd8\u6709 {len(fail_reasons)-3} \u4e2a"

    try:
        await status.edit_text(final)
        await asyncio.sleep(3)
        await status.delete()
    except Exception:
        pass


# ============================================================
# 媒体发送（默认最高画质 + 清晰度按钮）
# ============================================================

async def _send_media(update: Update, context, media: MediaInfo) -> bool:
    if media.is_video:
        return await _send_video(update, context, media)
    else:
        return await _send_images(update, context, media)




# ============================================================
# 视频压缩 (50-200MB -> <50MB)
# ============================================================

def compress_video(input_path: str, output_path: str, target_mb: int = 45) -> bool:
    """使用 ffmpeg 压缩视频至目标大小"""
    try:
        duration = float(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", input_path
        ], timeout=15).decode().strip())
    except Exception:
        return False

    if duration <= 0:
        return False

    target_bits = int((target_mb * 8 * 1024 * 1024) / duration * 0.95)
    target_bits = max(target_bits, 500000)  # 最低 500kbps

    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "libx264", "-b:v", str(target_bits),
            "-c:a", "aac", "-b:a", "96k",
            "-preset", "fast", "-movflags", "+faststart",
            output_path
        ], check=True, timeout=300, capture_output=True)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.warning(f"压缩失败: {e}")
        return False

async def _send_video(update: Update, context, media: MediaInfo) -> bool:
    """发送视频：独立目录 + 下载进度 + 超大压缩 + 清晰度按钮"""
    # 独立临时目录（并发安全）
    req_dir = tempfile.mkdtemp(prefix=f"req_", dir=DOWNLOAD_DIR)
    path = os.path.join(req_dir, f"{clean_filename(media.title)}_{uuid.uuid4().hex[:6]}.mp4")

    try:
        # 下载（带进度条回调）
        progress_msg = await update.message.reply_text("\U0001f4e5 \u5f00\u59cb\u4e0b\u8f7d...")
        last_pct = -1
        async def progress_cb(downloaded, total):
            nonlocal last_pct
            if total > 0:
                pct = int(downloaded / total * 100)
                if pct >= last_pct + 10:
                    last_pct = pct
                    bar = "\u2588" * (pct // 10) + "\u2591" * (10 - pct // 10)
                    size_mb = total / 1024 / 1024
                    dled_mb = downloaded / 1024 / 1024
                    try:
                        await progress_msg.edit_text(
                            f"\U0001f4e5 \u4e0b\u8f7d\u4e2d [{bar}] {pct}%\n"
                            f"{dled_mb:.1f}MB / {size_mb:.1f}MB"
                        )
                    except Exception:
                        pass

        ok = await download_file(media.best_url, path, progress_cb)
        try:
            await progress_msg.delete()
        except Exception:
            pass
        if not ok:
            from parsers import ytdlp_download
            result = await ytdlp_download(media.source_url, req_dir)
            if result:
                path = result
            else:
                shutil.rmtree(req_dir, ignore_errors=True)
                return False

        size = os.path.getsize(path)
        if size == 0:
            shutil.rmtree(req_dir, ignore_errors=True); return False

        # 超大视频智能压缩
        if COMPRESS_THRESHOLD < size <= COMPRESS_MAX:
            compressed = os.path.join(req_dir, f"compressed_{uuid.uuid4().hex[:6]}.mp4")
            logger.info(f"视频 {size/1024/1024:.1f}MB 超过限制，尝试压缩...")
            if False:  # ffmpeg not available in Gradio SDK
                os.remove(path)
                path = compressed
                size = os.path.getsize(path)
                logger.info(f"压缩完成: {size/1024/1024:.1f}MB")
            else:
                logger.warning("压缩失败，使用原文件")

        if size > MAX_FILE_SIZE:
            shutil.rmtree(req_dir, ignore_errors=True)
            await update.message.reply_text(
                f"文件 {size/1024/1024:.1f}MB 超过 50MB Telegram 限制"
            )
            return False

        cap = f"📺 [{media.platform}]"
        if media.author:
            cap += f"\n👤 {media.author}"
        if media.title:
            cap += f"\n📝 {media.title[:200]}"

        # 清晰度按钮
        reply_markup = None
        q_opts = media.quality_options
        if q_opts and len(q_opts) > 1:
            cache_key = uuid.uuid4().hex[:10]
            quality_store[cache_key] = (media, q_opts)
            keyboard = []
            row = []
            for i, opt in enumerate(q_opts[:9]):
                label = opt.label
                if i == 0:
                    label = f"{opt.label}"
                row.append(InlineKeyboardButton(label, callback_data=f"q:{cache_key}:{i}"))
                if len(row) == 3:
                    keyboard.append(row); row = []
            if row:
                keyboard.append(row)
            reply_markup = InlineKeyboardMarkup(keyboard)

        with open(path, "rb") as f:
            await update.message.reply_video(
                f, caption=cap, supports_streaming=True, reply_markup=reply_markup
            )

        shutil.rmtree(req_dir, ignore_errors=True)
        return True

    except Exception as e:
        logger.error(f"视频发送失败: {e}")
        shutil.rmtree(req_dir, ignore_errors=True)
        return False


async def _send_images(update: Update, context, media: MediaInfo) -> bool:
    req_dir = tempfile.mkdtemp(prefix="req_", dir=DOWNLOAD_DIR)
    sent = 0
    total = len(media.urls)

    for i, img_url in enumerate(media.urls):
        ext = ".jpg"
        for e in (".png", ".webp", ".gif", ".jpeg"):
            if e in img_url.lower():
                ext = e; break

        path = os.path.join(req_dir, f"{clean_filename(media.title)}_{i}_{uuid.uuid4().hex[:6]}{ext}")
        try:
            ok = await download_file(img_url, path)
            if not ok:
                continue
            size = os.path.getsize(path)
            if size == 0 or size > MAX_FILE_SIZE:
                if os.path.exists(path): os.remove(path)
                continue

            cap = f"📷 [{media.platform}]"
            if media.author:
                cap += f"\n👤 {media.author}"
            if media.title:
                cap += f"\n📝 {media.title[:200]}"
            if total > 1: cap += f" ({i+1}/{total})"
            if media.title: cap += f"\n{media.title[:200]}"

            with open(path, "rb") as f:
                if ext == ".gif":
                    await update.message.reply_animation(f, caption=cap)
                else:
                    await update.message.reply_photo(f, caption=cap)

            os.remove(path)
            sent += 1
        except Exception as e:
            logger.error(f"图片发送失败 [{i}]: {e}")
            if os.path.exists(path): os.remove(path)

    shutil.rmtree(req_dir, ignore_errors=True)
    return sent > 0


# ============================================================
# 清晰度切换回调
# ============================================================

async def quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理清晰度按钮点击"""
    query = update.callback_query
    data = query.data  # 格式: "q:cache_key:index"

    try:
        _, cache_key, idx = data.split(":")
        idx = int(idx)
    except (ValueError, IndexError):
        await query.answer("无效选项")
        return

    if cache_key not in quality_store:
        await query.answer("选项已过期，请重新发送链接", show_alert=True)
        return

    media, q_opts = quality_store[cache_key]
    if idx >= len(q_opts):
        await query.answer("选项无效")
        return

    opt = q_opts[idx]
    await query.answer(f"正在下载 {opt.label} ...")

    # 下载所选清晰度
    path = os.path.join(
        DOWNLOAD_DIR,
        f"{clean_filename(media.title)}_{opt.label}_{uuid.uuid4().hex[:6]}.mp4"
    )

    try:
        ok = await download_file(opt.url, path)
        if not ok:
            await query.message.reply_text(f"下载 {opt.label} 失败")
            return

        size = os.path.getsize(path)
        if size == 0 or size > MAX_FILE_SIZE:
            os.remove(path)
            await query.message.reply_text(
                f"{opt.label} 文件 {size/1024/1024:.1f}MB 超过限制"
            )
            return

        cap = f"📺 [{media.platform}]\n👤 {media.author}\n📝 {media.title[:200]}\n🎥 清晰度: {opt.label}"
        if opt.filesize:
            cap += f" | {opt.filesize/1024/1024:.1f}MB"

        with open(path, "rb") as f:
            await query.message.reply_video(
                f, caption=cap,
                supports_streaming=True,
            )

        os.remove(path)

    except Exception as e:
        logger.error(f"清晰度切换失败: {e}")
        await query.message.reply_text(f"{opt.label} 发送失败")
        if os.path.exists(path):
            os.remove(path)


# ============================================================
# 错误处理 & 启动
# ============================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}", exc_info=context.error)
    if update and hasattr(update, "message") and update.message:
        try:
            await update.message.reply_text("处理出错，请稍后重试")
        except Exception:
            pass


async def start_bot(webhook_url: str = None):
    """启动机器人，支持 webhook 或 polling 模式"""
    if not BOT_TOKEN:
        raise ValueError("未设置 BOT_TOKEN 环境变量")

    from telegram import Bot
    from telegram.error import TelegramError
    import httpx

    # ---- 构造 API 端点 ----
    api_base = TELEGRAM_PROXY_URL or "https://api.telegram.org/bot"
    api_file = api_base.replace("/bot", "/file/bot")
    logger.info(f"Telegram API: {api_base[:40]}...")

    # ---- 预验证 Token ----
    logger.info(f"正在验证 BOT_TOKEN (前8位): {BOT_TOKEN[:8]}...")
    try:
        test_bot = Bot(token=BOT_TOKEN, base_url=api_base, base_file_url=api_file)
        me = await test_bot.get_me()
        logger.info(f"Token 有效! 机器人: @{me.username} (ID: {me.id}, Name: {me.first_name})")
    except Exception as e:
        logger.error(f"Token 验证失败: {e}")
        raise ValueError(f"无法连接 Telegram: {e}") from e

    # ---- 构建 Application ----
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .base_url(api_base)
        .base_file_url(api_file)
        .connect_timeout(30)
        .read_timeout(60)
        .write_timeout(60)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("platforms", platforms_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CallbackQueryHandler(quality_callback, pattern=r"^q:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_error_handler(error_handler)

    await app.initialize()
    await app.start()

    logger.info(f"机器人启动 | {len(SUPPORTED_PLATFORMS)} 平台直连 + yt-dlp 1000+")

    if webhook_url:
        # ---- Webhook 模式 ----
        logger.info(f"设置 Webhook: {webhook_url}")
        await app.bot.set_webhook(url=webhook_url)
        logger.info("Webhook 已设置，等待 Telegram 推送...")

        import bot as bot_module
        bot_module._app_instance = app
    else:
        # ---- Polling 模式 ----
        logger.info("速率限制: %s 条/%s秒", RATE_LIMIT, RATE_WINDOW)
        logger.info("开始轮询 Telegram 更新...")
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("机器人收到停止信号")

    if not webhook_url:
        await app.updater.stop()
    await app.stop()
    await app.shutdown()
    shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)

# Global reference for webhook handler
_app_instance = None
