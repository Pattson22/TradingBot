"""
Standalone health-check for the live trading bot, meant to run on a schedule
(Windows Task Scheduler) independently of the bot process itself.

Reports whether main.py appears to be running, a fresh account/position
snapshot pulled directly from MT5 (a separate IPC connection from the bot's
own -- MT5 supports multiple simultaneous connections from different
processes to the same terminal), and the tail of logs/tradingbot.log.
Appends a timestamped block to logs/status_check.log every run.

Run manually with `python check_status.py`, or let the scheduled task do it.
"""

import datetime
import os
import subprocess

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import broker
import config

STATUS_LOG = os.path.join(config.LOG_DIR, "status_check.log")


def _bot_process_pids():
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
            "Where-Object { $_.CommandLine -match 'main.py' } | "
            "Select-Object -ExpandProperty ProcessId",
        ],
        capture_output=True, text=True, timeout=30,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _log_tail(n=15):
    with open(config.LOG_FILE, encoding="utf-8") as f:
        lines = f.readlines()
    return "".join(lines[-n:])


def main():
    lines = []
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"=== Status check at {now} ===")

    try:
        pids = _bot_process_pids()
        lines.append(f"Bot process: RUNNING (pid {', '.join(pids)})" if pids else "Bot process: NOT RUNNING")
    except Exception as exc:
        lines.append(f"Bot process check FAILED: {exc}")

    try:
        broker.connect()
        try:
            acct = broker.get_account_snapshot()
            positions = broker.get_open_positions()
            lines.append(
                f"Account: balance={acct['balance']:.2f} equity={acct['equity']:.2f} {acct['currency']}"
            )
            if positions:
                for p in positions:
                    lines.append(
                        f"  OPEN POSITION ticket={p.ticket} {p.symbol} vol={p.volume} "
                        f"type={p.type} profit={p.profit:.2f}"
                    )
            else:
                lines.append("Open positions: none")
        finally:
            broker.disconnect()
    except Exception as exc:
        lines.append(f"MT5 query FAILED: {exc}")

    try:
        lines.append("--- last 15 lines of tradingbot.log ---")
        lines.append(_log_tail())
    except Exception as exc:
        lines.append(f"Log tail FAILED: {exc}")

    block = "\n".join(lines) + "\n"
    os.makedirs(config.LOG_DIR, exist_ok=True)
    with open(STATUS_LOG, "a", encoding="utf-8") as f:
        f.write(block + "\n")

    print(block)


if __name__ == "__main__":
    main()
