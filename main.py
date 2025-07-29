import json
import os
import tweepy
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# Setup
load_dotenv()

app = FastAPI(title="X Poster MCP Server")

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

@app.post("/messages")
async def handle_messages(request: Request):
    """Handle MCP messages via StreamableHttp"""
    try:
        body = await request.json()
        
        method = body.get("method")
        request_id = body.get("id")
        params = body.get("params", {})
        
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
        
        elif method == "notifications/initialized":
            # Just acknowledge
            return JSONResponse(content=None, status_code=200)
        
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

@app.get("/")
async def root():
    return {"status": "healthy", "service": "X Poster MCP Server"}