"""
Vercel Serverless Function - Trigger GitHub Actions workflow
Endpoint: POST /api/refresh
"""
import os
import json
from http.client import HTTPSConnection

def handler(request):
    # Only allow POST
    if request.method != "POST":
        return {
            "statusCode": 405,
            "body": json.dumps({"error": "Method not allowed"})
        }

    # Get GitHub token from environment
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "GitHub token not configured"})
        }

    # Trigger GitHub Actions workflow
    try:
        conn = HTTPSConnection("api.github.com")
        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "SNS-Dashboard-Refresh",
            "Content-Type": "application/json"
        }

        # Trigger workflow_dispatch event
        body = json.dumps({"ref": "main"})
        conn.request(
            "POST",
            "/repos/ymi-flowing/sns-daily-report/actions/workflows/refresh.yml/dispatches",
            body=body,
            headers=headers
        )

        response = conn.getresponse()
        status = response.status

        if status == 204:
            return {
                "statusCode": 200,
                "headers": {
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json"
                },
                "body": json.dumps({
                    "success": True,
                    "message": "Refresh workflow triggered successfully"
                })
            }
        else:
            error_body = response.read().decode()
            return {
                "statusCode": status,
                "headers": {
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json"
                },
                "body": json.dumps({
                    "success": False,
                    "error": f"GitHub API returned {status}: {error_body}"
                })
            }

    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/json"
            },
            "body": json.dumps({
                "success": False,
                "error": str(e)
            })
        }
