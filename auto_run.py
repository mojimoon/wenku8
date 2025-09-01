import os
import datetime
import subprocess

record_file = "last_success_date.txt"

today = datetime.date.today().isoformat()

if os.path.exists(record_file):
    with open(record_file, "r") as f:
        last_success_date = f.read().strip()
    if last_success_date == today:
        exit(0)

try:
    subprocess.check_call(["python", "main.py"])
except subprocess.CalledProcessError:
    exit(1)

try:
    subprocess.check_call(["git", "add", "."])
    subprocess.check_call(["git", "commit", "-m", f"Auto update {today}"])
except subprocess.CalledProcessError:
    pass

try:
    subprocess.check_call(["git", "push", "origin", "main"])
except subprocess.CalledProcessError:
    exit(1)

with open(record_file, "w") as f:
    f.write(today)