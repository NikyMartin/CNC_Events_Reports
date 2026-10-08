#!/usr/bin/python
#
# Abstract:
# CNC UI provides details on the mechanism used to generate the alarm 
# (either TRAP, SYSLOG, gNMI or SYNTHETIC_EVENT) for alarm only
# Currently no way to get same detail for the events
# This script aims to fill this gap
# Used API is coming from INFRA API set
# /crosswork/platform/alarms/v1/events/
# and returns events for all devices
# Event descriptions are displayed in full.
#
# Generates three HTML frequency reports, excluding CLEARED events.
#
# Syntax to be used: 
# python <SCRIPT_NAME> <CNC IP> <CNC_port> <CNC Username> <CNC user Password>
# __________________________________________
# Versioning and updates:
#
# Oct 1st 2026
# First Release
# Tested on CNC rel 7.2

import ssl
import sys
import urllib
import requests
import urllib3
import json
import re
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# websocket.enableTrace(True)

ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_OPTIONAL  # WARNING: disables security!

headers = {
    'Accept': 'text/plain',
    'Cache-Control': 'no-cache',
    'Content-Type': 'application/x-www-form-urlencoded',
}


def log_execution(server_ip, username, log_file=None):
    """Append the start time, CNC IP and username, including failed attempts."""
    path = Path(log_file) if log_file is not None else Path(__file__).resolve().with_name("executions.log")
    entry = {
        "execution_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "server_ip": str(server_ip),
        "username": str(username),
    }
    # Keep the log beside the script so web runs survive temporary-report cleanup.
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\n")


def create_report_directory(server_ip, report_root=None):
    """Reserve a unique IP-and-time folder, including for simultaneous runs."""
    root = Path(report_root) if report_root is not None else Path(__file__).resolve().parent / "reports"
    root.mkdir(parents=True, exist_ok=True)
    safe_ip = re.sub(r"[^A-Za-z0-9._-]", "-", str(server_ip).strip("[]"))
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f%z")
    return Path(tempfile.mkdtemp(prefix=f"{safe_ip}_{timestamp}_", dir=root))


def get_ticket():
    print("Executing GET Ticket")
    params = (
        ('username', username),
        ('password', password),
    )
    url = base_url + "/crosswork/sso/v1/tickets"
    response = requests.post(url, headers=headers, params=params, verify=False)
    if response.status_code in [200,201]:
        return response.text
    else:
        print("Status Code: ", response.status_code)
        print(response.text)
        exit()

######################################
# Following function returns CW JWT
######################################

def get_token():
    print("Executing GET Token")
    ticket = get_ticket()
    params = (
        ('service', 'https://'+server_ip+':30701/app-dashboard'),
    )
    url = base_url + "/crosswork/sso/v1/tickets/"+ticket
    response = requests.post(url, headers=headers, params=params, verify=False)
    return ticket, response.text

######################################
# Function to translate from EPOC time
######################################

def convert_epoch_to_readable(epoch_ms):
    # Convert milliseconds to seconds
    seconds = int(epoch_ms) / 1000.0
    # Convert to readable format
    return datetime.fromtimestamp(seconds).strftime('%d-%m-%Y %H:%M:%S')


######################################
# Following function deletes CW Ticket
######################################

def delete_ticket(ticket, token):
    print("\nExecuting delete Ticket")
    url = base_url + "/crosswork/sso/v1/tickets/"+ticket
    del_headers = {
        'Content-Type': 'application/json',
        'Authorization': token,
    }
    try:
        response = requests.delete(url, headers=del_headers, verify=False)
        print("Status Code: ", response.status_code)
    except Exception as e:
        print(str(e))
        print("Cannot run DELETE "+url)
        exit()

    exit()

######################################
# Following function performs GET
# request on CW API
######################################

def run_get(url, token):
    
    print("Executing GET", url)
    
    auth_headers = {
        'accept': 'application/json',
        'Authorization': token,
        'Range': 'items=0-3999',
    }
    
    try:
        # time.sleep(0.01)
        response = requests.get(url, headers=auth_headers, verify=False)
        if 200 <= response.status_code < 300:
            return response.text
        print("\n\nStatus Code: ", response.status_code, "\n")
        print(response.text)
        print("Could not execute GET "+url)
        print("\nIf Status Code is 404, it can be common inventory cAPP is not installed\n")
        exit(1)
    except Exception as e:
        print(str(e))
        print("Cannot run GET "+url)
        exit(1)

    return response.text

######################################
# Get Events
######################################

def get_events(url, token):
    extracted_data = []
    data = json.loads(run_get(url, token))
    for event in data:
        extracted_data.append({
                "Device Name": event.get('displayName'),
                "Device IP": event.get('reportingEntityAddress') or event.get('source'),
                "Severity": event.get('severity'),
                "Time Stamp": event.get('notificationTimestamp'),
                "Mechanism": event.get('notificationDeliveryMechanism'),
                "Description": event.get('description')
            })
    return extracted_data


def print_event_statistics(events):
    if not events:
        print("No events found")

    mechanism_counts = Counter(
        str(event.get("Mechanism") or "UNKNOWN") for event in events
    )
    generic_event_count = sum(
        "Generic Event" in str(event.get("Description") or "")
        for event in events
    )
    total = len(events)
    def percentage(count):
        return 100 * count / total if total else 0.0

    print(f"\nTotal events: {total}")
    print(f"Generic Events: {generic_event_count} ({percentage(generic_event_count):.2f}%)")
    print("-" * 40)
    mechanism_order = {"SNMP_TRAP": 0, "SYSLOG": 1, "SYNTHETIC_EVENT": 2}
    for mechanism, count in sorted(
        mechanism_counts.items(),
        key=lambda item: (mechanism_order.get(item[0], 3), item[0]),
    ):
        print(f"{mechanism}: {count} ({percentage(count):.2f}%)")


def generate_html_reports(events, output_dir=".", hide_generic_events=False):
    """Count full descriptions, then write the three non-cleared reports."""
    included_events = [
        event for event in events
        if str(event.get("Severity") or "").strip().upper() != "CLEARED"
        and not (hide_generic_events and "Generic Event" in str(event.get("Description") or ""))
    ]
    per_device = defaultdict(Counter)
    network_counts = Counter()
    per_device_severities = defaultdict(lambda: defaultdict(Counter))
    network_severities = defaultdict(Counter)
    device_counts = Counter()
    for event in included_events:
        device = (
            str(event.get("Device Name") or "Unknown device"),
            str(event.get("Device IP") or ""),
        )
        description = str(event.get("Description") or "")
        severity = str(event.get("Severity") or "UNKNOWN").strip().upper() or "UNKNOWN"
        per_device[device][description] += 1
        network_counts[description] += 1
        per_device_severities[device][description][severity] += 1
        network_severities[description][severity] += 1
        device_counts[device] += 1

    def ranked(counter, limit):
        # Stable tie ordering makes repeated runs easy to compare.
        return sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]

    def display_severities(counts):
        if len(counts) == 1:
            return next(iter(counts))
        return ", ".join(
            f"{severity} ({count})" for severity, count in sorted(counts.items())
        )

    def table(columns, rows):
        heading = "".join(f"<th scope='col'>{escape(column)}</th>" for column in columns)
        description_index = columns.index("Event description") if "Event description" in columns else None
        body = "".join(
            ("<tr class='generic-event'>" if description_index is not None
             and "Generic Event" in str(row[description_index]) else "<tr>")
            + "".join(
                f"<td>{escape(str(value))}</td>"
                for value in row
            ) + "</tr>"
            for row in rows
        )
        if not body:
            body = f"<tr><td colspan='{len(columns)}'>No events found.</td></tr>"
        return f"<table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table>"

    generated_at = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    generic_note = "Generic Events are excluded. " if hide_generic_events else ""
    report_note = (
        f"<p>Generated: {generated_at}. Events counted: {len(included_events)}. "
        f"Events with CLEARED severity are excluded. {generic_note}Repeated events are grouped "
        "by their full description. When a description has multiple severities, "
        "each severity's count is shown in parentheses. "
        "Counts cover events returned by the API.</p>"
    )
    device_sections = []
    for device in sorted(
        per_device,
        key=lambda item: (-device_counts[item], item[0].casefold(), item[1]),
    ):
        name, address = device
        device_sections.append(
            f"<h2>{escape(name)} ({escape(address)})</h2>"
            f"<p>Total events: {device_counts[device]}</p>"
            + table(("Rank", "Severity", "Event description", "Count"), (
                (rank, display_severities(per_device_severities[device][description]),
                 description, count)
                for rank, (description, count) in enumerate(ranked(per_device[device], 15), 1)
            ))
        )

    reports = (
        ("event_report_top15_per_device.html", "Top 15 frequent events per device",
         "".join(device_sections) or "<p>No events found.</p>"),
        ("event_report_top20_network.html", "Top 20 frequent events across the network",
         table(("Rank", "Severity", "Event description", "Count"), (
             (rank, display_severities(network_severities[description]),
              description, count)
             for rank, (description, count) in enumerate(ranked(network_counts, 20), 1)
         ))),
        ("event_report_top10_devices.html", "Top 10 devices by event count",
         table(("Rank", "Device name", "Device IP", "Count"), (
             (rank, device[0], device[1], count)
             for rank, (device, count) in enumerate(ranked(device_counts, 10), 1)
         ))),
    )
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    paths = []
    for filename, title, content in reports:
        legend = ""
        if not hide_generic_events and filename != "event_report_top10_devices.html":
            legend = (
                "<aside class='legend' aria-label='Legend'><strong>Legend</strong>"
                "<div class='legend-item'><span class='legend-swatch' aria-hidden='true'></span>"
                "Dark yellow rows indicate Generic Events.</div></aside>"
            )
        document = (
            "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{escape(title)}</title>"
            "<style>body{font-family:Arial,sans-serif;margin:2rem;color:#222}"
            "table{border-collapse:collapse;width:100%;margin-bottom:2rem}"
            "th,td{border:1px solid #ccc;padding:.6rem;text-align:left;overflow-wrap:anywhere}"
            "th{background:#eef2f6}tbody tr:nth-child(even){background:#f8f9fa}"
            "tbody tr.generic-event,tbody tr.generic-event td{background:#d4ac0d}"
            ".report-header{display:flex;align-items:flex-start;gap:1.5rem;flex-wrap:wrap}"
            ".report-header h1{margin:0;flex:1}"
            ".legend{margin-left:auto;border:1px solid #ccc;border-radius:6px;padding:.75rem;font-size:.9rem}"
            ".legend-item{display:flex;align-items:center;gap:.5rem;margin-top:.5rem}"
            ".legend-swatch{display:inline-block;width:1.1rem;height:1.1rem;background:#d4ac0d;border:1px solid #947809;flex-shrink:0}"
            "h2{margin-top:2rem}</style></head><body>"
            f"<header class='report-header'><h1>{escape(title)}</h1>{legend}</header>"
            f"{report_note}{content}</body></html>"
        )
        path = output_path / filename
        path.write_text(document, encoding="utf-8")
        paths.append(path)
    return paths

######################################
# MAIN
######################################

def main(hide_generic_events=False, report_directory=None):
    global server_ip, cw_port_string, username, password, base_url
    if len(sys.argv) != 5:
       print('\nMust pass CNC IP, CW_PORT, CNC Username, and CNC User Password\n')
       exit()

    scripts, server_ip, cw_port_string, username, password = sys.argv
    log_execution(server_ip, username)

    password = urllib.parse.unquote(password)

    base_url = "https://" + server_ip + ":" + cw_port_string
    event_url = base_url + "/crosswork/platform/alarms/v1/events/?type=device&_COND=and&enableCache=true&_SORT=notificationTimestamp.DESC"

    ticket, token = get_token()

    try:
        events = get_events(event_url, token)
        print_event_statistics(events)
        output_directory = Path(report_directory) if report_directory is not None else create_report_directory(server_ip)
        for report_path in generate_html_reports(events, output_dir=output_directory, hide_generic_events=hide_generic_events):
            print(f"HTML report generated: {report_path.resolve()}")

    except KeyboardInterrupt:
        print("Script stopped by user.")

    delete_ticket(ticket, token)


if __name__ == "__main__":
    main()
