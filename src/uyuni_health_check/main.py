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
from rich.columns import Columns
from rich.console import Console
from rich.markdown import Markdown
from rich.pretty import pprint
from rich.table import Table


class HealthException(Exception):
    def __init__(self, message):
        super().__init__(message)


console = Console()
_hints = []


def show_data(metrics: dict):
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
       console.print("[yellow]Some metrics are still missing. Wait some seconds and execute again", justify="center")


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
    try:
        data = json.loads(process.stdout)
    except:
        print("[bold red]There was an error when fetching data from Loki")
        print(f"[bold red]{process.stdout.decode()}")
        return

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
    process = subprocess.run(logcli_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return process


def show_salt_jobs_summary(metrics: dict):
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Salt function name")
    table.add_column("Total")

    for metric, value in sorted(
        metrics["salt_jobs"].items(), reverse=True, key=lambda item: item[1]
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


def build_image(name, image_path=None, verbose=False):
    """
    Build a container image
    """
    expanded_path = os.path.join(os.path.dirname(__file__), image_path or name)
    try:
        kw = {}
        if not verbose:
           kw["stdout"] = subprocess.DEVNULL
           kw["stderr"] = subprocess.DEVNULL
        process = subprocess.run(
            ["podman", "build", "-t", name, "."],
            cwd=expanded_path,
            **kw
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


def check_postgres_service(server):
    """
    Check that postgresql service is running
    """
    try:
        process = ssh_call(server,
            ["systemctl", "status", "postgresql"]
        )
        if process.returncode != 0:
            msg = f"[bold red]WARNING: 'postgresql' service is NOT running!"
            _hints.append(msg)
            console.log(msg)
        else:
            console.log(f"[green]The postgresql service is running")
    except OSError:
        raise HealthException(f"The specified server '{server}' is not and Uyuni / SUSE Manager server!")


def check_spacewalk_services(server, verbose=False):
    """
    Check that spacewalk services are running
    """
    try:
        process = ssh_call(server,
            ["spacewalk-service", "list"]
        )
        if process.returncode != 0:
            raise HealthException(f"Failed to check spacewalk services")

        services = re.findall(r"(.+)\.service .*", process.stdout.decode())
        if verbose:
            console.log(f"Spacewalk services: {services}")
        all_running = True
        for service in services:
            process = ssh_call(server,
                ["systemctl", "status", service]
            )
            if process.returncode != 0:
                msg = f"[bold red]WARNING: '{service}' service is NOT running!"
                console.log(msg)
                _hints.append(msg)
                all_running = False
        if all_running:
                console.log(f"[green]All spacewalk services are running")

    except OSError:
        raise HealthException(f"The specified server '{server}' is not and Uyuni / SUSE Manager server!")


def container_is_running(name):
    """
    Check if a container with a given name is running in podman
    """
    try:
        process = subprocess.run(
            ["podman", "ps", "--quiet", "-f", f"name={name}"],
            stdout=subprocess.PIPE,
        )
        return process.stdout.decode() != ""
    except OSError:
        raise HealthException("podman is required to build the container images")


def build_logcli(verbose=False):
    """
    Build the container images
    """
    if image_exists("logcli"):
        console.log("[yellow]Skipped as the logcli image is already present")
        return

    # Fetch the logcli binary from the latest release
    url = "https://github.com/grafana/loki/releases/download/v2.5.0/logcli-linux-amd64.zip"
    dest_dir = os.path.join(os.path.dirname(__file__), "logcli")
    response = requests.get(url)
    zip = zipfile.ZipFile(io.BytesIO(response.content))
    zip.extract("logcli-linux-amd64", dest_dir)
    build_image("logcli", verbose=verbose)
    console.log("[green]The logcli image was built successfully")


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


def prepare_exporter(server, verbose=False):
    """
    Build the prometheus exporter image and deploy it on the server

    :param server: the Uyuni server to deploy the exporter on
    """
    console.log("[bold]Building uyuni-health-exporter image")
    if image_exists("uyuni-health-exporter"):
        console.log("[yellow]Skipped as the uyuni-health-exporter image is already present")
    else:
        build_image("uyuni-health-exporter", "exporter", verbose=verbose)
        console.log("[green]The uyuni-health-exporter image was built successfully")

    console.log("[bold]Deploying uyuni-health-exporter container")
    if container_is_running("uyuni-health-exporter"):
        console.log("[yellow]Skipped as the uyuni-health-exporter container is already running")
        return

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

        console.log("Saving the uyuni-health-exporter image...")
        subprocess.run(
            [
                "podman",
                "save",
                "--output",
                "/tmp/uyuni-health-exporter.tar",
                "uyuni-health-exporter",
            ]
        )

        console.log(f"Transfering the uyuni-health-exporter image to {server}...")
        subprocess.run(["scp", "/tmp/uyuni-health-exporter.tar", f"{server}:/tmp/"])

        console.log(f"Loading the uyuni-health-exporter image on {server}...")
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
            console.log(
                "[green]The uyuni-health-exporter container was started. You would probably need to wait some seconds until getting metrics"
            )
        else:
            console.log(
                "[yellow]No need to run the uyuni-health-exporter container as it is already running"
            )
    except OSError:
        raise HealthException("podman is required to extract the data")


def deploy_promtail():
    """
    Deploy promtail on the server
    """
    console.log("[grey35]Not implemented yet!")


def run_loki():
    """
    Run loki to aggregate the logs
    """
    console.log("[grey35]Not implemented yet!")


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
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show more stdout, including image building",
)
def health_check(server, exporter_port, loki, logs, verbose):
    """
    Build the necessary containers, deploy them, get the metrics and display them

    :param server: the server to connect to
    :param exporter_port: uyuni health exporter metrics port
    :param loki: URL to a loki instance. Setting it will skip the promtail and loki deployments
    """
    try:
        with console.status(status=None) as status:
            console.log("[bold]Preparing uyuni-health-exporter")
            prepare_exporter(server, verbose=verbose)

            console.log("[bold]Building logcli image")
            build_logcli()

            if not loki:
                console.log("[bold]Deploying promtail and Loki")
                deploy_promtail()
                console.log("[bold]Run promtail and Loki")
                run_loki()
            else:
                console.log(f"[yellow]Skipped to use Loki at {loki}")

            # Fetch metrics from uyuni-health-exporter
            console.log("[bold]Fetching metrics from uyuni-health-exporter")
            metrics = fetch_metrics_exporter(server, exporter_port)

            # Check spacewalk services
            console.log("[bold]Checking spacewalk services")
            check_spacewalk_services(server, verbose=verbose)

            # Check spacewalk services
            console.log("[bold]Checking postgresql service")
            check_postgres_service(server)

        # Gather and show the data
        console.print(Markdown("# Results"))
        show_data(metrics)

        console.print(Markdown("## Relevant Errors"))
        if not container_is_running("logcli"):
            console.print("[yellow]loki / logcli container is not running", justify="center")
        else:
            show_error_logs_stats(loki)
            if logs:
                show_full_error_logs(loki)

        show_relevant_hints()
    except HealthException as err:
        console.log("[red bold]" + str(err))


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

    console.log("[green]metrics have been successfully collected")
    return metrics


if __name__ == "__main__":
    print(Markdown("# Uyuni Health Check"))
    health_check()
