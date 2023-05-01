import os
import time
import psutil


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


class LogPhase:
    def __init__(self, name):
        self.start_time = time.time()
        self.name = name

    def stop(self):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        elapsed = time.time() - self.start_time
        print(f"[{timestamp}] {get_mem_info()}: ({elapsed:2.2f}s) {self.name}\n")
