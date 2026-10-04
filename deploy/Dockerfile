FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt requirements.lock.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY --chown=10001:10001 . .
RUN mkdir -p /app/var && chown -R 10001:10001 /app/var
USER 10001:10001
ENV BI_BIND_HOST=0.0.0.0 BI_PORT=8000 BI_DATA_DIR=/app/var
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('BI_PORT','8000')+'/healthz',timeout=3)"
CMD ["python", "run.py"]
