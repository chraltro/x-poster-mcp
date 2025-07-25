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

        logger.info(f"Handling request: {method} with ID: {request_id}")

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

# Simplified OAuth Discovery Endpoint
@app.get("/.well-known/oauth-authorization-server")
async def oauth_discovery():
    base_url = os.getenv("BASE_URL", "https://web-production-f408.up.railway.app")
    logger.info(f"OAuth discovery requested, base_url: {base_url}")
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
    logger.info(f"Client registration request: {body}")
    
    client_id = str(uuid.uuid4())
    client_secret = secrets.token_urlsafe(32)
    
    oauth_clients[client_id] = {
        "client_secret": client_secret,
        "redirect_uris": body.get("redirect_uris", []),
        "client_name": body.get("client_name", "MCP Client"),
        "created_at": datetime.utcnow()
    }
    
    logger.info(f"Registered client: {client_id}")
    logger.info(f"Current oauth_clients: {list(oauth_clients.keys())}")
    
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
    logger.info(f"Authorization request - client_id: {client_id}, redirect_uri: {redirect_uri}")
    logger.info(f"Available clients: {list(oauth_clients.keys())}")
    
    if client_id not in oauth_clients:
        logger.error(f"Invalid client_id: {client_id}")
        raise HTTPException(status_code=400, detail="Invalid client_id")
    
    client_data = oauth_clients[client_id]
    logger.info(f"Client data: {client_data}")
    
    if redirect_uri not in client_data["redirect_uris"]:
        logger.error(f"Invalid redirect_uri: {redirect_uri}, allowed: {client_data['redirect_uris']}")
        raise HTTPException(status_code=400, detail="Invalid redirect_uri")
    
    # Generate authorization code
    auth_code = secrets.token_urlsafe(32)
    oauth_codes[auth_code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "expires_at": datetime.utcnow() + timedelta(minutes=10)
    }
    
    logger.info(f"Generated auth code: {auth_code}")
    
    # In a real app, show user consent page
    # For now, auto-approve and redirect
    callback_url = f"{redirect_uri}?code={auth_code}"
    if state:
        callback_url += f"&state={state}"
    
    logger.info(f"Redirecting to: {callback_url}")
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
    logger.info(f"Token request - grant_type: {grant_type}, code: {code}, client_id: {client_id}")
    
    if grant_type != "authorization_code":
        raise HTTPException(status_code=400, detail="Unsupported grant type")
    
    if code not in oauth_codes:
        logger.error(f"Invalid authorization code: {code}")
        raise HTTPException(status_code=400, detail="Invalid authorization code")
    
    code_data = oauth_codes[code]
    
    if code_data["expires_at"] < datetime.utcnow():
        del oauth_codes[code]
        logger.error("Authorization code expired")
        raise HTTPException(status_code=400, detail="Authorization code expired")
    
    if code_data["client_id"] != client_id:
        logger.error(f"Client mismatch - code client: {code_data['client_id']}, request client: {client_id}")
        raise HTTPException(status_code=400, detail="Invalid client")
    
    if client_id not in oauth_clients or oauth_clients[client_id]["client_secret"] != client_secret:
        logger.error(f"Invalid client credentials for client: {client_id}")
        raise HTTPException(status_code=401, detail="Invalid client credentials")
    
    # Generate access token
    access_token = secrets.token_urlsafe(32)
    access_tokens[access_token] = {
        "client_id": client_id,
        "scope": code_data["scope"],
        "expires_at": datetime.utcnow() + timedelta(hours=24)
    }
    
    logger.info(f"Generated access token for client: {client_id}")
    
    # Clean up used code
    del oauth_codes[code]
    
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": 86400,  # 24 hours
        "scope": code_data["scope"]
    }

# Helper function to extract and validate auth token
async def get_auth_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        # Validate token
        if token in access_tokens:
            token_data = access_tokens[token]
            if token_data["expires_at"] > datetime.utcnow():
                return token
            else:
                # Token expired
                del access_tokens[token]
        logger.warning(f"Invalid or expired token used")
    return None

# Main MCP SSE Endpoint - Fixed to handle streaming properly
@app.post("/sse")
async def mcp_endpoint(request: Request):
    auth_token = await get_auth_token(request)
    logger.info(f"SSE POST request - auth_token present: {auth_token is not None}")
    
    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            # Read the request body
            body = await request.body()
            logger.info(f"Request body length: {len(body) if body else 0}")
            
            # Handle empty body (initial connection)
            if not body:
                # Send initialization message
                init_response = await mcp_server.handle_request({
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "id": 1
                }, auth_token)
                logger.info(f"Sending init response: {init_response}")
                yield f"data: {json.dumps(init_response)}\n\n"
                return
            
            # Parse the request
            request_data = json.loads(body)
            logger.info(f"Parsed request: {request_data}")
            
            # Handle the request
            response = await mcp_server.handle_request(request_data, auth_token)
            logger.info(f"Sending response: {response}")
            yield f"data: {json.dumps(response)}\n\n"
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {str(e)}"}
            }
            yield f"data: {json.dumps(error_response)}\n\n"
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": f"Internal error: {str(e)}"}
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
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "X-Accel-Buffering": "no"  # Disable Nginx buffering
        }
    )

# Add GET endpoint for SSE (some clients might use GET)
@app.get("/sse")
async def mcp_endpoint_get(request: Request):
    auth_token = await get_auth_token(request)
    logger.info(f"SSE GET request - auth_token present: {auth_token is not None}")
    
    async def event_stream() -> AsyncGenerator[str, None]:
        # Send initialization message
        init_response = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1
        }, auth_token)
        logger.info(f"Sending GET init response: {init_response}")
        yield f"data: {json.dumps(init_response)}\n\n"
        
        # Keep connection alive
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
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "X-Accel-Buffering": "no"
        }
    )

@app.options("/sse")
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
    return {"name": "X Poster MCP", "version": "1.0.0", "status": "running"}

# Health check endpoint
@app.get("/health")
async def health():
    return {"status": "healthy"}

# Debug endpoint to check stored clients
@app.get("/debug/clients")
async def debug_clients():
    return {
        "clients": {k: {**v, "client_secret": "***"} for k, v in oauth_clients.items()},
        "codes": list(oauth_codes.keys()),
        "tokens": list(access_tokens.keys())
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)