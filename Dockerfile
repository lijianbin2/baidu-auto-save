FROM python:3.13-slim

ENV TZ="Asia/Shanghai" \
    PYTHONUNBUFFERED=1 \
    PATH="/root/.local/bin:${PATH}" \
    BDPAN_CONFIG_DIR=/app/config/.bdpan \
    CONFIG_PATH=/app/config/baidu_config.json \
    SCRIPT_PATH=/app/baidu_auto_save.py

WORKDIR /app

# 依赖层前置：只改代码时直接命中缓存，不再重装
COPY requirements.txt /app/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r /app/requirements.txt

# bdpan：版本号 pin 在 ARG，升级只改一行；直链 tar 包比 installer 快
ARG BDPAN_VERSION=3.8.8
RUN ARCH=$(uname -m) \
 && if [ "$ARCH" = "x86_64" ]; then BDPAN_ARCH=amd64; else BDPAN_ARCH=arm64; fi \
 && curl -fsSL -o /tmp/bdpan.tgz https://issuecdn.baidupcs.com/issue/netdisk/ai-bdpan/linux/${BDPAN_VERSION}/bdpan-${BDPAN_VERSION}-linux-${BDPAN_ARCH}.tar.gz \
 && mkdir -p /root/.local/bin \
 && tar -xzf /tmp/bdpan.tgz -C /tmp \
 && install -m 0755 /tmp/bdpan/bdpan /root/.local/bin/bdpan \
 && rm -rf /tmp/bdpan.tgz /tmp/bdpan \
 && ln -sf /root/.local/bin/bdpan /usr/local/bin/bdpan \
 && bdpan version

COPY . /app

EXPOSE 5006
CMD ["python", "./app/run.py"]
