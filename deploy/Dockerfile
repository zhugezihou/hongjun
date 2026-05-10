# 鸿钧 · Dockerfile
# ====================
#
# 构建:  docker build -t hongjun:latest .
# 运行:  docker run -d -p 8000:8000 --env-file .env hongjun:latest
# 或用:  docker compose up -d
#
# 多阶段构建：
#   Stage 1: builder（安装依赖）
#   Stage 2: runtime（最小运行时）

FROM python:3.11-slim AS builder

WORKDIR /app

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖（隔离）
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt


# === Runtime Stage ===

FROM python:3.11-slim AS runtime

LABEL maintainer="鸿钧团队"
LABEL description="鸿钧 · 六部尚书协同 AI Agent 系统"

# 安装运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash hongjun

WORKDIR /app

# 从 builder 复制虚拟环境
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# 复制应用代码
COPY --chown=hongjun:hongjun src/ ./src/
COPY --chown=hongjun:hongjun config/ ./config/

# 数据目录
RUN mkdir -p /app/data && chown hongjun:hongjun /app/data

# 非 root 运行
USER hongjun

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 端口（供 docker compose 暴露）
EXPOSE 8000

# 默认启动命令（交互式 CLI）
CMD ["python3", "src/hongjun.py"]
