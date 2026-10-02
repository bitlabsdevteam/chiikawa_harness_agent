"""Day 5: bounded concurrent jobs with independent harnesses and ordered results.

Keep orchestration in the application, isolate ordinary job failures, and let
process-control exceptions propagate. Callers own workspace and session isolation.
"""

from concurrent.futures import ThreadPoolExecutor


def run_fleet(jobs, make_harness, max_workers=4):
    """Run independent jobs concurrently and return reports in original input order.

    Each worker constructs its own harness from the job's workdir. A failure in
    construction or execution becomes that job's report; other jobs still finish.
    Use separate workdirs or otherwise coordinate writes across jobs yourself.
    """
    def run_job(job):
        """Turn one task's completion or ordinary exception into a stable result."""
        try:
            report = make_harness(job["workdir"]).run(job["task"])
            return {"name": job["name"], "ok": True, "report": report}
        except Exception as exc:
            return {"name": job["name"], "ok": False,
                    "report": f"{type(exc).__name__}: {exc}"}

    # map executes concurrently while yielding in input order, unlike as_completed.
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(run_job, jobs))
