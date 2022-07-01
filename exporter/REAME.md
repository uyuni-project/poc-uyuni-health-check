# Requirements

The "uyuni-health-exporter" runs within a container in the Uyuni server. You would need to install the following packages:

- podman

# Building the container image


    podman build --tag=uyuni-health-exporter -f ./Dockerfile 

# Running the container

To run the container and expose the metrics you need to execute:

    podman run -u salt:$(id -g salt) -d --rm --network="host" -v /etc/salt:/etc/salt:ro -v /var/cache/salt/:/var/cache/salt/ uyuni-health-exporter

Then your metrics will appear at port "9000" in your Uyuni server.

# TODO
- Fix issue with active salt jobs metrics
- Add more relevant metrics for Salt and Uyuni
- Enhance performance for Salt Jobs and Uyuni gathering
