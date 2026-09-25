FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements-runtime.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements-runtime.txt

COPY bot.py ./
COPY cogs ./cogs
COPY config ./config
COPY services ./services

RUN mkdir -p /app/data && chown -R app:app /app

USER app

CMD ["python", "bot.py"]
