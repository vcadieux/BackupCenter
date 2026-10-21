FROM python:3.13-alpine
RUN apk add --no-cache openssh-client age tar tzdata && addgroup -g 10001 backup && adduser -D -u 10001 -G backup backup
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py engine.py remote.py maintenance.py maintenance_remote.py i18n.py messages.json ./
COPY public ./public
ENV BC_DATA=/data BC_ARCHIVES=/archives BC_SECURE_COOKIE=1 PYTHONUNBUFFERED=1
USER 10001:10001
CMD ["python", "app.py"]
