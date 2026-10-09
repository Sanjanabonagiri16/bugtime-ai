FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /bin/bash bugtime \
    && pip install --no-cache-dir pytest==8.3.4

WORKDIR /workspace
USER bugtime
ENV PYTHONPATH=/workspace
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Default entrypoint is overridden by the executor with structured pytest args.
ENTRYPOINT ["pytest"]
CMD ["--version"]
