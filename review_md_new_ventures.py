#!/usr/bin/env python3
"""Review all candidates in Application Review stage for Managing Director of New Ventures."""

import os
from pathlib import Path
from ashby_client import AshbyClient
from scorer import ResumeScorer

# Load environment variables
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key] = value

def review_all_candidates():
    client = AshbyClient()
    scorer = ResumeScorer()

    job_id = "e484d977-fe6b-47be-8dc2-78b44a2f51fa"  # Managing Director of New Ventures
    job_title = "Managing Director of New Ventures"

    print(f"\n{'='*80}")
    print(f"REVIEWING ALL CANDIDATES IN APPLICATION REVIEW")
    print(f"{'='*80}\n")
    print(f"Job: {job_title}")
    print(f"Job ID: {job_id}\n")

    # Fetch all applications for this job (with pagination)
    print("Fetching applications from Ashby...")
    result = client._request("/application.list", {"jobId": job_id})
    applications = result.get("results", [])

    # Handle pagination
    while result.get("nextCursor"):
        print(f"  Fetching more applications (cursor: {result['nextCursor'][:20]}...)...")
        result = client._request("/application.list", {
            "jobId": job_id,
            "cursor": result["nextCursor"]
        })
        applications.extend(result.get("results", []))

    print(f"Found {len(applications)} total applications\n")

    # Filter for Application Review stage
    review_stage_apps = []
    for app in applications:
        stage = app.get("currentInterviewStage", {})
        stage_title = stage.get("title", "")
        if stage_title == "Application Review":
            review_stage_apps.append(app)

    print(f"Found {len(review_stage_apps)} candidates in Application Review stage\n")
    print("="*80)

    if not review_stage_apps:
        print("No candidates to review.")
        return

    # Score each candidate
    results = []
    for i, app in enumerate(review_stage_apps, 1):
        app_id = app.get("id")
        candidate = app.get("candidate", {})
        candidate_id = candidate.get("id")
        candidate_name = candidate.get("name", "Unknown")

        print(f"\n[{i}/{len(review_stage_apps)}] Screening: {candidate_name}")
        print(f"  Candidate ID: {candidate_id}")
        print(f"  Application ID: {app_id}")

        try:
            # Get full application details
            app_details = client.get_application_details(app)
            resume_text = app_details.get("resume_text", "")

            if not resume_text or len(resume_text) < 100:
                print(f"  ⚠️  Minimal resume data ({len(resume_text)} chars)")

            # Score the candidate
            scores = scorer.score_resume(resume_text, job_title, candidate_name, job_id)
            total_score = scores.get("total_score", 0)

            print(f"  📊 Score: {total_score:.1f}/10.0")

            results.append({
                "name": candidate_name,
                "candidate_id": candidate_id,
                "application_id": app_id,
                "score": total_score,
                "scores": scores,
                "resume_length": len(resume_text)
            })

        except Exception as e:
            print(f"  ❌ Error screening candidate: {e}")
            results.append({
                "name": candidate_name,
                "candidate_id": candidate_id,
                "application_id": app_id,
                "score": 0,
                "error": str(e)
            })

    # Generate summary report
    print(f"\n{'='*80}")
    print(f"SUMMARY REPORT")
    print(f"{'='*80}\n")

    # Sort by score descending
    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    threshold = scorer.get_score_threshold(job_id=job_id, job_title=job_title)
    print(f"Alert Threshold: {threshold}\n")

    # Display results
    print(f"{'Rank':<6} {'Name':<30} {'Score':<10} {'Status':<15} {'Resume Data'}")
    print("-" * 80)

    for i, result in enumerate(results, 1):
        name = result["name"][:28]
        score = result.get("score", 0)
        resume_len = result.get("resume_length", 0)

        if "error" in result:
            status = "ERROR"
            data_quality = "Error"
        elif score >= threshold:
            status = "✅ ALERT"
            data_quality = f"{resume_len} chars"
        else:
            status = "❌ Skip"
            data_quality = f"{resume_len} chars"

        print(f"{i:<6} {name:<30} {score:<10.1f} {status:<15} {data_quality}")

    # Show top candidates details
    print(f"\n{'='*80}")
    print(f"TOP CANDIDATES (Score >= {threshold})")
    print(f"{'='*80}\n")

    top_candidates = [r for r in results if r.get("score", 0) >= threshold]

    if not top_candidates:
        print("No candidates meet the threshold.")
    else:
        for result in top_candidates:
            print(f"\n📋 {result['name']}")
            print(f"   Score: {result['score']:.1f}/10.0")
            print(f"   Candidate ID: {result['candidate_id']}")
            print(f"   Application ID: {result['application_id']}")

            scores_obj = result.get("scores", {})
            if "criteria_labels" in scores_obj:
                print(f"   Top dimensions:")
                # Get top 3 scoring dimensions
                dimension_scores = []
                for name, label in scores_obj["criteria_labels"].items():
                    score_val = scores_obj.get(name)
                    if isinstance(score_val, (int, float)):
                        dimension_scores.append((label, score_val))

                dimension_scores.sort(key=lambda x: x[1], reverse=True)
                for label, score_val in dimension_scores[:3]:
                    print(f"     - {label}: {score_val}/10")

            if "fit_summary" in scores_obj:
                print(f"   Summary: {scores_obj['fit_summary']}")

    print(f"\n{'='*80}\n")

if __name__ == "__main__":
    review_all_candidates()
