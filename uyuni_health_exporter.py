import sys
import os
import time
import yaml

import salt.config
import salt.runner

from prometheus_client.core import GaugeMetricFamily, REGISTRY
from prometheus_client import start_http_server


class UyuniDataGatherer(object):
    def __init__(self):
        self._init_runner()
        self.refresh()

    def _init_runner(self):
        self.master_opts = salt.config.client_config('/etc/salt/master')
        self.master_opts["quiet"] = True
        self.runner = salt.runner.RunnerClient(self.master_opts)

    def execute_db_query(self, query: str) -> list:
        start = time.time()
        kwargs = {
            "user": self.master_opts["postgres"]["user"],
            "password": self.master_opts["postgres"]["pass"],
            "host": self.master_opts["postgres"]["host"],
            "port": self.master_opts["postgres"]["port"],
            "maintenance_db": self.master_opts["postgres"]["db"],
        }
        print("* execute db query {} - took: {} seconds".format(query, time.time() - start))
        return self.runner.cmd("salt.cmd", ["postgres.psql_query", query], kwarg=kwargs)

    def find_salt_jobs(self) -> dict:
        start = time.time()
        ret = self.runner.cmd("jobs.list_jobs")
        print("* find_salt_jobs took: {} seconds".format(time.time() - start))
        return ret

    def summarize_salt_jobs(self, jobs: dict) -> dict:
        summary = {
            "functions": {},
            "total": 0,
        }
        for jid in jobs:
            if jobs[jid]['Function'] == "state.apply" and jobs[jid]['Arguments'].get(0, {}).get("mods"):
                tag = "{}_{}".format(jobs[jid]['Function'], jobs[jid]['Arguments'][0]["mods"])
            else:
                tag = jobs[jid]['Function']
            summary['functions'].setdefault(tag, 0)
            summary['functions'][tag] += 1
            summary['total'] += 1
        return summary

    def refresh(self):
        self.channels = self.execute_db_query("select count(*) from rhnchannel")
        self.packages = self.execute_db_query("select count(*) from rhnpackage")
        self.systems = self.execute_db_query("select count(*) from rhnserver")
        self.actions = self.execute_db_query("select count(*) from rhnserveraction")
        self.actions_last_day = self.execute_db_query("select * from rhnserveraction WHERE created >= NOW() - '1 day'::INTERVAL")
        self.failed_actions_last_day = [x for x in self.actions_last_day if x["status"] == "3"]
        self.completed_actions_last_day = [x for x in self.actions_last_day if x["status"] == "2"]
        self.salt_jobs = self.summarize_salt_jobs(self.find_salt_jobs())
        sys.stdout.flush()

class UyuniMetricsCollector(object):
    def __init__(self, gatherer):
        self.gatherer = gatherer

    def collect(self):
        channels = self.gatherer.channels
        packages = self.gatherer.packages
        systems = self.gatherer.systems
        actions = self.gatherer.actions
        actions_last_day = self.gatherer.actions_last_day
        failed_actions_last_day = self.gatherer.failed_actions_last_day
        completed_actions_last_day = self.gatherer.completed_actions_last_day
        salt_jobs = self.gatherer.salt_jobs

        gauge = GaugeMetricFamily("salt_jobs", "Salt jobs in the last 24 hours", labels=["salt_jobs"])
        gauge.add_metric(['salt_jobs_total'], salt_jobs["total"])
        for func in salt_jobs["functions"]:
            gauge.add_metric(['salt_jobs_{}_total'.format(func)], salt_jobs["functions"][func])
        yield gauge

        gauge2 = GaugeMetricFamily("uyuni_summary", "Some relevant metrics in the context of Uyuni", labels=["uyuni_summary"])
        gauge2.add_metric(['uyuni_summary_channels_total'], int(channels[0]["count"]))
        gauge2.add_metric(['uyuni_summary_packages_total'], int(packages[0]["count"]))
        gauge2.add_metric(['uyuni_summary_systems_total'], int(systems[0]["count"]))
        gauge2.add_metric(['uyuni_summary_actions_total'], int(actions[0]["count"]))
        gauge2.add_metric(['uyuni_summary_actions_last_24hours_total'], len(actions_last_day))
        gauge2.add_metric(['uyuni_summary_actions_failed_last_24hours_total'], len(failed_actions_last_day))
        gauge2.add_metric(['uyuni_summary_actions_completed_last_24hours_total'], len(completed_actions_last_day))
        yield gauge2


if __name__ == "__main__":
    port = 9000
    frequency = 60
    if os.path.exists('config.yml'):
        with open('config.yml', 'r') as config_file:
            try:
                config = yaml.safe_load(config_file)
                port = int(config['port'])
                frequency = config['scrape_frequency']
            except yaml.YAMLError as error:
                print(error)

    start_http_server(port)
    uyuni_data_gatherer = UyuniDataGatherer()
    REGISTRY.register(UyuniMetricsCollector(uyuni_data_gatherer))
    while True:
        # period between collection
        time.sleep(frequency)
        uyuni_data_gatherer.refresh()
