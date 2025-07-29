import asyncio
import json
import logging
import sys
from typing import Any, Dict

import tweepy
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
from pydantic import AnyUrl
import os
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

# Initialize the MCP server
server = Server("x-poster")

def get_twitter_client():
    """Initialize Twitter client with credentials from environment variables"""
    return tweepy.Client(
        bearer_token=os.getenv("TWITTER_BEARER_TOKEN"),
        consumer_key=os.getenv("TWITTER_CONSUMER_KEY"),
        consumer_secret=os.getenv("TWITTER_CONSUMER_SECRET"),
        access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
        access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
    )

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools for posting tweets"""
    return [
        Tool(
            name="send_tweet",
            description="Send a tweet to X/Twitter",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The tweet text to post (max 280 characters)"
                    }
                },
                "required": ["text"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> list[TextContent]:
    """Handle tool calls"""
    if name == "send_tweet":
        try:
            tweet_text = arguments.get("text", "").strip()
            
            if not tweet_text:
                return [TextContent(type="text", text="❌ Error: Tweet text cannot be empty")]
            
            if len(tweet_text) > 280:
                return [TextContent(type="text", text="❌ Error: Tweet text exceeds 280 character limit")]
            
            # Post the tweet
            client = get_twitter_client()
            response = client.create_tweet(text=tweet_text)
            
            if response.data:
                tweet_id = response.data['id']
                tweet_url = f"https://twitter.com/user/status/{tweet_id}"
                return [TextContent(
                    type="text", 
                    text=f"✅ Tweet posted successfully!\nTweet ID: {tweet_id}\nURL: {tweet_url}"
                )]
            else:
                return [TextContent(type="text", text="❌ Error: Failed to post tweet - no response data")]
                
        except Exception as e:
            logger.error(f"Failed to post tweet: {e}")
            return [TextContent(type="text", text=f"❌ Error posting tweet: {str(e)}")]
    
    else:
        return [TextContent(type="text", text=f"❌ Unknown tool: {name}")]

async def main():
    """Main function to run the MCP server"""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())