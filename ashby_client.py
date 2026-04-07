"""Ashby API client for fetching job applications and candidate data."""

import os
import io
import time
import requests
from base64 import b64encode
from datetime import datetime, timedelta
from PyPDF2 import PdfReader
from duckduckgo_search import DDGS


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

    def download_file(self, file_handle: str, file_id: str = None) -> bytes:
        """Download a file from Ashby using its file handle.

        Args:
            file_handle: The file handle/token from Ashby
            file_id: The file ID (optional, for alternative endpoints)

        Returns:
            File content as bytes
        """
        try:
            # Try method 1: /file.info to get download URL
            try:
                if file_id:
                    result = self._request("/file.info", {"id": file_id})
                    if "results" in result and "url" in result["results"]:
                        download_url = result["results"]["url"]
                        response = requests.get(download_url)
                        response.raise_for_status()
                        return response.content
            except Exception as e:
                print(f"  /file.info attempt failed: {e}")

            # Try method 2: Direct download URL with handle
            # The handle might be a direct access token
            try:
                # Ashby might use a pattern like: https://api.ashbyhq.com/file/{handle}
                download_url = f"{self.BASE_URL}/file/{file_handle}"
                response = requests.get(download_url, headers=self.headers)
                response.raise_for_status()
                return response.content
            except Exception as e:
                print(f"  Direct URL attempt failed: {e}")

            # Try method 3: /candidate.resume endpoint
            try:
                result = self._request("/candidate.resume", {"handle": file_handle})
                if "results" in result:
                    # Response might contain the file content or a URL
                    if "content" in result["results"]:
                        return result["results"]["content"].encode()
                    elif "url" in result["results"]:
                        download_url = result["results"]["url"]
                        response = requests.get(download_url)
                        response.raise_for_status()
                        return response.content
            except Exception as e:
                print(f"  /candidate.resume attempt failed: {e}")

            return None
        except Exception as e:
            print(f"Error downloading file (all methods failed): {e}")
            return None

    def parse_pdf_resume(self, pdf_content: bytes) -> str:
        """Parse PDF content and extract text.

        Args:
            pdf_content: PDF file content as bytes

        Returns:
            Extracted text from the PDF
        """
        try:
            # Create a PDF reader from bytes
            pdf_file = io.BytesIO(pdf_content)
            pdf_reader = PdfReader(pdf_file)

            # Extract text from all pages
            text_parts = []
            for page in pdf_reader.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)

            # Combine all pages
            full_text = "\n\n".join(text_parts)
            return full_text.strip()

        except Exception as e:
            print(f"Error parsing PDF: {e}")
            return ""

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

        # Extract work history from email addresses
        # Email domains often indicate employment history (e.g., name@elevationcapital.com)
        email_addresses = candidate.get("emailAddresses", [])
        work_history = []
        for email in email_addresses:
            email_value = email.get("value", "")
            if "@" in email_value and not email.get("isPrimary", False):
                domain = email_value.split("@")[1].lower()
                # Skip common personal email domains
                if domain not in ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"]:
                    # Convert domain to company name (e.g., elevationcapital.com -> Elevation Capital)
                    company_name = domain.replace(".com", "").replace(".co", "").replace(".", " ").title()
                    # Handle known firms
                    if "elevation" in domain.lower():
                        company_name = "Elevation Capital"
                    elif "mckinsey" in domain.lower():
                        company_name = "McKinsey & Company"
                    elif "saif" in domain.lower():
                        company_name = "SAIF Partners"
                    elif "sequoia" in domain.lower():
                        company_name = "Sequoia Capital"
                    elif "accel" in domain.lower():
                        company_name = "Accel"
                    work_history.append(company_name)

        if work_history:
            parts.append(f"\nWork History (from email addresses):")
            for company in work_history:
                parts.append(f"  - {company}")

        school = candidate.get("school", "")
        if school:
            parts.append(f"\nEducation: {school}")

        # Add location if available
        location = candidate.get("location", {})
        if location:
            loc_parts = []
            if location.get("locationSummary"):
                parts.append(f"Location: {location['locationSummary']}")
            elif location.get("locationComponents"):
                loc_names = [comp.get("name", "") for comp in location["locationComponents"] if comp.get("name")]
                if loc_names:
                    parts.append(f"Location: {', '.join(loc_names)}")

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

        return "\n".join(parts)

    def enrich_with_web_search(self, candidate: dict) -> str:
        """Enrich candidate profile using web search.

        Args:
            candidate: The candidate object from Ashby

        Returns:
            Enriched profile text from web search results
        """
        try:
            name = candidate.get("name", "")
            company = candidate.get("company", "")
            position = candidate.get("position", "")

            if not name:
                return ""

            # Build search query
            # Include company and position if available to get more specific results
            search_terms = [name]

            # Try to infer current or recent company from emails
            email_addresses = candidate.get("emailAddresses", [])
            companies_from_email = []
            for email in email_addresses:
                email_value = email.get("value", "")
                if "@" in email_value and not email.get("isPrimary", False):
                    domain = email_value.split("@")[1].lower()
                    if domain not in ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"]:
                        # Extract company name from domain
                        if "elevation" in domain:
                            companies_from_email.append("Elevation Capital")
                        elif "mckinsey" in domain:
                            companies_from_email.append("McKinsey")
                        elif "sequoia" in domain:
                            companies_from_email.append("Sequoia Capital")

            # Use the most relevant company for search
            search_company = companies_from_email[0] if companies_from_email else company

            if search_company:
                search_terms.append(search_company)

            search_query = " ".join(search_terms)

            print(f"  🔍 Enriching profile via web search: '{search_query}'...", flush=True)

            # Perform web search with retry logic
            results = []
            max_retries = 3
            retry_delay = 2  # seconds

            for attempt in range(max_retries):
                try:
                    # Add a small delay to avoid rate limiting
                    if attempt > 0:
                        wait_time = retry_delay * (attempt + 1)
                        print(f"  ⏱️  Waiting {wait_time}s before retry {attempt + 1}/{max_retries}...", flush=True)
                        time.sleep(wait_time)

                    with DDGS() as ddgs:
                        results = list(ddgs.text(search_query, max_results=5))

                    if results:
                        break  # Success!

                except Exception as e:
                    if "Ratelimit" in str(e) and attempt < max_retries - 1:
                        print(f"  ⚠️  Rate limited, will retry...", flush=True)
                        continue
                    else:
                        print(f"  ⚠️  Web search failed: {e}", flush=True)
                        if attempt == max_retries - 1:
                            return ""

            if not results:
                print(f"  ⚠️  No web search results found after {max_retries} attempts", flush=True)
                return ""

            # Extract relevant information from search results
            enriched_parts = [f"Candidate: {name}"]

            # Collect snippets that might contain useful info
            for result in results:
                title = result.get("title", "")
                body = result.get("body", "")

                # Add relevant snippets
                if name.lower() in body.lower():
                    enriched_parts.append(f"\n{body}")

            enriched_text = "\n".join(enriched_parts)

            if len(enriched_text) > 200:
                print(f"  ✅ Enriched profile with web search ({len(enriched_text)} characters)", flush=True)
                return enriched_text
            else:
                print(f"  ⚠️  Web search enrichment returned minimal data", flush=True)
                return ""

        except Exception as e:
            print(f"  ⚠️  Error during web search enrichment: {e}", flush=True)
            return ""

    def get_linkedin_profile_text(self, linkedin_url: str) -> str:
        """Fetch LinkedIn profile text using web scraping.

        Args:
            linkedin_url: The LinkedIn profile URL

        Returns:
            Extracted profile text
        """
        try:
            print(f"  🔗 Fetching LinkedIn profile from {linkedin_url}...", flush=True)

            # Try to fetch the page with requests
            # LinkedIn public profiles are sometimes accessible without login
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
            }

            response = requests.get(linkedin_url, headers=headers, timeout=10)
            response.raise_for_status()

            html_content = response.text

            # Simple extraction - look for key sections
            # LinkedIn uses specific class names and structure
            profile_parts = []

            # Extract name from title or meta tags
            if '<title>' in html_content:
                title_start = html_content.find('<title>') + 7
                title_end = html_content.find('</title>', title_start)
                title = html_content[title_start:title_end].strip()
                if title and '| LinkedIn' in title:
                    title = title.replace('| LinkedIn', '').strip()
                    profile_parts.append(f"Name: {title}")

            # Extract description/summary from meta tags
            if 'meta name="description"' in html_content or 'meta property="og:description"' in html_content:
                desc_patterns = [
                    'meta name="description" content="',
                    'meta property="og:description" content="'
                ]
                for pattern in desc_patterns:
                    if pattern in html_content:
                        desc_start = html_content.find(pattern) + len(pattern)
                        desc_end = html_content.find('"', desc_start)
                        description = html_content[desc_start:desc_end].strip()
                        if description:
                            profile_parts.append(f"\nSummary: {description}")
                            break

            linkedin_text = "\n".join(profile_parts)

            if linkedin_text and len(linkedin_text) > 50:
                print(f"  ✅ Extracted LinkedIn profile ({len(linkedin_text)} characters)", flush=True)
                return linkedin_text
            else:
                print(f"  ⚠️  LinkedIn profile extraction returned minimal data", flush=True)
                return ""

        except Exception as e:
            print(f"  ⚠️  Error fetching LinkedIn profile: {e}", flush=True)
            return ""

    def get_candidate_profile_text(self, candidate_id: str) -> str:
        """Fetch candidate profile text - tries resume first, falls back to profile summary."""
        try:
            candidate = self.get_candidate(candidate_id)

            # Try to get parsed resume text from API
            resume_text = ""

            # Check for resume file handle with parsed text
            if candidate.get("resumeFileHandle"):
                file_handle_obj = candidate["resumeFileHandle"]
                if file_handle_obj.get("parsedText"):
                    resume_text = file_handle_obj["parsedText"]

            # Check for resume in fileHandles
            if not resume_text:
                file_handles = candidate.get("fileHandles", [])
                for fh in file_handles:
                    if fh.get("type") == "Resume" and fh.get("parsedText"):
                        resume_text = fh["parsedText"]
                        break

            # If we have pre-parsed text, return it
            if resume_text:
                return resume_text

            # Try to download and parse the PDF resume
            print(f"  📄 No pre-parsed text found. Attempting PDF download...", flush=True)

            if candidate.get("resumeFileHandle"):
                file_handle_obj = candidate["resumeFileHandle"]
                file_handle = file_handle_obj.get("handle")
                file_id = file_handle_obj.get("id")

                if file_handle:
                    print(f"  📥 Downloading resume PDF (file_id: {file_id})...", flush=True)
                    pdf_content = self.download_file(file_handle, file_id)

                    if pdf_content:
                        print(f"  📖 Parsing PDF ({len(pdf_content)} bytes)...", flush=True)
                        resume_text = self.parse_pdf_resume(pdf_content)

                        if resume_text and len(resume_text) > 100:
                            print(f"  ✅ Successfully extracted {len(resume_text)} characters from PDF", flush=True)
                            return resume_text
                        else:
                            print(f"  ⚠️  PDF parsing returned insufficient text ({len(resume_text)} chars)", flush=True)
                    else:
                        print(f"  ⚠️  Failed to download PDF", flush=True)

            # Try LinkedIn profile if available
            social_links = candidate.get("socialLinks", [])
            for link in social_links:
                if link.get("type") == "LinkedIn" and link.get("url"):
                    linkedin_url = link["url"]
                    linkedin_text = self.get_linkedin_profile_text(linkedin_url)
                    if linkedin_text and len(linkedin_text) > 100:
                        # Combine LinkedIn with basic profile info
                        profile_summary = self._build_profile_summary(candidate)
                        combined = f"{linkedin_text}\n\n{profile_summary}"
                        return combined

            # Try web search enrichment (optional, controlled by env var)
            if os.getenv("ENABLE_WEB_SEARCH_ENRICHMENT", "false").lower() == "true":
                print(f"  🌐 Web search enrichment enabled, attempting...", flush=True)
                web_enriched_text = self.enrich_with_web_search(candidate)

                if web_enriched_text and len(web_enriched_text) > 200:
                    # Combine web search data with basic profile info
                    profile_summary = self._build_profile_summary(candidate)
                    combined = f"{web_enriched_text}\n\n{profile_summary}"
                    return combined
            else:
                print(f"  ℹ️  Web search enrichment disabled (set ENABLE_WEB_SEARCH_ENRICHMENT=true to enable)", flush=True)

            # Final fallback: Build profile summary from basic fields only
            print(f"  ℹ️  Using basic profile summary only", flush=True)
            profile_summary = self._build_profile_summary(candidate)
            return profile_summary

        except Exception as e:
            print(f"Error fetching profile for candidate {candidate_id}: {e}")
            import traceback
            traceback.print_exc()
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
