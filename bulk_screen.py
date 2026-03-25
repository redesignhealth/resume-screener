"""
Bulk Resume Screener - Screens applications across ALL active roles in config.yaml.

Loops through every role, fetches applications from Ashby, scores each one,
and sends Slack alerts for high-scoring candidates.

Usage:
    python3 bulk_screen.py
"""

import os
import sys
import time
from datetime import datetime
from dotenv import load_dotenv

from ashby_client import AshbyClient
from scorer import ResumeScorer
from slack_notifier import SlackNotifier
from tracker import ApplicationTracker

# Load environment variables
load_dotenv()

# Configuration
MAX_APPLICATIONS = 600
DELAY_BETWEEN_CANDIDATES = 2.5  # seconds
STAGE_FILTER = "Application Review"  # Only process applications in this stage (set to None for all)

# Roles to SKIP (job_titles containing these strings will be excluded)
EXCLUDED_ROLES = ["Executive Assistant"]

# ONLY run this specific role (set to None to run all roles)
ONLY_RUN_JOB_ID = None


def fetch_applications_for_job(ashby: AshbyClient, job_id: str, max_count: int, stage_filter: str = None) -> list:
    """Fetch applications for a specific job from Ashby, optionally filtered by stage."""
    print(f"Fetching applications for job: {job_id}", flush=True)
    if stage_filter:
        print(f"Filtering by stage: {stage_filter}", flush=True)
    print(f"Maximum applications to fetch: {max_count}", flush=True)

    all_applications = []
    filtered_applications = []

    try:
        result = ashby._request("/application.list", {"jobId": job_id})
        applications = result.get("results", [])
        all_applications.extend(applications)
        print(f"  Fetched {len(applications)} applications (page 1)", flush=True)

        page = 2
        while result.get("nextCursor"):
            result = ashby._request("/application.list", {
                "jobId": job_id,
                "cursor": result["nextCursor"]
            })
            applications = result.get("results", [])
            all_applications.extend(applications)
            print(f"  Fetched {len(applications)} applications (page {page})", flush=True)
            page += 1

        if stage_filter:
            for app in all_applications:
                stage = app.get("currentInterviewStage", {})
                stage_title = stage.get("title", "") if stage else ""
                if stage_title == stage_filter:
                    filtered_applications.append(app)
            print(f"  Filtered to {len(filtered_applications)} applications in '{stage_filter}'", flush=True)
            all_applications = filtered_applications

        if len(all_applications) > max_count:
            print(f"  Trimming to {max_count} applications", flush=True)
            all_applications = all_applications[:max_count]

    except Exception as e:
        print(f"Error fetching applications: {e}", flush=True)

    return all_applications


def load_config():
    """Load configuration from config.yaml."""
    import yaml
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def screen_role(ashby, scorer, slack, tracker, role_config, job_id):
    """Screen all applications for a single role. Returns a summary dict."""
    score_threshold = scorer.get_score_threshold(job_id=job_id)
    archive_threshold = role_config.get("archive_threshold")
    archive_stage_id = role_config.get("archive_stage_id")
    archive_reason_id = role_config.get("archive_reason_id") or role_config.get("archive_reason")  # Backward compatibility
    nyc_hard_gate = role_config.get("nyc_hard_gate", False)

    print(f"Score threshold for alerts: {score_threshold}", flush=True)
    if archive_threshold:
        print(f"Archive threshold: {archive_threshold}", flush=True)
    print("=" * 60, flush=True)

    applications = fetch_applications_for_job(ashby, job_id, MAX_APPLICATIONS, STAGE_FILTER)
    total_count = len(applications)
    print(f"\nFound {total_count} applications for job {job_id}", flush=True)

    if total_count == 0:
        print("No applications to process.", flush=True)
        return {"total": 0, "reviewed": 0, "skipped": 0, "high_score": 0, "alerts": 0, "archived": 0, "errors": 0}

    reviewed_count = 0
    skipped_count = 0
    high_score_count = 0
    alerts_sent = 0
    archived_count = 0
    errors = 0

    for i, app in enumerate(applications, 1):
        app_id = app.get("id")
        print(f"\n[{i}/{total_count}] Processing application {app_id}...", flush=True)

        if tracker.is_processed(app_id):
            print(f"  Already processed, skipping.", flush=True)
            skipped_count += 1
            continue

        try:
            details = ashby.get_application_details(app)
            candidate_name = details["candidate_name"]
            candidate_id = details["candidate_id"]
            job_title = details["job_title"]
            email = details["candidate_email"]
            resume_text = details["resume_text"]

            print(f"  Candidate: {candidate_name}", flush=True)
            print(f"  Job: {job_title}", flush=True)

            if not resume_text or not resume_text.strip():
                print(f"  No resume text available, skipping.", flush=True)
                tracker.mark_processed(
                    application_id=app_id,
                    candidate_name=candidate_name,
                    score=0,
                    recommendation="skip_no_resume",
                )
                skipped_count += 1
                continue

            print(f"  Scoring resume...", flush=True)
            scores = scorer.score_resume(resume_text, job_title, candidate_name, job_id=job_id)
            total_score = scores.get("total_score", 0)

            print(f"  Scores:", flush=True)
            criteria_labels = scores.get("criteria_labels", {})
            for criterion_name, label in criteria_labels.items():
                score_val = scores.get(criterion_name, "N/A")
                print(f"    {label}: {score_val}/10", flush=True)
            print(f"  Total Score: {total_score}/10", flush=True)
            print(f"  Assessment: {scores.get('fit_summary', 'N/A')}", flush=True)

            nyc_confirmed = scores.get("nyc_confirmed", True)
            if nyc_hard_gate:
                print(f"  NYC Location: {'Confirmed' if nyc_confirmed else 'NOT CONFIRMED'}", flush=True)

            reviewed_count += 1

            if total_score >= score_threshold:
                high_score_count += 1
                if nyc_hard_gate and not nyc_confirmed:
                    print(f"  *** HIGH SCORE but NOT in NYC - Skipping alert", flush=True)
                    recommendation = "skip_nyc"
                else:
                    print(f"  *** HIGH SCORE - Sending Slack alert...", flush=True)
                    success = slack.send_candidate_alert(
                        candidate_name=candidate_name,
                        job_title=job_title,
                        email=email,
                        scores=scores,
                        candidate_id=candidate_id,
                    )
                    recommendation = "alert"
                    if success:
                        alerts_sent += 1
                        print(f"  Slack alert sent!", flush=True)
                    else:
                        print(f"  Failed to send Slack alert", flush=True)
            elif archive_threshold and total_score < archive_threshold:
                print(f"  *** LOW SCORE (< {archive_threshold}) - Auto-archiving...", flush=True)
                if archive_stage_id and archive_reason_id:
                    success = ashby.archive_application(app_id, archive_stage_id, archive_reason_id)
                    if success:
                        archived_count += 1
                        print(f"  Archived successfully!", flush=True)
                        recommendation = "archived"
                    else:
                        print(f"  Failed to archive", flush=True)
                        recommendation = "skip"
                else:
                    print(f"  Archive config missing, skipping archival", flush=True)
                    recommendation = "skip"
            else:
                print(f"  Score below threshold, no alert.", flush=True)
                recommendation = "skip"

            tracker.mark_processed(
                application_id=app_id,
                candidate_name=candidate_name,
                score=total_score,
                recommendation=recommendation,
            )

        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
            errors += 1

        if i < total_count:
            time.sleep(DELAY_BETWEEN_CANDIDATES)

    return {
        "total": total_count,
        "reviewed": reviewed_count,
        "skipped": skipped_count,
        "high_score": high_score_count,
        "alerts": alerts_sent,
        "archived": archived_count,
        "errors": errors,
    }


def bulk_screen():
    """Loop through all roles in config.yaml and screen each one."""
    print("=" * 60, flush=True)
    print("  Bulk Resume Screener — All Roles", flush=True)
    print(f"  Time: {datetime.utcnow().isoformat()}Z", flush=True)
    print("=" * 60, flush=True)

    # Initialize clients
    ashby = AshbyClient()
    scorer = ResumeScorer()
    slack = SlackNotifier()
    tracker = ApplicationTracker()

    # Load all roles from config
    config = load_config()
    all_roles = config.get("roles", [])

    # Filter out excluded roles
    roles_to_run = []
    for role in all_roles:
        title = role.get("job_title", "")
        job_id = role.get("job_id", "")

        # If ONLY_RUN_JOB_ID is set, skip all other roles
        if ONLY_RUN_JOB_ID and job_id != ONLY_RUN_JOB_ID:
            print(f"Skipping role (not target job_id): {title}", flush=True)
            continue

        if any(excluded.lower() in title.lower() for excluded in EXCLUDED_ROLES):
            print(f"Skipping excluded role: {title}", flush=True)
        else:
            roles_to_run.append(role)

    print(f"\nRoles to screen: {len(roles_to_run)}", flush=True)
    for role in roles_to_run:
        print(f"  - {role.get('job_title')}", flush=True)
    print("=" * 60, flush=True)

    # Track overall summary
    overall_summary = []

    # Loop through each role
    for role in roles_to_run:
        job_id = role.get("job_id")
        job_title = role.get("job_title", "Unknown Role")

        print(f"\n{'=' * 60}", flush=True)
        print(f"  ROLE: {job_title}", flush=True)
        print(f"  Job ID: {job_id}", flush=True)
        print("=" * 60, flush=True)

        summary = screen_role(ashby, scorer, slack, tracker, role, job_id)
        summary["job_title"] = job_title
        overall_summary.append(summary)

    # Print overall summary
    print("\n" + "=" * 60, flush=True)
    print("  BULK SCREENING COMPLETE — ALL ROLES", flush=True)
    print("=" * 60, flush=True)
    for s in overall_summary:
        print(f"\n  Role: {s['job_title']}", flush=True)
        print(f"    Applications found:    {s['total']}", flush=True)
        print(f"    Already processed:     {s['skipped']}", flush=True)
        print(f"    Reviewed:              {s['reviewed']}", flush=True)
        print(f"    High scores:           {s['high_score']}", flush=True)
        print(f"    Slack alerts sent:     {s['alerts']}", flush=True)
        print(f"    Auto-archived:         {s['archived']}", flush=True)
        print(f"    Errors:                {s['errors']}", flush=True)
    print("\n" + "=" * 60, flush=True)
    print(f"  Completed at: {datetime.utcnow().isoformat()}Z", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    bulk_screen()