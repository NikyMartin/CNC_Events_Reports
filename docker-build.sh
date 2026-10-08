#!/bin/bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

find_network_command() {
    command -v "$1" 2>/dev/null || {
        for location in /usr/sbin /sbin /usr/bin /bin; do
            if [[ -x "$location/$1" ]]; then
                printf '%s\n' "$location/$1"
                return 0
            fi
        done
        return 1
    }
}

# An explicit IP works even on machines with no network inspection commands.
local_ip="${CNC_CERTIFICATE_IP:-}"
if [[ -z "$local_ip" ]]; then
case "$(uname -s)" in
    Darwin)
        # Prefer an active LAN adapter over VPN tunnels and Docker bridges.
        ifconfig_command="$(command -v ifconfig || printf '%s' /sbin/ifconfig)"
        local_ip="$("$ifconfig_command" | awk '
            /^[^[:space:]]/ { lan = ($1 ~ /^en[0-9]+:/); address = "" }
            lan && $1 == "inet" && $2 !~ /^(127\.|169\.254\.)/ { address = $2 }
            lan && $1 == "status:" && $2 == "active" && address != "" {
                print address; exit
            }
        ')"
        ;;
    Linux)
        ip_command="$(find_network_command ip || true)"
        if [[ -n "$ip_command" ]]; then
            network_interface="$("$ip_command" -4 route show default 2>/dev/null | awk '
                { for (i = 1; i < NF; i++)
                    if ($i == "dev" && $(i + 1) !~ /^(tun|tap|wg|utun|docker|veth|br-)/) {
                        print $(i + 1); exit
                    }
                }
            ' || true)"
            if [[ -n "$network_interface" ]]; then
                local_ip="$("$ip_command" -o -4 addr show dev "$network_interface" scope global 2>/dev/null | awk '
                    $3 == "inet" && $4 !~ /^(127\.|169\.254\.)/ {
                        split($4, address, "/"); print address[1]; exit
                    }
                ' || true)"
            else
                local_ip="$("$ip_command" -o -4 addr show scope global 2>/dev/null | awk '
                    $2 !~ /^(lo|tun|tap|wg|utun|docker|veth|br-)/ && $3 == "inet" && $4 !~ /^(127\.|169\.254\.)/ {
                        split($4, address, "/"); print address[1]; exit
                    }
                ' || true)"
            fi
        fi
        if [[ -z "$local_ip" ]]; then
            ifconfig_command="$(find_network_command ifconfig || true)"
            if [[ -n "$ifconfig_command" ]]; then
                local_ip="$("$ifconfig_command" 2>/dev/null | awk '
                    /^[^[:space:]]/ {
                        interface = $1; sub(/:$/, "", interface)
                        lan = (interface !~ /^(lo|tun|tap|wg|utun|docker|veth|br-)/)
                    }
                    lan && $1 == "inet" {
                        address = $2; sub(/^addr:/, "", address)
                        if (address !~ /^(127\.|169\.254\.)/) {
                            print address; exit
                        }
                    }
                ' || true)"
            fi
        fi
        if [[ -z "$local_ip" ]]; then
            hostname_command="$(find_network_command hostname || true)"
            if [[ -n "$hostname_command" ]]; then
                local_ip="$("$hostname_command" -I 2>/dev/null | awk '
                    { for (i = 1; i <= NF; i++)
                        if ($i ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ && $i !~ /^(127\.|169\.254\.)/) {
                            print $i; exit
                        }
                    }
                ' || true)"
            fi
        fi
        ;;
    *)
        echo "Automatic local IP detection supports macOS and Linux." >&2
        exit 1
        ;;
esac
fi

if [[ -z "$local_ip" ]]; then
    echo "Cannot detect a LAN IPv4 address. Set it explicitly: CNC_CERTIFICATE_IP=<local-IP> ./docker-build.sh" >&2
    exit 1
fi

if ! awk -v address="$local_ip" 'BEGIN {
    if (address !~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) exit 1
    split(address, octets, ".")
    for (i = 1; i <= 4; i++) if (octets[i] > 255) exit 1
}' </dev/null; then
    echo "Invalid local IPv4 address: $local_ip" >&2
    exit 1
fi

echo "Building cnc-event-report:v1 with certificate IP: $local_ip"
docker build --build-arg "CNC_CERTIFICATE_IP=$local_ip" -t cnc-event-report:v2 "$script_dir"
