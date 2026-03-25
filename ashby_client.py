"""Ashby API client for fetching job applications and candidate data."""

import os
import requests
from base64 import b64encode
from datetime import datetime, timedelta


class AshbyClient:
    """Client for interacting with the Ashby API."""

    BASE_URL = "https://api.ashbyhq.com"

    def __init__(self):
        api_key = os.getenv("ASHBY_API_KEY")
        if not api_key:
            raise ValueError("ASHBY_API_KEY environment variable not set")

        # Ashby uses Basic Auth with API key as username, empty password
        credentials = b64encode(f"{api_key}:".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        }

    def _request(self, endpoint: str, data: dict = None) -> dict:
        """Make a POST request to the Ashby API."""
        response = requests.post(
            f"{self.BASE_URL}{endpoint}",
            headers=self.headers,
            json=data or {},
        )
        response.raise_for_status()
        return response.json()

    def list_open_jobs(self) -> list:
        """List all open jobs."""
        result = self._request("/job.list", {"status": "Open"})
        return result.get("results", [])

    def get_recent_applications(self, hours: int = 1, job_ids: list = None) -> list:
        """Fetch applications submitted in the last N hours for specific jobs.

        Note: The Ashby API's createdAfter parameter doesn't work reliably,
        so we fetch all applications for the specified jobs and filter locally.
        """
        since = datetime.utcnow() - timedelta(hours=hours)
        all_recent = []

        if not job_ids:
            # If no job_ids specified, try the API filter (may not work)
            result = self._request("/application.list", {
                "createdAfter": since.isoformat() + "Z",
            })
            return result.get("results", [])

        # Fetch applications for each job and filter by date locally
        for job_id in job_ids:
            result = self._request("/application.list", {"jobId": job_id})
            apps = result.get("results", [])

            # Paginate to get all applications
            while result.get("nextCursor"):
                result = self._request("/application.list", {
                    "jobId": job_id,
                    "cursor": result["nextCursor"]
                })
                apps.extend(result.get("results", []))

            # Filter by createdAt date locally
            for app in apps:
                created_at = app.get("createdAt", "")
                if created_at:
                    try:
                        # Parse ISO format datetime
                        app_date = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        since_aware = since.replace(tzinfo=app_date.tzinfo)
                        if app_date >= since_aware:
                            all_recent.append(app)
                    except (ValueError, TypeError):
                        pass

        return all_recent

    def get_candidate(self, candidate_id: str) -> dict:
        """Fetch candidate details by ID."""
        result = self._request("/candidate.info", {"id": candidate_id})
        return result.get("results", {})

    def get_job(self, job_id: str) -> dict:
        """Fetch job details by ID."""
        result = self._request("/job.info", {"id": job_id})
        return result.get("results", {})

    def _build_profile_summary(self, candidate: dict) -> str:
        """Build a profile summary from available candidate data."""
        parts = []

        name = candidate.get("name", "")
        if name:
            parts.append(f"Candidate: {name}")

        position = candidate.get("position", "")
        if position:
            parts.append(f"Current Position: {position}")

        company = candidate.get("company", "")
        if company:
            parts.append(f"Current Company: {company}")

        school = candidate.get("school", "")
        if school:
            parts.append(f"Education: {school}")

        # Add social links
        social_links = candidate.get("socialLinks", [])
        for link in social_links:
            link_type = link.get("type", "")
            url = link.get("url", "")
            if link_type and url:
                parts.append(f"{link_type}: {url}")

        # Add tags if any
        tags = candidate.get("tags", [])
        if tags:
            tag_names = [t.get("title", "") for t in tags if t.get("title")]
            if tag_names:
                parts.append(f"Tags: {', '.join(tag_names)}")

        # Add location if available
        location = candidate.get("location", {})
        if location:
            loc_parts = []
            if location.get("city"):
                loc_parts.append(location["city"])
            if location.get("region"):
                loc_parts.append(location["region"])
            if location.get("country"):
                loc_parts.append(location["country"])
            if loc_parts:
                parts.append(f"Location: {', '.join(loc_parts)}")

        return "\n".join(parts)

    def get_candidate_profile_text(self, candidate_id: str) -> str:
        """Fetch candidate profile text - tries resume first, falls back to profile summary."""
        try:
            candidate = self.get_candidate(candidate_id)

            # Try to get parsed resume text
            resume_text = ""

            # Check for resume file handle with parsed text
            if candidate.get("resumeFileHandle"):
                file_handle = candidate["resumeFileHandle"]
                if file_handle.get("parsedText"):
                    resume_text = file_handle["parsedText"]

            # Check for resume in fileHandles
            if not resume_text:
                file_handles = candidate.get("fileHandles", [])
                for fh in file_handles:
                    if fh.get("type") == "Resume" and fh.get("parsedText"):
                        resume_text = fh["parsedText"]
                        break

            # If we have resume text, return it
            if resume_text:
                return resume_text

            # Fall back to building a profile summary from available data
            profile_summary = self._build_profile_summary(candidate)
            return profile_summary

        except Exception as e:
            print(f"Error fetching profile for candidate {candidate_id}: {e}")
            return ""

    def get_application_details(self, application: dict) -> dict:
        """Get full details for an application including candidate and job info."""
        # Extract embedded candidate data (Ashby includes it in application response)
        embedded_candidate = application.get("candidate", {})
        embedded_job = application.get("job", {})

        # Get candidate ID from embedded data or top level
        candidate_id = embedded_candidate.get("id") or application.get("candidateId")
        job_id = embedded_job.get("id") or application.get("jobId")

        # Extract candidate name from embedded data
        name = embedded_candidate.get("name", "")
        if not name:
            first = embedded_candidate.get("firstName", "")
            last = embedded_candidate.get("lastName", "")
            name = f"{first} {last}".strip()

        # Extract email from embedded data
        email = ""
        primary_email = embedded_candidate.get("primaryEmailAddress", {})
        if primary_email.get("value"):
            email = primary_email["value"]
        elif embedded_candidate.get("emailAddresses"):
            emails = embedded_candidate["emailAddresses"]
            if emails and len(emails) > 0:
                email = emails[0].get("value", "")

        # Extract job title from embedded data
        job_title = embedded_job.get("title", "Unknown Position")

        # Fetch profile text (resume or profile summary)
        resume_text = ""
        if candidate_id:
            resume_text = self.get_candidate_profile_text(candidate_id)

        return {
            "application_id": application.get("id"),
            "candidate_id": candidate_id,
            "candidate_name": name or "Unknown",
            "candidate_email": email,
            "job_id": job_id,
            "job_title": job_title,
            "resume_text": resume_text,
            "applied_at": application.get("createdAt"),
        }

    def archive_application(self, application_id: str, stage_id: str, reason_id: str) -> bool:
        """Move an application to the archived stage.

        Args:
            application_id: The Ashby application ID
            stage_id: The interview stage ID for the archived stage
            reason_id: The archive reason ID (not the text reason)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Move to archived stage with required archive reason ID
            payload = {
                "applicationId": application_id,
                "interviewStageId": stage_id
            }

            # Archive reason ID is required when moving to Archived stage
            if reason_id:
                payload["archiveReasonId"] = reason_id
            else:
                print(f"  ⚠️ Warning: No archive reason ID provided", flush=True)
                return False

            response = self._request("/application.changeStage", payload)

            # Check if the API returned success: false
            if isinstance(response, dict) and response.get("success") is False:
                errors = response.get("errors", [])
                error_info = response.get("errorInfo", {})
                error_msg = error_info.get("message", str(errors))
                print(f"  ⚠️ API returned error: {error_msg}", flush=True)
                print(f"  Debug - Payload was: {payload}", flush=True)
                return False

            return True

        except Exception as e:
            print(f"  ⚠️ Error archiving application: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return False
