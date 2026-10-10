FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# The API is reached through nginx (and Caddy) on private Docker networks. Trust X-Forwarded-For
# only from those hops so request.client is the real visitor; uvicorn takes the right-most
# untrusted address, so a spoofed header from the internet cannot impersonate another client.
ENV FORWARDED_ALLOW_IPS="127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 crm && useradd --uid 10001 --gid crm --create-home crm \
    && mkdir -p /data/files && chown crm:crm /data/files
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY --chown=crm:crm . .
USER crm
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
