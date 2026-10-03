# The homelab's scheduled jobs. Python for the collector and doctor; bash and curl
# for backup-vault.sh, which is shipped as-is rather than reimplemented so there
# is one backup script, not two. The NAS host has no python and no cron, which is
# the reason this is a container at all.
FROM python:3.13-alpine
RUN apk add --no-cache bash curl
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY app/ /app/
CMD ["python3", "/app/nas-jobs/scheduler.py"]
