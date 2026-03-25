"""
Resume Screener - Automated candidate screening with Ashby, Claude, and Slack.

Polls Ashby for new applications, scores resumes using Claude,
and sends Slack alerts for high-scoring candidates.

Supports multiple roles with role-specific scoring criteria.
"""

import os
import sys
import time
import yaml
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from ashby_client import AshbyClient
from scorer import ResumeScorer
from slack_notifier import SlackNotifier
from tracker import ApplicationTracker

# Load environment variables
load_dotenv()

# Configuration (can be overridden via env vars)
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "60"))
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "1"))
HEALTH_CHECK_PORT = int(os.getenv("PORT", "8080"))


def load_config():
    """Load configuration from config.yaml."""
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for health checks."""

    def do_GET(self):
        """Respond to GET requests with OK."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        """Suppress default logging to keep logs clean."""
        pass


def start_health_server():
    """Start the health check server in a background thread."""
    server = HTTPServer(("0.0.0.0", HEALTH_CHECK_PORT), HealthCheckHandler)
    print(f"Health check server running on port {HEALTH_CHECK_PORT}", flush=True)
    server.serve_forever()


def process_applications():
    """Fetch and process new job applications for all configured roles."""
    print(f"\n{'='*60}", flush=True)
    print(f"Starting application processing run...", flush=True)
    print(f"Time: {datetime.utcnow().isoformat()}Z", flush=True)
    print(f"{'='*60}", flush=True)

    try:
        # Initialize clients
        ashby = AshbyClient()
        scorer = ResumeScorer()
        slack = SlackNotifier()
        tracker = ApplicationTracker()

        # Load config to get role definitions
        config = load_config()
        roles = config.get("roles", [])
        role_job_ids = {role["job_id"]: role for role in roles}

        print(f"Monitoring {len(roles)} role(s):", flush=True)
        for role in roles:
            print(f"  - {role['job_title']} (threshold: {role['threshold']})", flush=True)

        # Get list of job IDs to monitor
        job_ids = [role["job_id"] for role in roles]

        # Fetch recent applications for monitored jobs
        print(f"\nFetching applications from the last {LOOKBACK_HOURS} hour(s)...", flush=True)
        applications = ashby.get_recent_applications(hours=LOOKBACK_HOURS, job_ids=job_ids)
        print(f"Found {len(applications)} application(s)", flush=True)

        new_count = 0
        alert_count = 0
        archived_count = 0
        skipped_no_role = 0

        for app in applications:
            app_id = app.get("id")

            # Skip if already processed
            if tracker.is_processed(app_id):
                continue

            # Get full application details
            details = ashby.get_application_details(app)
            job_id = details.get("job_id")

            # Check if this job is one we're monitoring
            if job_id not in role_job_ids:
                skipped_no_role += 1
                continue

            new_count += 1
            role_config = role_job_ids[job_id]

            candidate_name = details["candidate_name"]
            candidate_id = details["candidate_id"]
            job_title = details["job_title"]
            email = details["candidate_email"]
            resume_text = details["resume_text"]

            print(f"\nProcessing application {app_id}...", flush=True)
            print(f"  Candidate: {candidate_name}", flush=True)
            print(f"  Job: {job_title}", flush=True)

            # Score the resume using role-specific criteria
            print(f"  Scoring resume...", flush=True)
            scores = scorer.score_resume(resume_text, job_title, candidate_name, job_id=job_id)
            total_score = scores.get("total_score", 0)

            # Log scores dynamically based on role criteria
            print(f"  Scores:", flush=True)
            criteria_labels = scores.get("criteria_labels", {})
            for criterion_name, label in criteria_labels.items():
                score_val = scores.get(criterion_name, "N/A")
                print(f"    {label}: {score_val}/10", flush=True)
            print(f"  Total Score: {total_score}/10", flush=True)
            print(f"  Assessment: {scores.get('fit_summary', 'N/A')}", flush=True)

            # Get role-specific threshold and archive settings
            score_threshold = role_config.get("threshold", 7.0)
            archive_threshold = role_config.get("archive_threshold")
            archive_stage_id = role_config.get("archive_stage_id")
            archive_reason = role_config.get("archive_reason")

            # Check NYC hard gate if applicable
            nyc_hard_gate = role_config.get("nyc_hard_gate", False)
            nyc_confirmed = scores.get("nyc_confirmed", True)  # Default to True if not checked

            if nyc_hard_gate:
                print(f"  NYC Location: {'Confirmed' if nyc_confirmed else 'NOT CONFIRMED'}", flush=True)

            # Decide action based on score and NYC gate
            if total_score >= score_threshold:
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
                        alert_count += 1
                        print(f"  Slack alert sent successfully!", flush=True)
                    else:
                        print(f"  Failed to send Slack alert", flush=True)
            elif archive_threshold and total_score < archive_threshold:
                print(f"  *** LOW SCORE (< {archive_threshold}) - Auto-archiving...", flush=True)
                if archive_stage_id and archive_reason:
                    success = ashby.archive_application(app_id, archive_stage_id, archive_reason)
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
                print(f"  Score below threshold ({score_threshold}), logging only", flush=True)
                recommendation = "skip"

            # Mark as processed
            tracker.mark_processed(
                application_id=app_id,
                candidate_name=candidate_name,
                score=total_score,
                recommendation=recommendation,
            )

        # Summary
        stats = tracker.get_stats()
        print(f"\n{'='*60}", flush=True)
        print(f"Run complete!", flush=True)
        print(f"  New applications processed: {new_count}", flush=True)
        print(f"  Skipped (not monitored roles): {skipped_no_role}", flush=True)
        print(f"  Alerts sent this run: {alert_count}", flush=True)
        print(f"  Auto-archived (low score): {archived_count}", flush=True)
        print(f"  Total processed all-time: {stats['total_processed']}", flush=True)
        print(f"{'='*60}\n", flush=True)

        return True

    except Exception as e:
        print(f"Error during processing: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        return False


def main():
    """Main entry point - runs health server and polling loop."""
    # Disable output buffering
    sys.stdout.reconfigure(line_buffering=True)

    # Start health check server in background thread
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    # Load config to display roles
    config = load_config()
    roles = config.get("roles", [])

    print("=" * 60, flush=True)
    print("  Resume Screener - Starting Up", flush=True)
    print("=" * 60, flush=True)
    print(f"Configuration:", flush=True)
    print(f"  Poll interval: {POLL_INTERVAL_MINUTES} minutes", flush=True)
    print(f"  Lookback period: {LOOKBACK_HOURS} hour(s)", flush=True)
    print(f"  Health check port: {HEALTH_CHECK_PORT}", flush=True)
    print(f"  Roles monitored: {len(roles)}", flush=True)
    for role in roles:
        print(f"    - {role['job_title']} (threshold: {role['threshold']})", flush=True)
    print("=" * 60, flush=True)

    # Calculate sleep time in seconds
    sleep_seconds = POLL_INTERVAL_MINUTES * 60

    # Run forever
    while True:
        # Process applications
        process_applications()

        # Wait for next run
        next_run = datetime.utcnow().isoformat()
        print(f"Sleeping for {POLL_INTERVAL_MINUTES} minutes until next run...", flush=True)
        print(f"Current time: {next_run}Z", flush=True)
        sys.stdout.flush()

        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
