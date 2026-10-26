FROM python:3.13-slim

ENV TZ="Asia/Shanghai" \
    PYTHONUNBUFFERED=1 \
    BDPAN_CONFIG_DIR=/app/config/.bdpan \
    CONFIG_PATH=/app/config/baidu_config.json \
    SCRIPT_PATH=/app/baidu_auto_save.py

WORKDIR /app
COPY . /app

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r requirements.txt \
 && ARCH=$(uname -m) \
 && if [ "$ARCH" = "x86_64" ]; then BDPAN_ARCH=amd64; else BDPAN_ARCH=arm64; fi \
 && curl -fsSL -o /tmp/bdpan-installer https://issuecdn.baidupcs.com/issue/netdisk/ai-bdpan/installer/3.8.4/bdpan-installer-linux-${BDPAN_ARCH} \
 && chmod +x /tmp/bdpan-installer \
 && /tmp/bdpan-installer --yes \
 && rm -f /tmp/bdpan-installer \
 && bdpan version || true

EXPOSE 5006
CMD ["python", "./app/run.py"]
