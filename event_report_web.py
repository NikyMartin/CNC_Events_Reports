#!/usr/bin/env python3
"""Local web interface for create_event_report.py (no web framework needed)."""

import argparse
import ipaddress
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import tempfile
import threading
from collections import OrderedDict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
TLS_DIRECTORY = ROOT / ".event_report_tls"
DEFAULT_HOST = "192.168.2.236"
REPORTS = (
    ("event_report_top15_per_device.html", "Top 15 events per device"),
    ("event_report_top20_network.html", "Top 20 events across the network"),
    ("event_report_top10_devices.html", "Top 10 devices by event count"),
)
STATISTIC = re.compile(r"^(?:Total events|Generic Events|[A-Z][A-Z0-9_]*): \d+(?: \(\d+\.\d{2}%\))?$")
# Pass credentials via stdin, rather than exposing them in the process command.
RUNNER = """
import json, runpy, sys
from pathlib import Path
from urllib.parse import quote
script = sys.argv[1]
connection = json.load(sys.stdin)
sys.argv = [script, connection['cnc_ip'], str(connection['cw_port']),
            connection['username'], quote(connection['password'], safe='')]
namespace = runpy.run_path(script, run_name='cnc_event_report')
namespace['main'](hide_generic_events=connection.get('hide_generic_events', False), report_directory=Path.cwd())
"""


def validate_connection(data):
    if not isinstance(data, dict):
        raise ValueError("Enter the CNC connection details.")
    if any(not isinstance(data.get(key), str) for key in ("cnc_ip", "cw_port", "username", "password")):
        raise ValueError("All four connection fields are required.")
    address = data["cnc_ip"].strip()
    try:
        parsed_ip = ipaddress.ip_address(address)
    except ValueError:
        raise ValueError("CNC IP must be a valid IP address.") from None
    try:
        port = int(data["cw_port"])
    except ValueError:
        raise ValueError("CW_PORT must be a number from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("CW_PORT must be a number from 1 to 65535.")
    username = data["username"].strip()
    if not username or not data["password"]:
        raise ValueError("CNC Username and CNC User Password are required.")
    hide_generic_events = data.get("hide_generic_events", False)
    if not isinstance(hide_generic_events, bool):
        raise ValueError("Hide Generic Events must be selected or unselected.")
    return {
        "cnc_ip": f"[{parsed_ip}]" if parsed_ip.version == 6 else str(parsed_ip),
        "cw_port": str(port),
        "username": username,
        "password": data["password"],
        "hide_generic_events": hide_generic_events,
    }


def run_reports(connection):
    """Keep each run in its own permanent IP-and-time folder."""
    report_root = ROOT / "reports"
    report_root.mkdir(parents=True, exist_ok=True)
    safe_ip = re.sub(r"[^A-Za-z0-9._-]", "-", str(connection.get("cnc_ip", "unknown")).strip("[]"))
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f%z")
    directory = Path(tempfile.mkdtemp(prefix=f"{safe_ip}_{timestamp}_", dir=report_root))
    try:
        result = subprocess.run(
            [sys.executable, "-c", RUNNER, str(ROOT / "create_event_report.py")],
            input=json.dumps(connection), text=True, capture_output=True,
            cwd=directory, timeout=180, check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Report generation timed out. Check CNC connectivity and try again.") from None
    paths = [directory / filename for filename, _ in REPORTS]
    if result.returncode != 0 or not all(path.is_file() for path in paths):
        if "No module named 'requests'" in result.stderr or "No module named 'urllib3'" in result.stderr:
            raise RuntimeError("The backend needs requests and urllib3. Install them with python3 -m pip install requests urllib3.")
        raise RuntimeError("Report generation failed. Check the CNC address, port, credentials, and connectivity.")
    # Only publish statistics, never raw API responses or authentication output.
    statistics = [line.strip() for line in result.stdout.splitlines() if STATISTIC.fullmatch(line.strip())]
    return statistics, {path.name: path.read_bytes() for path in paths}


def ensure_local_certificate(directory=TLS_DIRECTORY, host="127.0.0.1"):
    """Create a self-signed certificate for the server IP, retaining its key."""
    host = str(ipaddress.IPv4Address(host))
    directory = Path(directory)
    name = "localhost" if host == "127.0.0.1" else f"server-{host}"
    cert, key = directory / f"{name}.crt", directory / f"{name}.key"
    if cert.is_file() and key.is_file():
        return cert, key
    if cert.exists() or key.exists():
        raise RuntimeError("Incomplete local certificate files. Supply --cert and --key or restore the missing file.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        temporary = Path(temporary)
        config = temporary / "openssl.cnf"
        config.write_text(
            "[req]\ndistinguished_name=dn\nx509_extensions=extensions\n"
            f"[dn]\n[extensions]\nsubjectAltName=DNS:localhost,IP:127.0.0.1,IP:{host}\n"
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n", encoding="utf-8",
        )
        try:
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
                 "-subj", f"/CN={host}", "-config", str(config),
                 "-out", str(temporary / cert.name), "-keyout", str(temporary / key.name)],
                check=True, capture_output=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError("Could not generate the local HTTPS certificate. Install OpenSSL or supply --cert and --key.") from None
        (temporary / key.name).chmod(0o600)
        (temporary / key.name).replace(key)
        (temporary / cert.name).replace(cert)
    return cert, key


def create_tls_context(cert, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return context


class ReportServer(ThreadingHTTPServer):
    def __init__(self, address, tls_context):
        super().__init__(address, ReportHandler)
        self.tls_context = tls_context
        self.form_token = secrets.token_urlsafe(32)
        self.results = OrderedDict()
        self.results_lock = threading.Lock()

    def process_request_thread(self, request, client_address):
        # Complete TLS in the worker, so a browser preconnection cannot block
        # the listening thread and every subsequent request.
        try:
            request.settimeout(10)
            request = self.tls_context.wrap_socket(request, server_side=True)
            request.settimeout(30)
        except OSError:
            request.close()
            return
        super().process_request_thread(request, client_address)


class ReportHandler(BaseHTTPRequestHandler):
    def respond(self, status, content, content_type="application/json; charset=utf-8"):
        if isinstance(content, dict):
            content = json.dumps(content).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            page = (ROOT / "event_report_web.html").read_text(encoding="utf-8")
            self.respond(200, page.replace("{{FORM_TOKEN}}", self.server.form_token).encode("utf-8"), "text/html; charset=utf-8")
            return
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "reports":
            with self.server.results_lock:
                reports = self.server.results.get(parts[1], {})
                content = reports.get(parts[2])
            if content is not None:
                self.respond(200, content, "text/html; charset=utf-8")
                return
        self.respond(404, {"error": "Page or report not found. Generate reports again if the backend was restarted."})

    def do_POST(self):
        if self.path != "/api/reports":
            self.respond(404, {"error": "Endpoint not found."})
            return
        if not secrets.compare_digest(self.headers.get("X-Form-Token", ""), self.server.form_token):
            self.respond(403, {"error": "Reload this page before generating reports."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384:
                raise ValueError("Invalid request size.")
            connection = validate_connection(json.loads(self.rfile.read(size)))
        except (ValueError, UnicodeDecodeError) as error:
            message = str(error) if not isinstance(error, json.JSONDecodeError) else "Invalid connection details."
            self.respond(400, {"error": message})
            return
        try:
            statistics, reports = run_reports(connection)
        except RuntimeError as error:
            self.respond(502, {"error": str(error)})
            return
        job_id = secrets.token_urlsafe(24)
        with self.server.results_lock:
            self.server.results[job_id] = reports
            # Keep the last 32 runs in memory; each run has its own report URLs.
            while len(self.server.results) > 32:
                self.server.results.popitem(last=False)
        self.respond(200, {
            "statistics": statistics,
            "reports": [{"title": title, "url": f"/reports/{job_id}/{filename}"} for filename, title in REPORTS],
        })

    def log_message(self, format, *args):
        # Do not log request bodies or report access tokens.
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=ipaddress.IPv4Address, default=DEFAULT_HOST,
                        help=f"Local IP to listen on (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=7977, help="Local web server port (default: 7977)")
    parser.add_argument("--cert", type=Path, help="HTTPS certificate PEM file (default: generated certificate for the server IP)")
    parser.add_argument("--key", type=Path, help="HTTPS private key PEM file, used with --cert")
    parser.add_argument("--certificate-ip", type=ipaddress.IPv4Address,
                        default=os.environ.get("CNC_CERTIFICATE_IP"),
                        help="IP to include in the generated certificate (or set CNC_CERTIFICATE_IP)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if bool(args.cert) != bool(args.key):
        parser.error("Provide both --cert and --key")
    try:
        certificate_ip = args.certificate_ip or (args.host if not args.host.is_unspecified else ipaddress.IPv4Address("127.0.0.1"))
        cert, key = (args.cert, args.key) if args.cert else ensure_local_certificate(host=certificate_ip)
        tls_context = create_tls_context(cert, key)
    except (RuntimeError, OSError, ssl.SSLError) as error:
        parser.error(str(error))
    with ReportServer((str(args.host), args.port), tls_context) as server:
        print(f"Listening on {args.host}:{args.port} (HTTPS).", flush=True)
        print(f"Open https://{certificate_ip}:{args.port} to generate CNC event reports.", flush=True)
        if not args.cert:
            print("Using a self-signed local certificate; your browser may ask you to accept it.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nBackend stopped.")


if __name__ == "__main__":
    main()
