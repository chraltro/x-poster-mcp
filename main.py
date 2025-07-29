import os
import tweepy
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Setup
load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("x-poster")

def get_twitter_client():
    """Initialize Twitter client with credentials from environment variables"""
    return tweepy.Client(
        bearer_token=os.getenv("TWITTER_BEARER_TOKEN"),
        consumer_key=os.getenv("TWITTER_CONSUMER_KEY"),
        consumer_secret=os.getenv("TWITTER_CONSUMER_SECRET"),
        access_token=os.getenv("TWITTER_ACCESS_TOKEN"),
        access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
    )

@mcp.tool()
async def send_tweet(text: str) -> str:
    """Send a tweet to X/Twitter.

    Args:
        text: The tweet text to post (max 280 characters)
    """
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

if __name__ == "__main__":
    # For web deployment, use streamable http transport
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable_http", host="0.0.0.0", port=port)