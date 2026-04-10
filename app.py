#!/usr/bin/env python3
"""
Zerodha Kite Auto-Login Service

A simple HTTP API service that handles Zerodha Kite login with TOTP
and provides access tokens to authorized clients.

Usage:
    python app.py

Endpoints:
    GET  /health           - Health check
    POST /login            - Perform full login, returns access token
    GET  /token            - Get current access token
    POST /ensure           - Ensure authenticated (login if needed)

Environment Variables:
    KITE_API_KEY         - Your Kite Connect API key
    KITE_API_SECRET      - Your Kite Connect API secret
    KITE_USER_ID         - Your Zerodha user ID (e.g., AB1234)
    KITE_PASSWORD        - Your Zerodha password
    KITE_TOTP_SECRET     - Your TOTP secret (base32)
    KITE_REDIRECT_URL    - Redirect URL (default: this service's URL)
    API_KEY              - Optional API key for client authentication
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Header, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import pyotp


# FastAPI app
app = FastAPI(
    title="Zerodha Kite Auto-Login Service",
    description="Automated Zerodha Kite login with TOTP support",
    version="1.0.0"
)


# Configuration
class Config:
    KITE_API_KEY: str = os.getenv("KITE_API_KEY", "")
    KITE_API_SECRET: str = os.getenv("KITE_API_SECRET", "")
    KITE_USER_ID: str = os.getenv("KITE_USER_ID", "")
    KITE_PASSWORD: str = os.getenv("KITE_PASSWORD", "")
    KITE_TOTP_SECRET: str = os.getenv("KITE_TOTP_SECRET", "")
    KITE_REDIRECT_URL: str = os.getenv("KITE_REDIRECT_URL", "")
    API_KEY: str = os.getenv("API_KEY", "")

    # Auto-generate redirect URL if not set
    @classmethod
    def get_redirect_url(cls) -> str:
        if cls.KITE_REDIRECT_URL:
            return cls.KITE_REDIRECT_URL
        # Use Railway's URL if available, else default
        railway_url = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
        if railway_url:
            return f"https://{railway_url}/callback"
        return "http://127.0.0.1:5000/callback"


# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Zerodha API endpoints
_LOGIN_URL = "https://kite.zerodha.com/api/login"
_TWOFA_URL = "https://kite.zerodha.com/api/twofa"


# In-memory token storage
class TokenStore:
    access_token: Optional[str] = None
    login_time: Optional[float] = None
    user_id: Optional[str] = None


token_store = TokenStore()


# Response models
class HealthResponse(BaseModel):
    status: str
    authenticated: bool
    user_id: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    user_id: str
    login_time: float
    expires_at: str  # ISO timestamp


class ErrorResponse(BaseModel):
    error: str
    message: str


# API Key authentication
async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    """Verify API key if configured."""
    if Config.API_KEY and x_api_key != Config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def validate_env_vars() -> None:
    """Validate required environment variables."""
    required = ["KITE_API_KEY", "KITE_API_SECRET", "KITE_USER_ID", "KITE_PASSWORD", "KITE_TOTP_SECRET"]
    missing = [var for var in required if not getattr(Config, var)]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"Missing required environment variables: {', '.join(missing)}"
        )


def generate_totp() -> str:
    """Generate current TOTP code."""
    clean_secret = Config.KITE_TOTP_SECRET.replace(" ", "").replace("-", "")
    totp = pyotp.TOTP(clean_secret)
    return totp.now()


def get_kite_login_url() -> str:
    """Generate the Kite login URL."""
    redirect_url = Config.get_redirect_url()
    return f"https://kite.zerodha.com/connect/login?v=3&api_key={Config.KITE_API_KEY}&redirect_url={redirect_url}"


async def generate_session(request_token: str) -> dict:
    """Exchange request_token for access_token using kiteconnect SDK."""
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=Config.KITE_API_KEY)

    # Run in thread pool since kiteconnect is synchronous
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(
        None,
        lambda: kite.generate_session(request_token, api_secret=Config.KITE_API_SECRET)
    )

    if data.get("status") != "success":
        raise Exception(f"Session generation failed: {data.get('message')}")

    return data["data"]


async def get_request_token() -> str:
    """Obtain request_token via Zerodha's HTTP login API."""
    login_url = get_kite_login_url()
    logger.info("Starting HTTP-based login flow...")

    async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(30.0)) as client:
        # Step 1: POST credentials
        login_resp = await client.post(
            _LOGIN_URL,
            data={"user_id": Config.KITE_USER_ID, "password": Config.KITE_PASSWORD},
        )

        if login_resp.status_code != 200:
            raise Exception(f"Login failed: {login_resp.status_code}")

        login_data = login_resp.json()
        if login_data.get("status") != "success":
            raise Exception(f"Login rejected: {login_data.get('message')}")

        request_id = login_data["data"]["request_id"]

        # Step 2: POST TOTP
        totp_code = generate_totp()
        logger.debug(f"Generated TOTP code: {totp_code[:2]}******")

        twofa_resp = await client.post(
            _TWOFA_URL,
            data={
                "user_id": Config.KITE_USER_ID,
                "request_id": request_id,
                "twofa_value": totp_code,
                "twofa_type": "totp",
            },
        )

        if twofa_resp.status_code != 200:
            raise Exception(f"2FA failed: {twofa_resp.status_code}")

        twofa_data = twofa_resp.json()
        if twofa_data.get("status") != "success":
            raise Exception(f"2FA rejected: {twofa_data.get('message')}")

        # Step 3: Follow redirect chain for request_token
        current_url = login_url
        max_hops = 5

        for hop in range(max_hops):
            resp = await client.get(current_url)

            if resp.status_code in (301, 302, 303, 307):
                location = resp.headers.get("location", "")
                if not location:
                    raise Exception(f"Redirect with no Location header at hop {hop + 1}")

                parsed = urlparse(location)
                params = parse_qs(parsed.query)

                if "request_token" in params:
                    request_token = params["request_token"][0]
                    logger.info(f"Obtained request_token at hop {hop + 1}")
                    return request_token

                current_url = location
                continue

            elif resp.status_code == 200:
                parsed = urlparse(str(resp.url))
                params = parse_qs(parsed.query)

                if "request_token" in params:
                    request_token = params["request_token"][0]
                    logger.info("Obtained request_token from final URL")
                    return request_token
                break

        raise Exception(f"Could not obtain request_token after {max_hops} hops")


async def perform_login() -> str:
    """Perform full login flow and return access token."""
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Login attempt {attempt}/{max_retries} for user {Config.KITE_USER_ID}")

            request_token = await get_request_token()
            data = await generate_session(request_token)

            access_token = data["access_token"]
            login_time = time.time()

            # Update token store
            token_store.access_token = access_token
            token_store.login_time = login_time
            token_store.user_id = Config.KITE_USER_ID

            logger.info(f"Login successful for user {Config.KITE_USER_ID}")
            return access_token

        except Exception as e:
            logger.warning(f"Login attempt {attempt} failed: {e}")
            if attempt == max_retries:
                raise
            await asyncio.sleep(2 ** attempt)

    raise Exception("Login failed")


async def validate_session() -> bool:
    """Check if current session is still valid."""
    if not token_store.access_token:
        return False

    profile_url = "https://api.kite.trade/user/profile"
    headers = {"Authorization": f"token {Config.KITE_API_KEY}:{token_store.access_token}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(profile_url, headers=headers)

            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    return True
        return False
    except Exception:
        return False


# API Endpoints
@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    is_valid = await validate_session() if token_store.access_token else False
    return {
        "status": "healthy",
        "authenticated": is_valid,
        "user_id": token_store.user_id if is_valid else None
    }


@app.post("/login", response_model=TokenResponse)
async def login(x_api_key: Optional[str] = Header(None)):
    """Perform full Zerodha login and return access token."""
    await verify_api_key(x_api_key)
    validate_env_vars()

    try:
        access_token = await perform_login()

        # Calculate expiry (approximately 6 AM next day)
        import datetime
        expiry = datetime.datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
        expiry += datetime.timedelta(days=1)

        return {
            "access_token": access_token,
            "user_id": Config.KITE_USER_ID,
            "login_time": token_store.login_time or time.time(),
            "expires_at": expiry.isoformat()
        }
    except Exception as e:
        logger.error(f"Login failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/token", response_model=TokenResponse)
async def get_token(x_api_key: Optional[str] = Header(None)):
    """Get current access token."""
    await verify_api_key(x_api_key)

    if not token_store.access_token:
        raise HTTPException(status_code=404, detail="No active session. Call /login or /ensure first.")

    # Check if token is still valid
    if not await validate_session():
        raise HTTPException(status_code=401, detail="Session expired. Call /login or /ensure.")

    import datetime
    expiry = datetime.datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
    expiry += datetime.timedelta(days=1)

    return {
        "access_token": token_store.access_token,
        "user_id": token_store.user_id,
        "login_time": token_store.login_time or time.time(),
        "expires_at": expiry.isoformat()
    }


@app.post("/ensure", response_model=TokenResponse)
async def ensure_authenticated(x_api_key: Optional[str] = Header(None)):
    """Ensure authenticated - login only if needed."""
    await verify_api_key(x_api_key)
    validate_env_vars()

    # Check if current session is valid
    if token_store.access_token and await validate_session():
        logger.info("Existing session is still valid")

        import datetime
        expiry = datetime.datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
        expiry += datetime.timedelta(days=1)

        return {
            "access_token": token_store.access_token,
            "user_id": token_store.user_id,
            "login_time": token_store.login_time or time.time(),
            "expires_at": expiry.isoformat()
        }

    # Need to login
    logger.info("Session expired or invalid, logging in...")
    try:
        access_token = await perform_login()

        import datetime
        expiry = datetime.datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
        expiry += datetime.timedelta(days=1)

        return {
            "access_token": access_token,
            "user_id": Config.KITE_USER_ID,
            "login_time": token_store.login_time or time.time(),
            "expires_at": expiry.isoformat()
        }
    except Exception as e:
        logger.error(f"Login failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/callback")
async def callback():
    """OAuth callback endpoint for Zerodha redirect."""
    return {"status": "callback received", "message": "This service handles OAuth flow internally"}


# Startup event
@app.on_event("startup")
async def startup_event():
    """Run on startup."""
    logger.info("=" * 60)
    logger.info("Zerodha Kite Auto-Login Service")
    logger.info("=" * 60)
    logger.info(f"Redirect URL: {Config.get_redirect_url()}")
    logger.info(f"API Key protection: {'Enabled' if Config.API_KEY else 'Disabled (WARNING!)'}")

    # Validate environment
    try:
        validate_env_vars()
        logger.info("Environment variables validated")
    except Exception as e:
        logger.warning(f"Environment validation failed: {e}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 5000))
    uvicorn.run(app, host="0.0.0.0", port=port)
