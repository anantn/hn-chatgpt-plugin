import os
import copy
import time
import psutil
from datetime import datetime


def get_mem_info():
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    return f"{mem_info.rss / (1024 * 1024):5.2f}MB"


def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[{timestamp}] {msg}")


def log_with_mem(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[{timestamp}] {get_mem_info()}: {msg}")


def get_db_stats(db_conn, embed_conn):
    stats = {}
    cursor = db_conn.cursor()
    cursor.execute("SELECT MAX(id), COUNT(*) FROM items")
    stats["db_max_id"], stats["db_total_items"] = cursor.fetchone()
    cursor.execute("SELECT MAX(id) FROM items WHERE type='story'")
    stats["db_max_story_id"] = cursor.fetchone()[0]
    cursor.close()

    cursor = embed_conn.cursor()
    cursor.execute("SELECT MAX(story), COUNT(DISTINCT story), COUNT(*) FROM embeddings")
    (
        stats["db_max_story"],
        stats["db_total_doc"],
        stats["db_total_embed"],
    ) = cursor.fetchone()
    cursor.close()
    return stats


def print_db_stats(db_conn, embed_conn):
    stats = get_db_stats(db_conn, embed_conn)
    print(f"{stats['db_max_id']:8}: Max item in db")
    print(f"{stats['db_total_items']:8}: Total items in db")
    print(f"{stats['db_max_story_id']:8}: Max story in db")
    print(f"{stats['db_max_story']:8}: Max story embedded")
    print(f"{stats['db_total_doc']:8}: Total docs embedded")
    print(f"{stats['db_total_embed']:8}: Total embeddings\n")


def get_time_now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def print_since(input):
    if input == 0:
        return "Never"
    dt_format = "%Y-%m-%d %H:%M:%S"
    dt_obj = datetime.strptime(input, dt_format)

    now = datetime.now()
    time_diff = now - dt_obj
    hours, remainder = divmod(time_diff.seconds, 3600)
    minutes = remainder // 60

    return f"{input} ({hours}:{minutes:02d} hours ago)"


class LogPhase:
    def __init__(self, name):
        self.start_time = time.time()
        self.name = name

    def stop(self):
        timestamp = get_time_now()
        elapsed = time.time() - self.start_time
        print(f"[{timestamp}] {get_mem_info()}: ({elapsed:2.2f}s) {self.name}\n")


class Telemetry:
    def __init__(self) -> None:
        self.metrics = {
            "counters": {
                "updates": 0,
                "embed_runs": 0,
                "items_updated": 0,
                "users_updated": 0,
                "total_affected_stories": 0,
                "total_embedded_stories": 0,
            },
            "times": {"last_update": 0, "last_embed": 0, "start_time": get_time_now()},
            "memory": {},
            "flags": {},
        }

    def connect(self, db_conn, embed_conn, sync_server, encoder):
        self.db_conn = db_conn
        self.embed_conn = embed_conn
        self.sync_server = sync_server
        self.encoder = encoder
        self.metrics["db"] = get_db_stats(db_conn, embed_conn)

    def inc(self, metric, amount=1):
        self.metrics["counters"][metric] += amount
        if metric == "updates":
            self.metrics["times"]["last_update"] = get_time_now()
        if metric == "total_embedded_stories":
            self.metrics["counters"]["embed_runs"] += 1
            self.metrics["times"]["last_embed"] = get_time_now()

    def report(self, update_db=False):
        report = copy.deepcopy(self.metrics)
        report["counters"]["cache_size"] = len(self.encoder.cache)
        report["counters"]["cache_hits"] = self.encoder.cache_hits

        report["memory"]["used"] = psutil.virtual_memory().used >> 20
        report["memory"]["free"] = psutil.virtual_memory().free >> 20

        report["flags"]["embed_realtime"] = self.sync_server.embed_realtime
        report["flags"][
            "initial_fetch_completed"
        ] = self.sync_server.initial_fetch_completed

        for key in report["times"]:
            report["times"][key] = print_since(report["times"][key])

        if update_db:
            self.metrics["db"] = get_db_stats(self.db_conn, self.embed_conn)
            report["db"] = self.metrics["db"]
        return report
