# SPDX-FileCopyrightText: 2023 SUSE LLC
#
# SPDX-License-Identifier: Apache-2.0

FROM registry.suse.com/bci/bci-micro:latest

LABEL org.opencontainers.image.authors="cbosdonnat@suse.com"

COPY promtail-linux-amd64 /usr/bin/promtail
RUN chmod a+x /usr/bin/promtail

ENTRYPOINT ["promtail"]
CMD ["--config.file=/etc/promtail/config.yml"]

