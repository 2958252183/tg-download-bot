FROM python:3.11-slim

WORKDIR /code

# 系统依赖：ffmpeg (合并/压缩) + git (自动更新) + curl (健康检查)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg git curl && \
    rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 项目文件
COPY . .

RUN mkdir -p downloads

EXPOSE 7860 8080 8443

# 环境变量默认值
ENV UPDATE_INTERVAL=86400
ENV HEALTH_PORT=8080
ENV WEBHOOK_PORT=8443

# Docker 健康检查（每30秒检查 /health 端点）
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "app.py"]
