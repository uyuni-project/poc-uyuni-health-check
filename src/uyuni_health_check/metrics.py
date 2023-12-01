# SPDX-FileCopyrightText: 2023 SUSE LLC
#
# SPDX-License-Identifier: Apache-2.0

import json
import re
import time
from datetime import datetime, timedelta
from json.decoder import JSONDecodeError

import requests
from rich import print
from rich.columns import Columns
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from uyuni_health_check.util import HealthException, podman


def show_uyuni_live_server_metrics(metrics: dict, console: "Console"):
    """
    Gather the data from the exporter and loki and display them
    """
    console.print(Markdown("## Uyuni server and Salt Master stats"))
    console.print()
    if metrics:
        tables = []
        tables.append(show_salt_jobs_summary(metrics))
        tables.append(show_salt_master_stats(metrics))
        tables.append(show_uyuni_summary(metrics))
        console.print(Columns(tables), justify="center")
    else:
        console.print(
            "[yellow]Some metrics are still missing. Wait some seconds and execute again",
            justify="center",
        )


def show_supportconfig_metrics(metrics: dict, console: "Console"):
    if metrics:
        tables = []
        tables.append(show_salt_jobs_summary(metrics))
        tables.append(show_salt_keys_summary(metrics))
        tables.append(show_salt_master_configuration_summary(metrics))
        console.print(Columns(tables), justify="center")
    else:
        console.print(
            "[yellow]Some metrics are still missing. Wait some seconds and execute again",
            justify="center",
        )


def show_relevant_hints(hints, console: "Console"):
    console.print(Markdown("## Relevant hints. Please take a look!"))
    console.print()

    if not hints:
        console.print(
            Panel(Text("Good news! There are no relevant hints.", justify="center")),
            style="italic green",
        )
    else:
        for hint in hints:
            console.print(hint, justify="center")

    console.print()


def show_error_logs_stats(loki, since, console: "Console"):
    """
    Get and show the error logs stats
    """
    print(Markdown(f"- Errors in logs over the last {since} days:"))
    print()
    loki_url = loki or "http://loki:3100"
    process = podman(
        [
            "run",
            "-ti",
            "--rm",
            "--pod",
            "uyuni-health-check",
            "--name",
            "logcli",
            "logcli",
            "--quiet",
            f"--addr={loki_url}",
            "instant-query",
            "--limit",
            "150",
            'count_over_time({job=~".+"} |~ `(?i)error|(?i)severe|(?i)critical|(?i)fatal` ['
            + str(since)
            + "d])",
        ]
    )
    response = process.stdout.read()
    try:
        data = json.loads(response)
    except JSONDecodeError:
        raise HealthException(f"Invalid logcli response: {response}")

    if data:
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("File")
        table.add_column("Errors")

        for metric in data:
            table.add_row(metric["metric"]["filename"], metric["value"][1])

        print(table)
    else:
        console.print(
            Panel(
                Text(
                    f"Good news! No errors detected in logs in the last {since} days.",
                    justify="center",
                )
            ),
            style="italic green",
        )


def show_full_error_logs(loki, since, console: "Console"):
    """
    Get and show the error logs
    """
    print()
    print(Markdown(f"- Error logs of the last {since} days:"))
    from_time = (datetime.utcnow() - timedelta(days=since)).isoformat()
    loki_url = loki or "http://loki:3100"
    podman(
        [
            "run",
            "-ti",
            "--rm",
            "--pod",
            "uyuni-health-check",
            "--name",
            "logcli",
            "logcli",
            "--quiet",
            f"--addr={loki_url}",
            "query",
            f"--from={from_time}Z",
            "--limit=150",
            '{job=~".+"} |~ `(?i)error|(?i)severe|(?i)critical|(?i)fatal`',
        ],
        console=console,
    )
    print()


def show_salt_jobs_summary(metrics: dict):
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Salt function name")
    table.add_column("Total")

    for metric, value in sorted(
        metrics["salt_jobs"].items(), reverse=True, key=lambda item: item[1]
    ):
        table.add_row(metric, str(int(value)))

    return table


def show_salt_keys_summary(metrics: dict):
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Salt keys")
    table.add_column("Total")

    for metric, value in sorted(
        metrics["salt_keys"].items(), reverse=True, key=lambda item: item[1]
    ):
        table.add_row(metric, str(int(value)))

    return table


def show_salt_master_configuration_summary(metrics: dict):
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Salt Master Configuration")
    table.add_column("Value")

    for metric, value in sorted(
        metrics["salt_master_config"].items(), reverse=True, key=lambda item: item[1]
    ):
        table.add_row(metric, str(int(value)))

    return table


def show_salt_master_stats(metrics: dict):
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name")
    table.add_column("Total")

    for metric, value in sorted(
        metrics["salt_master_stats"].items(), key=lambda item: item[0]
    ):
        table.add_row(metric, str(int(value)))

    return table


def show_uyuni_summary(metrics: dict):
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name")
    table.add_column("Total")

    for metric, value in sorted(
        metrics["uyuni_summary"].items(), key=lambda item: item[0]
    ):
        table.add_row(metric, str(int(value)))

    return table


def _fetch_metrics_from_exporter(
    console: "Console", host="localhost", port=9000, max_retries=5
):
    for i in range(max_retries):
        try:
            metrics_raw = requests.get(f"http://{host}:{port}").content.decode()
            return metrics_raw
        except requests.exceptions.RequestException as exc:
            if i < max_retries - 1:
                time.sleep(1)
                console.log("[italic]retrying...")
            else:
                console.log(
                    "[italic red]There was an error while fetching metrics from exporter[/italic red]"
                )
                print(f"{exc}")
                exit(1)


def fetch_metrics_from_supportconfig_exporter(
    console: "Console", host="localhost", port=9000, max_retries=5
):
    if not host:
        host = "localhost"

    metrics_raw = _fetch_metrics_from_exporter(console, host, port, max_retries)

    salt_jobs = re.findall(r'salt_jobs{fun="(.+)",jid="(.+)"} (.+)', metrics_raw)
    salt_keys = re.findall(r'salt_keys{name="(.+)"} (.+)', metrics_raw)
    salt_master_config = re.findall(
        r'salt_master_config{name="(.+)"} (.+)', metrics_raw
    )

    if not salt_jobs or not salt_keys or not salt_master_config:
        console.log(
            "[yellow]Some metrics are still missing. Wait some seconds and execute again"
        )
        return {}

    metrics = {
        "salt_jobs": {},
        "salt_keys": {},
        "salt_master_config": {},
    }

    for m in salt_jobs:
        if m[0] in metrics["salt_jobs"]:
            metrics["salt_jobs"][m[0]] += 1
        else:
            metrics["salt_jobs"][m[0]] = 1

    for m in salt_master_config:
        metrics["salt_master_config"][m[0]] = float(m[1])

    for m in salt_keys:
        metrics["salt_keys"][m[0]] = float(m[1])

    console.log("[green]metrics have been successfully collected")
    return metrics


def fetch_metrics_from_uyuni_health_exporter(
    console: "Console", host="localhost", port=9000, max_retries=5
):
    if not host:
        host = "localhost"

    metrics_raw = _fetch_metrics_from_exporter(console, host, port, max_retries)

    salt_metrics = re.findall(r'salt_jobs{fun="(.+)",name="(.+)"} (.+)', metrics_raw)
    uyuni_metrics = re.findall(r'uyuni_summary{name="(.+)"} (.+)', metrics_raw)
    salt_master_metrics = re.findall(
        r'salt_master_stats{name="(.+)"} (.+)', metrics_raw
    )

    if not salt_metrics or not uyuni_metrics or not salt_master_metrics:
        console.log(
            "[yellow]Some metrics are still missing. Wait some seconds and execute again"
        )
        return {}

    metrics = {
        "salt_jobs": {},
        "salt_master_stats": {},
        "uyuni_summary": {},
    }

    for m in salt_metrics:
        metrics["salt_jobs"][m[0]] = float(m[2])

    for m in salt_master_metrics:
        metrics["salt_master_stats"][m[0]] = float(m[1])

    for m in uyuni_metrics:
        metrics["uyuni_summary"][m[0]] = float(m[1])

    console.log("[green]metrics have been successfully collected")
    return metrics
