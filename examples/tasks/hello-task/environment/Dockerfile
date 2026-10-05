FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d
# 预装固定 Agent，避免测试网无法连接原生 bootstrap 下载域名。
RUN apt-get update && apt-get install -y --no-install-recommends bash curl nodejs npm procps && npm install -g @anthropic-ai/claude-code@2.1.81 && apt-get clean && rm -rf /var/lib/apt/lists/*
WORKDIR /app
