FROM registry.suse.com/bci/python:latest

LABEL maintainer Pablo Suárez Hernández <psuarezhernandez@suse.com>

COPY requirements.txt /opt/
COPY uyuni_health_exporter.py /opt/
COPY uyuni-health-check.py /opt/
COPY config.yml /opt/

RUN zypper -n ref
RUN zypper -n install python3-PyYAML python3-salt python3-pip postgresql
RUN pip3.6 install -r /opt/requirements.txt

ENTRYPOINT python3.6 /opt/uyuni_health_exporter.py
