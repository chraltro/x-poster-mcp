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

load_dotenv()

app = FastAPI()

# OAuth state storage (in production, use proper database)
oauth_clients = {}
oauth_codes = {}
access_tokens = {}

# Twitter client setup
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
        
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "x-poster-mcp",
                        "version": "1.0.0"
                    }
                }
            }
        
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": self.tools}
            }
        
        elif method == "tools/call":
            # Check authentication for tool calls
            if not auth_token or auth_token not in access_tokens:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32600, "message": "Authentication required"}
                }
            
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
            tweet_text = args["text"]
            
            # Post the tweet
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

# OAuth Discovery Endpoint
@app.get("/.well-known/oauth-authorization-server")
async def oauth_discovery():
    base_url = "https://web-production-f408.up.railway.app"
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"]
    }

# Dynamic Client Registration
@app.post("/oauth/register")
async def register_client(request: Request):
    body = await request.json()
    
    client_id = str(uuid.uuid4())
    client_secret = secrets.token_urlsafe(32)
    
    oauth_clients[client_id] = {
        "client_secret": client_secret,
        "redirect_uris": body.get("redirect_uris", []),
        "client_name": body.get("client_name", "MCP Client"),
        "created_at": datetime.utcnow()
    }
    
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_id_issued_at": int(datetime.utcnow().timestamp()),
        "client_secret_expires_at": 0  # Never expires
    }

# Authorization Endpoint
@app.get("/oauth/authorize")
async def authorize(
    client_id: str,
    redirect_uri: str,
    response_type: str = "code",
    scope: str = "mcp",
    state: str = None
):
    if client_id not in oauth_clients:
        raise HTTPException(status_code=400, detail="Invalid client_id")
    
    if redirect_uri not in oauth_clients[client_id]["redirect_uris"]:
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")
    
    # Generate authorization code
    auth_code = secrets.token_urlsafe(32)
    oauth_codes[auth_code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "expires_at": datetime.utcnow() + timedelta(minutes=10)
    }
    
    # In a real app, show user consent page
    # For now, auto-approve and redirect
    callback_url = f"{redirect_uri}?code={auth_code}"
    if state:
        callback_url += f"&state={state}"
    
    return RedirectResponse(callback_url)

# Token Endpoint
@app.post("/oauth/token")
async def token_endpoint(
    grant_type: str = Form(),
    code: str = Form(),
    redirect_uri: str = Form(),
    client_id: str = Form(),
    client_secret: str = Form()
):
    if grant_type != "authorization_code":
        raise HTTPException(status_code=400, detail="Unsupported grant type")
    
    if code not in oauth_codes:
        raise HTTPException(status_code=400, detail="Invalid authorization code")
    
    code_data = oauth_codes[code]
    
    if code_data["expires_at"] < datetime.utcnow():
        del oauth_codes[code]
        raise HTTPException(status_code=400, detail="Authorization code expired")
    
    if code_data["client_id"] != client_id:
        raise HTTPException(status_code=400, detail="Invalid client")
    
    if client_id not in oauth_clients or oauth_clients[client_id]["client_secret"] != client_secret:
        raise HTTPException(status_code=401, detail="Invalid client credentials")
    
    # Generate access token
    access_token = secrets.token_urlsafe(32)
    access_tokens[access_token] = {
        "client_id": client_id,
        "scope": code_data["scope"],
        "expires_at": datetime.utcnow() + timedelta(hours=24)
    }
    
    # Clean up used code
    del oauth_codes[code]
    
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 86400,  # 24 hours
        "scope": code_data["scope"]
    }

# Helper function to extract auth token
async def get_auth_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None

# Main MCP SSE Endpoint
@app.post("/sse")
async def mcp_endpoint(request: Request):
    auth_token = await get_auth_token(request)
    
    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            body = await request.json()
            response = await mcp_server.handle_request(body, auth_token)
            yield f"data: {json.dumps(response)}\n\n"
            
        except Exception as e:
            error_response = {
                "jsonrpc": "2.0",
                "id": body.get("id") if 'body' in locals() else None,
                "error": {"code": -32700, "message": f"Parse error: {str(e)}"}
            }
            yield f"data: {json.dumps(error_response)}\n\n"
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization"
        }
    )

@app.options("/sse")
async def options():
    return JSONResponse(
        {"message": "OK"},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization"
        }
    )

@app.get("/")
async def root():
    return {"name": "X Poster MCP", "version": "1.0.0", "status": "running"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)