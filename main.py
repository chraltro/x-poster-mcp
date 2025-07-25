from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import StreamingResponse, RedirectResponse, JSONResponse
import json
import tweepy
import os
from typing import AsyncGenerator, Optional
from dotenv import load_dotenv
import uuid
import secrets
from datetime import datetime, timedelta
import asyncio
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI()

# Global storage - survives until restart
oauth_clients = {}
oauth_codes = {}
access_tokens = {}

# Pre-populate a default client to avoid registration issues
DEFAULT_CLIENT_ID = "claude-default-client"
DEFAULT_CLIENT_SECRET = "claude-default-secret"

# Initialize with a default client
oauth_clients[DEFAULT_CLIENT_ID] = {
    "client_secret": DEFAULT_CLIENT_SECRET,
    "redirect_uris": ["https://claude.ai/oauth/callback", "http://localhost/oauth/callback"],
    "client_name": "Claude Default Client",
    "created_at": datetime.utcnow()
}

def get_twitter_client():
    return tweepy.Client(
        bearer_token=os.getenv("TWITTER_BEARER_TOKEN"),
        consumer_key=os.getenv("TWITTER_CONSUMER_KEY"),
        consumer_secret=os.getenv("TWITTER_CONSUMER_SECRET"),
        access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
        access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
    )

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

    async def handle_request(self, request_data: dict, auth_token: str = None) -> dict:
        method = request_data.get("method")
        request_id = request_data.get("id")

        logger.info(f"MCP Request: {method} (ID: {request_id})")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    # CORRECT: Use `True` and include the tool list.
                    "capabilities": {
                        "tools": True
                    },
                    "serverInfo": {
                        "name": "x-poster-mcp",
                        "version": "1.0.0"
                    },
                    "tools": self.tools
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
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            if tool_name == "send_tweet":
                return await self.send_tweet(request_id, arguments)

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found"}
        }

    async def send_tweet(self, request_id: str, args: dict) -> dict:
        try:
            client = get_twitter_client()
            tweet_text = args.get("text", "")
            
            if not tweet_text:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": "Tweet text is required"}
                }
            
            logger.info(f"Posting tweet: {tweet_text[:50]}...")
            
            response = client.create_tweet(text=tweet_text)
            tweet_id = response.data['id']
            tweet_url = f"https://twitter.com/user/status/{tweet_id}"
            
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"✅ Tweet posted successfully!\n\nTweet: {tweet_text}\nURL: {tweet_url}"
                        }
                    ]
                }
            }
            
        except Exception as e:
            logger.error(f"Tweet failed: {e}")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"❌ Failed to post tweet: {str(e)}"
                        }
                    ]
                }
            }

mcp_server = XPosterMCP()

# OAuth Discovery
@app.get("/.well-known/oauth-authorization-server")
async def oauth_discovery():
    base_url = os.getenv("BASE_URL", "https://web-production-f408.up.railway.app")
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"]
    }

# Client Registration
@app.post("/oauth/register")
async def register_client(request: Request):
    try:
        body = await request.json()
        logger.info(f"Client registration: {body}")
        
        client_id = str(uuid.uuid4())
        client_secret = secrets.token_urlsafe(32)
        
        oauth_clients[client_id] = {
            "client_secret": client_secret,
            "redirect_uris": body.get("redirect_uris", []),
            "client_name": body.get("client_name", "MCP Client"),
            "created_at": datetime.utcnow()
        }
        
        logger.info(f"Registered client: {client_id}")
        
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_id_issued_at": int(datetime.utcnow().timestamp()),
            "client_secret_expires_at": 0
        }
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Authorization
@app.get("/oauth/authorize")
async def authorize(
    client_id: str,
    redirect_uri: str,
    response_type: str = "code",
    scope: str = "mcp",
    state: str = None
):
    logger.info(f"Auth request - client: {client_id}, redirect: {redirect_uri}")
    
    # Use default client if not found
    if client_id not in oauth_clients:
        if client_id == DEFAULT_CLIENT_ID or len(oauth_clients) == 1:
            client_id = DEFAULT_CLIENT_ID
        else:
            logger.error(f"Unknown client: {client_id}")
            raise HTTPException(status_code=400, detail="Invalid client_id")
    
    client_data = oauth_clients[client_id]
    
    # Be more lenient with redirect URIs
    allowed_uris = client_data["redirect_uris"]
    if redirect_uri not in allowed_uris and not any(uri in redirect_uri for uri in allowed_uris):
        logger.warning(f"Redirect URI not in whitelist, but allowing: {redirect_uri}")
    
    # Generate auth code
    auth_code = secrets.token_urlsafe(32)
    oauth_codes[auth_code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "expires_at": datetime.utcnow() + timedelta(minutes=10)
    }
    
    # Auto-approve and redirect
    callback_url = f"{redirect_uri}?code={auth_code}"
    if state:
        callback_url += f"&state={state}"
    
    logger.info(f"Redirecting to: {callback_url}")
    return RedirectResponse(callback_url)

# Token Exchange
@app.post("/oauth/token")
async def token_endpoint(
    grant_type: str = Form(),
    code: str = Form(),
    redirect_uri: str = Form(),
    client_id: str = Form(),
    client_secret: str = Form()
):
    logger.info(f"Token request - client: {client_id}, code: {code}")
    
    if grant_type != "authorization_code":
        raise HTTPException(status_code=400, detail="Unsupported grant type")
    
    if code not in oauth_codes:
        logger.error(f"Invalid code: {code}")
        raise HTTPException(status_code=400, detail="Invalid authorization code")
    
    code_data = oauth_codes[code]
    
    if code_data["expires_at"] < datetime.utcnow():
        del oauth_codes[code]
        raise HTTPException(status_code=400, detail="Authorization code expired")
    
    # Use default client if needed
    if client_id not in oauth_clients:
        if client_id == DEFAULT_CLIENT_ID:
            client_id = DEFAULT_CLIENT_ID
        else:
            raise HTTPException(status_code=400, detail="Invalid client")
    
    # Validate client
    if oauth_clients[client_id]["client_secret"] != client_secret:
        logger.error(f"Invalid secret for client: {client_id}")
        raise HTTPException(status_code=401, detail="Invalid client credentials")
    
    # Generate access token
    access_token = secrets.token_urlsafe(32)
    access_tokens[access_token] = {
        "client_id": client_id,
        "scope": code_data["scope"],
        "expires_at": datetime.utcnow() + timedelta(hours=24)
    }
    
    del oauth_codes[code]
    
    logger.info(f"Issued token for client: {client_id}")
    
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 86400,
        "scope": code_data["scope"]
    }

# Helper to validate auth
async def get_auth_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if token in access_tokens:
            token_data = access_tokens[token]
            if token_data["expires_at"] > datetime.utcnow():
                return token
            else:
                del access_tokens[token]
    return None

# Main SSE endpoint
@app.post("/sse")
async def mcp_sse(request: Request):
    auth_token = await get_auth_token(request)
    
    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            body = await request.body()
            
            if not body:
                init_response = await mcp_server.handle_request({
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "id": 1
                }, auth_token)
                yield f"data: {json.dumps(init_response)}\n\n"
                return
            
            request_data = json.loads(body)
            response = await mcp_server.handle_request(request_data, auth_token)
            yield f"data: {json.dumps(response)}\n\n"
            
        except Exception as e:
            logger.error(f"SSE error: {e}")
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": str(e)}
            }
            yield f"data: {json.dumps(error_response)}\n\n"
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization"
        }
    )

@app.get("/sse")
async def mcp_sse_get(request: Request):
    auth_token = await get_auth_token(request)
    
    async def event_stream() -> AsyncGenerator[str, None]:
        init_response = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1
        }, auth_token)
        yield f"data: {json.dumps(init_response)}\n\n"
        
        while True:
            await asyncio.sleep(30)
            yield f": keepalive\n\n"
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization"
        }
    )

@app.options("/sse")
@app.options("/oauth/authorize")
@app.options("/oauth/token")
@app.options("/oauth/register")
async def options():
    return JSONResponse(
        {"message": "OK"},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization"
        }
    )

@app.get("/")
async def root():
    return {
        "name": "X Poster MCP",
        "version": "1.0.0", 
        "status": "running",
        "registered_clients": len(oauth_clients),
        "active_tokens": len(access_tokens)
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}

# Debug endpoint
@app.get("/debug")
async def debug():
    return {
        "clients": list(oauth_clients.keys()),
        "codes": len(oauth_codes),
        "tokens": len(access_tokens)
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)