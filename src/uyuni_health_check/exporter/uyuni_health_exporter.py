import os
import signal
import sys
import time
from multiprocessing import Process, Queue

import salt.config
import yaml
from prometheus_client import start_http_server
from prometheus_client.core import REGISTRY, GaugeMetricFamily


def sigterm_handler(signal, frame):
    print("Detected SIGTERM. Exiting.")
    sys.exit(0)


signal.signal(signal.SIGTERM, sigterm_handler)


def runner_process(queue):
    gatherer = UyuniDataGathererTasks()
    gatherer.refresh()
    queue.put(gatherer.get_data())


class UyuniDataGatherer(object):
    def __init__(self):
        self.data = {}
        self.refresh()

    def __getattr__(self, item):
        return self.data[item]

    def refresh(self):
        q = Queue()
        process = Process(target=runner_process, args=(q,))
        process.start()
        process.join()
        self.data = q.get()


class UyuniDataGathererTasks(object):
    def __init__(self):
        self._init_runner()
        self.refresh()

    def _init_runner(self):
        import salt.runner

        self.master_opts = salt.config.master_config("/etc/salt/master")
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
        ret = self.runner.cmd("salt.cmd", ["postgres.psql_query", query], kwarg=kwargs)
        print(
            "* execute db query {} - took: {} seconds".format(
                query, time.time() - start
            )
        )
        return ret

    def list_active_salt_jobs(self) -> dict:
        start = time.time()
        ret = self.runner.cmd("jobs.active")
        print("* list_active_salt_jobs took: {} seconds".format(time.time() - start))
        return ret

    def find_salt_jobs(self) -> dict:
        start = time.time()
        ret = self.runner.cmd("jobs.list_jobs")
        print("* find_salt_jobs took: {} seconds".format(time.time() - start))
        return ret

    def test_ping(self) -> dict:
        start = time.time()
        self.runner.cmd("salt.cmd", ["test.ping"])
        end = time.time()
        print("* test_ping took: {} seconds".format(end - start))
        return end - start

    def salt_alived_minions(self) -> list:
        start = time.time()
        ret = self.runner.cmd("manage.alived")
        print("* salt manage.alived took: {} seconds".format(time.time() - start))
        return ret

    def summarize_salt_jobs(self, jobs: dict) -> dict:
        summary = {
            "functions": {},
        }
        for jid in jobs:
            if jobs[jid]["Function"] == "state.apply" and jobs[jid]["Arguments"]:
                if isinstance(jobs[jid]["Arguments"][0], dict) and jobs[jid][
                    "Arguments"
                ][0].get("mods"):
                    tag = "{}_{}".format(
                        jobs[jid]["Function"],
                        "_".join(jobs[jid]["Arguments"][0]["mods"]),
                    )
                else:
                    tag = "{}_{}".format(
                        jobs[jid]["Function"], jobs[jid]["Arguments"][0]
                    )
            else:
                tag = jobs[jid]["Function"]
            summary["functions"].setdefault(tag, 0)
            summary["functions"][tag] += 1
        return summary

    def refresh(self):
        self.channels = self.execute_db_query("select count(*) from rhnchannel")
        self.packages = self.execute_db_query("select count(*) from rhnpackage")
        self.systems = self.execute_db_query("select count(*) from rhnserver")
        self.actions = self.execute_db_query("select count(*) from rhnserveraction")
        self.actions_pending = self.execute_db_query(
            "select count(*) from rhnserveraction WHERE status = 1"
        )
        self.actions_last_day = self.execute_db_query(
            "select * from rhnserveraction WHERE created >= NOW() - '1 day'::INTERVAL"
        )
        self.failed_actions_last_day = [
            x for x in self.actions_last_day if x["status"] == "3"
        ]
        self.completed_actions_last_day = [
            x for x in self.actions_last_day if x["status"] == "2"
        ]
        self.salt_jobs = self.summarize_salt_jobs(self.find_salt_jobs())
        self.active_salt_jobs = self.summarize_salt_jobs(self.list_active_salt_jobs())
        self.master_test_ping = self.test_ping()
        self.zeromq_alived_minions = self.salt_alived_minions()
        sys.stdout.flush()

    def get_data(self):
        return {
            "channels": self.channels,
            "packages": self.packages,
            "systems": self.systems,
            "actions": self.actions,
            "actions_pending": self.actions_pending,
            "actions_last_day": self.actions_last_day,
            "failed_actions_last_day": self.failed_actions_last_day,
            "completed_actions_last_day": self.completed_actions_last_day,
            "salt_jobs": self.salt_jobs,
            "active_salt_jobs": self.active_salt_jobs,
            "master_test_ping": self.master_test_ping,
            "zeromq_alived_minions": self.zeromq_alived_minions,
        }


class UyuniMetricsCollector(object):
    def __init__(self, gatherer):
        self.gatherer = gatherer

    def collect(self):
        channels = self.gatherer.channels
        packages = self.gatherer.packages
        systems = self.gatherer.systems
        actions = self.gatherer.actions
        actions_pending = self.gatherer.actions_pending
        actions_last_day = self.gatherer.actions_last_day
        failed_actions_last_day = self.gatherer.failed_actions_last_day
        completed_actions_last_day = self.gatherer.completed_actions_last_day
        salt_jobs = self.gatherer.salt_jobs
        active_salt_jobs = self.gatherer.active_salt_jobs
        master_test_ping = self.gatherer.master_test_ping
        zeromq_alived_minions = self.gatherer.zeromq_alived_minions

        gauge = GaugeMetricFamily(
            "salt_jobs", "Salt jobs in the last 24 hours", labels=["name", "fun"]
        )
        for func in active_salt_jobs["functions"]:
            gauge.add_metric(
                ["salt_jobs_active_{}_total".format(func), func],
                active_salt_jobs["functions"][func],
            )
        for func in salt_jobs["functions"]:
            gauge.add_metric(
                ["salt_jobs_{}_total".format(func), func], salt_jobs["functions"][func]
            )
        yield gauge

        gauge2 = GaugeMetricFamily(
            "salt_master_stats",
            "Some stats from Salt master",
            labels=["name"],
        )
        gauge2.add_metric(["salt_master_test_ping_duration_seconds"], master_test_ping)
        gauge2.add_metric(
            ["salt_master_zeromq_alived_minions_total"], len(zeromq_alived_minions)
        )
        for minion in zeromq_alived_minions:
            gauge2.add_metric(["salt_master_zeromq_alived_minion_{}".format(minion)], 1)
        yield gauge2

        gauge3 = GaugeMetricFamily(
            "uyuni_summary",
            "Some relevant metrics in the context of Uyuni",
            labels=["name"],
        )
        gauge3.add_metric(["uyuni_summary_channels_total"], int(channels[0]["count"]))
        gauge3.add_metric(["uyuni_summary_packages_total"], int(packages[0]["count"]))
        gauge3.add_metric(["uyuni_summary_systems_total"], int(systems[0]["count"]))
        gauge3.add_metric(
            ["uyuni_summary_actions_pending_total"], int(actions_pending[0]["count"])
        )
        gauge3.add_metric(["uyuni_summary_actions_total"], int(actions[0]["count"]))
        gauge3.add_metric(
            ["uyuni_summary_actions_last_24hours_total"], len(actions_last_day)
        )
        gauge3.add_metric(
            ["uyuni_summary_actions_failed_last_24hours_total"],
            len(failed_actions_last_day),
        )
        gauge3.add_metric(
            ["uyuni_summary_actions_completed_last_24hours_total"],
            len(completed_actions_last_day),
        )
        yield gauge3


def main():
    print("Uyuni Health Exporter started")
    port = 9000
    frequency = 60
    if os.path.exists("config.yml"):
        with open("config.yml", "r") as config_file:
            try:
                config = yaml.safe_load(config_file)
                port = int(config["port"])
                frequency = config["scrape_frequency"]
            except yaml.YAMLError as error:
                print(error)

    start_http_server(port)
    uyuni_data_gatherer = UyuniDataGatherer()
    REGISTRY.register(UyuniMetricsCollector(uyuni_data_gatherer))
    print("Uyuni Health Exporter is ready")
    while True:
        # period between collection
        time.sleep(frequency)
        uyuni_data_gatherer.refresh()


if __name__ == "__main__":
    main()
