import io
import json
import os
import os.path
import re
import subprocess
import time
import zipfile
from datetime import datetime, timedelta
from json.decoder import JSONDecodeError
from time import sleep

import click
import requests
from rich import print
from rich.columns import Columns
from rich.console import Console
from rich.markdown import Markdown
from rich.pretty import pprint
from rich.table import Table

from uyuni_health_check.util import HealthException, podman, ssh_call

# Update this number if adding more targets to the promtail config
PROMTAIL_TARGETS = 5


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
        console.print(
            "[yellow]Some metrics are still missing. Wait some seconds and execute again",
            justify="center",
        )


def show_relevant_hints():
    console.print(Markdown("## Relevant hints. Please take a look!"))
    console.print()

    if not _hints:
        console.print("[italic]There are no relevant hints", justify="center")
    else:
        for hint in _hints:
            console.print(hint, justify="center")

    console.print()


def wait_loki_init(server):
    """
    Try to figure out when loki is ready to answer our requests.
    There are two things to wait for:
      - loki to be up
      - promtail to have read the logs and the loki ingester having handled them
    """
    metrics = None

    # Wait for promtail to be ready
    # TODO Add a timeout here in case something went really bad
    # TODO checking the lags won't work when working on older logs,
    # we could try to compare the positions with the size of the files in such a case
    while (
        not metrics
        or metrics["active"] < PROMTAIL_TARGETS
        or not metrics["lags"]
        or any([v >= 10 for v in metrics["lags"].values()])
    ):
        sleep(1)
        response = requests.get(f"http://{server}:9081/metrics")
        if response.status_code == 200:
            content = response.content.decode()
            active = re.findall("promtail_targets_active_total ([0-9]+)", content)
            lags = re.findall(
                'promtail_stream_lag_seconds{filename="([^"]+)".*} ([0-9.]+)', content
            )
            metrics = {
                "lags": {row[0]: float(row[1]) for row in lags},
                "active": int(active[0]) if active else 0,
            }


def show_error_logs_stats(loki):
    """
    Get and show the error logs stats
    """
    loki_url = loki or "http://localhost:3100"
    process = podman(
        [
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
    )
    response = process.stdout.read()
    try:
        data = json.loads(response)
    except JSONDecodeError:
        raise HealthException(f"Invalid logcli response: {response}")

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
    print(Markdown("- Error logs of the last 7 days"))
    podman(
        [
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
    )


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
    process = podman(["build", "-t", name, expanded_path], console=console if verbose else None)
    if process.returncode != 0:
        raise HealthException(f"Failed to build {name} image")


def pod_exists(pod, server=None):
    """
    Check if the image pod is up and running
    """
    return (
        podman(["pod", "list", "--quiet", f"-fname={pod}"], server=server)
        .stdout.read()
        .strip()
        != ""
    )


def image_exists(image):
    """
    Check if the image is present in podman images result
    """
    return (
        podman(["images", "--quiet", "-f", f"reference={image}"]).stdout.read().strip()
        != ""
    )


def check_postgres_service(server):
    """
    Check that postgresql service is running
    """
    try:
        process = ssh_call(server, ["systemctl", "status", "postgresql"])
        if process.returncode != 0:
            msg = "[bold red]WARNING: 'postgresql' service is NOT running!"
            _hints.append(msg)
            console.log(msg)
        else:
            console.log("[green]The postgresql service is running")
    except OSError:
        raise HealthException(
            f"The specified server '{server}' is not and Uyuni / SUSE Manager server!"
        )


def check_spacewalk_services(server, verbose=False):
    """
    Check that spacewalk services are running
    """
    try:
        process = ssh_call(server, ["spacewalk-service", "list"])
        if process.returncode != 0:
            raise HealthException("Failed to check spacewalk services")

        services = re.findall(r"(.+)\.service .*", process.stdout.read())
        if verbose:
            console.log(f"Spacewalk services: {services}")
        all_running = True
        for service in services:
            process = ssh_call(server, ["systemctl", "status", service])
            if process.returncode != 0:
                msg = f"[bold red]WARNING: '{service}' service is NOT running!"
                console.log(msg)
                _hints.append(msg)
                all_running = False
        if all_running:
            console.log("[green]All spacewalk services are running")

    except OSError:
        raise HealthException(
            f"The specified server '{server}' is not and Uyuni / SUSE Manager server!"
        )


def container_is_running(name, server=None):
    """
    Check if a container with a given name is running in podman
    """
    process = podman(
        ["ps", "--quiet", "-f", f"name={name}"],
        server=server
    )
    return process.stdout.read() != ""


def build_loki_image(image, verbose=False):
    if image_exists(image):
        print(f"[yellow]Skipped as the {image} image is already present")
        return

    # Fetch the logcli binary from the latest release
    url = f"https://github.com/grafana/loki/releases/download/v2.5.0/{image}-linux-amd64.zip"
    dest_dir = os.path.join(os.path.dirname(__file__), image)
    response = requests.get(url)
    zip = zipfile.ZipFile(io.BytesIO(response.content))
    zip.extract(f"{image}-linux-amd64", dest_dir)
    build_image(image, verbose=verbose)
    console.log(f"[green]The {image} image was built successfully")


def transfer_image(server, image):
    """
    Copy a container image over to the server

    :param server: the server to transfer the image to
    """
    # Save, deploy and load the image
    # TODO Handle errors
    local_image_path = f"/tmp/{image}.tar"
    if os.path.exists(local_image_path):
        # podman doesn't like if the image is already present
        os.unlink(local_image_path)

    print(f"Saving the {image} image...")
    podman(["save", "--output", local_image_path, image])

    print(f"Transfering the {image} image to {server}...")
    subprocess.run(["scp", "-q", local_image_path, f"{server}:/tmp/"])

    print(f"Loading the {image} image on {server}...")
    podman(["load", "--input", f"/tmp/{image}.tar"], server)


def prepare_exporter(server, verbose=False):
    """
    Build the prometheus exporter image and deploy it on the server

    :param server: the Uyuni server to deploy the exporter on
    """
    console.log("[bold]Building uyuni-health-exporter image")
    if image_exists("uyuni-health-exporter"):
        console.log(
            "[yellow]Skipped as the uyuni-health-exporter image is already present"
        )
    else:
        build_image("uyuni-health-exporter", "exporter", verbose=verbose)
        console.log("[green]The uyuni-health-exporter image was built successfully")

    # Run the container
    console.log("[bold]Deploying uyuni-health-exporter container")
    if container_is_running("uyuni-health-exporter", server=server):
        console.log(
            "[yellow]Skipped as the uyuni-health-exporter container is already running"
        )
        return

    # Transfering the image
    if server:
        transfer_image(server, "uyuni-health-exporter")

    # Get the Salt UID/GID
    id_process = ssh_call(server, ["id", "salt"])
    if id_process.returncode != 0:
        err = id_process.stderr.read()
        if "no such user" in err:
            raise HealthException(
                "Salt is not installed... is the tool running on an Uyuni server?"
            )
        else:
            raise HealthException(f"Failed to get Salt GID on server: {err}")
    id_out = id_process.stdout.read()
    salt_uid = re.match(".*uid=([0-9]+)", id_out).group(1)
    salt_gid = re.match(".*gid=([0-9]+)", id_out).group(1)

    # Run the container
    podman(
        [
            "run",
            "-u",
            f"{salt_uid}:{salt_gid}",
            "-d",
            "--rm",
            "--network=host",
            "-v",
            "/etc/salt:/etc/salt:ro",
            "-v",
            "/var/cache/salt/:/var/cache/salt",
            "--name",
            "uyuni-health-exporter",
            "uyuni-health-exporter",
        ],
        server,
    )


def run_loki(server):
    """
    Run promtail and loki to aggregate the logs

    :param server: the Uyuni server to deploy the exporter on or localhost
    """
    console.log("[grey35]Not implemented yet!")

    if pod_exists("uyuni-health-check", server=server):
        print("Skipped as the uyuni-health-check pod is already running")
    else:
        podman(
            [
                "pod",
                "create",
                "-p",
                "3100:3100",
                "-p",
                "9081:9081",
                "--replace",
                "-n",
                "uyuni-health-check",
            ],
            server,
            console=console,
        )

        # TODO Prepare config to tune the oldest message allowed
        podman(
            [
                "run",
                "--pod",
                "uyuni-health-check",
                "--rm",
                "--replace",
                "-d",
                "--name",
                "loki",
                "docker.io/grafana/loki",
            ],
            server,
            console=console,
        )

        # Copy the promtail config
        promtail_cfg = os.path.join(
            os.path.dirname(__file__), "promtail", "promtail.yaml"
        )
        if server:
            try:
                subprocess.run(
                    ["scp", "-q", promtail_cfg, f"{server}:/tmp/"], check=True
                )
                promtail_cfg = "/tmp/promtail.yaml"
            except Exception:
                raise HealthException(
                    f"Failed to copy promtail configuration to {server}"
                )

        # Run promtail only now since it pushes data to loki
        print(Markdown("- Building promtail image"))
        build_loki_image("promtail")
        if server:
            transfer_image(server, "promtail")
        podman(
            [
                "run",
                "--replace",
                "-d",
                "--rm",
                "-v",
                f"{promtail_cfg}:/etc/promtail/config.yml",
                "-v",
                "/var/log/:/var/log/",
                "--name",
                "promtail",
                "--pod",
                "uyuni-health-check",
                "promtail",
            ],
            server,
            console=console,
        )


def clean_server(server):
    """
    Remove the containers we spawned on the server now that everything is finished

    :param server: server to clean
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
        with console.status(status=None):
            console.log("[bold]Preparing uyuni-health-exporter")
            prepare_exporter(server, verbose=verbose)

        console.log("[bold]Building logcli image")
        build_loki_image("logcli")

        console.log("Deploying promtail and Loki")
        if not loki:
            run_loki(server)
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

        print(Markdown("- Waiting for loki to be ready"))
        host = server or "localhost"
        wait_loki_init(host)

        # Gather and show the data
        console.print(Markdown("# Results"))
        show_data(metrics)

        console.print(Markdown("## Relevant Errors"))
        loki_url = loki if loki else f"http://{host}:3100"
        show_error_logs_stats(loki_url)
        if logs:
            show_full_error_logs(loki_url)
    except HealthException as err:
        console.log("[red bold]" + str(err))
    finally:
        clean_server(server)


def fetch_metrics_exporter(host="localhost", port=9000, max_retries=5):
    if not host:
        host = "localhost"

    for i in range(max_retries):
        try:
            metrics_raw = requests.get(f"http://{host}:{port}").content.decode()
            salt_metrics = re.findall(
                r'salt_jobs{fun="(.+)",name="(.+)"} (.+)', metrics_raw
            )
            uyuni_metrics = re.findall(r'uyuni_summary{name="(.+)"} (.+)', metrics_raw)
            salt_master_metrics = re.findall(
                r'salt_master_stats{name="(.+)"} (.+)', metrics_raw
            )
            break
        except requests.exceptions.RequestException as exc:
            if i < max_retries - 1:
                time.sleep(1)
                console.log("[italic]retrying...")
            else:
                console.log(
                    "[italic red]There was an error while fetching metrics from uyuni-health-exporter[/italic red]"
                )
                print(f"{exc}")
                exit(1)

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


if __name__ == "__main__":
    print(Markdown("# Uyuni Health Check"))
    health_check()
