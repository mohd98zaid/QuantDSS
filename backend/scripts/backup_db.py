"""
Automated Database Backup Script for QuantDSS.
Runs pg_dump and enforces retention policy:
- 7 daily
- 4 weekly
- 3 monthly
"""
import os
import re
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

try:
    from app.core.config import settings
    db_url = settings.DATABASE_URL
except Exception:
    db_url = os.environ.get("DATABASE_URL", "")

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "backups"))

def run_backup():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    
    if not db_url:
        print("Error: DATABASE_URL not found.")
        return False
        
    parsed = urlparse(db_url.replace("+asyncpg", ""))
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = BACKUP_DIR / f"quantdss_backup_{timestamp}.sql.gz"
    
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
        
    cmd = [
        "pg_dump",
        "-h", parsed.hostname or "localhost",
        "-p", str(parsed.port or 5432),
        "-U", parsed.username or "postgres",
        "-d", parsed.path.lstrip("/") or "quantdss",
        "-F", "c", # custom compressed format
        "-Z", "9", # max compression
        "-f", str(filename)
    ]
    
    print(f"Starting backup to {filename}...")
    try:
        subprocess.run(cmd, env=env, check=True, capture_output=True)
        print(f"Backup successful: {filename}")
        enforce_retention()
        return True
    except subprocess.CalledProcessError as e:
        print(f"Backup failed: {e.stderr.decode()}")
        return False
    except FileNotFoundError:
        print("Backup failed: pg_dump not found in PATH.")
        return False

def enforce_retention():
    print("Enforcing retention policy...")
    now = datetime.now()
    backups = sorted(BACKUP_DIR.glob("quantdss_backup_*.sql.gz"))
    
    found_days = set()
    found_weeks = set()
    found_months = set()
    
    for backup in reversed(backups):
        m = re.search(r"quantdss_backup_(\d{8})_(\d{6})\.sql\.gz", backup.name)
        if not m:
            continue
        
        b_date = datetime.strptime(m.group(1), "%Y%m%d")
        age_days = (now - b_date).days
        
        keep = False
        
        # 1. Keep last 7 days (Daily)
        if age_days < 7:
            day_str = b_date.strftime("%Y-%m-%d")
            if day_str not in found_days:
                found_days.add(day_str)
                keep = True
                
        # 2. Keep 4 weekly (Weekly)
        week_str = b_date.strftime("%Y-%W")
        if age_days >= 7 and age_days < 35:
            if week_str not in found_weeks:
                found_weeks.add(week_str)
                keep = True
                
        # 3. Keep 3 monthly (Monthly)
        month_str = b_date.strftime("%Y-%m")
        if age_days >= 35 and age_days < 125: # approx 3 further months
            if month_str not in found_months:
                found_months.add(month_str)
                keep = True
                
        if not keep:
            print(f"Deleting retired backup: {backup.name}")
            try:
                os.remove(backup)
            except Exception as e:
                print(f"Could not delete {backup}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backup QuantDSS Database")
    args = parser.parse_args()
    run_backup()
