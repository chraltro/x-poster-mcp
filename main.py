import asyncio
import json
import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, AsyncGenerator, Optional

import tweepy
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import StreamingResponse, RedirectResponse
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

# Initialize FastAPI app
app = FastAPI(title="X Poster MCP Server", version="1.0.0")

# OAuth storage (in production, use a proper database)
oauth_clients = {}
oauth_codes = {}
access_tokens = {}

def get_twitter_client():
    """Initialize Twitter client with credentials from environment variables"""
    return tweepy.Client(
        bearer_token=os.getenv("TWITTER_BEARER_TOKEN"),
        consumer_key=os.getenv("TWITTER_CONSUMER_KEY"),
        consumer_secret=os.getenv("TWITTER_CONSUMER_SECRET"),
        access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
        access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
    )

async def handle_mcp_request(request_data: dict) -> dict:
    """Handle MCP protocol requests"""
    method = request_data.get("method")
    request_id = request_data.get("id")
    
    logger.info(f"Handling MCP request: {method}")
    
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
                    "name": "x-poster",
                    "version": "1.0.0"
                }
            }
        }
    
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
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
            }
        }
    
    elif method == "tools/call":
        params = request_data.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        if tool_name == "send_tweet":
            try:
                tweet_text = arguments.get("text", "").strip()
                
                if not tweet_text:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "❌ Error: Tweet text cannot be empty"
                                }
                            ]
                        }
                    }
                
                if len(tweet_text) > 280:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "❌ Error: Tweet text exceeds 280 character limit"
                                }
                            ]
                        }
                    }
                
                # Post the tweet
                client = get_twitter_client()
                response = client.create_tweet(text=tweet_text)
                
                if response.data:
                    tweet_id = response.data['id']
                    tweet_url = f"https://twitter.com/user/status/{tweet_id}"
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"✅ Tweet posted successfully!\nTweet ID: {tweet_id}\nURL: {tweet_url}"
                                }
                            ]
                        }
                    }
                else:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "❌ Error: Failed to post tweet - no response data"
                                }
                            ]
                        }
                    }
                    
            except Exception as e:
                logger.error(f"Failed to post tweet: {e}")
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"❌ Error posting tweet: {str(e)}"
                            }
                        ]
                    }
                }
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Unknown tool: {tool_name}"
                }
            }
    
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        }

# OAuth 2.0 Endpoints
@app.get("/.well-known/oauth-authorization-server")
async def oauth_discovery():
    """OAuth 2.0 Authorization Server Metadata"""
    base_url = os.getenv("BASE_URL", "https://web-production-f408.up.railway.app")
    return {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth/authorize",
        "token_endpoint": f"{base_url}/oauth/token",
        "registration_endpoint": f"{base_url}/oauth/register"
    }

@app.post("/oauth/register")
async def register_client(request: Request):
    """OAuth 2.0 Dynamic Client Registration"""
    try:
        body = await request.json()
        client_name = body.get("client_name", "Claude MCP Client")
        redirect_uris = body.get("redirect_uris", ["https://claude.ai/api/mcp/auth_callback"])
        
        client_id = secrets.token_urlsafe(32)
        client_secret = secrets.token_urlsafe(32)
        
        oauth_clients[client_id] = {
            "client_secret": client_secret,
            "client_name": client_name,
            "redirect_uris": redirect_uris,
            "created_at": datetime.utcnow()
        }
        
        logger.info(f"Registered OAuth client: {client_id}")
        
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_id_issued_at": int(datetime.utcnow().timestamp()),
            "client_secret_expires_at": 0
        }
    except Exception as e:
        logger.error(f"Client registration error: {e}")
        raise HTTPException(status_code=400, detail="Invalid request")

@app.get("/oauth/authorize")
async def authorize(
    client_id: str, 
    redirect_uri: str, 
    response_type: str = "code",
    state: Optional[str] = None,
    scope: Optional[str] = None
):
    """OAuth 2.0 Authorization Endpoint"""
    logger.info(f"Authorization request: client_id={client_id}, redirect_uri={redirect_uri}")
    
    # Validate client
    if client_id not in oauth_clients:
        raise HTTPException(status_code=400, detail="Invalid client_id")
    
    client = oauth_clients[client_id]
    if redirect_uri not in client["redirect_uris"]:
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")
    
    # Generate authorization code
    auth_code = secrets.token_urlsafe(32)
    oauth_codes[auth_code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "expires_at": datetime.utcnow() + timedelta(minutes=10)
    }
    
    # Redirect back to Claude with authorization code
    callback_url = f"{redirect_uri}?code={auth_code}"
    if state:
        callback_url += f"&state={state}"
    
    logger.info(f"Redirecting to: {callback_url}")
    return RedirectResponse(callback_url)

@app.post("/oauth/token")
async def token_endpoint(
    grant_type: str = Form(),
    code: str = Form(),
    client_id: str = Form(),
    client_secret: str = Form(),
    redirect_uri: str = Form(None)
):
    """OAuth 2.0 Token Endpoint"""
    logger.info(f"Token request: client_id={client_id}, grant_type={grant_type}")
    
    # Validate client credentials
    if client_id not in oauth_clients:
        raise HTTPException(status_code=401, detail="Invalid client")
    
    client = oauth_clients[client_id]
    if client["client_secret"] != client_secret:
        raise HTTPException(status_code=401, detail="Invalid client credentials")
    
    # Validate authorization code
    if code not in oauth_codes:
        raise HTTPException(status_code=400, detail="Invalid authorization code")
    
    code_data = oauth_codes.pop(code)
    if code_data["client_id"] != client_id:
        raise HTTPException(status_code=400, detail="Authorization code mismatch")
    
    if code_data["expires_at"] < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Authorization code expired")
    
    # Generate access token
    access_token = secrets.token_urlsafe(32)
    access_tokens[access_token] = {
        "client_id": client_id,
        "expires_at": datetime.utcnow() + timedelta(hours=24)
    }
    
    logger.info(f"Issued access token for client: {client_id}")
    
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 86400
    }

async def validate_auth_token(request: Request) -> bool:
    """Validate Bearer token from Authorization header"""
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    
    token = auth_header[7:]  # Remove "Bearer " prefix
    
    if token not in access_tokens:
        return False
    
    token_data = access_tokens[token]
    if token_data["expires_at"] < datetime.utcnow():
        # Clean up expired token
        del access_tokens[token]
        return False
    
    return True

@app.post("/sse")
async def handle_sse(request: Request):
    """Handle SSE requests from Claude"""
    # Validate OAuth token
    if not await validate_auth_token(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            body = await request.body()
            if body:
                request_data = json.loads(body.decode())
                response = await handle_mcp_request(request_data)
                yield f"data: {json.dumps(response)}\n\n"
            else:
                # Handle empty body with initialization
                init_response = await handle_mcp_request({
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "id": 1
                })
                yield f"data: {json.dumps(init_response)}\n\n"
                
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
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Methods": "*"
        }
    )

@app.get("/")
async def root():
    """Health check endpoint"""
    return {"status": "healthy", "service": "X Poster MCP Server"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)