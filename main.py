import asyncio
import json
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import AsyncGenerator, Optional

import tweepy
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

# --- Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

app = FastAPI(
    title="X Poster MCP Connector",
    description="A Claude custom connector for posting tweets to X/Twitter.",
    version="3.0.0", # The one that actually works
)

# --- OAuth 2.0 In-Memory Storage & Static Client ---
oauth_clients = {}
oauth_codes = {}
access_tokens = {}

# --- THE REAL FIX ---
# We are now using the EXACT client_id that Claude is sending in the logs.
# Claude has this ID permanently cached for your URL. We MUST accept it.
CLAUDE_PERMANENT_CLIENT_ID = "da265f69-b19f-477b-bb5f-c18dcdb3564d"

# We must also define a secret for it. Set this in your Railway environment variables.
# You can use the value "super-secret-key-that-i-made-up" or generate your own.
CLIENT_SECRET = os.getenv("MY_CONNECTOR_SECRET")

if not CLIENT_SECRET:
    raise ValueError("CRITICAL ERROR: MY_CONNECTOR_SECRET environment variable is not set.")

oauth_clients[CLAUDE_PERMANENT_CLIENT_ID] = {
    "client_secret": CLIENT_SECRET,
    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
    "client_name": "The Only Client Claude Remembers",
    "created_at": datetime.utcnow()
}
logger.info(f"SERVER IS READY. Accepting ONLY client_id: {CLAUDE_PERMANENT_CLIENT_ID}")


# --- Twitter Client Setup ---
def get_twitter_client():
    return tweepy.Client(
        bearer_token=os.getenv("TWITTER_BEARER_TOKEN"),
        consumer_key=os.getenv("TWITTER_CONSUMER_KEY"),
        consumer_secret=os.getenv("TWITTER_CONSUMER_SECRET"),
        access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
        access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
    )


# --- Claude Model Context Protocol (MCP) Server ---
# This class is correct and does not need changes.
class XPosterMCP:
    def __init__(self):
        self.tools = [
            {
                "name": "send_tweet",
                "description": "Send a tweet to X/Twitter.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "The tweet text to post"}},
                    "required": ["text"]
                }
            }
        ]

    async def handle_request(self, request_data: dict) -> dict:
        method = request_data.get("method")
        request_id = request_data.get("id")
        logger.info(f"MCP Request Received: {method}")

        if method == "initialize":
            return {
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": True},
                    "serverInfo": {"name": "x-poster-mcp", "version": "3.0.0"},
                    "tools": self.tools
                }
            }
        elif method == "tools/call":
            return await self.send_tweet(request_id, request_data.get("params", {}).get("arguments", {}))
        
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}

    async def send_tweet(self, request_id: str, args: dict) -> dict:
        try:
            tweet_text = args.get("text", "").strip()
            if not tweet_text: raise ValueError("Tweet text cannot be empty.")
            
            logger.info(f"Posting tweet...")
            client = get_twitter_client()
            response = client.create_tweet(text=tweet_text)
            tweet_id = response.data['id']
            tweet_url = f"https://twitter.com/user/status/{tweet_id}"
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"✅ Tweet posted successfully!\nURL: {tweet_url}"}]}}
        except Exception as e:
            logger.error(f"Failed to post tweet: {e}")
            return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": f"❌ Failed to post tweet: {e}"}]}}

mcp_server = XPosterMCP()


# --- OAuth 2.0 Endpoints ---
@app.get("/.well-known/oauth-authorization-server", tags=["OAuth"])
async def oauth_discovery():
    base_url = os.getenv("BASE_URL")
    return {
        "issuer": base_url, "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token", "registration_endpoint": f"{base_url}/oauth/register",
    }

# This endpoint is now effectively dead, but we leave it for compliance.
# Claude is not calling it, but it's good practice to have it.
@app.post("/oauth/register", tags=["OAuth"])
async def register_client(request: Request):
    logger.warning("Register endpoint was called, but we are ignoring it and returning the permanent client ID.")
    return {
        "client_id": CLAUDE_PERMANENT_CLIENT_ID, "client_secret": CLIENT_SECRET,
        "client_id_issued_at": int(datetime.utcnow().timestamp()),
        "client_secret_expires_at": 0
    }

@app.get("/oauth/authorize", tags=["OAuth"])
async def authorize(client_id: str, redirect_uri: str, response_type: str = "code", state: Optional[str] = None):
    logger.info(f"Authorization request received for client_id: {client_id}")
    
    # Check 1: Does the client ID match the one Claude is hardcoded to use?
    if client_id != CLAUDE_PERMANENT_CLIENT_ID:
        logger.error(f"FATAL: Received client_id '{client_id}' does not match the permanent ID '{CLAUDE_PERMANENT_CLIENT_ID}'.")
        raise HTTPException(status_code=400, detail="Invalid client_id")
    
    # Check 2: Is the redirect URI valid?
    if redirect_uri not in oauth_clients[client_id]["redirect_uris"]:
        logger.error(f"FATAL: redirect_uri '{redirect_uri}' is not in the allowed list.")
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")
    
    logger.info("Client ID and Redirect URI are valid. Proceeding.")
    auth_code = secrets.token_urlsafe(32)
    oauth_codes[auth_code] = {"client_id": client_id, "expires_at": datetime.utcnow() + timedelta(minutes=10)}
    callback_url = f"{redirect_uri}?code={auth_code}" + (f"&state={state}" if state else "")
    return RedirectResponse(callback_url)

@app.post("/oauth/token", tags=["OAuth"])
async def token_endpoint(grant_type: str = Form(), code: str = Form(), client_id: str = Form(), client_secret: str = Form()):
    if client_id != CLAUDE_PERMANENT_CLIENT_ID or client_secret != CLIENT_SECRET:
        raise HTTPException(status_code=401, detail="Invalid client credentials on token endpoint")
    if code not in oauth_codes or oauth_codes[code]["client_id"] != client_id:
        raise HTTPException(status_code=400, detail="Invalid authorization code")
    
    code_data = oauth_codes.pop(code)
    if code_data["expires_at"] < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Authorization code expired")
    
    access_token = f"tk_{secrets.token_urlsafe(32)}"
    access_tokens[access_token] = {"client_id": client_id, "expires_at": datetime.utcnow() + timedelta(hours=24)}
    return {"access_token": access_token, "token_type": "Bearer", "expires_in": 86400}

# --- Main SSE Endpoint (This logic is correct) ---
async def validate_auth_token(request: Request) -> bool:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "): return False
    token = auth_header[7:]
    return token in access_tokens and access_tokens[token]["expires_at"] > datetime.utcnow()

@app.post("/sse", tags=["MCP"])
async def mcp_sse_post(request: Request):
    if not await validate_auth_token(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            body = await request.body()
            if not body:
                response = await mcp_server.handle_request({"jsonrpc": "2.0", "method": "initialize", "id": "init_1"})
            else:
                response = await mcp_server.handle_request(json.loads(body))
            yield f"data: {json.dumps(response)}\n\n"
        except Exception as e:
            logger.error(f"SSE stream error: {e}")
            yield f"data: {json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32603, 'message': str(e)}})}\n\n"
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")

# --- Utility Endpoints ---
@app.get("/", tags=["Health"])
async def root(): return {"status": "running", "service": "X Poster MCP", "version": "3.0.0"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)