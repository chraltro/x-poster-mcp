from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, RedirectResponse, HTMLResponse
import json
import tweepy
import os
from typing import AsyncGenerator
from dotenv import load_dotenv
import uuid

load_dotenv()

app = FastAPI()

# Store "authenticated" sessions (in real app, use proper storage)
authenticated_sessions = set()

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

# OAuth-style endpoints
@app.get("/")
async def root():
    return {"name": "X Poster MCP", "version": "1.0.0", "status": "running"}

@app.get("/auth")
async def auth_page():
    # Create a session token
    session_token = str(uuid.uuid4())
    authenticated_sessions.add(session_token)
    
    # Return a simple auth page
    html = f"""
    <html>
    <body>
        <h1>X Poster Authentication</h1>
        <p>Click to authorize access:</p>
        <a href="/callback?token={session_token}">Authorize</a>
    </body>
    </html>
    """
    return HTMLResponse(html)

@app.get("/callback")
async def auth_callback(token: str = None):
    if token and token in authenticated_sessions:
        # Redirect back to Claude with success
        return RedirectResponse("https://claude.ai?auth=success")
    else:
        return {"error": "Invalid token"}

# Main MCP endpoint
@app.post("/sse")
async def mcp_endpoint(request: Request):
    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            body = await request.json()
            response = await mcp_server.handle_request(body)
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
            "Access-Control-Allow-Headers": "Content-Type"
        }
    )

@app.options("/sse")
async def options():
    return {"message": "OK"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)