#!/usr/bin/env python3

import os
import time
import feedparser
import openai
import discord
from discord.ext import commands
from dotenv import load_dotenv

# ---------------------
# Configuration Section
# ---------------------

# option for using keys inside a .env file
load_dotenv()  # this reads .env and loads into environment variables
load_dotenv(dotenv_path=r"E:\smart_task_management\weekend-project\tokens.env")

# >>>>>>>>> SET YOUR DISCORD BOT TOKEN HERE (each user must create their own bot) <<<<<<<<
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# >>>>>>>>> SET YOUR OPENROUTER API KEY HERE <<<<<<<<
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# OpenRouter base URL (so the openai library calls OpenRouter)
openai.api_base = "https://openrouter.ai/api/v1"
openai.api_key = OPENROUTER_API_KEY

# Example GPT model (replace with "openai/gpt-4o-mini" if available)
OPENAI_MODEL = "anthropic/claude-3.5-haiku-20241022"

# Decide how often (in minutes) to automatically check the feeds
CHECK_INTERVAL_MINUTES = 15

# File paths
USER_FILE = "user.txt"
USER_PROFILE_FILE = "user_profile.txt"
RSS_FEED_SOURCES_FILE = "RSS_feed_sources.txt"
NOTES_DIR = "Notes"

# A base system prompt that sets the context for the AI
BASE_SYSTEM_PROMPT = """You are an RSS summarizer AI. You read the user's interests from user.txt
and user_profile.txt. You then summarize each RSS entry. If relevant to the user, you provide a short summary
over Discord DM. If the user reacts with a thumbs up, give a more detailed summary. If thumbs down, do not provide more details.
If raised hands, gather feedback from the user on how to adjust user_profile.txt.
"""

# Because we’re dealing with direct messages, we need appropriate intents
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
intents.dm_messages = True  # Important for DMs

bot = commands.Bot(command_prefix="!", intents=intents)

# We’ll store info about each message so we know which RSS entry it references
message_article_map = {}  # message.id -> dict with metadata (feed_title, entry, summary, etc.)

# -----------
# Main Logic
# -----------

def load_text_file(file_path):
    """Load text from a file or return empty string if nonexistent."""
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def save_text_file(file_path, content):
    """Save text to a file (overwrites existing)."""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

def append_text_file(file_path, content):
    """Append text to a file."""
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(content + "\n")

def generate_filename_from_title(title):
    """Generate a safe filename from an RSS entry title."""
    safe_title = "".join(c for c in title if c.isalnum() or c in "-_ ()[]")
    return f"{safe_title[:50]}.txt"  # limit length

async def get_summary_with_ai(title, content):
    """Use OpenRouter (via openai library) to get a summary for the RSS entry."""
    # Prepare the messages for the model
    messages = [
        {"role": "system", "content": BASE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Summarize the following blog post:\nTitle: {title}\nContent: {content}\nMake it concise."
        },
    ]

    response = await openai.ChatCompletion.acreate(
        model=OPENAI_MODEL,
        messages=messages,
        max_tokens=200,
        temperature=0.7,
    )

    summary = response.choices[0].message["content"]
    return summary.strip()

def check_relevance(title, summary, user_data, user_profile):
    """
    Simple approach: if the user_data or user_profile mention certain keywords,
    we check for them in the summary/title (case-insensitive).
    """
    relevant_keywords = set()
    for keyword in user_data.split():
        relevant_keywords.add(keyword.lower())
    for keyword in user_profile.split():
        relevant_keywords.add(keyword.lower())

    text = f"{title} {summary}".lower()
    for kw in relevant_keywords:
        if kw in text:
            return True
    return False

async def process_rss_feed(feed_url, user):
    """Parse the RSS feed, summarize each entry, save notes, DM user if relevant."""
    feed = feedparser.parse(feed_url)
    if feed.bozo:  # bozo=1 means feedparser had trouble parsing
        print(f"[ERROR] Could not parse RSS feed: {feed_url}")
        return

    user_data = load_text_file(USER_FILE)
    user_profile = load_text_file(USER_PROFILE_FILE)

    # Read ALL entries from the feed
    entries = feed.entries

    for idx, entry in enumerate(entries):
        title = entry.title if hasattr(entry, 'title') else "No Title"
        content = entry.summary if hasattr(entry, 'summary') else "No Summary"

        # Summarize the article using the AI
        summary = await get_summary_with_ai(title, content)

        # Save to notes folder
        if not os.path.exists(NOTES_DIR):
            os.makedirs(NOTES_DIR)
        filename = generate_filename_from_title(title)
        note_path = os.path.join(NOTES_DIR, filename)
        note_content = (
            f"Title: {title}\nURL: {entry.link}\n\n"
            f"AI Summary:\n{summary}"
        )
        save_text_file(note_path, note_content)

        # Check if relevant
        if check_relevance(title, summary, user_data, user_profile):
            short_summary_message = (
                f":thumbsup: :thumbsdown: :raised_hands:\n"
                f"**Title:** {title}\n"
                f"**Summary (truncated):** {summary[:200]}..."
                f"\n(Link: {entry.link})"
            )
            # DM the user
            sent_msg = await user.send(short_summary_message)

            # Store reference data for reaction handling
            message_article_map[sent_msg.id] = {
                "feed_title": getattr(feed.feed, "title", "Unknown Feed"),
                "entry_index": idx,
                "summary": summary,
                "entry_title": title,
                "entry_link": entry.link,
            }

async def process_all_feeds_for_user(user):
    """Reads all feed URLs from RSS_feed_sources.txt and processes them for a single user."""
    if not os.path.exists(RSS_FEED_SOURCES_FILE):
        print(f"[ERROR] {RSS_FEED_SOURCES_FILE} not found!")
        return

    with open(RSS_FEED_SOURCES_FILE, "r", encoding="utf-8") as f:
        feed_urls = [line.strip() for line in f if line.strip()]

    for url in feed_urls:
        print(f"Processing feed: {url}")
        await process_rss_feed(url, user)

def _update_user_profile_positive(topic):
    append_text_file(USER_PROFILE_FILE, f"Positive interest in: {topic}")

def _update_user_profile_negative(topic):
    append_text_file(USER_PROFILE_FILE, f"Negative interest in: {topic}")

# -------------
# Discord Bot Events
# -------------

@bot.event
async def on_ready():
    """Called when the bot is ready."""
    print(f"Bot logged in as {bot.user}. DM the bot `!run` (in a private channel) to parse feeds immediately.")

@bot.event
async def on_reaction_add(reaction, user):
    """Handle user reactions in direct messages."""
    # If the reaction is from the bot, ignore
    if user == bot.user:
        return
    msg_id = reaction.message.id
    if msg_id not in message_article_map:
        return  # Reaction on some other message or old message

    info = message_article_map[msg_id]
    summary = info["summary"]
    entry_title = info["entry_title"]
    entry_link = info["entry_link"]

    if str(reaction.emoji) == "👍":  # Thumbs Up
        detailed_msg = (
            f"**Detailed Summary for:** {entry_title}\n\n"
            f"{summary}\n\n"
            f"Link: {entry_link}"
        )
        # Send as a reply in DM
        bot.loop.create_task(reaction.message.channel.send(detailed_msg))
        _update_user_profile_positive(entry_title)

    elif str(reaction.emoji) == "👎":  # Thumbs Down
        bot.loop.create_task(reaction.message.channel.send("Noted! We won't expand on this topic."))
        _update_user_profile_negative(entry_title)

    elif str(reaction.emoji) == "🙌":  # Raised Hands
        bot.loop.create_task(reaction.message.channel.send(
            "Please type your feedback in chat. Start with `!feedback <your text>`."
        ))

@bot.command(name="feedback")
async def custom_feedback(ctx, *, arg):
    """
    Capture custom feedback from user after they used :raised_hands: reaction.
    Usage: !feedback <Your detailed text>
    """
    append_text_file(USER_PROFILE_FILE, f"Custom Feedback: {arg}")
    await ctx.send("Feedback noted! user_profile.txt has been updated.")

@bot.command(name="run")
async def run_now(ctx):
    """
    Manually run the RSS feed parsing immediately (on demand).
    Usage: !run (in DM with the bot).
    """
    # Ensure the command is used in DM
    if ctx.guild is not None:
        await ctx.send("Please use this command in a direct message (DM) with me.")
        return

    user = ctx.author
    await ctx.send("Starting RSS feed parsing now...")
    await process_all_feeds_for_user(user)
    await ctx.send("Parsing complete!")

# -------------
# Scheduling / Main
# -------------

def run_bot():
    """Start the Discord bot (blocking)."""
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    run_bot()
