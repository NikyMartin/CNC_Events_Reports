FROM ubuntu:24.04

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Rome

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates openssl python3 python3-requests python3-urllib3 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin cnc \
    && mkdir -p /app/.event_report_tls /app/reports \
    && chown -R cnc:cnc /app \
    && chmod 700 /app/.event_report_tls

WORKDIR /app
COPY --chown=cnc:cnc create_event_report.py event_report_web.py event_report_web.html ./

ARG CNC_CERTIFICATE_IP=127.0.0.1
ENV CNC_CERTIFICATE_IP=${CNC_CERTIFICATE_IP}

USER cnc
EXPOSE 7977
VOLUME ["/app/.event_report_tls", "/app/reports"]
STOPSIGNAL SIGINT

ENTRYPOINT ["python3", "event_report_web.py"]
CMD ["--host", "0.0.0.0", "--port", "7977"]
