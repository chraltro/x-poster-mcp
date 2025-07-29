import asyncio
import json
import logging
import os
from typing import Any, Dict, AsyncGenerator

import tweepy
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

# Initialize FastAPI app
app = FastAPI(title="X Poster MCP Server", version="1.0.0")

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

@app.post("/sse")
async def handle_sse(request: Request):
    """Handle SSE requests from Claude"""
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