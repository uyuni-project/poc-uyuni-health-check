import subprocess

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
        universal_newlines=True,
    )
    if console:
        while True:
            line = process.stdout.readline() or process.stderr.readline()
            if not line:
                break
            console.log(Text.from_ansi(line.strip()))
    returncode = process.wait()
    if returncode == 127:
        raise OSError(f"Command not found: {cmd[0]}")
    elif returncode == 255:
        raise HealthException(f"There has been an error running: {cmd}")
    return process


def podman(cmd, server=None, console=None):
    """
    Run a podman command

    :param cmd: the command in an array format without the initial "podman" part
    """
    try:
        return ssh_call(server, ["podman"] + cmd, console)
    except OSError:
        raise HealthException(
            "podman is required {}".format("on " + server if server else "")
        )
