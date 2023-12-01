<!--
SPDX-FileCopyrightText: 2023 SUSE LLC

SPDX-License-Identifier: Apache-2.0
-->

---
name: New Data Request template
about: Request more data in the tool output
title: ''
labels: data, enhancement
assignees: ''

---

*Before adding a data request ensure the tool does not show it already*

# Where to find the data

Tell us where the data are to be found on an Uyuni server. It could be log files, journald, the server database, prometheus metrics or the output of some command.

# How to process and interpret the data

Tell us how to figure out if the data are indicating a bad state of the server. We basically need to know how  to process the raw data and what thresholds or values indicate a bad state
