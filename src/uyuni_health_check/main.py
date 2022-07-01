from pprint import pprint

from uyuni_health_exporter import UyuniDataGatherer

gatherer = UyuniDataGatherer()


def find_salt_jobs() -> dict:
    return runner.cmd("jobs.list_jobs")


def summary_salt_jobs(jobs: dict) -> dict:
    jobs = find_salt_jobs()
    summary = {
        "functions": {},
        "total": 0,
    }
    for jid in jobs:
        summary["functions"].setdefault(jobs[jid]["Function"], 0)
        summary["functions"][jobs[jid]["Function"]] += 1
        summary["total"] += 1
    return summary


def execute_db_query(query: str) -> list:
    kwargs = {
        "user": master_opts["postgres"]["user"],
        "password": master_opts["postgres"]["pass"],
        "host": master_opts["postgres"]["host"],
        "port": master_opts["postgres"]["port"],
        "maintenance_db": master_opts["postgres"]["db"],
    }
    return runner.cmd("salt.cmd", ["postgres.psql_query", query], kwarg=kwargs)


def summary_db():
    channels = execute_db_query("select count(*) from rhnchannel")
    packages = execute_db_query("select count(*) from rhnpackage")
    systems = execute_db_query("select count(*) from rhnserver")
    actions = execute_db_query("select count(*) from rhnserveraction")
    actions_last_day = execute_db_query(
        "select * from rhnserveraction WHERE created >= NOW() - '1 day'::INTERVAL"
    )
    failed_actions_last_day = [x for x in actions_last_day if x["status"] == "3"]
    completed_actions_last_day = [x for x in actions_last_day if x["status"] == "2"]

    print("* Total of channels: {}".format(channels[0]["count"]))
    print("* Total of packages: {}".format(packages[0]["count"]))
    print("* Total of systems: {}".format(systems[0]["count"]))
    print("* Total of actions: {}".format(actions[0]["count"]))
    print("* Total of actions in last 24 hours: {}".format(len(actions_last_day)))
    print("   - Failed: {}".format(len(failed_actions_last_day)))
    print("   - Completed: {}".format(len(completed_actions_last_day)))


def run():
    """
    Main entry point for the tool
    """
    summary_db()
    jobs = find_salt_jobs()
    pprint(summary_salt_jobs(jobs))


if __name__ == "__main__":
    run()
