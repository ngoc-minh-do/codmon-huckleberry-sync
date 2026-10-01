FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Tokyo

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

VOLUME ["/data"]
ENV DATA_DIR=/data

ENTRYPOINT ["python", "-m", "codmon_sync"]
CMD ["sync"]