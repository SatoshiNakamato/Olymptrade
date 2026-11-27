"""Voroa Cron Job entry point: scan 05:00-20:00 IST, then exit cleanly."""
from central_worker import main


if __name__ == "__main__":
    main(exit_after_window=True)
