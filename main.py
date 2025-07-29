import json
import os
import tweepy
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Setup
load_dotenv()

app = FastAPI(title="X Poster MCP Server")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_twitter_client():
    """Initialize Twitter client with credentials from environment variables"""
    return tweepy.Client(
        bearer_token=os.getenv("TWITTER_BEARER_TOKEN"),
        consumer_key=os.getenv("TWITTER_CONSUMER_KEY"),
        consumer_secret=os.getenv("TWITTER_CONSUMER_SECRET"),
        access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
        access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
    )

async def send_tweet_tool(text: str) -> str:
    """Send a tweet to X/Twitter."""
    try:
        tweet_text = text.strip()
        
        if not tweet_text:
            return "❌ Error: Tweet text cannot be empty"
        
        if len(tweet_text) > 280:
            return "❌ Error: Tweet text exceeds 280 character limit"
        
        # Post the tweet
        client = get_twitter_client()
        response = client.create_tweet(text=tweet_text)
        
        if response.data:
            tweet_id = response.data['id']
            tweet_url = f"https://twitter.com/user/status/{tweet_id}"
            return f"✅ Tweet posted successfully!\nTweet ID: {tweet_id}\nURL: {tweet_url}"
        else:
            return "❌ Error: Failed to post tweet - no response data"
            
    except Exception as e:
        return f"❌ Error posting tweet: {str(e)}"

@app.api_route("/messages", methods=["GET", "POST", "HEAD"])
async def handle_messages(request: Request):
    """Handle MCP messages via StreamableHttp - support both GET and POST"""
    try:
        if request.method == "POST":
            body = await request.json()
        else:
            # For GET requests, return initialization
            body = {"method": "initialize", "id": 1}
        
        method = body.get("method")
        request_id = body.get("id")
        params = body.get("params", {})
        
        if method == "initialize":
            print(f"Initialize request: {body}")
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {
                        "tools": {
                            "listChanged": False
                        }
                    },
                    "serverInfo": {
                        "name": "x-poster",
                        "version": "1.0.0"
                    },
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
            print(f"Initialize response: {response}")
            return response
        
        elif method == "notifications/initialized":
            return JSONResponse(content=None, status_code=200)
        
        elif method == "tools/list":
            print(f"Tools/list request: {body}")
            response = {
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
            print(f"Tools/list response: {response}")
            return response
        
        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            
            if tool_name == "send_tweet":
                result = await send_tweet_tool(arguments.get("text", ""))
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": result
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
            
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32603,
                "message": f"Internal error: {str(e)}"
            }
        }

# OAuth endpoints that Claude is looking for
@app.get("/.well-known/oauth-authorization-server")
async def oauth_discovery():
    return {
        "issuer": "https://web-production-f408.up.railway.app",
        "authorization_endpoint": "https://web-production-f408.up.railway.app/oauth/authorize",
        "token_endpoint": "https://web-production-f408.up.railway.app/oauth/token"
    }

@app.get("/oauth/authorize")
async def authorize():
    return {"access_token": "dummy_token", "token_type": "Bearer"}

@app.post("/oauth/token")
async def token():
    return {"access_token": "dummy_token", "token_type": "Bearer", "expires_in": 3600}

@app.post("/register")
async def register():
    return {"client_id": "dummy_client", "client_secret": "dummy_secret"}

@app.get("/")
async def root():
    return {"status": "healthy", "service": "X Poster MCP Server"}