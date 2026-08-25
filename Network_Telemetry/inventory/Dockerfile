FROM python:3.12-slim

WORKDIR /app

RUN groupadd -r exporter \
 && useradd -r -g exporter -m -d /home/exporter -s /usr/sbin/nologin exporter \
 && mkdir -p /var/lib/network-telemetry/inventory \
 && chown -R exporter:exporter /home/exporter /var/lib/network-telemetry

ENV PYTHONDONTWRITEBYTECODE=1

COPY inventory/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chmod=0644 inventory/inventory_lib.py inventory/inventory_exporter.py ./

USER exporter
EXPOSE 9824 9825

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9824/metrics',timeout=4).status==200 else 1)"

CMD ["python", "-u", "inventory_exporter.py"]
