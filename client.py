#!/usr/bin/env python3
"""
Simple client for Zerodha Login Service.

Usage:
    python client.py              # Get token using /ensure
    python client.py --login      # Force new login
    python client.py --health     # Check service health
"""

import os
import sys
import argparse
import requests
import json


def get_token(service_url: str, api_key: str = None, force_login: bool = False) -> dict:
    """Get access token from the service."""
    endpoint = "/login" if force_login else "/ensure"

    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    response = requests.post(f"{service_url}{endpoint}", headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def get_health(service_url: str) -> dict:
    """Get service health status."""
    response = requests.get(f"{service_url}/health", timeout=10)
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser(description="Zerodha Login Service Client")
    parser.add_argument("--url", default=os.getenv("ZERODHA_LOGIN_SERVICE_URL", "http://localhost:5000"),
                        help="Service URL")
    parser.add_argument("--api-key", default=os.getenv("ZERODHA_LOGIN_SERVICE_API_KEY"),
                        help="API key for authentication")
    parser.add_argument("--login", action="store_true", help="Force new login")
    parser.add_argument("--health", action="store_true", help="Check service health")
    parser.add_argument("--raw", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    try:
        if args.health:
            data = get_health(args.url)
            if args.raw:
                print(json.dumps(data, indent=2))
            else:
                print(f"Status: {data['status']}")
                print(f"Authenticated: {data['authenticated']}")
                if data.get('user_id'):
                    print(f"User: {data['user_id']}")
        else:
            data = get_token(args.url, args.api_key, args.login)
            if args.raw:
                print(json.dumps(data, indent=2))
            else:
                print(f"Access Token: {data['access_token']}")
                print(f"User: {data['user_id']}")
                print(f"Expires: {data['expires_at']}")
                print(f"\nToken only (for scripts):")
                print(data['access_token'])
        return 0
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
