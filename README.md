# CNC Event Reports Web Page

Generate HTML summaries of Cisco Crosswork Network Controller (CNC) device events from a browser or the command line. The web page runs `create_event_report.py` and provides statistics and links to three reports.

## Installation requirements

For Docker, use a macOS or Linux host with Docker installed and its daemon running. The Docker image supplies Ubuntu 24.04, Python, OpenSSL, `requests`, and `urllib3`.

For a local Python installation, use Python 3.10 or later, OpenSSL, and the Python packages `requests` and `urllib3`.

In either case, the machine running the backend must be able to reach the CNC IP and API port. Use a CNC account with access to device events. The original reporting script notes testing against CNC release 7.2.

## Starting the web service

### Option 1: Docker container

Run the following commands from the directory containing the application files and `Dockerfile`.

Build the image:

```bash
chmod +x docker-build.sh
./docker-build.sh
```

The build script detects the host's local IPv4 address and passes it as `CNC_CERTIFICATE_IP`. On macOS, it selects an active LAN adapter. On Linux, it tries `ip`, then `ifconfig`, then `hostname -I`. Installing `iproute2` is not required.

If automatic detection fails or selects the wrong interface, supply the IP explicitly:

```bash
CNC_CERTIFICATE_IP=192.168.2.236 ./docker-build.sh
```

Replace the example address with the IP of the **Docker host**, not the CNC server being queried. This address is included in the generated HTTPS certificate.

The current build script creates `cnc-event-report:v2`. Its printed status message still mentions `v1`, and the supplied `docker-run.sh` currently starts `v1`. Use the following command to start the current image with persistent report storage:

```bash
docker run -d --name cnc-event-report --restart unless-stopped \
  -p 7977:7977 \
  -v cnc-event-report-tls:/app/.event_report_tls \
  -v cnc-event-report-reports:/app/reports \
  cnc-event-report:v2
```

The container runs as user `cnc` (UID `10001`) and listens on `0.0.0.0:7977`. Docker publishes the port so other devices can connect through the host's network IP. The container restarts automatically unless explicitly stopped. Report timestamps use the `Europe/Rome` timezone.

To build without the helper script:

```bash
docker build --build-arg CNC_CERTIFICATE_IP=192.168.2.236 \
  -t cnc-event-report:v2 .
```

To change the certificate IP without rebuilding, add `-e CNC_CERTIFICATE_IP=NEW_HOST_IP` before the image name in `docker run`. If no IP is supplied at build time or runtime, the certificate IP defaults to `127.0.0.1`.

### Option 2: Local host with Python

Create a Python environment and install the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install requests urllib3
```

OpenSSL must also be available on the host. Start the HTTPS service with your host's actual IP:

```bash
WEB_HOST_IP=192.168.2.236
python3 event_report_web.py \
  --host 0.0.0.0 \
  --port 7977 \
  --certificate-ip "$WEB_HOST_IP"
```

Keep the terminal open while the service runs. Press **Ctrl+C** to stop it. The application directory must be writable so the backend can create certificates, report folders, and `executions.log`.

Always specify `--host` on a different machine: the Python server's current default is the original development host's IP, `192.168.2.236`.

## Opening the web page

After starting either deployment, open:

```text
https://HOST_IP:7977/
```

Use the real IP address of the host running the service. `0.0.0.0` is a listening address, not a browser destination. The service uses HTTPS; entering `http://` can produce a connection reset.

The backend creates a one-year self-signed certificate when no certificate is supplied. Browsers may show a certificate warning because it is not issued by a trusted certificate authority. Certificates and private keys are stored under `.event_report_tls/`, or `/app/.event_report_tls/` in Docker.

To use your own certificate with the local server:

```bash
python3 event_report_web.py --host 0.0.0.0 --port 7977 \
  --cert /path/to/server.crt \
  --key /path/to/server.key
```

For Docker, mount a certificate directory and override the startup arguments:

```bash
docker run -d --name cnc-event-report --restart unless-stopped \
  -p 7977:7977 \
  -v /absolute/path/to/certificates:/certificates:ro \
  -v cnc-event-report-reports:/app/reports \
  cnc-event-report:v2 --host 0.0.0.0 --port 7977 \
    --cert /certificates/server.crt --key /certificates/server.key
```

The certificate must cover the IP or hostname used by clients, and both files must be readable by container user UID `10001`.

## Purpose

The application retrieves device events from the CNC API:

```text
/crosswork/platform/alarms/v1/events/
```

It summarizes repeated event descriptions, their severity, the devices generating them, and their delivery mechanisms. Each execution produces the following reports:

| Report | Contents | File |
| --- | --- | --- |
| Top 15 events per device | Up to 15 most frequent descriptions for each device, with severity and counts. Devices are ordered by total event count, highest first. | `event_report_top15_per_device.html` |
| Top 20 events across the network | Up to 20 most frequent descriptions across all retrieved devices, with severity and counts. | `event_report_top20_network.html` |
| Top 10 devices | Up to 10 devices with the highest total event counts, including device name and IP. | `event_report_top10_devices.html` |

All three reports exclude events with `CLEARED` severity. Frequency counts group events by their full description. If one description occurs with multiple severities, the severity column shows the count for each severity. Descriptions are displayed in full.

The API request retrieves at most **4,000 device events**, ordered by notification timestamp descending. Reports summarize that returned set rather than the complete event history.

## User interface

Enter the connection details:

| Input | Meaning |
| --- | --- |
| CNC IP | Address of the CNC server to query. |
| CW_PORT | CNC API port; prefilled with `30603` and editable. |
| CNC Username | CNC account used for the request. |
| CNC User Password | Password for that account. |

Select **Generate reports**. After a successful request, the page shows statistics and links that open the three HTML reports in separate tabs. Web report generation has a three-minute timeout.

### Generic Events

The **Hide Generic Events in all three reports** checkbox controls filtering for the next execution:

- Selected: descriptions containing `Generic Event` are excluded from all three reports before counts and rankings are calculated.
- Unselected: matching events remain included. In the first two reports, their entire rows have a dark yellow background, explained by a legend at the top right.

Matching is case-sensitive. The substring `Generic Event` matches both `Generic Event` and `Generic Events`. Changing the checkbox does not modify reports that have already been generated; run the request again.

### Statistics

The terminal and web page display total events, the Generic Events count and percentage, and counts and percentages for each delivery mechanism. Common mechanisms are ordered as `SNMP_TRAP`, `SYSLOG`, and `SYNTHETIC_EVENT`, with a separator after Generic Events.

Percentages use the total retrieved event count. Statistics include all retrieved events, including `CLEARED` events and Generic Events, regardless of the report filter. Generic Events can belong to any mechanism, so their percentage overlaps the mechanism percentages.

## Report folders and execution log

Each execution saves its three reports in a dedicated folder:

```text
reports/<CNC-IP>_<YYYYMMDD_HHMMSS_microseconds_timezone>_<unique-suffix>/
```

The unique suffix prevents collisions even when several users query the same CNC server at the same time. Reports from previous runs remain saved.

For a local installation, `reports/` is beside the Python scripts. In Docker, it is `/app/reports/`. The named volume `cnc-event-report-reports` preserves these folders when a container is replaced.

Web links are retained in memory for the most recent **32 runs** and expire when the backend restarts. Expired links do not delete saved report folders. To copy the saved reports from Docker:

```bash
docker cp cnc-event-report:/app/reports ./saved-reports
```

Every invocation with the required script arguments appends its start timestamp, CNC server IP, and username to `executions.log`, including failed connection attempts. Entries are JSON objects, one per line:

```json
{"execution_time": "2026-10-08T10:30:00+02:00", "server_ip": "10.10.10.10", "username": "operator"}
```

The log is beside the script, or `/app/executions.log` in Docker. It is separate from the report and certificate volumes: it survives a container restart but not container removal. Copy it out before replacing a container if you need to retain its history:

```bash
docker cp cnc-event-report:/app/executions.log ./executions.log
```

The execution log does not record passwords. The web backend passes connection details to its child process through standard input, and the form clears the password after a request.

## Running the reporting script directly

The script can also run without the web service:

```bash
python3 create_event_report.py CNC_IP CW_PORT CNC_USERNAME 'CNC_PASSWORD'
```

It prints statistics and the generated report paths. It does not print an event table and does not require a `truncate` or `no-truncate` argument. Direct command-line runs include Generic Events by default.

## Managing the Docker service

View startup output:

```bash
docker logs -f cnc-event-report
```

Stop the service:

```bash
docker stop cnc-event-report
```
