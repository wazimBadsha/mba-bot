import json

def load_config(path="config.json"):
    with open(path, "r") as f:
        return json.load(f)

def watch_config(path="config.json"):
    import os, time
    last_mtime = os.path.getmtime(path)
    while True:
        new_mtime = os.path.getmtime(path)
        if new_mtime != last_mtime:
            cfg = load_config(path)
            yield cfg
            last_mtime = new_mtime
        time.sleep(1)