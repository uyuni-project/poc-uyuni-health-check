<!--
SPDX-FileCopyrightText: 2023 SUSE LLC

SPDX-License-Identifier: Apache-2.0
-->

[![REUSE status](https://api.reuse.software/badge/git.fsfe.org/reuse/api)](https://api.reuse.software/info/git.fsfe.org/reuse/api)

# uyuni-health-check <img src="https://img.shields.io/badge/EXPERIMENTAL-WIP-red" />

A tool providing dashboard, metrics and logs from an Uyuni server to show its health status.

## Requirements

* `python3`
* `python3-pip`
* `python3-virtualenv` (optional)

NOTE: `podman` is required on the Uyuni server where we want to get its health status.

## Building and installing

### In the local system

    # pip3 install .

### Using a virtualenv

    # virtualenv venv
    # . venv/bin/activate
    # pip3 install .

##  Running

    # uyuni-health-check --help
    ╔═════════════════════════════════════════════════════════════════════════════════╗
    ║                               Uyuni Health Check                                ║
    ╚═════════════════════════════════════════════════════════════════════════════════╝
    Usage: uyuni-health-check [OPTIONS] COMMAND [ARGS]...
    
    Options:
      -s, --server TEXT  Uyuni Server to connect to if not running directly on the
                         server
      -v, --verbose      Show more stdout, including image building
      --help             Show this message and exit.
    
    Commands:
      clean  Remove all the containers we spawned on the server
      run    Start execution of Uyuni Health Check
      start  Start the containers on the server if already present
      stop   Stop the containers on the server if already present

## Getting started

This tool takes care of building and deploying the necessary containers to scrape some metrics and logs from an Uyuni server. The "podman" CLI is required on the Uyuni server where we want to get the metrics and logs.

### You can run this tool directly on your running Uyuni server:

    uyuni-health-check run

### Or alternatively accessing Uyuni server via SSH:

    uyuni-health-check -s my_uyuni_server.fqdn run

It will create a POD where the following containers will run:

- uyuni-health-exporter
- grafana
- prometheus
- loki
- promtail
- logcli

After the metrics are collected and displayed in the CLI, the containers will keep running and collecting more metrics that will be stored on the running containers.

The tool is providing a provisioned grafana instance with a dashboard that exposes the metrics.

This is a summary of exposed ports:

- 3000 -> grafana UI
- 9000 -> metrics from `uyuni-health-exporter`
- 9100 -> loki API
- 9090 -> prometheus
- 9081 -> promtail

You can stop the containers with:

    uyuni-health-check stop

To clean and remove all containers:

    uyuni-health-check clean

## Security notes
After running this tool, and until containers are destroyed, the Grafana Dashboards (and other metrics) are exposing metrics and logs messages that may contain sensitive data and information to any non-root user in the system or to anyone that have access to this host in the network.

Please, be careful when running this tool.

## TODO

* Cleaner and more compact final view of the data
* Gather more data like:
  * the state of the systemd services
  * Scrape `journal` logs to look for OOM killers
  * ...
* Loki / promtail fixes
  * Remove the timestamp for the log lines for cleaner output
  * Parse and unify the log levels in promtail
    The issue here is that `Critical` is showing in some log messages as parts of class names and probably we are missing some log entries due to slightly different wordings.

## Changelog

* Add support to run the tool based on a "supportconfig" instead of running server.
* Deploy Loki, prometheus and grafana containers and dashboard.
* Enhance `uyuni-health-check` CLI - new commands added.
* Fix problems building Python package.
* Fix memory leak on uyuni-health-exporter container.
* Run all containers using same POD.

## Authors

- Pablo Suárez Hernández - <psuarezhernandez@suse.de>
- Cédric Bosdonnat - <cbosdonnat@suse.com>

## Notes

This project was started during [SUSE Hack Week 21](https://hackweek.opensuse.org/21/projects/create-tool-to-analyze-supportconfig-to-spot-common-suse-manager-issues)
