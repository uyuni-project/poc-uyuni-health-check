# uyuni-health-check

A tool providing metrics and logs from an Uyuni server to show its health status.

## Requirements

* `podman`
* Python 3

## Building

    python3 -m build

##  Running

For now the tool only deploy the prometheus exporter and builds the logcli container image.
The loki and promtail instances are manually setup, so the tool could be run with a similar command:

    python3 src/uyuni_health_check/main.py --loki 'http://demo-grafana.mgr.lab:3100' --server demo-srv.mgr.lab

## TODO

* Deploy promtail and loki using containers if not using `--loki`
* Cleaner and more compact final view of the data
* Gather more data like:
  * the state of the systemd services
  * Scrape `journal` logs to look for OOM killers
  * ...
* Loki / promtail fixes
  * Remove the timestamp for the log lines for cleaner output
  * Parse and unify the log levels in promtail
    The issue here is that `Critical` is showing in some log messages as parts of class names and probably we are missing some log entries due to slightly different wordings.

## Authors

- Pablo Suárez Hernández - <psuarezhernandez@suse.de>
- Cédric Bosdonnat - <cbosdonnat@suse.com>
