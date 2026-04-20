FROM python:3.12-slim AS base

LABEL maintainer="XJD Agent Team"
LABEL description="小巨蛋智能体 — AI Agent Platform"

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    # Computer Use 依赖
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    xdotool \
    wmctrl \
    xvfb \
    # 浏览器自动化依赖
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# 虚拟显示 (headless Computer Use)
ENV DISPLAY=:99

WORKDIR /app

# 安装 Python 依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[all]" 2>/dev/null || pip install --no-cache-dir -e "."

# 复制源码
COPY . .

# 安装项目
RUN pip install --no-cache-dir -e .

# 数据目录
RUN mkdir -p /data/.xjd-agent
ENV XJD_HOME=/data/.xjd-agent

# 暴露端口 (Gateway WebSocket)
EXPOSE 18789

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:18789/health || exit 1

# 默认命令
CMD ["xjd-agent"]


# ═══════════════════════════════════════════════════════════════════
#  多阶段构建 — 精简镜像
# ═══════════════════════════════════════════════════════════════════
FROM python:3.12-slim AS production

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    xdotool \
    wmctrl \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

ENV DISPLAY=:99
WORKDIR /app

# 从 base 阶段复制已安装的包
COPY --from=base /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=base /usr/local/bin /usr/local/bin
COPY --from=base /app /app

RUN mkdir -p /data/.xjd-agent
ENV XJD_HOME=/data/.xjd-agent

EXPOSE 18789

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:18789/health || exit 1

# 非 root 用户
RUN useradd -m -s /bin/bash xjd && chown -R xjd:xjd /app /data
USER xjd

CMD ["xjd-agent"]
