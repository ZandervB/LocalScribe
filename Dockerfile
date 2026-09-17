# Official llama.cpp runtime, selected by Compose and pinned to build b11011.
# Keeping its Ubuntu base also keeps the correct native runtime libraries.
ARG LLAMA_IMAGE=ghcr.io/ggml-org/llama.cpp:server@sha256:b97738dc7c62a5eefbfc2f910ff727cc2bcc7dbe9e613d95532fcc32f9374025
FROM ${LLAMA_IMAGE}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && find /var/lib/apt/lists -type f -delete
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    LD_LIBRARY_PATH="/app" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/localscribe
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt
COPY app.py run.py setup_model.py docker_entry.py smoke_check.py ./
COPY static ./static
COPY samples/SOURCES.md ./samples/SOURCES.md
RUN mkdir -p data models

EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=30m --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3)"]
ENTRYPOINT ["python", "docker_entry.py"]
