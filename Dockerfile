FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements-server.txt .
RUN pip install -r requirements-server.txt
COPY core ./core
COPY web ./web
COPY server.py manage.py ./
RUN useradd -m app && mkdir -p /app/data && chown -R app /app/data
USER app
ENV QD_HOST=0.0.0.0 PORT=8000 QD_DATA=/app/data QD_REQUIRE_AUTH=1 QD_SECURE_COOKIES=1 QD_TRUST_PROXY=1
EXPOSE 8000
# 2 workers x 4 threads is plenty for a small team; saved project files live on the mounted /app/data volume
CMD ["gunicorn", "-w", "2", "--threads", "4", "-b", "0.0.0.0:8000", "--timeout", "180", "server:app"]
