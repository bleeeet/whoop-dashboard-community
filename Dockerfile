FROM python:3.12-slim
WORKDIR /app
COPY whoop.py report.py explain.py cloud_server.py weekly.py store.py ./
# 平台会注入 PORT（CloudBase 云托管默认 80）；DATA_ROOT 只是本地缓存，持久数据在 CloudBase 数据库。
ENV PYTHONUNBUFFERED=1 DATA_ROOT=/data PORT=8080
EXPOSE 8080
CMD ["python", "cloud_server.py"]
