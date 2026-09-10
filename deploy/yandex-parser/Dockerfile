FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright DISPLAY=:99
WORKDIR /app
COPY requirements.txt requirements-parser.txt ./
RUN pip install --no-cache-dir -r requirements-parser.txt \
    && playwright install --with-deps chromium \
    && apt-get update && apt-get install -y --no-install-recommends xvfb xauth \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 parser \
    && mkdir -p /app/data/yandex-storefront /app/secrets \
    && chown -R parser:parser /app/data
COPY app ./app
COPY scripts ./scripts
USER parser
CMD ["xvfb-run", "-a", "python", "-u", "scripts/parse_yandex_storefront_prices.py", "--loop"]
