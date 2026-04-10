# Zerodha Kite Auto-Login Service

A standalone HTTP API service that handles Zerodha Kite login with TOTP 2FA. Provides access tokens to authorized clients.

## Why This Service?

Solves the redirect URL problem when using the same Zerodha API credentials for:
- Local development apps (localhost)
- Railway deployed apps (public URL)

Instead of configuring different redirect URLs for each app, both can call this service to get access tokens.

## Architecture

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────┐
│  Local App      │────────>│  Zerodha Login   │<───────>│  Zerodha Kite   │
│  (localhost)    │  /token │  Service         │  OAuth  │  API            │
└─────────────────┘         └──────────────────┘         └─────────────────┘
                                      ▲
                                      │ /token
┌─────────────────┐                   │
│  Railway App    │───────────────────┘
│  (OpenClaw)     │
└─────────────────┘
```

## API Endpoints

### `GET /health`
Health check and authentication status.

**Response:**
```json
{
  "status": "healthy",
  "authenticated": true,
  "user_id": "AB1234"
}
```

### `POST /login`
Perform full Zerodha login with TOTP. Returns access token.

**Headers:**
```
X-API-Key: your-api-key  (optional, if API_KEY is set)
```

**Response:**
```json
{
  "access_token": "xyz...",
  "user_id": "AB1234",
  "login_time": 1682748432.123,
  "expires_at": "2026-04-11T06:00:00"
}
```

### `GET /token`
Get current access token (must be already logged in).

**Response:** Same as `/login`

### `POST /ensure`
Ensure authenticated - performs login only if current session is expired.

**Response:** Same as `/login`

## Railway Setup

### 1. Create New Project

```bash
# Via Railway CLI
railway new
cd zerodha-login-service
railway up
```

Or via Railway dashboard:
1. Click "New Project"
2. Select "Deploy from GitHub repo"
3. Or start empty and connect later

### 2. Set Environment Variables

Add these in Railway dashboard:

| Variable | Description | Example |
|----------|-------------|---------|
| `KITE_API_KEY` | Your Kite Connect API key | `kite_api_key` |
| `KITE_API_SECRET` | Your Kite Connect API secret | `your_secret` |
| `KITE_USER_ID` | Your Zerodha user ID | `AB1234` |
| `KITE_PASSWORD` | Your Zerodha password | `your_password` |
| `KITE_TOTP_SECRET` | TOTP secret (base32) | `JBSWY3DPEHPK3PXP` |
| `API_KEY` | (Optional) Client API key for security | `your_secret_key` |

**Note:** `KITE_REDIRECT_URL` is auto-generated from Railway's public URL.

### 3. Update Zerodha App Settings

In your Zerodha Kite Connect app settings:
- Set **Redirect URL** to: `https://your-service.railway.app/callback`

Replace `your-service.railway.app` with your actual Railway URL.

## Usage Examples

### From Local App (Python/requests)

```python
import requests

SERVICE_URL = "https://your-service.railway.app"
API_KEY = "your-api-key"  # if set

# Get access token
response = requests.post(
    f"{SERVICE_URL}/ensure",
    headers={"X-API-Key": API_KEY}
)
data = response.json()
access_token = data["access_token"]

# Use token with Kite Connect
from kiteconnect import KiteConnect
kite = KiteConnect(api_key="your_api_key")
kite.set_access_token(access_token)
```

### From Railway OpenClaw / Vicky

```bash
# Before checking Zerodha levels
curl -X POST https://your-service.railway.app/ensure \
  -H "X-API-Key: your-api-key" \
  -o /tmp/kite_token.json

# Extract token
TOKEN=$(cat /tmp/kite_token.json | jq -r '.access_token')
```

### From Shell

```bash
# Get access token
curl -X POST https://your-service.railway.app/login \
  -H "X-API-Key: your-api-key" \
  | jq '.access_token'
```

## Security Notes

1. **API Key**: Set `API_KEY` environment variable to require authentication
2. **HTTPS**: Always use HTTPS in production (Railway provides this)
3. **Secrets**: Never commit credentials to git
4. **Token Expiry**: Access tokens expire around 6 AM IST daily

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export KITE_API_KEY="your_key"
export KITE_API_SECRET="your_secret"
export KITE_USER_ID="AB1234"
export KITE_PASSWORD="your_password"
export KITE_TOTP_SECRET="your_totp_secret"

# Run
python app.py
```

Service runs on `http://localhost:5000`

## Monitoring

- **Health Check**: `GET /health`
- **Logs**: View in Railway dashboard
- **Metrics**: Token store is in-memory (resets on restart)

## Troubleshooting

### "Missing required environment variables"
- Check all 5 required KITE_* variables are set in Railway

### "2FA rejected"
- Verify TOTP secret matches your authenticator app

### "Session expired"
- Call `/ensure` instead of `/token` to auto-renew

### "Invalid API key"
- Check X-API-Key header matches API_KEY env var (if set)

## License

MIT
