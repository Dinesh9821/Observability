FROM python:3.12-slim

WORKDIR /app

# A real, writable home directory. `useradd -r` alone leaves HOME pointing at
# a directory that does not exist, which surfaces later as intermittent
# "Permission denied" on unrelated operations.
RUN groupadd -r exporter \
 && useradd -r -g exporter -m -d /home/exporter -s /usr/sbin/nologin exporter \
 && mkdir -p /home/exporter/.cache \
 && chown -R exporter:exporter /home/exporter \
 && chmod -R 700 /home/exporter

ENV HOME=/home/exporter \
    XDG_CACHE_HOME=/home/exporter/.cache \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# COPY preserves the source file's mode. Setting it explicitly makes the image
# independent of whatever umask built it.
COPY --chmod=0644 vmanage_exporter.py .

USER exporter
EXPOSE 9823

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9823/metrics',timeout=4).status==200 else 1)"

CMD ["python", "-u", "vmanage_exporter.py"]
