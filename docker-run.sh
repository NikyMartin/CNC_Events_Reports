#!/bin/bash

docker run -d --name cnc-event-report --restart unless-stopped \
-p 7977:7977 \
-v cnc-event-report-tls:/app/.event_report_tls \
cnc-event-report:v2
