# SPDX-FileCopyrightText: 2023 SUSE LLC
#
# SPDX-License-Identifier: Apache-2.0

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
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from uyuni_health_check.metrics import (
    fetch_metrics_from_supportconfig_exporter,
    fetch_metrics_from_uyuni_health_exporter,
    show_error_logs_stats,
    show_full_error_logs,
    show_relevant_hints,
    show_salt_jobs_summary,
    show_salt_keys_summary,
    show_salt_master_configuration_summary,
    show_salt_master_stats,
    show_supportconfig_metrics,
    show_uyuni_live_server_metrics,
    show_uyuni_summary,
)
from uyuni_health_check.util import (
    HealthException,
    podman,
    render_promtail_cfg,
    render_supportconfig_exporter_cfg,
    ssh_call,
)

# Update this number if adding more targets to the promtail config
PROMTAIL_TARGETS = 6

# Max number of seconds to wait for Loki to be ready
LOKI_WAIT_TIMEOUT = 120

console = Console()
_hints = []


def wait_loki_init(server, verbose=False):
    """
    Try to figure out when loki is ready to answer our requests.
    There are two things to wait for:
      - loki to be up
      - promtail to have read the logs and the loki ingester having handled them
    """
    metrics = None
    timeouted = False
    start_time = time.time()
    ready = False

    # Wait for promtail to be ready
    # TODO Add a timeout here in case something went really bad
    # TODO checking the lags won't work when working on older logs,
    # we could try to compare the positions with the size of the files in such a case
    while (
        not metrics
        or metrics["active"] < PROMTAIL_TARGETS
        or (not metrics["lags"] and metrics["active_files"] == 0)
        or any([v >= 10 for v in metrics["lags"].values()])
        or (metrics["lags"] and metrics["active_files"])
        or not ready
        and not timeouted
    ):
        sleep(1)
        response = requests.get(f"http://{server}:9081/metrics")
        if response.status_code == 200:
            content = response.content.decode()
            active = re.findall("promtail_targets_active_total ([0-9]+)", content)
            active_files = re.findall("promtail_files_active_total ([0-9]+)", content)
            lags = re.findall(
                'promtail_stream_lag_seconds{filename="([^"]+)".*} ([0-9.]+)', content
            )
            metrics = {
                "lags": {row[0]: float(row[1]) for row in lags},
                "active": int(active[0]) if active else 0,
                "active_files": int(active_files[0]) if active_files else 0,
            }

        # check if loki is ready
        response = requests.get(f"http://{server}:3100/ready")
        if response.status_code == 200:
            content = response.content.decode()
            if content == "ready\n":
                ready = True

        # check if promtail is ready
        response = requests.get(f"http://{server}:9081/ready")
        if response.status_code == 200:
            content = response.content.decode()
            if content == "Ready":
                ready = True
            else:
                ready = False
        # check timeout
        if (time.time() - start_time) > LOKI_WAIT_TIMEOUT:
            timeouted = True
    if timeouted:
        raise HealthException(
            "[red bold]Timeout has been reached waiting for Loki and promtail. Something unexpected may happen. Please check and try again."
        )
    else:
        console.log("[bold]Loki and promtail are now ready to receive requests")


def build_image(name, image_path=None, verbose=False, server=None):
    """
    Build a container image
    """
    expanded_path = os.path.join(os.path.dirname(__file__), image_path or name)
    process = podman(
        ["build", "-t", name, expanded_path],
        console=console if verbose else None,
        server=server,
    )
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


def image_exists(image, server=None):
    """
    Check if the image is present in podman images result
    """
    return (
        podman(["images", "--quiet", "-f", f"reference={image}"], server=server)
        .stdout.read()
        .strip()
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
    process = podman(["ps", "--quiet", "-f", f"name={name}"], server=server)
    return process.stdout.read() != ""


def build_loki_image(image, verbose=False, server=None):
    if image_exists(image, server=server):
        console.log(f"[yellow]Skipped as the {image} image is already present")
        return

    # Fetch the logcli binary from the latest release
    url = f"https://github.com/grafana/loki/releases/download/v2.9.2/{image}-linux-amd64.zip"
    #    url = f"https://github.com/grafana/loki/releases/download/v2.8.6/{image}-linux-amd64.zip"
    dest_dir = os.path.join(os.path.dirname(__file__), image)
    response = requests.get(url)
    zip = zipfile.ZipFile(io.BytesIO(response.content))
    zip.extract(f"{image}-linux-amd64", dest_dir)
    build_image(image, verbose=verbose, server=server)
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

    console.log(f"[bold]Saving the {image} image...")
    podman(["save", "--output", local_image_path, image])

    console.log(f"[bold]Transfering the {image} image to {server}...")
    subprocess.run(["scp", "-q", local_image_path, f"{server}:/tmp/"])

    console.log(f"[bold]Loading the {image} image on {server}...")
    podman(["load", "--input", f"/tmp/{image}.tar"], server)


def prepare_exporter(server, verbose=False, supportconfig_path=None):
    """
    Build the prometheus exporter image and deploy it on the server

    :param server: the Uyuni server to deploy the exporter on
    """
    if supportconfig_path:
        exporter_name = "supportconfig-exporter"
        exporter_dir = "supportconfig_exporter"
        render_supportconfig_exporter_cfg(supportconfig_path)
    else:
        exporter_name = "uyuni-health-exporter"
        exporter_dir = "exporter"

    console.log(f"[bold]Building {exporter_name} image")
    if image_exists(f"{exporter_name}"):
        console.log(f"[yellow]Skipped as the {exporter_name} image is already present")
    else:
        build_image(f"{exporter_name}", exporter_dir, verbose=verbose)
        console.log(f"[green]The {exporter_name} image was built successfully")

    # Run the container
    console.log(f"[bold]Deploying {exporter_name} container")
    if container_is_running(f"{exporter_name}", server=server):
        console.log(
            f"[yellow]Skipped as the {exporter_name} container is already running"
        )
        return

    # Transfering the image
    if server:
        transfer_image(server, f"{exporter_name}")

    if not supportconfig_path:
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

    # Prepare arguments for Podman call
    podman_args = [
        "run",
        "--pod",
        "uyuni-health-check",
        "-d",
        "--network=host",
    ]

    if supportconfig_path:
        podman_args.extend(
            [
                "-v",
                f"{supportconfig_path}:{supportconfig_path}",
            ]
        )
    else:
        podman_args.extend(
            [
                "-u",
                f"{salt_uid}:{salt_gid}",
                "-v",
                "/etc/salt:/etc/salt:ro",
                "-v",
                "/var/cache/salt/:/var/cache/salt",
            ]
        )

    podman_args.extend(
        [
            "--name",
            f"{exporter_name}",
            f"{exporter_name}",
        ]
    )

    # Run the container
    podman(
        podman_args,
        server,
        console=console,
    )


def prepare_grafana(server, verbose=False):
    if container_is_running("uyuni-health-check-grafana", server=server):
        console.log(
            "[yellow]Skipped as the uyuni-health-check-grafana container is already running"
        )
    else:
        # Copy the grafana config
        grafana_cfg = os.path.join(os.path.dirname(__file__), "grafana")

        if server:
            try:
                subprocess.run(
                    ["scp", "-rq", grafana_cfg, f"{server}:/tmp/"], check=True
                )
                grafana_cfg = "/tmp/grafana"
            except Exception:
                raise HealthException(
                    f"Failed to copy grafana configuration to {server}"
                )

        # Run the container
        podman(
            [
                "run",
                "--pod",
                "uyuni-health-check",
                "-d",
                "-v",
                f"{grafana_cfg}/datasources.yaml:/etc/grafana/provisioning/datasources/ds.yaml",
                "-v",
                f"{grafana_cfg}/dashboard.yaml:/etc/grafana/provisioning/dashboards/main.yaml",
                "-v",
                f"{grafana_cfg}/dashboards:/var/lib/grafana/dashboards",
                "-e",
                "GF_PATHS_PROVISIONING=/etc/grafana/provisioning",
                "-e",
                "GF_AUTH_ANONYMOUS_ENABLED=true",
                "-e",
                "GF_AUTH_ANONYMOUS_ORG_ROLE=Admin",
                "--name",
                "uyuni-health-check-grafana",
                "docker.io/grafana/grafana:9.2.1",
                "run.sh",
            ],
            server,
            console=console,
        )


def prepare_prometheus(server, verbose=False):
    if container_is_running("uyuni-health-check-prometheus", server=server):
        console.log(
            "[yellow]Skipped as the uyuni-health-check-prometheus container is already running"
        )
    else:
        # Copy the prometheus config
        prometheus_cfg = os.path.join(
            os.path.dirname(__file__), "prometheus", "prometheus.yml"
        )

        if server:
            try:
                subprocess.run(
                    ["scp", "-rq", prometheus_cfg, f"{server}:/tmp/"], check=True
                )
                prometheus_cfg = "/tmp/prometheus.yml"
            except Exception:
                raise HealthException(
                    f"Failed to copy prometheus configuration to {server}"
                )

        # Run the container
        podman(
            [
                "run",
                "--pod",
                "uyuni-health-check",
                "-d",
                "-v",
                f"{prometheus_cfg}:/etc/prometheus/prometheus.yml",
                "--name",
                "uyuni-health-check-prometheus",
                "docker.io/prom/prometheus",
            ],
            server,
            console=console,
        )


def create_pod(server):
    """
    Create uyuni-health-check pod where we run the containers

    :param server: the Uyuni server to create the pod on or localhost
    """
    if pod_exists("uyuni-health-check", server=server):
        console.log("[yellow]Skipped as the uyuni-health-check pod is already running")
    else:
        podman(
            [
                "pod",
                "create",
                "-p",
                "3100:3100",
                "-p",
                "9081:9081",
                "-p",
                "3000:3000",
                "-p",
                "9090:9090",
                "--replace",
                "-n",
                "uyuni-health-check",
            ],
            server=server,
            console=console,
        )


def run_loki(server, supportconfig_path=None, verbose=False):
    """
    Run promtail and loki to aggregate the logs

    :param server: the Uyuni server to deploy the exporter on or localhost
    """
    if container_is_running("loki", server=server):
        console.log("[yellow]Skipped as the loki container is already running")
    else:
        loki_cfg = os.path.join(os.path.dirname(__file__), "loki/config.yaml")
        promtail_cfg = render_promtail_cfg(supportconfig_path)

        # Copy the promtail and loki config files if necessary
        if server:
            try:
                subprocess.run(
                    ["scp", "-q", promtail_cfg, f"{server}:/tmp/"], check=True
                )
                promtail_cfg = "/tmp/promtail.yaml"
                subprocess.run(["scp", "-q", loki_cfg, f"{server}:/tmp/"], check=True)
                promtail_cfg = "/tmp/loki.yaml"
            except Exception:
                raise HealthException(
                    f"Failed to copy promtail configuration to {server}"
                )

        # Run loki container
        podman(
            [
                "run",
                "--pod",
                "uyuni-health-check",
                "--replace",
                "-d",
                "--name",
                "loki",
                "-v",
                f"{loki_cfg}:/etc/loki/local-config.yaml",
                "docker.io/grafana/loki",
            ],
            server,
            console=console,
        )

        # Run promtail only now since it pushes data to loki
        console.log("[bold]Building promtail image")
        build_loki_image("promtail", verbose=verbose)
        if server:
            transfer_image(server, "promtail")

        podman_args = [
            "run",
            "--replace",
            "-d",
            "-v",
            f"{promtail_cfg}:/etc/promtail/config.yml",
            "-v",
            "/var/log/:/var/log/",
        ]

        if supportconfig_path:
            podman_args.extend(
                [
                    "-v",
                    f"{supportconfig_path}:{supportconfig_path}",
                ]
            )

        podman_args.extend(
            [
                "--name",
                "promtail",
                "--pod",
                "uyuni-health-check",
                "promtail",
            ]
        )
        podman(
            podman_args,
            server,
            console=console,
        )


def clean_server(server):
    """
    Remove the containers we spawned on the server now that everything is finished

    :param server: server to clean
    """
    with console.status(status=None):
        console.log("[bold]Cleaning up containers after execution")
        if not pod_exists("uyuni-health-check", server=server):
            console.log("[yellow]Skipped as the uyuni-health-check pod is not running")
        else:
            podman(
                [
                    "pod",
                    "rm",
                    "-f",
                    "uyuni-health-check",
                ],
                server,
                console=console,
            )
            console.log("[green]Containers have been removed")

        console.log("[bold]Removing promtail and exporter images")
        images_to_remove = [
            "localhost/promtail",
            "localhost/supportconfig-exporter",
            "localhost/uyuni-health-exporter",
        ]
        for image in images_to_remove:
            if image_exists(image, server=server):
                podman(
                    [
                        "rmi",
                        image,
                    ],
                    server,
                    console=console,
                )
                console.log(f"[green]{image} image has been removed")


@click.group()
@click.option(
    "-s",
    "--server",
    default=None,
    help="Uyuni Server to connect to if not running directly on the server",
)
@click.option(
    "-i",
    "--supportconfig_path",
    default=None,
    help="Use a supportconfig path as the data source",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show more stdout, including image building",
)
@click.pass_context
def cli(ctx, server, supportconfig_path, verbose):
    # ensure that ctx.obj exists and is a dict (in case `cli()` is called
    # by means other than the `if` block below)
    ctx.ensure_object(dict)
    ctx.obj["server"] = server
    ctx.obj["verbose"] = verbose
    ctx.obj["supportconfig_path"] = supportconfig_path

    if server and supportconfig_path:
        console.log(
            "[red bold] Cannot pass both 'server' and 'supportconfig_path' at the same time!"
        )
        console.print(Markdown("# Execution Finished"))
        exit(1)

    try:
        console.log("[bold]Checking connection with podman:")
        ssh_call(server, ["podman", "--version"], console=console, quiet=False)
        ssh_call(server, ["podman", "images"], console=console, quiet=True)
    except HealthException as err:
        console.log("[red bold]" + str(err))
        console.print(Markdown("# Execution Finished"))
        exit(1)


@cli.command()
@click.pass_context
def clean(ctx):
    """
    Remove all the containers we spawned on the server

    :param server: server where containers are running
    """
    server = ctx.obj["server"]
    clean_server(server)
    console.print(Markdown("# Execution Finished"))


@cli.command()
@click.pass_context
def stop(ctx):
    """
    Stop the containers on the server if already present

    :param server: server where containers are running
    """
    server = ctx.obj["server"]
    with console.status(status=None):
        console.log("[bold]Stopping uyuni-health-check containers")
        if not pod_exists("uyuni-health-check", server=server):
            console.log("[yellow]Skipped as the uyuni-health-check pod does not exist")
        else:
            podman(
                [
                    "pod",
                    "stop",
                    "uyuni-health-check",
                ],
                server,
                console=console,
            )
            console.log("[green]Containers have been stopped")
    console.print(Markdown("# Execution Finished"))


@cli.command()
@click.pass_context
def start(ctx):
    """
    Start the containers on the server if already present

    :param server: server where to start the containers
    """
    server = ctx.obj["server"]
    with console.status(status=None):
        console.log("[bold]Starting uyuni-health-check containers")
        if not pod_exists("uyuni-health-check", server=server):
            console.log("[yellow]Skipped as the uyuni-health-check pod does not exist")
        else:
            podman(
                [
                    "pod",
                    "start",
                    "uyuni-health-check",
                ],
                server,
                console=console,
            )
            console.log("[green]Containers have been started")
    console.print(Markdown("# Execution Finished"))


@cli.command()
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
    "--logs",
    is_flag=True,
    help="Show the error logs",
)
@click.option(
    "--since",
    default=7,
    type=int,
    help="Show logs from last X days. (Default: 7)",
)
@click.option(
    "-c",
    "--clean",
    is_flag=True,
    help="Remove containers after execution",
)
@click.pass_context
def run(ctx, exporter_port, loki, logs, since, clean):
    """
    Start execution of Uyuni Health Check

    Build the necessary containers, deploy them, get the metrics and display them

    :param server: the server to connect to
    :param exporter_port: uyuni health exporter metrics port
    :param loki: URL to a loki instance. Setting it will skip the promtail and loki deployments
    """
    server = ctx.obj["server"]
    verbose = ctx.obj["verbose"]
    supportconfig_path = ctx.obj["supportconfig_path"]

    try:
        with console.status(status=None):
            console.log("[bold]Creating POD for containers")
            create_pod(server)

            console.log("[bold]Building logcli image")
            build_loki_image("logcli", server=server, verbose=verbose)

            console.log("[bold]Deploying promtail and Loki")
            if not loki:
                run_loki(server, supportconfig_path=supportconfig_path, verbose=verbose)
            else:
                console.log(f"[yellow]Skipped to use Loki at {loki}")

            console.log("[bold]Preparing prometheus exporter")
            prepare_exporter(
                server, supportconfig_path=supportconfig_path, verbose=verbose
            )

            console.log("[bold]Preparing grafana")
            prepare_grafana(server, verbose=verbose)

            console.log("[bold]Preparing prometheus")
            prepare_prometheus(server, verbose=verbose)

            if not supportconfig_path:
                # Fetch metrics from uyuni-health-exporter
                console.log("[bold]Fetching metrics from uyuni-health-exporter")
                metrics = fetch_metrics_from_uyuni_health_exporter(
                    console, server, exporter_port
                )

                # Check spacewalk services
                console.log("[bold]Checking spacewalk services")
                check_spacewalk_services(server, verbose=verbose)

                # Check spacewalk services
                console.log("[bold]Checking postgresql service")
                check_postgres_service(server)
            else:
                # Fetch metrics from supportconfig-exporter
                console.log("[bold]Fetching metrics from supportconfig-exporter")
                metrics = fetch_metrics_from_supportconfig_exporter(
                    console, server, exporter_port
                )

            console.log(
                "[bold]Waiting for Loki and promtail to be ready. This may take some time..."
            )
            host = server or "localhost"
            wait_loki_init(host, verbose=verbose)

        # Gather and show the data
        console.print(Markdown("# Results"))
        if supportconfig_path:
            show_supportconfig_metrics(metrics, console)
        else:
            show_uyuni_live_server_metrics(metrics, console)

        show_relevant_hints(_hints, console)
        console.print(Markdown("## Relevant Errors"))
        loki_url = loki if loki else f"http://{host}:3100"
        show_error_logs_stats(loki_url, since, console)
        if logs:
            show_full_error_logs(loki_url, since, console)

    except HealthException as err:
        console.log("[red bold]" + str(err))
    finally:
        if clean:
            clean_server(server)

    if not clean:
        grafana_host = server or "localhost"
        console.print(
            Panel(
                Text(
                    f"You can visit now the live dashboards to see metrics and relevant errors at http://{grafana_host}:3000/dashboards",
                    justify="center",
                )
            ),
            style="italic green",
        )
    console.print(Markdown("# Execution Finished"))


def main():
    print(Markdown("# Uyuni Health Check"))
    cli()


if __name__ == "__main__":
    main()
