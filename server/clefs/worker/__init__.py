"""
The Clefs worker process (python -m clefs.worker). See clefs/worker/loop.py.

It drives clefs.jobs.tick() (the scheduler, under an advisory lock, and the
SKIP LOCKED queue), wakes on LISTEN clefs_jobs, runs each module's
worker_services and writes heartbeats. On Vercel there is no worker:
/internal/cron/tick runs the same tick.
"""
