# SPDX-FileCopyrightText: 2023 SUSE LLC
#
# SPDX-License-Identifier: Apache-2.0

FROM registry.suse.com/bci/bci-micro:latest

LABEL org.opencontainers.image.authors="cbosdonnat@suse.com"

COPY logcli-linux-amd64 /usr/bin/logcli
RUN chmod a+x /usr/bin/logcli

ENTRYPOINT ["logcli"]
