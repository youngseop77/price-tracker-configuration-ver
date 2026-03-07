import urllib.request, json
url = "https://api.github.com/repos/eungseop2/Lowest-Price-Tracker/actions/runs?per_page=1"
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    data = json.loads(response.read())
run = data["workflow_runs"][0]
print(f"Run ID: {run['id']}, Status: {run['status']}, Conclusion: {run['conclusion']}")

url = f"https://api.github.com/repos/eungseop2/Lowest-Price-Tracker/actions/runs/{run['id']}/jobs"
req = urllib.request.Request(url)
with urllib.request.urlopen(req) as response:
    jobs = json.loads(response.read())["jobs"]

for job in jobs:
    print(f"Job: {job['name']}, Conclusion: {job['conclusion']}")
    if job["conclusion"] != "success":
        log_url = f"https://api.github.com/repos/eungseop2/Lowest-Price-Tracker/actions/jobs/{job['id']}/logs"
        try:
            req = urllib.request.Request(log_url)
            with urllib.request.urlopen(req) as log_res:
                log_text = log_res.read().decode('utf-8')
                print('\n--- LOGS (Last 2000 chars) ---')
                print(log_text[-2000:])
        except Exception as e:
            print('Could not fetch log:', e)
