import io
import json
import os
import os.path
import re
import subprocess
import zipfile
from datetime import datetime, timedelta

import click
import requests
from rich import print
from rich.console import Console
from rich.markdown import Markdown
from rich.pretty import pprint
from rich.table import Table


class HealthException(Exception):
    def __init__(self, message):
        super().__init__(message)


def show_data(metrics: dict):
    """
    Gather the data from the exporter and loki and display them
    """
    print(Markdown("# Results"))
    show_salt_jobs_summary(metrics)
    show_salt_master_stats(metrics)
    show_uyuni_summary(metrics)


def show_error_logs_stats(loki):
    """
    Get and show the error logs stats
    """
    loki_url = loki or "http://localhost:3100"
    logcli_cmd = [
        "podman",
        "run",
        "-ti",
        "--rm",
        "--name",
        "logcli",
        "logcli",
        "--quiet",
        f"--addr={loki_url}",
        "instant-query",
        'count_over_time({job=~".+"} |~ `(?i)error` [7d])',
    ]
    process = subprocess.run(logcli_cmd, stdout=subprocess.PIPE)
    data = json.loads(process.stdout)

    print(Markdown("- Errors in logs over the last 7 days"))
    print()
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("File")
    table.add_column("Errors")

    for metric in data:
        table.add_row(metric["metric"]["filename"], metric["value"][1])

    print(table)


def show_full_error_logs(loki):
    """
    Get and show the error logs
    """
    loki_url = loki or "http://localhost:3100"
    from_time = (datetime.utcnow() - timedelta(days=7)).isoformat()
    logcli_cmd = [
        "podman",
        "run",
        "-ti",
        "--rm",
        "--name",
        "logcli",
        "logcli",
        "--quiet",
        f"--addr={loki_url}",
        "query",
        f"--from={from_time}Z",
        "--limit=100",
        '{job=~".+"} |~ `(?i)error`',
    ]
    print(Markdown("- Error logs of the last 7 days"))
    subprocess.run(logcli_cmd)


def show_salt_jobs_summary(metrics: dict):
    print(Markdown("- Summary of Salt jobs in last 24 hours"))
    print()
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Salt function name")
    table.add_column("Total")

    for metric, value in sorted(
        metrics["salt_jobs"].items(), reverse=True, key=lambda item: item[1]
    ):
        table.add_row(metric, str(int(value)))

    print(table)


def show_salt_master_stats(metrics: dict):
    print(Markdown("- Salt Master stats"))
    print()
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name")
    table.add_column("Total")

    for metric, value in sorted(
        metrics["salt_master_stats"].items(), key=lambda item: item[0]
    ):
        table.add_row(metric, str(int(value)))

    print(table)


def show_uyuni_summary(metrics: dict):
    print(Markdown("- Uyuni Summary"))
    print()
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Name")
    table.add_column("Total")

    for metric, value in sorted(
        metrics["uyuni_summary"].items(), key=lambda item: item[0]
    ):
        table.add_row(metric, str(int(value)))

    print(table)


def build_image(name, image_path=None):
    """
    Build a container image
    """
    print(f"Building {name} image")

    expanded_path = os.path.join(os.path.dirname(__file__), image_path or name)
    try:
        process = subprocess.run(
            ["podman", "build", "-t", name, "."],
            cwd=expanded_path,
            stdout=subprocess.PIPE,
        )
        if process.returncode != 0:
            raise HealthException(f"Failed to build {name} image")
    except OSError:
        raise HealthException("podman is required to build the container images")


def image_exists(image):
    """
    Check if the image is present in podman images result
    """
    try:
        process = subprocess.run(
            ["podman", "images", "--quiet", "-f", f"reference={image}"],
            stdout=subprocess.PIPE,
        )
        return process.stdout.decode() != ""
    except OSError:
        raise HealthException("podman is required to build the container images")


def build_logcli():
    """
    Build the container images
    """
    if image_exists("logcli"):
        print("Skipped as the logcli image is already present")
        return

    # Fetch the logcli binary from the latest release
    url = "https://github.com/grafana/loki/releases/download/v2.5.0/logcli-linux-amd64.zip"
    dest_dir = os.path.join(os.path.dirname(__file__), "logcli")
    response = requests.get(url)
    zip = zipfile.ZipFile(io.BytesIO(response.content))
    zip.extract("logcli-linux-amd64", dest_dir)
    build_image("logcli")


def ssh_call(server, cmd):
    """
    Run a command over SSH.

    If the server value is `None` run the command locally.

    For now the function assumes passwordless connection to the server on default SSH port.
    Use SSH agent and config to adjust if needed.
    """
    if server:
        ssh_cmd = ["ssh", "-q", server] + cmd
    else:
        ssh_cmd = cmd
    process = subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return process


def prepare_exporter(server):
    """
    Build the prometheus exporter image and deploy it on the server

    :param server: the Uyuni server to deploy the exporter on
    """
    if image_exists("uyuni-health-exporter"):
        print("Skipped as the uyuni-health-exporter image is already present")
        return

    build_image("uyuni-health-exporter", "exporter")

    id_cmd = ["id", "-g", "salt"]
    id_process = ssh_call(server, id_cmd)
    if id_process.returncode != 0:
        if "no such user" in id_process.stderr:
            raise HealthException(
                "Salt is not installed... is the tool running on an Uyuni server?"
            )
        else:
            raise HealthException(
                f"Failed to get Salt GID on server: {id_process.stderr}"
            )
    salt_gid = id_process.stdout.decode().strip()

    if server:
        # Save, deploy and load the image
        # TODO Handle errors
        if os.path.exists("/tmp/uyuni-health-exporter.tar"):
            # podman doesn't like if the image is already present
            os.unlink("/tmp/uyuni-health-exporter.tar")

        print("Saving the uyuni-health-exporter image...")
        subprocess.run(
            [
                "podman",
                "save",
                "--output",
                "/tmp/uyuni-health-exporter.tar",
                "uyuni-health-exporter",
            ]
        )

        print(f"Transfering the uyuni-health-exporter image to {server}...")
        subprocess.run(["scp", "/tmp/uyuni-health-exporter.tar", f"{server}:/tmp/"])

        print(f"Loading the uyuni-health-exporter image on {server}...")
        ssh_call(
            server, ["podman", "load", "--input", "/tmp/uyuni-health-exporter.tar"]
        )

    # Run the container
    try:
        ps_process = ssh_call(
            server, ["podman", "ps", "-f", "name=uyuni-health-exporter", "--quiet"]
        )
        if ps_process.stdout.decode() == "":
            run_cmd = [
                "podman",
                "run",
                "-u",
                f"salt:{salt_gid}",
                "-d",
                "--rm",
                '--network="host"',
                "-v",
                "/etc/salt:/etc/salt:ro",
                "-v",
                "/var/cache/salt/:/var/cache/salt",
                "--name",
                "uyuni-health-exporter",
                "uyuni-health-exporter",
            ]
            ssh_call(server, run_cmd)
        print(
            "No need to run the uyuni-health-exporter container as it is already running"
        )
    except OSError:
        raise HealthException("podman is required to extract the data")


def deploy_promtail():
    """
    Deploy promtail on the server
    """


def run_loki():
    """
    Run loki to aggregate the logs
    """


@click.command()
@click.option(
    "-ep",
    "--exporter-port",
    type=int,
    default=9000,
    help="uyuni health exporter metrics port",
)
@click.option(
    "--loki",
    default=None,
    help="URL of an existing loki instance to use to fetch the logs",
)
@click.option(
    "-s",
    "--server",
    default=None,
    help="Uyuni Server to connect to if not running directly on the server",
)
@click.option(
    "--logs",
    is_flag=True,
    help="Show the error logs",
)
def health_check(server, exporter_port, loki, logs):
    """
    Build the necessary containers, deploy them, get the metrics and display them

    :param server: the server to connect to
    :param exporter_port: uyuni health exporter metrics port
    :param loki: URL to a loki instance. Setting it will skip the promtail and loki deployments
    """
    console = Console()
    try:
        print(Markdown("- Preparing uyuni-health-exporter"))
        prepare_exporter(server)

        print(Markdown("- Building logcli image"))
        build_logcli()

        print(Markdown("- Deploying promtail and Loki"))
        if not loki:
            deploy_promtail()
            run_loki()
        else:
            console.print(f"Skipped to use Loki at {loki}")

        # Fetch metrics from uyuni-health-exporter
        print(Markdown("- Fetching metrics from uyuni-health-exporter"))
        metrics = fetch_metrics_exporter(server, exporter_port)

        # Gather and show the data
        show_data(metrics)
        show_error_logs_stats(loki)
        if logs:
            show_full_error_logs(loki)
    except HealthException as err:
        console.print("[red bold]" + str(err))


def fetch_metrics_exporter(host="localhost", port=9000):
    try:
        metrics_raw = requests.get(f"http://{host}:{port}").content.decode()
    except requests.exceptions.RequestException as exc:
        print(
            "[italic red]There was an error while fetching metrics from uyuni-health-exporter[/italic red]"
        )
        print(f"{exc}")
        exit(1)

    salt_metrics = re.findall(r'salt_jobs{fun="(.+)",name="(.+)"} (.+)', metrics_raw)
    uyuni_metrics = re.findall(r'uyuni_summary{name="(.+)"} (.+)', metrics_raw)
    salt_master_metrics = re.findall(
        r'salt_master_stats{name="(.+)"} (.+)', metrics_raw
    )

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

    return metrics


if __name__ == "__main__":
    print(Markdown("# Uyuni Health Check"))
    health_check()
