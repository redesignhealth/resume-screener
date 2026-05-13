# Resume Screener

Automated candidate screening system using Claude AI to evaluate job applications from Ashby ATS.

## Overview

This application:
- Polls Ashby ATS every 60 minutes for new job applications
- Scores resumes using Claude AI based on role-specific criteria
- Sends Slack alerts for high-scoring candidates (≥7.0/10)
- Auto-archives low-scoring candidates below threshold
- Monitors 8 different roles with custom scoring criteria

## Architecture

- **Runtime**: Python 3.9
- **Port**: 8080
- **Health Check**: GET / returns "OK"
- **State**: JSON file (`processed_applications.json`) to track processed applications
- **Hosting**: Dokploy (internal RH platform)

## Environment Variables

### Required (API Keys)
- `ANTHROPIC_API_KEY` - Claude AI API key
- `ASHBY_API_KEY` - Ashby ATS API key
- `SLACK_WEBHOOK_URL` - Slack webhook for candidate alerts

### Optional (Have Defaults)
- `PORT` - Port to listen on (default: 8080)
- `POLL_INTERVAL_MINUTES` - Polling frequency (default: 60)
- `LOOKBACK_HOURS` - How far back to check for applications (default: 1)
- `TRACKER_FILE` - Tracking file name (default: processed_applications.json)
- `ENABLE_WEB_SEARCH_ENRICHMENT` - Enable web search for profile enrichment (default: false)

## How Resume Data is Extracted

The system attempts to get candidate information in this order:

1. **Parsed resume text from Ashby API** (if available)
2. **PDF resume download and parsing** (if Ashby provides file access)
3. **LinkedIn profile scraping** (if URL available, but often blocked)
4. **Email-based work history extraction** ✅ **Primary fallback**
   - Extracts company names from email addresses
   - Example: `mayank@elevationcapital.com` → "Elevation Capital"
   - Identifies top firms: McKinsey, Elevation, Sequoia, etc.
5. **Web search enrichment** (optional, requires `ENABLE_WEB_SEARCH_ENRICHMENT=true`)
   - Uses DuckDuckGo to find public professional information
   - May hit rate limits, recommended for manual use only

### Why Email-Based Extraction Works Well

Most professional candidates have work email addresses in Ashby (from previous applications or sourcing). The system:
- Extracts domain names from non-personal emails
- Maps domains to company names (e.g., `elevationcapital.com` → Elevation Capital)
- Provides enough signal for venture experience, consulting background, etc.
- Achieves scores of 7+ for strong candidates (triggering manual review)

**Trade-off:** May miss specific focus areas (e.g., "healthcare investor") but captures the firm quality, which is the primary signal.

## Local Development

1. **Create `.env` file** with your API keys:
   ```bash
   ANTHROPIC_API_KEY=your_key_here
   ASHBY_API_KEY=your_key_here
   SLACK_WEBHOOK_URL=your_webhook_here
   ```

2. **Install dependencies** (requires [uv](https://docs.astral.sh/uv/)):
   ```bash
   uv sync
   ```

3. **Run with Docker**:
   ```bash
   docker compose up --build
   ```

3. **Test health endpoint**:
   ```bash
   curl http://localhost:8080
   # Should return: OK
   ```

## Dokploy Deployment

### Prerequisites
- Repository must be in `redesignhealth` GitHub org
- Dokploy project created by tech team
- Environment variables configured in Dokploy

### Deployment Configuration

**Build Settings:**
- Dockerfile path: `./Dockerfile`
- Build context: `.` (root)

**Runtime:**
- Port: `8080`
- Health check path: `/`
- Health check interval: `30s`

**Persistent Storage (Required!):**
- Mount: `/app/processed_applications.json`
- Purpose: Prevents reprocessing applications across restarts

### Requesting Deployment

Contact tech team (#helpdesk) with:
1. App name: `resume-screener`
2. Repository: `https://github.com/redesignhealth/resume-screener`
3. Branch: `main`
4. Environment variables (provide securely)
5. Persistent storage requirement

## Monitored Roles

Currently monitoring 8 roles:
1. Talent Engineering Lead, India
2. Executive Assistant & Workplace Operations
3. Managing Director of New Ventures
4. Director of Global Development, India
5. Managing Director of Ventures, India
6. Associate, AI Buyouts
7. Managing Director, Southeast Asia
8. Managing Director, AI Buyouts

Each role has custom scoring criteria defined in `config.yaml`.

## Configuration

Role-specific scoring criteria are configured in `config.yaml`. Each role defines:
- Score threshold for alerts
- Archive threshold for auto-archiving
- Custom scoring dimensions with weights
- Special rules (e.g., NYC location requirement, dual-track scoring)

## No Database Required

This app does not need a Postgres database. It uses a JSON file for state tracking.
