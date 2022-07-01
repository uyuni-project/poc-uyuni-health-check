import click
from rich import print


def show_data():
    """
    Gather the data from the exporter and loki and display them
    """
    # TODO Gather the data
    # TODO Display them!
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
@click.argument("server")
def health_check(server, port):
    """
    Build the necessary containers, deploy them, get the metrics and display them

    :param server: the server to connect to
    :param port: the SSH port of the server
    """
    # TODO build the containers
    build()

    # TODO Deploy the exporter
    deploy_exporter(server, port)

    # TODO Deploy promtail and Loki
    deploy_promtail(server, port)
    run_loki()

    # Gather and show the data
    show_data()


if __name__ == "__main__":
    health_check()
