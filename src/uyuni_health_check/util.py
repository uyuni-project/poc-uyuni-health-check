# SPDX-FileCopyrightText: 2023 SUSE LLC
#
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess

from jinja2 import Environment, FileSystemLoader
from rich.text import Text


class HealthException(Exception):
    def __init__(self, message):
        super().__init__(message)


def ssh_call(server, cmd, console=None, quiet=True):
    """
    Run a command over SSH.

    If the server value is `None` run the command locally.

    For now the function assumes passwordless connection to the server on default SSH port.
    Use SSH agent and config to adjust if needed.
    """
    if server and quiet:
        ssh_cmd = ["ssh", "-q", server] + cmd
    elif server:
        ssh_cmd = ["ssh", server] + cmd
    else:
        ssh_cmd = cmd
    process = subprocess.Popen(
        ssh_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        universal_newlines=True,
    )

    if console and not quiet:
        while True:
            line = process.stdout.readline() or process.stderr.readline()
            if not line:
                break
            console.log(Text.from_ansi(line.strip()))

    returncode = process.wait()
    if returncode == 127:
        raise OSError(f"Command not found: {cmd[0]}")
    elif returncode == 125:
        raise HealthException(
            "An error had happened while running Podman. Maybe you don't have enough privileges to run it."
        )
    elif returncode == 255:
        raise HealthException(f"There has been an error running: {cmd}")
    return process


def podman(cmd, server=None, console=None):
    """
    Run a podman command

    :param cmd: the command in an array format without the initial "podman" part
    """
    try:
        return ssh_call(server, ["podman"] + cmd, console, quiet=not console)
    except OSError:
        raise HealthException(
            "podman is required {}".format("on " + server if server else "")
        )


def render_promtail_cfg(supportconfig_path=None):
    """
    Render promtail configuration file

    :param supportconfig_path: render promtail configuration based on this path to a supportconfig
    """
    loader = FileSystemLoader(os.path.join(os.path.dirname(__file__), "promtail"))
    env = Environment(loader=loader)
    template = env.get_template("promtail.yaml.j2")
    promtail_cfg = os.path.join(os.path.dirname(__file__), "promtail", "promtail.yaml")

    if supportconfig_path:
        opts = {
            "rhn_logs_path": os.path.join(
                supportconfig_path, "spacewalk-debug/rhn-logs/rhn/"
            ),
            "cobbler_logs_file": os.path.join(
                supportconfig_path, "spacewalk-debug/cobbler-logs/cobbler.log"
            ),
            "salt_logs_path": os.path.join(
                supportconfig_path, "spacewalk-debug/salt-logs/salt/"
            ),
            "postgresql_logs_path": os.path.join(
                supportconfig_path, "spacewalk-debug/database/"
            ),
            "apache2_logs_path": os.path.join(
                supportconfig_path, "spacewalk-debug/httpd-logs/apache2/"
            ),
        }
    else:
        opts = {
            "rhn_logs_path": "/var/log/rhn/",
            "cobbler_logs_file": "/var/log/cobbler.log",
            "salt_logs_path": "/var/log/salt/",
            "apache2_logs_path": "/var/log/apache2/",
            "postgresql_logs_path": "/var/lib/pgsql/data/log/",
        }

    # Write rendered promtail configuration file
    with open(promtail_cfg, "w") as promtail_cfg_file:
        promtail_cfg_file.write(template.render(**opts))

    return promtail_cfg


def render_supportconfig_exporter_cfg(supportconfig_path=None):
    loader = FileSystemLoader(
        os.path.join(os.path.dirname(__file__), "supportconfig_exporter")
    )
    env = Environment(loader=loader)
    template = env.get_template("config.yml.j2")
    exporter_cfg = os.path.join(
        os.path.dirname(__file__), "supportconfig_exporter", "config.yml"
    )

    opts = {"supportconfig_path": supportconfig_path}

    # Write rendered promtail configuration file
    with open(exporter_cfg, "w") as exporter_cfg_file:
        exporter_cfg_file.write(template.render(**opts))

    return exporter_cfg
