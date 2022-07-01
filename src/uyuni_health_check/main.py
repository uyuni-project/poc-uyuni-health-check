import os.path
import re
import subprocess

import click
import requests
from rich import print
from rich.console import Console
from rich.markdown import Markdown
from rich.pretty import pprint
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table


class HealthException(Exception):
    def __init__(self, message):
        super().__init__(message)


def show_data(metrics: dict):
    """
    Gather the data from the exporter and loki and display them
    """
    # TODO Gather the data
    # TODO Display them!
    print()
    print(Markdown("# Results"))
    show_salt_jobs_summary(metrics)
    show_salt_master_stats(metrics)
    show_uyuni_summary(metrics)
    print("[italic red]Data will soon be output here[/italic red]")


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


def build_image(name, path=None):
    """
    Build a container image
    """
    with Progress(
        SpinnerColumn(),
        TextColumn("{task.description}"),
        TimeElapsedColumn(),
    ) as progress:
        exporter_task = progress.add_task(f"Build {name} image")

        expanded_path = os.path.join(os.path.dirname(__file__), path or name)
        progress.start_task(exporter_task)
        try:
            process = subprocess.Popen(
                ["podman", "build", "-t", name, "."],
                cwd=expanded_path,
                stdout=subprocess.PIPE,
            )
            for line in process.stdout:
                progress.log(line.decode().strip())
            ret = process.wait()
            if ret != 0:
                raise HealthException(f"Failed to build {name} image")
        except OSError:
            raise HealthException("podman is required to build the container images")
        finally:
            progress.stop_task(exporter_task)


def build():
    """
    Build the container images
    """
    build_image("uyuni-health-exporter", "exporter")
    build_image("logcli")


def deploy_exporter():
    """
    Deploy the prometheus exporter on the server
    """
    id_process = subprocess.run(["id", "-g", "salt"], stdout=subprocess.PIPE)
    if id_process.returncode != 0:
        raise HealthException(
            "Salt is not installed... is the tool running on an Uyuni server?"
        )
    salt_gid = id_process.stdout.decode().strip()

    cmd = [
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
    try:
        ps_process = subprocess.run(
            ["podman", "ps", "-f", "name=eloquent_euler", "--quiet"],
            stdout=subprocess.PIPE,
        )
        if ps_process.stdout == "":
            subprocess.run(cmd, check=True)
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
@click.argument("server")
def health_check(server, exporter_port):
    """
    Build the necessary containers, deploy them, get the metrics and display them

    :param server: the server to connect to
    :param exporter_port: uyuni health exporter metrics port
    """
    console = Console()
    try:
        print(Markdown("- Building containers images"))
        build()

        # TODO Deploy the exporter
        print(Markdown("- Deploying uyuni-health-exporter container"))
        deploy_exporter()

        # TODO Deploy promtail and Loki
        print(Markdown("- Deploying promtail and Loki"))
        deploy_promtail()
        run_loki()

        # Fetch metrics from uyuni-health-exporter
        print(Markdown("- Fetching metrics from uyuni-health-exporter"))
        metrics = fetch_metrics_exporter(server, exporter_port)

        # Gather and show the data
        show_data(metrics)
    except HealthException as err:
        console.print("[red bold]" + str(err))


def fetch_metrics_exporter(host, port=9000):
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
