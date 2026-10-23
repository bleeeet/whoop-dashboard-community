FROM python:3.12-slim
WORKDIR /app
COPY whoop.py report.py explain.py cloud_server.py weekly.py ./
ENV PYTHONUNBUFFERED=1 DATA_ROOT=/data PORT=8080
EXPOSE 8080
CMD ["python", "cloud_server.py"]
