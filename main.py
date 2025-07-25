from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import json
import tweepy
import os
from typing import AsyncGenerator
from dotenv import load_dotenv
import asyncio
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI()

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

    async def handle_request(self, request_data: dict) -> dict:
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
            
            logger.info(f"Attempting to send tweet: {tweet_text}")
            
            # Post the tweet
            response = client.create_tweet(text=tweet_text)
            
            tweet_id = response.data['id']
            tweet_url = f"https://twitter.com/user/status/{tweet_id}"
            
            logger.info(f"Tweet posted successfully: {tweet_url}")
            
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
            logger.error(f"Failed to post tweet: {str(e)}")
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

# Simple API key validation (using a fixed key for simplicity)
API_KEY = os.getenv("MCP_API_KEY", "12345")

def validate_api_key(request: Request) -> bool:
    """Simple API key validation"""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return False
    
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return token == API_KEY
    
    return False

# Main MCP Endpoint - No OAuth, just simple API key
@app.post("/mcp")
async def mcp_endpoint(request: Request):
    # Validate API key
    if not validate_api_key(request):
        logger.warning("Unauthorized request - invalid API key")
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    try:
        body = await request.json()
        logger.info(f"Received MCP request: {body}")
        
        response = await mcp_server.handle_request(body)
        logger.info(f"Sending MCP response: {response}")
        
        return response
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": f"Parse error: {str(e)}"}
        }
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32603, "message": f"Internal error: {str(e)}"}
        }

# SSE Endpoint for MCP protocol
@app.post("/sse")
async def mcp_sse_endpoint(request: Request):
    # Validate API key
    if not validate_api_key(request):
        logger.warning("Unauthorized SSE request - invalid API key")
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            # Read the request body
            body = await request.body()
            logger.info(f"SSE Request body length: {len(body) if body else 0}")
            
            # Handle empty body (initial connection)
            if not body:
                # Send initialization message
                init_response = await mcp_server.handle_request({
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "id": 1
                })
                logger.info(f"Sending SSE init response: {init_response}")
                yield f"data: {json.dumps(init_response)}\n\n"
                return
            
            # Parse the request
            request_data = json.loads(body)
            logger.info(f"Parsed SSE request: {request_data}")
            
            # Handle the request
            response = await mcp_server.handle_request(request_data)
            logger.info(f"Sending SSE response: {response}")
            yield f"data: {json.dumps(response)}\n\n"
            
        except json.JSONDecodeError as e:
            logger.error(f"SSE JSON decode error: {e}")
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {str(e)}"}
            }
            yield f"data: {json.dumps(error_response)}\n\n"
        except Exception as e:
            logger.error(f"SSE Unexpected error: {e}")
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
            "X-Accel-Buffering": "no"
        }
    )

# GET endpoint for SSE
@app.get("/sse")
async def mcp_sse_get(request: Request):
    # Validate API key
    if not validate_api_key(request):
        logger.warning("Unauthorized SSE GET request - invalid API key")
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    async def event_stream() -> AsyncGenerator[str, None]:
        # Send initialization message
        init_response = await mcp_server.handle_request({
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1
        })
        logger.info(f"Sending SSE GET init response: {init_response}")
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
@app.options("/mcp")
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
        "endpoints": {
            "mcp": "/mcp",
            "sse": "/sse"
        },
        "auth": "Bearer token required"
    }

# Health check endpoint
@app.get("/health")
async def health():
    return {"status": "healthy"}

# Test Twitter connection
@app.get("/test-twitter")
async def test_twitter(request: Request):
    if not validate_api_key(request):
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    try:
        client = get_twitter_client()
        me = client.get_me()
        return {"status": "connected", "user": me.data.username if me.data else "unknown"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)