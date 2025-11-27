JOBS = {}

def check_stop(job_id):
    return JOBS.get(job_id, {}).get("status") == "cancelled"

def register_job(job_id):
    JOBS[job_id] = {"status": "running", "progress": 0, "message": "Starting..."}

def update_job(job_id, status=None, progress=None, message=None):
    if job_id not in JOBS: return
    if status: JOBS[job_id]["status"] = status
    if progress is not None: JOBS[job_id]["progress"] = progress
    if message: JOBS[job_id]["message"] = message

def complete_job(job_id, status="completed", message="Done"):
    update_job(job_id, status=status, message=message)
