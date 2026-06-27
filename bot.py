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
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "8443"))
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
    top = SUPPORTED_PLATFORMS[:14]
    await update.message.reply_text(
        f"xdca 万能链接解析机器人\n\n"
        f"xdad 直连平台：{' | '.join(top)} ...\n"
        f"xd4a yt-dlp 引擎额外覆盖 1000+ 全球网站\n\n"
        f"xdad 默认发送最高画质，点击按钮切换清晰度\n"
        f"xdad 速率限制：每分钟 {RATE_LIMIT} 条\n"
        f"xdad 群聊：设为管理员后自动解析"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "xdca 使用说明\n\n"
        "1. 发送链接，自动解析并发送最高画质\n"
        "2. 视频下方按钮可切换 720p/1080p/4K 等\n"
        f"3. 每分钟最多 {RATE_LIMIT} 条链接\n"
        "4. 支持平台列表：/platforms\n"
        "5. 每24小时自动更新解析引擎"
    )


async def platforms_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [f"xdad 直连平台（{len(SUPPORTED_PLATFORMS)} 个）:\n"]
    cols = 3
    for i in range(0, len(SUPPORTED_PLATFORMS), cols):
        chunk = SUPPORTED_PLATFORMS[i:i+cols]
        lines.append("  |  ".join(f"{i+j+1}.{p}" for j, p in enumerate(chunk)))
    lines.append("\nxd4a yt-dlp 引擎额外覆盖 1000+ 网站")
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
                message_text=f"xdca 正在解析 {platform} 链接...\n{url}"
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
            f"xd34 速率限制：每分钟最多 {RATE_LIMIT} 条链接，请稍后再试"
        )
        return

    total_links = min(len(links), 5)

    if total_links == 1:
        status = await message.reply_text(f"xdad 解析中... [{links[0][1]}]")
    else:
        plats = ", ".join(dict.fromkeys(p for _, p in links[:total_links]))
        status = await message.reply_text(
            f"xdad 检测到 {total_links} 个链接 [{plats}]，解析中..."
            f"\n(剩余 {remaining} 次)"
        )

    ok = 0
    fail = 0

    for url, platform in links[:total_links]:
        try:
            await status.edit_text(f"xdad [{platform}] 解析中...\n{url[:60]}...")
            media = await parse_url(url)

            if not media:
                fail += 1
                continue

            sent = await _send_media(update, context, media)
            if sent:
                ok += 1
            else:
                fail += 1

        except Exception as e:
            logger.error(f"[{platform}] {e}")
            fail += 1

    parts = []
    if ok > 0:
        parts.append(f"xe285 {ok} 个成功")
    if fail > 0:
        parts.append(f"xe29d {fail} 个失败")
    final = " | ".join(parts) if parts else "xe29d 全部解析失败"

    try:
        await status.edit_text(final)
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
        # 下载（带进度回调）
        last_pct = -1
        async def progress_cb(downloaded, total):
            nonlocal last_pct
            if total > 0:
                pct = int(downloaded / total * 100)
                if pct >= last_pct + 20:  # 每20%更新一次
                    last_pct = pct
                    try:
                        await update.message.reply_text(f"xdad 下载中... {pct}%")
                    except Exception:
                        pass

        ok = await download_file(media.best_url, path)
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
            if compress_video(path, compressed, target_mb=45):
                os.remove(path)
                path = compressed
                size = os.path.getsize(path)
                logger.info(f"压缩完成: {size/1024/1024:.1f}MB")
            else:
                logger.warning("压缩失败，使用原文件")

        if size > MAX_FILE_SIZE:
            shutil.rmtree(req_dir, ignore_errors=True)
            await update.message.reply_text(
                f"xd34 文件 {size/1024/1024:.1f}MB 超过 50MB Telegram 限制"
            )
            return False

        cap = media.summary()

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
                    label = f"xdad {opt.label}"
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

            cap = f"[{media.platform}]"
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
    await query.answer(f"xd4a 正在下载 {opt.label} ...")

    # 下载所选清晰度
    path = os.path.join(
        DOWNLOAD_DIR,
        f"{clean_filename(media.title)}_{opt.label}_{uuid.uuid4().hex[:6]}.mp4"
    )

    try:
        ok = await download_file(opt.url, path)
        if not ok:
            await query.message.reply_text(f"xe29d 下载 {opt.label} 失败")
            return

        size = os.path.getsize(path)
        if size == 0 or size > MAX_FILE_SIZE:
            os.remove(path)
            await query.message.reply_text(
                f"xd34 {opt.label} 文件 {size/1024/1024:.1f}MB 超过限制"
            )
            return

        cap = f"{media.summary()}\nxd4a 清晰度: {opt.label}"
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
        await query.message.reply_text(f"xe29d {opt.label} 发送失败")
        if os.path.exists(path):
            os.remove(path)


# ============================================================
# 错误处理 & 启动
# ============================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}", exc_info=context.error)
    if update and hasattr(update, "message") and update.message:
        try:
            await update.message.reply_text("xe29d 处理出错，请稍后重试")
        except Exception:
            pass


async def start_bot():
    if not BOT_TOKEN:
        raise ValueError("未设置 BOT_TOKEN 环境变量")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("platforms", platforms_cmd))
    app.add_handler(CallbackQueryHandler(quality_callback, pattern=r"^q:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 内联模式
    from telegram.ext import InlineQueryHandler
    app.add_handler(InlineQueryHandler(inline_query))

    app.add_error_handler(error_handler)

    logger.info(f"xdca 机器人启动 | {len(SUPPORTED_PLATFORMS)} 平台直连 + yt-dlp 1000+")
    logger.info(f"速率限制: {RATE_LIMIT} 条/{RATE_WINDOW}秒")

    await app.initialize()
    await app.start()

    if WEBHOOK_URL:
        webhook_path = f"{WEBHOOK_URL.rstrip('/')}/webhook"
        await app.bot.set_webhook(url=webhook_path)
        logger.info(f"Webhook 模式: {webhook_path}")
        await app.run_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            webhook_url=webhook_path,
        )
    else:
        logger.info("Polling 模式")
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    await app.stop()
    await app.shutdown()
    shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)
