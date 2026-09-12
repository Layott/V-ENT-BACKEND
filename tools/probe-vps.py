#!/usr/bin/env python3
"""Measure what this machine can actually reach, and say WHY when it cannot.

Run it:

    python V-ENT-BACKEND/tools/probe-vps.py
    python V-ENT-BACKEND/tools/probe-vps.py --self-test
    python V-ENT-BACKEND/tools/probe-vps.py --host 162.35.101.16 --json

## Why this exists

On 7 September the VPS was declared unreachable and two pieces of work were
abandoned as blocked on it. The measurement behind that verdict was:

    a direct TCP connect to 138.68.126.199 on 22, 80 AND 443 all time out

which was true, and irrelevant. `138.68.126.199` is the `evotv` host in
`~/.ssh/config`, a different project on a different provider. The V-ENT box is
`162.35.101.16`, which is what `v-ent.co` resolves to and what every SSH line
in this repo has always used. A whole day of work was written off against an
address that was never the box.

So this file does two things the ad-hoc probing did not:

1. **It resolves the name and probes what the name points AT**, then reports the
   literal address beside every result. An address quoted from a config file is
   a guess; an address that came back from DNS this minute is a measurement.

2. **It distinguishes a timeout from a refusal**, because they mean opposite
   things and the fix is different for each:

   | Result | What it means | What to do |
   |---|---|---|
   | `refused` | The host is up and answered. Nothing is listening on that port | Start the service, or use the right port |
   | `timeout` | Packets went out and nothing came back at all | A firewall is dropping them, or the address is not a live host |
   | `open` | A TCP connection was established | Nothing |
   | `dns-failed` | The name does not resolve | Fix DNS before reading anything else here |

   A refusal is a conversation. A timeout is silence, and silence from the wrong
   address looks exactly like silence from a box that is down. That confusion is
   the entire reason this file is in the repo.

Every line carries the seconds it took, because 2.0s and 0.02s to the same port
are different findings even when both say `open`.
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import sys
import time
from urllib import error as urlerror
from urllib import request as urlrequest

# The V-ENT production box. Confirmed by `nslookup v-ent.co` rather than quoted
# from a note, and cross-checked against every `ssh` line in this repo, all of
# which use vent@162.35.101.16 with ~/.ssh/vent_vps.
DEFAULT_HOST = "162.35.101.16"

# The address the 7 September probe used, kept here deliberately. It is the
# `evotv` host, NOT V-ENT. Probing it alongside the real one is what makes the
# report show the mistake instead of merely not repeating it.
WRONG_HOST = "138.68.126.199"

DEFAULT_PORTS = [22, 80, 443]
DEFAULT_NAMES = ["v-ent.co", "api.v-ent.co"]
DEFAULT_URLS = ["https://v-ent.co/", "https://api.v-ent.co/ranking/"]

TIMEOUT = 8.0


def resolve(name: str) -> dict:
    """Turn a name into addresses, timed. A name that does not resolve is its
    own finding, and reading a port result without it is reading noise."""
    started = time.monotonic()
    try:
        infos = socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return {
            "name": name,
            "result": "dns-failed",
            "seconds": round(time.monotonic() - started, 3),
            "detail": str(exc),
            "addresses": [],
        }
    addresses = sorted({info[4][0] for info in infos})
    return {
        "name": name,
        "result": "resolved",
        "seconds": round(time.monotonic() - started, 3),
        "detail": ", ".join(addresses),
        "addresses": addresses,
    }


def probe_tcp(host: str, port: int, timeout: float = TIMEOUT) -> dict:
    """One TCP connect, and an honest name for what came back.

    The distinction this function exists for lives in the except clauses:
    `socket.timeout` means nothing answered, `ConnectionRefusedError` means
    something did. Catching both as "failed" is what made a wrong IP address
    look like a firewalled box.
    """
    started = time.monotonic()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
    except socket.timeout:
        return {
            "host": host,
            "port": port,
            "result": "timeout",
            "seconds": round(time.monotonic() - started, 3),
            "detail": f"nothing answered within {timeout:g}s: dropped by a firewall, or not a live host",
        }
    except ConnectionRefusedError as exc:
        return {
            "host": host,
            "port": port,
            "result": "refused",
            "seconds": round(time.monotonic() - started, 3),
            "detail": f"the host answered and refused: nothing is listening ({exc.errno})",
        }
    except OSError as exc:
        # Unreachable networks and host-unreachable ICMP land here. Named
        # separately because "no route" is a local problem and a timeout is not.
        return {
            "host": host,
            "port": port,
            "result": "error",
            "seconds": round(time.monotonic() - started, 3),
            "detail": f"{type(exc).__name__}: {exc}",
        }
    else:
        banner = ""
        if port == 22:
            # SSH speaks first. The banner names the daemon, which proves the
            # thing listening is an SSH server rather than something else on 22.
            try:
                sock.settimeout(3.0)
                banner = sock.recv(256).decode("utf-8", "replace").strip()
            except OSError:
                banner = ""
        return {
            "host": host,
            "port": port,
            "result": "open",
            "seconds": round(time.monotonic() - started, 3),
            "detail": banner or "connected",
        }
    finally:
        try:
            sock.close()
        except OSError:
            pass


def probe_https(url: str, timeout: float = TIMEOUT) -> dict:
    """Fetch a URL and report the status code. A 4xx or 5xx is still an answer,
    so it is recorded as one rather than as a failure."""
    started = time.monotonic()
    req = urlrequest.Request(url, headers={"User-Agent": "vent-probe/1.0"})
    ctx = ssl.create_default_context()
    try:
        with urlrequest.urlopen(req, timeout=timeout, context=ctx) as res:
            body = res.read(2048)
            return {
                "url": url,
                "result": "answered",
                "status": res.status,
                "seconds": round(time.monotonic() - started, 3),
                "detail": f"{len(body)} bytes read, server={res.headers.get('Server', '?')}",
            }
    except urlerror.HTTPError as exc:
        # An HTTP error is an ANSWER. The server was reached and had an opinion.
        return {
            "url": url,
            "result": "answered",
            "status": exc.code,
            "seconds": round(time.monotonic() - started, 3),
            "detail": f"HTTP {exc.code} {exc.reason}",
        }
    except urlerror.URLError as exc:
        reason = exc.reason
        kind = "timeout" if isinstance(reason, socket.timeout) else "unreachable"
        return {
            "url": url,
            "result": kind,
            "status": None,
            "seconds": round(time.monotonic() - started, 3),
            "detail": str(reason),
        }
    except OSError as exc:
        return {
            "url": url,
            "result": "unreachable",
            "status": None,
            "seconds": round(time.monotonic() - started, 3),
            "detail": f"{type(exc).__name__}: {exc}",
        }


def run(hosts: list[str], ports: list[int], names: list[str], urls: list[str]) -> dict:
    report = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dns": [resolve(name) for name in names],
        "tcp": [],
        "https": [probe_https(url) for url in urls],
    }
    for host in hosts:
        for port in ports:
            report["tcp"].append(probe_tcp(host, port))
    return report


def render(report: dict) -> str:
    lines = [f"VPS probe, {report['when']}", ""]

    lines.append("DNS")
    for row in report["dns"]:
        lines.append(f"  {row['name']:<24} {row['result']:<12} {row['seconds']:>6.3f}s  {row['detail']}")

    lines.append("")
    lines.append("TCP")
    for row in report["tcp"]:
        where = f"{row['host']}:{row['port']}"
        lines.append(f"  {where:<24} {row['result']:<12} {row['seconds']:>6.3f}s  {row['detail']}")

    lines.append("")
    lines.append("HTTPS")
    for row in report["https"]:
        status = str(row["status"]) if row["status"] is not None else "-"
        lines.append(f"  {row['url']:<38} {row['result']:<12} {status:<5} {row['seconds']:>6.3f}s  {row['detail']}")

    # The reading, spelled out. A table of results that nobody interprets is how
    # the wrong address survived: every number in it was correct.
    lines.append("")
    lines.append("Reading")
    resolved = {a for row in report["dns"] for a in row.get("addresses", [])}
    probed = {row["host"] for row in report["tcp"]}
    for host in sorted(probed):
        results = {row["port"]: row["result"] for row in report["tcp"] if row["host"] == host}
        opened = sorted(p for p, r in results.items() if r == "open")
        belongs = "serves v-ent.co" if host in resolved else "NOT an address v-ent.co resolves to"
        if opened:
            lines.append(f"  {host}: open on {', '.join(str(p) for p in opened)} ({belongs})")
        elif set(results.values()) == {"timeout"}:
            lines.append(f"  {host}: every port timed out ({belongs})")
        else:
            lines.append(f"  {host}: {results} ({belongs})")
    return "\n".join(lines)


# ------------------------------------------------------------------ self-test
#
# The point of a self-test on a probe is narrow and worth being clear about: it
# cannot prove the network is a given shape, because the network is the thing
# being measured. What it CAN prove is that each outcome is classified
# correctly, which is the part that was wrong in September. So it builds each
# condition locally and checks the name that comes back.


def self_test() -> int:
    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, wanted {want!r}")
        else:
            print(f"  ok   {label}: {got}")

    # 1. A refusal. Bind a socket, learn the port, close it. Nothing is
    #    listening on that port now but localhost is definitely up, so a connect
    #    gets an RST rather than silence.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()
    check("closed port on a live host is 'refused'",
          probe_tcp("127.0.0.1", dead_port, timeout=3.0)["result"], "refused")

    # 2. An open port. A listening socket we control.
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    live_port = server.getsockname()[1]
    try:
        check("a listening port is 'open'",
              probe_tcp("127.0.0.1", live_port, timeout=3.0)["result"], "open")
    finally:
        server.close()

    # 3. A timeout. 192.0.2.0/24 is TEST-NET-1 (RFC 5737): reserved for
    #    documentation, routed nowhere, so packets vanish. That is exactly the
    #    silence a firewall drop produces, which is the case being distinguished.
    started = time.monotonic()
    result = probe_tcp("192.0.2.1", 22, timeout=2.0)
    elapsed = time.monotonic() - started
    check("a black-holed address is 'timeout'", result["result"], "timeout")
    if elapsed < 1.5:
        failures.append(f"timeout returned after {elapsed:.2f}s, so it did not actually wait")
    else:
        print(f"  ok   the timeout waited {elapsed:.2f}s before giving up")

    # 4. A name that cannot resolve.
    check("an unresolvable name is 'dns-failed'",
          resolve("no-such-host.v-ent-probe.invalid")["result"], "dns-failed")

    # 5. The distinction that matters, stated as an assertion rather than as
    #    prose: refused and timeout must never collapse into one name.
    refused = probe_tcp("127.0.0.1", dead_port, timeout=3.0)["result"]
    timed_out = probe_tcp("192.0.2.1", 22, timeout=2.0)["result"]
    check("refused and timeout are different words", refused != timed_out, True)

    print()
    if failures:
        for line in failures:
            print(f"  FAIL {line}")
        print(f"\n{len(failures)} failed")
        return 1
    print("self-test: all passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", action="append", default=None,
                        help="address to probe. Repeatable. Defaults to the V-ENT box and, "
                             "for contrast, the wrong address the September probe used")
    parser.add_argument("--port", action="append", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    hosts = args.host or [DEFAULT_HOST, WRONG_HOST]
    ports = args.port or DEFAULT_PORTS
    report = run(hosts, ports, DEFAULT_NAMES, DEFAULT_URLS)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))

    # Exit non-zero only when the box that serves the site cannot be reached on
    # any port at all. A wrong-address host timing out is expected and must not
    # fail the run, or the signal is lost in the noise it was added to expose.
    real = [row for row in report["tcp"] if row["host"] == DEFAULT_HOST]
    if real and not any(row["result"] == "open" for row in real):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
