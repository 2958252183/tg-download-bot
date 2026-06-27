# xdca 万能链接解析 Telegram 机器人

自动识别 **32+ 直连平台 + yt-dlp 1000+ 全球网站** 的分享链接，下载最高画质发送到 Telegram。

## xdca 功能一览

| 类别 | 能力 |
|---|---|
| 平台覆盖 | 抖音/快手/B站/微博/小红书 + YouTube/X/Instagram/Facebook/TikTok/Reddit/Pinterest/Vimeo/Twitch 等 32 平台直连，yt-dlp 引擎额外覆盖 1000+ |
| 画质 | 默认最高画质，内联按钮一键切换 720p/1080p/4K 等 |
| 可靠性 | 3次指数退避重试 + 5 User-Agent 轮换 + 代理支持 + Cookie 注入 |
| 速率 | 每用户每分钟最多 10 条链接 |
| 大视频 | 50-200MB 自动 ffmpeg 压缩至 ~45MB |
| 群聊 | 设为管理员后自动解析群内链接 |
| 内联 | 任意聊天框 @机器人 链接 即可解析 |
| 更新 | 每24小时自动更新 yt-dlp + Git Pull |
| 模式 | 支持 Polling（默认）和 Webhook（生产） |

---

## 第一步：准备 Telegram Bot Token

1. 在 Telegram 搜索并打开 **@BotFather**
2. 发送命令 `/newbot`
3. 输入机器人名称（如 `万能链接解析`）
4. 输入机器人用户名（必须以 `bot` 结尾，如 `my_link_parser_bot`）
5. 创建成功后会返回一串 Token，类似：
   ```
   1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
   ```
6. **复制并保存这个 Token**

### 让机器人在群聊中工作

在 @BotFather 中操作：
- 发送 `/setprivacy`
- 选择你的机器人
- 选择 **Disable**（关闭隐私模式）
- 发送 `/setjoingroups`
- 选择你的机器人
- 选择 **Enable**

---

## 第二步：托管代码到 GitHub

### 2.1 创建 GitHub 仓库

1. 打开 https://github.com/new
2. Repository name 填写 `tg-link-bot`
3. 选择 **Public**（公开）
4. **不要**勾选 "Add a README file"
5. 点击 **Create repository**

### 2.2 推送代码

打开电脑上的 **PowerShell**（开始菜单搜索 PowerShell），逐行复制执行：

```powershell
cd C:\Users\29582\Desktop\TG\tg-bot

# 初始化 Git
git init
git add .
git commit -m "万能链接解析TG机器人 v2"

# 关联你的 GitHub 仓库（替换为你的用户名）
git branch -M main
git remote add origin https://github.com/你的GitHub用户名/tg-link-bot.git

# 推送
git push -u origin main
```

弹出 GitHub 登录窗口时，用浏览器授权即可。

---

## 第三步：部署到 HuggingFace Spaces

### 3.1 创建 Space

1. 打开 https://huggingface.co/spaces
2. 点击右上角 **Create new Space**
3. 填写：
   - Space name: `tg-link-bot`
   - License: `mit`
   - SDK: **Docker**
   - Docker template: **Blank**
4. 点击 **Create Space**

### 3.2 设置密钥

1. 在 Space 页面点击 **Settings** 标签
2. 找到 **Repository Secrets** 区域
3. 点击 **New secret**，添加以下密钥：

| Name | Value |
|---|---|
| `BOT_TOKEN` | 你的 Telegram Bot Token（第一步获取的） |
| `SPACE_URL` | `https://你的用户名-tg-link-bot.hf.space` |

4. 可选密钥（按需添加）：

| Name | 用途 |
|---|---|
| `HTTPS_PROXY` | 代理地址（国内访问国际平台用） |
| `COOKIES_FILE` | Cookie 文件路径 |
| `WEBHOOK_URL` | Webhook 模式（生产部署用） |

### 3.3 关联 GitHub 并部署

1. 在 Space 的 **Settings** 页面
2. 找到 **Connected GitHub Repo** 
3. 填写 `你的GitHub用户名/tg-link-bot`
4. 点击 **Connect**
5. 然后点击 **Factory Rebuild** 触发首次构建
6. 等待 3-5 分钟，查看 **Logs** 确认启动成功

看到类似输出即表示成功：
```
xdca 机器人启动 (Polling) | 健康检查 :8080
```

### 3.4 验证部署

访问 `https://你的用户名-tg-link-bot.hf.space` 看到 Gradio 面板即成功。

---

## 第四步：测试机器人

1. 在 Telegram 搜索你的机器人用户名
2. 发送 `/start`
3. 发送一个测试链接，例如：
   ```
   https://youtube.com/watch?v=dQw4w9WgXcQ
   ```
4. 机器人应自动回复解析后的视频

---

## 第五步：防止休眠（重要！）

HuggingFace 免费 Space 48小时无访问会休眠。

### 配置 UptimeRobot（推荐，免费）

1. 打开 https://uptimerobot.com 注册
2. 点击 **+ Create New Monitor**
3. Monitor Type 选择 **HTTP(s)**
4. Friendly Name 填 `TG Bot`
5. URL 填 `https://你的用户名-tg-link-bot.hf.space/health`
6. Monitoring Interval 选 **5 分钟**
7. 点击 **Create Monitor**

这样即使 Space 无人访问，UptimeRobot 每 5 分钟 ping 一次保持活跃。

---

## 常见问题

**Q: 构建失败怎么办？**
查看 Space 的 Logs 标签，最常见的错误是 BOT_TOKEN 未设置或 Token 无效。

**Q: 机器人不回复？**
确认 BOT_TOKEN 正确，且在 @BotFather 中隐私模式已禁（Disable）。

**Q: 国内平台链接解析失败？**
部分平台（抖音/小红书）反爬严格。尝试设置 `HTTPS_PROXY` 代理，或稍后重试（代码内置重试）。

**Q: 如何更新代码？**
```bash
cd C:\Users\29582\Desktop\TG\tg-bot
git add .
git commit -m "更新"
git push
```
HuggingFace 自动检测推送并重新构建。

---

## 项目文件

```
tg-bot/
  app.py             # HuggingFace 入口（Gradio + 健康检查 + 自动更新）
  bot.py             # Telegram 机器人（命令/消息/内联/清晰度选择）
  parsers.py         # 解析引擎（32平台识别 + yt-dlp + 重试/代理/Cookie）
  Dockerfile         # Docker 构建（ffmpeg + git + HEALTHCHECK）
  requirements.txt   # Python 依赖
  .dockerignore      # 构建优化
  README.md          # 本文件
```
