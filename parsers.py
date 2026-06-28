"""
万能媒体解析器 - yt-dlp主引擎 (1000+平台) + 自定义解析器
支持：抖音/快手/B站/微博/小红书 + YouTube/X/Instagram/Facebook/TikTok/Reddit 等全球平台
"""

import re
import json
import os
import asyncio
import tempfile
from typing import Optional, List, Tuple, Dict
from dataclasses import dataclass, field

import httpx
import http.cookiejar
from bs4 import BeautifulSoup


# ============================================================
# 基础设施：重试 / 代理 / Cookie
# ============================================================

import functools
import random

# 多 User-Agent 轮换
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

_random_ua = lambda: random.choice(_USER_AGENTS)

# 代理
def _get_proxy() -> Optional[str]:
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        v = os.environ.get(var, "")
        if v:
            return v
    return None

PROXY_URL = _get_proxy()

# Cookie 文件
COOKIES_FILE = os.environ.get("COOKIES_FILE", "")

def _load_cookies_dict() -> dict:
    """从 Netscape 格式 cookie 文件解析"""
    if not COOKIES_FILE or not os.path.exists(COOKIES_FILE):
        return {}
    cookies = {}
    with open(COOKIES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("	")
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
    return cookies

# 重试装饰器
def _retry(max_tries: int = 3, base_delay: float = 1.0):
    """异步指数退避重试"""
    def deco(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(max_tries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_err = e
                    if attempt < max_tries - 1:
                        d = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        print(f"[重试] {func.__name__} #{attempt+1} 失败, {d:.1f}s 后重试: {e}")
                        await asyncio.sleep(d)
            raise last_err
        return wrapper
    return deco


# ============================================================
# 数据模型
# ============================================================

# ============================================================
# 数据模型
# ============================================================
@dataclass
class QualityOption:
    """视频清晰度选项"""
    label: str          # 显示标签如 "1080p"
    height: int         # 像素高度
    url: str            # 直链
    filesize: Optional[int] = None  # 文件大小
    fps: int = 0        # 帧率
    codec: str = ""     # 编码格式




@dataclass
class MediaInfo:
    """解析出的媒体信息"""
    media_type: str          # "video" | "image" | "gif"
    urls: List[str]          # 媒体文件直链列表
    title: str = ""          # 标题
    platform: str = ""       # 平台名称
    duration: Optional[int] = None
    author: str = ""
    source_url: str = ""     # 原始链接
    quality_options: list = field(default_factory=list)  # 所有可用清晰度

    @property
    def is_video(self) -> bool:
        return self.media_type == "video"

    @property
    def is_image(self) -> bool:
        return self.media_type in ("image", "gif")

    @property
    def best_url(self) -> str:
        return self.urls[0] if self.urls else ""

    def summary(self) -> str:
        """生成简短摘要"""
        parts = [f"[{self.platform}]"]
        if self.author:
            parts.append(self.author)
        if self.title:
            parts.append(self.title[:80])
        return " ".join(parts)


# ============================================================
# 请求头 & 工具函数
# ============================================================

HEADERS = {
    "mobile": {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    },
    "desktop": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    },
}


def clean_filename(title: str, max_len: int = 80) -> str:
    title = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", title)
    return title.strip()[:max_len] or "media"


@_retry(max_tries=3, base_delay=1.5)
async def download_file(url: str, save_path: str, progress_cb=None) -> bool:
    """流式下载文件（支持代理+重试+进度回调）"""
    client_kwargs = {
        "timeout": 180.0,
        "follow_redirects": True,
        "headers": {**HEADERS["desktop"], "User-Agent": _random_ua()},
    }
    if PROXY_URL:
        client_kwargs["proxy"] = PROXY_URL

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                with open(save_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb and total > 0:
                            await progress_cb(downloaded, total)
        return True
    except Exception as e:
        print(f"[下载] {e}")
        return False


def _safe_json(html: str, pattern: str) -> Optional[dict]:
    """安全提取JSON，将undefined替换为null"""
    m = re.search(pattern, html, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1).replace("undefined", "null"))
    except (json.JSONDecodeError, KeyError):
        return None


# ============================================================
# 全局平台识别表
# ============================================================

PLATFORM_MAP: Dict[str, Tuple[str, List[str]]] = {
    # --- 国内平台（优先自定义解析器） ---
    "抖音":   ([r"v\.douyin\.com", r"douyin\.com/video/",
                 r"iesdouyin\.com", r"douyin\.com/note/"], True),
    "小红书": ([r"xhslink\.com", r"xiaohongshu\.com/discovery/",
                 r"xiaohongshu\.com/explore/"], True),
    "快手":   ([r"v\.kuaishou\.com", r"kuaishou\.com/short-video/",
                 r"kuaishou\.com/f/"], True),
    "微博":   ([r"weibo\.(?:com|cn)/", r"m\.weibo\.cn/"], True),

    # --- B站（yt-dlp 支持很好，但自定义备用） ---
    "B站":    ([r"b23\.tv", r"bilibili\.com/video/",
                 r"bilibili\.com/bangumi/"], True),

    # --- 国际平台（yt-dlp 主引擎） ---
    "YouTube":      ([r"youtube\.com/", r"youtu\.be/"], False),
    "X":            ([r"(?:twitter\.com|x\.com)/"], False),
    "Instagram":    ([r"instagram\.com/"], False),
    "Facebook":     ([r"(?:facebook\.com|fb\.com|fb\.watch)/"], False),
    "TikTok":       ([r"tiktok\.com/"], False),
    "Reddit":       ([r"reddit\.com/", r"redd\.it/"], False),
    "Pinterest":    ([r"pinterest\.[a-z]+/", r"pin\.it/"], False),
    "Vimeo":        ([r"vimeo\.com/"], False),
    "Dailymotion":  ([r"dailymotion\.com/", r"dai\.ly/"], False),
    "Twitch":       ([r"twitch\.tv/"], False),
    "Snapchat":     ([r"snapchat\.com/"], False),
    "LinkedIn":     ([r"linkedin\.com/"], False),
    "Telegram":     ([r"t\.me/"], False),
    "VK":           ([r"vk\.com/", r"vk\.ru/"], False),
    "SoundCloud":   ([r"soundcloud\.com/"], False),
    "Imgur":        ([r"imgur\.com/"], False),
    "Tumblr":       ([r"tumblr\.com/"], False),
    "Flickr":       ([r"flickr\.com/"], False),

    # --- 其他国内平台 ---
    "知乎":     ([r"zhihu\.com/", r"zh\.com/"], False),
    "豆瓣":     ([r"douban\.com/"], False),
    "腾讯视频": ([r"v\.qq\.com/"], False),
    "优酷":     ([r"youku\.com/"], False),
    "西瓜视频": ([r"ixigua\.com/"], False),
    "虎牙":     ([r"huya\.com/"], False),
    "斗鱼":     ([r"douyu\.com/"], False),
    "网易云音乐": ([r"music\.163\.com/"], False),
    "QQ音乐":   ([r"y\.qq\.com/"], False),
}

# 需要自定义解析器的平台集合
CUSTOM_PARSER_PLATFORMS = {k for k, (_, needs_custom) in PLATFORM_MAP.items() if needs_custom}

# 预编译正则
_url_re = re.compile(r"https?://[^\s<>\"{}|\\^`\[\]]+", re.IGNORECASE)


def detect_platform(url: str) -> str:
    """识别链接所属平台"""
    for name, (patterns, _) in PLATFORM_MAP.items():
        for p in patterns:
            if re.search(p, url, re.IGNORECASE):
                return name
    return "其他"


def extract_all_links(text: str) -> List[Tuple[str, str]]:
    """从文本中提取所有链接及其平台"""
    results = []
    seen = set()
    for url in _url_re.findall(text):
        url = url.rstrip(".,;:!?）)】」'\"")
        if url in seen:
            continue
        seen.add(url)
        platform = detect_platform(url)
        results.append((url, platform))
    return results


# ============================================================
# 自定义解析器（处理 yt-dlp 薄弱的平台）
# ============================================================

class DouyinParser:
    """抖音解析器 - 从页面 ROUTER_DATA 提取（2024.06 抖音API需要a-bogus签名，改为SSR页面解析）"""
    PLATFORM = "抖音"
    @classmethod
    def matches(cls, url: str) -> bool:
        return bool(re.search(r"v\.douyin\.com|douyin\.com/(video|note)/|iesdouyin\.com", url))
    async def parse(self, url: str) -> Optional[MediaInfo]:
        try:
            ckwargs = {"follow_redirects": True, "timeout": 20.0, "headers": HEADERS["mobile"]}
            if PROXY_URL: ckwargs["proxy"] = PROXY_URL
            async with httpx.AsyncClient(**ckwargs) as client:
                # 获取页面 HTML
                resp = await client.get(url)
                html = resp.text
                # 从 HTML 中提取 window._ROUTER_DATA（抖音 SSR 渲染数据）
                aweme = None
                scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
                for s in scripts:
                    if s.strip().startswith('window._ROUTER_DATA'):
                        json_str = s.strip()
                        prefix = 'window._ROUTER_DATA = '
                        json_str = json_str[len(prefix):].rstrip(';').strip()
                        try:
                            router_data = json.loads(json_str)
                            for k, v in router_data.get('loaderData', {}).items():
                                if v and 'video' in k.lower():
                                    vir = v.get('videoInfoRes', {})
                                    items = vir.get('item_list', [])
                                    if items:
                                        aweme = items[0]
                                    break
                        except Exception:
                            pass
                        break
                if not aweme:
                    return None
                title = aweme.get("desc", "")
                author = aweme.get("author", {}).get("nickname", "")
                # 图集
                images = aweme.get("images", [])
                if images:
                    urls = []
                    for img in images:
                        ul = img.get("url_list", [])
                        if ul: urls.append(ul[0])
                    if urls:
                        return MediaInfo("image", urls, title, self.PLATFORM, author=author)
                # 视频
                vi = aweme.get("video", {})
                for key in ("play_addr_h265", "play_addr_h264", "play_addr"):
                    addr = vi.get(key, {})
                    ul = addr.get("url_list", [])
                    if ul:
                        vid_url = ul[0].replace("playwm", "play")
                        # 多清晰度选项
                        quality_options = []
                        for ratio, label in [("1080p", "1080p"), ("720p", "720p"), ("540p", "540p"), ("480p", "480p")]:
                            q_url = re.sub(r'ratio=\w+', f'ratio={ratio}', vid_url)
                            quality_options.append(QualityOption(label, int(ratio.replace('p', '')), q_url))
                        return MediaInfo(
                            "video", [vid_url],
                            title, self.PLATFORM,
                            aweme.get("duration", 0) // 1000, author,
                            quality_options=quality_options
                        )
                return None
        except Exception as e:
            print(f"[抖音] {e}")
            return None
class XiaohongshuParser:
    """小红书解析器"""
    PLATFORM = "小红书"

    @classmethod
    def matches(cls, url: str) -> bool:
        return bool(re.search(r"xhslink\.com|xiaohongshu\.com/(discovery|explore)/", url))

    async def parse(self, url: str) -> Optional[MediaInfo]:
        try:
            headers = {**HEADERS["desktop"], "Referer": "https://www.xiaohongshu.com/"}
            ckwargs = {"follow_redirects": True, "timeout": 20.0, "headers": headers}
            if PROXY_URL: ckwargs["proxy"] = PROXY_URL
            async with httpx.AsyncClient(**ckwargs) as client:
                if "xhslink.com" in url:
                    url = str((await client.get(url)).url)
                html = (await client.get(url)).text

                title = ""
                urls = []

                data = _safe_json(html, r"window\.__INITIAL_STATE__\s*=\s*(.+?)\s*</script>")
                if data:
                    note_map = data.get("note", {}).get("noteDetailMap", {})
                    nid = next(iter(note_map), "")
                    note = note_map.get(nid, {}).get("note", {})
                    title = note.get("title", note.get("desc", ""))

                    for img in note.get("imageList", []):
                        u = img.get("urlDefault", img.get("url", ""))
                        if u:
                            urls.append(u.split("!")[0] if "!" in u else u)

                    video = note.get("video", {})
                    if video:
                        for codec in ("h265", "h264"):
                            streams = video.get("media", {}).get("stream", {}).get(codec, [])
                            if streams and streams[0]:
                                mu = streams[0].get("masterUrl", "")
                                if mu:
                                    urls = [mu]
                                    break

                if not title:
                    soup = BeautifulSoup(html, "lxml")
                    og = soup.find("meta", property="og:title")
                    title = og["content"] if og else ""

                if urls:
                    mt = "video" if ".mp4" in urls[0] else "image"
                    return MediaInfo(mt, urls, title[:100] or "小红书", self.PLATFORM)
                return None
        except Exception as e:
            print(f"[小红书] {e}")
            return None


class KuaishouParser:
    """快手解析器"""
    PLATFORM = "快手"

    @classmethod
    def matches(cls, url: str) -> bool:
        return bool(re.search(r"v\.kuaishou\.com|kuaishou\.com/(short-video|f)/", url))

    async def parse(self, url: str) -> Optional[MediaInfo]:
        try:
            ckwargs = {"follow_redirects": True, "timeout": 20.0, "headers": HEADERS["mobile"]}
            if PROXY_URL: ckwargs["proxy"] = PROXY_URL
            async with httpx.AsyncClient(**ckwargs) as client:
                if "v.kuaishou.com" in url:
                    url = str((await client.get(url)).url)
                html = (await client.get(url, headers=HEADERS["desktop"])).text

                title = ""
                video_url = None

                # __NEXT_DATA__
                m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>', html, re.DOTALL)
                if m:
                    try:
                        d = json.loads(m.group(1))
                        vi = d.get("props", {}).get("pageProps", {}).get("videoInfo", {})
                        title = vi.get("caption", vi.get("name", ""))
                        video_url = vi.get("photoUrl", vi.get("videoUrl", ""))
                    except (json.JSONDecodeError, KeyError):
                        pass

                if not video_url:
                    for key in ("videoUrl", "photoUrl"):
                        m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', html)
                        if m:
                            video_url = m.group(1)
                            break

                if not title:
                    soup = BeautifulSoup(html, "lxml")
                    og = soup.find("meta", property="og:title")
                    title = og["content"] if og else ""

                if video_url:
                    return MediaInfo("video", [video_url], title or "快手", self.PLATFORM,
                                     quality_options=[QualityOption("默认", 0, video_url)])
                return None
        except Exception as e:
            print(f"[快手] {e}")
            return None


class BilibiliParser:
    """B站解析器 - 直接调用B站API（yt-dlp被WAF拦截）"""
    PLATFORM = "B站"

    @classmethod
    def matches(cls, url: str) -> bool:
        return bool(re.search(r"b23\.tv|bilibili\.com/video/|bilibili\.com/bangumi/", url))

    async def parse(self, url: str) -> Optional[MediaInfo]:
        try:
            headers = {**HEADERS["desktop"], "Referer": "https://www.bilibili.com/"}
            ckwargs = {"follow_redirects": True, "timeout": 20.0, "headers": headers}
            if PROXY_URL: ckwargs["proxy"] = PROXY_URL

            async with httpx.AsyncClient(**ckwargs) as client:
                await client.get("https://www.bilibili.com/", headers=headers)

                if "b23.tv" in url:
                    resp = await client.get(url)
                    url = str(resp.url)

                bvid = None
                for seg in ["/video/", "/bangumi/play/"]:
                    if seg in url:
                        bvid = url.split(seg)[1].split("?")[0].split("/")[0].strip("/")
                        break
                if not bvid:
                    m = re.search(r"BV[a-zA-Z0-9]+", url)
                    if m: bvid = m.group(0)
                if not bvid:
                    return None

                api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
                ah = {**headers, "Referer": f"https://www.bilibili.com/video/{bvid}"}
                resp = await client.get(api, headers=ah)
                data = resp.json()
                vdata = data.get("data", {})
                if not vdata:
                    return None

                title = vdata.get("title", "")
                author = vdata.get("owner", {}).get("name", "")
                pages = vdata.get("pages", [{}])
                cid = pages[0].get("cid", vdata.get("cid", ""))

                play_api = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&qn=120&fnval=4048&fourk=1"
                resp2 = await client.get(play_api, headers=ah)
                pdata = resp2.json().get("data", {})
                if not pdata:
                    return None

                quality_options = []
                best_url = ""

                dash = pdata.get("dash", {})
                dash_videos = dash.get("video", [])
                if dash_videos:
                    dash_videos.sort(key=lambda x: x.get("height", 0), reverse=True)
                    seen_heights = set()
                    for v in dash_videos:
                        h = v.get("height", 0)
                        if h > 0 and h not in seen_heights:
                            seen_heights.add(h)
                            q_url = v.get("baseUrl") or v.get("base_url", "")
                            if q_url:
                                quality_options.append(QualityOption(f"{h}p", h, q_url))
                    if quality_options:
                        best_url = quality_options[0].url

                durl_list = pdata.get("durl", [])
                if not quality_options and durl_list:
                    for i, du in enumerate(durl_list[:4]):
                        du_url = du.get("url", "")
                        if du_url:
                            quality_options.append(QualityOption(f"P{i+1}", 0, du_url))
                    if quality_options:
                        best_url = quality_options[0].url

                if not best_url:
                    return None

                duration = vdata.get("duration", 0)
                return MediaInfo(
                    "video", [best_url],
                    title, self.PLATFORM,
                    duration, author,
                    quality_options=quality_options if quality_options else None
                )
        except Exception as e:
            print(f"[B站] {e}")
            return None


class WeiboParser:
    """微博解析器"""
    PLATFORM = "微博"

    @classmethod
    def matches(cls, url: str) -> bool:
        return bool(re.search(r"weibo\.(?:com|cn)/|m\.weibo\.cn/", url))

    async def parse(self, url: str) -> Optional[MediaInfo]:
        try:
            # 转为移动版
            if "m.weibo.cn" not in url:
                bid = url.split("/status/")[1].split("?")[0] if "/status/" in url else url.rstrip("/").split("/")[-1]
                url = f"https://m.weibo.cn/detail/{bid}"

            ckwargs = {"follow_redirects": True, "timeout": 20.0, "headers": HEADERS["mobile"]}
            if PROXY_URL: ckwargs["proxy"] = PROXY_URL
            async with httpx.AsyncClient(**ckwargs) as client:
                html = (await client.get(url)).text
                title = ""
                urls = []

                data = _safe_json(html, r"var\s+\$render_data\s*=\s*\[(.+?)\]\s*\[0\]")
                if data:
                    status = data.get("status", data)
                    text = status.get("text", "")
                    title = BeautifulSoup(text, "lxml").get_text(strip=True) if text else ""

                    # 视频
                    mi = status.get("page_info", {}).get("media_info", {})
                    stream = mi.get("stream_url_hd") or mi.get("stream_url") or mi.get("mp4_720p_mp4") or ""
                    if stream:
                        urls.append(stream)

                    # 图片
                    for pic in status.get("pics", []):
                        pu = pic.get("large", {}).get("url") or pic.get("url", "")
                        if pu: urls.append(pu)

                if not urls:
                    soup = BeautifulSoup(html, "lxml")
                    v = soup.find("video")
                    if v and v.get("src"): urls.append(v["src"])
                    for img in soup.find_all("img"):
                        if "sinaimg.cn" in (img.get("src") or ""): urls.append(img["src"])

                if not title:
                    soup = BeautifulSoup(html, "lxml")
                    og = soup.find("meta", property="og:title")
                    title = og["content"] if og else ""

                if urls:
                    mt = "video" if any(".mp4" in u for u in urls) else "image"
                    return MediaInfo(mt, urls, title[:100] or "微博", self.PLATFORM)
                return None
        except Exception as e:
            print(f"[微博] {e}")
            return None


# 自定义解析器实例
CUSTOM_PARSERS = [
    DouyinParser(),
    BilibiliParser(),
    XiaohongshuParser(),
    KuaishouParser(),
    WeiboParser(),
]


def _dispatch_custom(url: str) -> Optional[object]:
    for p in CUSTOM_PARSERS:
        if p.__class__.matches(url):
            return p
    return None


async def custom_parse(url: str) -> Optional[MediaInfo]:
    """运行自定义解析器"""
    parser = _dispatch_custom(url)
    if not parser:
        return None
    return await parser.parse(url)


# ============================================================
# yt-dlp 主引擎（覆盖 1000+ 平台）
# ============================================================

async def ytdlp_parse(url: str, platform: str = "其他") -> Optional[MediaInfo]:
    """使用 yt-dlp 解析链接"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _ytdlp_extract, url, platform)


def _fetch_guest_cookies(domain: str) -> Optional[str]:
    """获取网站访客cookie，返回临时cookiefile路径"""
    try:
        import tempfile
        jar = http.cookiejar.CookieJar()
        with httpx.Client(follow_redirects=True, timeout=15,
                          headers=HEADERS["desktop"]) as client:
            client.get(f"https://{domain}/")
            fd, path = tempfile.mkstemp(suffix=".txt", prefix="cookies_")
            with os.fdopen(fd, "w") as f:
                f.write("# Netscape HTTP Cookie File\n")
                cookies_dict = {}
                for cookie in client.cookies.jar:
                    cookies_dict[cookie.name] = cookie
                for name, cookie in cookies_dict.items():
                    cd = cookie.domain or f".{domain}"
                    cp = cookie.path or "/"
                    sec = "TRUE" if cookie.secure else "FALSE"
                    exp = str(cookie.expires) if cookie.expires else "0"
                    f.write(f"{cd}\tTRUE\t{cp}\t{sec}\t{exp}\t{name}\t{cookie.value}\n")
            return path
    except Exception:
        return None

def _ytdlp_extract(url: str, platform: str) -> Optional[MediaInfo]:
    try:
        import yt_dlp
    except ImportError:
        print("[yt-dlp] 未安装")
        return None

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "socket_timeout": 30,
    }
    if PROXY_URL:
        opts["proxy"] = PROXY_URL
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    # Try to get guest cookies
    if not opts.get("cookiefile"):
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        if domain:
            cookie_path = _fetch_guest_cookies(domain)
            if cookie_path:
                opts["cookiefile"] = cookie_path
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        # Clean up temp cookie file
        if "cookiefile" in opts and opts["cookiefile"] and opts["cookiefile"] != COOKIES_FILE:
            try:
                os.remove(opts["cookiefile"])
            except Exception:
                pass
        print(f"[yt-dlp] 提取失败: {e}")
        return None

    if not info:
        return None

    title = info.get("title", "")
    duration = info.get("duration")
    uploader = info.get("uploader") or info.get("channel") or ""
    ext = info.get("ext", "")

    # 检查是否为播放列表
    if info.get("_type") == "playlist":
        entries = info.get("entries", [])
        if entries:
            info = entries[0]

    formats = info.get("formats", [])

    # 检查是否为纯图片（如 Instagram 单图）
    if not formats and ext in ("jpg", "jpeg", "png", "webp", "gif"):
        thumb = info.get("thumbnail", "")
        if thumb:
            return MediaInfo("image", [thumb], title, platform, author=uploader)

    if not formats:
        thumb = info.get("thumbnail", "")
        if thumb:
            return MediaInfo("image", [thumb], title, platform, author=uploader)
        return None

    # 收集所有清晰度选项 + 挑选最佳
    quality_map = {}  # height -> QualityOption
    best_url = ""
    best_h = 0

    for f in formats:
        if f.get("vcodec") == "none":
            continue
        h = f.get("height") or 0
        u = f.get("url", "")
        if not u:
            continue

        fps = f.get("fps") or 0
        label = f"{h}p"
        if fps and fps > 30:
            label += f" {fps}fps"

        if h not in quality_map:
            quality_map[h] = QualityOption(
                label=label, height=h, url=u,
                filesize=f.get("filesize"), fps=fps,
                codec=f.get("vcodec", "")[:10]
            )

        if h > best_h and f.get("ext") == "mp4":
            best_h = h
            best_url = u

    if not best_url:
        for f in formats:
            if f.get("url") and f.get("vcodec") != "none":
                best_url = f["url"]
                break

    quality_list = sorted(quality_map.values(), key=lambda q: -q.height)

    if best_url:
        return MediaInfo(
            "video", [best_url], title, platform, duration, uploader, url,
            quality_options=quality_list
        )

    # 纯音频
    for f in formats:
        if f.get("url") and f.get("acodec") != "none":
            return MediaInfo("video", [f["url"]], title, platform, duration, uploader, url)

    return None


async def ytdlp_download(url: str, out_dir: str) -> Optional[str]:
    """使用 yt-dlp 下载到本地"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _ytdlp_download_sync, url, out_dir)


def _ytdlp_download_sync(url: str, out_dir: str) -> Optional[str]:
    try:
        import yt_dlp
    except ImportError:
        return None

    opts = {
        "outtmpl": os.path.join(out_dir, "%(title).50s_%(id)s.%(ext)s"),
        "quiet": True, "no_warnings": True,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "max_filesize": 50 * 1024 * 1024,
        "socket_timeout": 30,
    }
    if PROXY_URL:
        opts["proxy"] = PROXY_URL
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            base = os.path.splitext(path)[0]
            for ext in (".mp4", ".mkv", ".webm"):
                if os.path.exists(base + ext):
                    return base + ext
            if os.path.exists(path):
                return path
    except Exception as e:
        print(f"[yt-dlp下载] {e}")
    return None


# ============================================================
# 统一解析入口（三层回退链）
# ============================================================

async def parse_url(url: str) -> Optional[MediaInfo]:
    """
    三层回退解析：
    1. 自定义解析器（抖音/小红书/快手/微博）
    2. yt-dlp 主引擎（1000+ 全球平台）
    3. 返回 None（不支持）
    """
    platform = detect_platform(url)

    # Layer 1: 自定义解析器
    if platform in CUSTOM_PARSER_PLATFORMS:
        result = await custom_parse(url)
        if result:
            result.platform = platform
            result.source_url = url
            return result
        print(f"[回退] 自定义解析失败，尝试 yt-dlp: {platform}")

    # Layer 2: yt-dlp 主引擎
    result = await ytdlp_parse(url, platform)
    if result:
        result.source_url = url
        return result

    # Layer 3: 如果 yt-dlp 返回空但平台已知，再次尝试自定义
    if platform not in CUSTOM_PARSER_PLATFORMS:
        result = await custom_parse(url)
        if result:
            result.platform = platform
            result.source_url = url
            return result

    return None
