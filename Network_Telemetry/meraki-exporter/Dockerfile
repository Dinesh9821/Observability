FROM python:3.12-slim

WORKDIR /app

# The exporter user needs a REAL, WRITABLE home directory.
#
# `useradd -r` alone creates a system account with no home dir, but HOME still
# points at /home/exporter. Meraki SDK 4.x writes cache and state under the
# home directory on some code paths, so those calls fail with
# "[Errno 13] Permission denied: '/home/exporter'" while others succeed --
# which reads like an intermittent API problem rather than a filesystem one.
RUN groupadd -r exporter \
 && useradd -r -g exporter -m -d /home/exporter -s /usr/sbin/nologin exporter \
 && mkdir -p /home/exporter/.cache /home/exporter/.config \
 && chown -R exporter:exporter /home/exporter \
 && chmod -R 700 /home/exporter

ENV HOME=/home/exporter \
    XDG_CACHE_HOME=/home/exporter/.cache \
    XDG_CONFIG_HOME=/home/exporter/.config \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# COPY preserves the SOURCE file's permissions. If exporter.py is 0600 on the
# build host (common after unzipping, or under a restrictive umask), the
# non-root runtime user cannot read its own program and the container
# crash-loops. Setting mode explicitly makes the image independent of host
# permissions.
COPY --chmod=0644 exporter.py .

USER exporter
EXPOSE 9822

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9822/metrics',timeout=4).status==200 else 1)"

CMD ["python", "-u", "exporter.py"]
