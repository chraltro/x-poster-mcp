import asyncio
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import AsyncGenerator, Optional

import tweepy
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

# --- Setup ---
# Set up logging to see requests and errors in your server logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

app = FastAPI(
    title="X Poster MCP Connector",
    description="A Claude custom connector for posting tweets to X/Twitter.",
    version="1.2.0",
)


# --- OAuth 2.0 In-Memory Storage & Static Client ---
# The core of the fix is to use a predictable, static client that survives restarts.
# When the app starts, this client is always available.

oauth_clients = {}
oauth_codes = {}
access_tokens = {}

# Pre-populate a default, static client. This is the source of truth.
DEFAULT_CLIENT_ID = "claude-default-client-v1" # Using a simple, predictable ID
DEFAULT_CLIENT_SECRET = os.getenv("CONNECTOR_CLIENT_SECRET", "a-very-strong-default-secret") # It's better to load this from env

oauth_clients[DEFAULT_CLIENT_ID] = {
    "client_secret": DEFAULT_CLIENT_SECRET,
    "redirect_uris": ["https://claude.ai/oauth/callback", "http://localhost/oauth/callback"],
    "client_name": "Claude Default X Poster Client",
    "created_at": datetime.utcnow()
}
logger.info(f"Default client initialized: {DEFAULT_CLIENT_ID}")


# --- Twitter Client Setup ---
def get_twitter_client():
    """Initializes and returns a Tweepy client using credentials from environment variables."""
    return tweepy.Client(
        bearer_token=os.getenv("TWITTER_BEARER_TOKEN"),
        consumer_key=os.getenv("TWITTER_CONSUMER_KEY"),
        consumer_secret=os.getenv("TWITTER_CONSUMER_SECRET"),
        access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
        access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
    )


# --- Claude Model Context Protocol (MCP) Server ---
class XPosterMCP:
    def __init__(self):
        self.tools = [
            {
                "name": "send_tweet",
                "description": "Send a tweet to X/Twitter",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The tweet text to post (max 280 characters)"
                        }
                    },
                    "required": ["text"]
                }
            }
        ]

    async def handle_request(self, request_data: dict) -> dict:
        method = request_data.get("method")
        request_id = request_data.get("id")

        logger.info(f"MCP Request Received: {method} (ID: {request_id})")

        # --- FIX #1: Correctly advertise tool capabilities ---
        # The `initialize` method must respond with `"tools": True` and should include
        # the tool list to ensure Claude sees that the tool is available for use.
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": True  # This tells Claude that tools are available
                    },
                    "serverInfo": {
                        "name": "x-poster-mcp",
                        "version": "1.2.0"
                    },
                    "tools": self.tools # This lists the tools for efficiency
                }
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": self.tools}
            }

        elif method == "tools/call":
            params = request_data.get("params", {})
            if params.get("name") == "send_tweet":
                return await self.send_tweet(request_id, params.get("arguments", {}))

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"}
        }

    async def send_tweet(self, request_id: str, args: dict) -> dict:
        try:
            client = get_twitter_client()
            tweet_text = args.get("text", "").strip()

            if not tweet_text:
                return {
                    "jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32602, "message": "Tweet text is required."}
                }

            logger.info(f"Posting tweet: '{tweet_text[:50]}...'")
            response = client.create_tweet(text=tweet_text)
            tweet_id = response.data['id']
            tweet_url = f"https://twitter.com/user/status/{tweet_id}"

            return {
                "jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text", "text": f"✅ Tweet posted successfully!\nURL: {tweet_url}"}]}
            }

        except Exception as e:
            logger.error(f"Failed to post tweet: {e}")
            return {
                "jsonrpc": "2.0", "id": request_id,
                "result": {"content": [{"type": "text", "text": f"❌ Failed to post tweet: {e}"}]}
            }

mcp_server = XPosterMCP()


# --- OAuth 2.0 Endpoints ---

@app.get("/.well-known/oauth-authorization-server", tags=["OAuth"])
async def oauth_discovery():
    base_url = os.getenv("BASE_URL", "https://your-app-name.up.railway.app")
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"]
    }

# --- FIX #2: Always register with the static client ---
# This function is called by Claude once during installation. It MUST return
# the same, permanent credentials every time to prevent auth errors after restarts.
@app.post("/oauth/register", tags=["OAuth"])
async def register_client(request: Request):
    logger.info("Client registration request received. Returning static default credentials.")
    return {
        "client_id": DEFAULT_CLIENT_ID,
        "client_secret": DEFAULT_CLIENT_SECRET,
        "client_id_issued_at": int(oauth_clients[DEFAULT_CLIENT_ID]["created_at"].timestamp()),
        "client_secret_expires_at": 0  # Never expires
    }

@app.get("/oauth/authorize", tags=["OAuth"])
async def authorize(client_id: str, redirect_uri: str, response_type: str = "code", state: Optional[str] = None):
    logger.info(f"Authorization request for client_id: {client_id}")

    if client_id not in oauth_clients:
        logger.error(f"Authorization failed: client_id '{client_id}' not found.")
        raise HTTPException(status_code=400, detail="Invalid client_id")

    if redirect_uri not in oauth_clients[client_id]["redirect_uris"]:
        logger.error(f"Authorization failed: redirect_uri '{redirect_uri}' is not allowed for this client.")
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")

    auth_code = secrets.token_urlsafe(32)
    oauth_codes[auth_code] = {
        "client_id": client_id,
        "expires_at": datetime.utcnow() + timedelta(minutes=10)
    }

    callback_url = f"{redirect_uri}?code={auth_code}"
    if state:
        callback_url += f"&state={state}"

    logger.info(f"Authorization successful. Redirecting to callback.")
    return RedirectResponse(callback_url)

@app.post("/oauth/token", tags=["OAuth"])
async def token_endpoint(grant_type: str = Form(), code: str = Form(), redirect_uri: str = Form(), client_id: str = Form(), client_secret: str = Form()):
    logger.info(f"Token request for client_id: {client_id}")

    if client_id not in oauth_clients or oauth_clients[client_id]["client_secret"] != client_secret:
        logger.error("Token request failed: Invalid client credentials.")
        raise HTTPException(status_code=401, detail="Invalid client credentials")

    if code not in oauth_codes or oauth_codes[code]["client_id"] != client_id:
        logger.error("Token request failed: Invalid or mismatched authorization code.")
        raise HTTPException(status_code=400, detail="Invalid authorization code")

    code_data = oauth_codes.pop(code)
    if code_data["expires_at"] < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Authorization code expired")

    access_token = f"tk_{secrets.token_urlsafe(32)}"
    access_tokens[access_token] = {
        "client_id": client_id,
        "expires_at": datetime.utcnow() + timedelta(hours=24)
    }

    logger.info(f"Token issued successfully for client_id: {client_id}")
    return {"access_token": access_token, "token_type": "Bearer", "expires_in": 86400}


# --- Main SSE Endpoint & Auth Helper ---
async def validate_auth_token(request: Request) -> bool:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    token = auth_header[7:]
    if token in access_tokens and access_tokens[token]["expires_at"] > datetime.utcnow():
        return True
    return False

@app.post("/sse", tags=["MCP"])
async def mcp_sse_post(request: Request):
    if not await validate_auth_token(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            request_data = await request.json()
            response = await mcp_server.handle_request(request_data)
            yield f"data: {json.dumps(response)}\n\n"
        except Exception as e:
            logger.error(f"SSE stream error: {e}")
            yield f"data: {json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32603, 'message': str(e)}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# --- Utility & Health Check Endpoints ---
@app.get("/", tags=["Health"])
async def root():
    return {"status": "running", "service": "X Poster MCP", "version": "1.2.0"}

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy"}

@app.get("/debug", tags=["Debug"])
async def debug_state():
    return {
        "registered_clients": list(oauth_clients.keys()),
        "active_auth_codes": len(oauth_codes),
        "active_access_tokens": len(access_tokens)
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)