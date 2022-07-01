import re

import click
import requests
from rich import print
from rich.markdown import Markdown
from rich.pretty import pprint


def show_data(metrics: dict):
    """
    Gather the data from the exporter and loki and display them
    """
    # TODO Gather the data
    # TODO Display them!
    print()
    print(Markdown("# Results"))

    pprint(metrics)
    print("[italic red]Data will soon be output here[/italic red]")


def build():
    """
    Build the container images
    """


def deploy_exporter(server, port):
    """
    Deploy the prometheus exporter on the server
    """


def deploy_promtail(server, port):
    """
    Deploy promtail on the server
    """


def run_loki():
    """
    Run loki to aggregate the logs
    """


@click.command()
@click.option("-p", "--port", type=int, default=22, help="server SSH port")
@click.option(
    "-ep",
    "--exporter-port",
    type=int,
    default=9000,
    help="uyuni health exporter metrics port",
)
@click.argument("server")
def health_check(server, port, exporter_port):
    """
    Build the necessary containers, deploy them, get the metrics and display them

    :param server: the server to connect to
    :param port: the SSH port of the server
    :param exporter_port: uyuni health exporter metrics port
    """

    # TODO build the containers
    print(Markdown("- Building containers images"))
    build()

    # TODO Deploy the exporter
    print(Markdown("- Deploying uyuni-health-exporter container"))
    deploy_exporter(server, port)

    # TODO Deploy promtail and Loki
    print(Markdown("- Deploying promtail and Loki"))
    deploy_promtail(server, port)
    run_loki()

    # Fetch metrics from uyuni-health-exporter
    print(Markdown("- Fetching metrics from uyuni-health-exporter"))
    metrics = fetch_metrics_exporter(server, exporter_port)

    # Gather and show the data
    show_data(metrics)


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
        metrics["salt_jobs"][m[0]] = m[2]

    for m in salt_master_metrics:
        metrics["salt_master_stats"][m[0]] = m[1]

    for m in uyuni_metrics:
        metrics["uyuni_summary"][m[0]] = m[1]

    return metrics


if __name__ == "__main__":
    print(Markdown("# Uyuni Health Check"))
    health_check()
