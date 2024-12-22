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

OPENAI_MODEL = "openai/gpt-3.5-turbo"

CHECK_INTERVAL_MINUTES = 15

USER_FILE = "user.txt"  # read-only
USER_PROFILE_FILE = "user_profile.txt"  # updated with feedback
RSS_FEED_SOURCES_FILE = "RSS_feed_sources.txt"
NOTES_DIR = "Notes"

# We need these intents to handle DMs and reaction events
intents = discord.Intents.default()
intents.message_content = True  # needed for reading messages/commands
intents.dm_messages = True       # needed for DM-based commands
intents.reactions = True         # needed to detect reaction events
bot = commands.Bot(command_prefix="!", intents=intents)

# This dictionary tracks message -> article info for reaction logic
message_article_map = {}


# --------------------------------
# Utility Functions
# --------------------------------

def load_text_file(file_path):
    """Reads the entire text file if it exists, else returns an empty string."""
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    return ""

def append_text_file(file_path, content):
    """Appends a line to the given text file."""
    with open(file_path, 'a', encoding='utf-8') as f:
        f.write(content + "\n")

def check_relevance(title, text, user_data, user_profile):
    """Simple keyword-based approach to decide if post is relevant."""
    combined = f"{title.lower()} {text.lower()}"
    keywords = set(user_data.lower().split() + user_profile.lower().split())
    for kw in keywords:
        if kw in combined:
            return True
    return False

def ensure_notes_dir():
    """Ensures the Notes/ directory exists."""
    if not os.path.exists(NOTES_DIR):
        os.makedirs(NOTES_DIR)

# --------------------------------
# Summaries via GPT
# --------------------------------

async def summarize_with_ai(title, content):
    """
    Return (short_summary, long_summary), both including the link at the end.
    We'll let GPT do it, or we can append the link ourselves if we prefer.
    """
    system_prompt = (
        "You are an RSS summarizer AI. Summarize the blog post in user-friendly text."
    )

    user_prompt_short = (
        f"Article title: {title}\nContent:\n{content}\n\n"
        "Provide a short summary (1-3 sentences). Then provide the link at the end in parentheses, like (Link: ...)."
    )

    user_prompt_long = (
        f"Article title: {title}\nContent:\n{content}\n\n"
        "Provide a more detailed summary (2-4 paragraphs). Then provide the link at the end in parentheses, like (Link: ...)."
    )

    # Short Summary
    resp_short = await openai.ChatCompletion.acreate(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt_short},
        ],
        max_tokens=300,
        temperature=0.7,
    )
    short_summary = resp_short.choices[0].message["content"].strip()

    # Long Summary
    resp_long = await openai.ChatCompletion.acreate(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt_long},
        ],
        max_tokens=600,
        temperature=0.7,
    )
    long_summary = resp_long.choices[0].message["content"].strip()

    return short_summary, long_summary

# --------------------------------
# File/Folder Organization
# --------------------------------

_feed_counter = 1  # increments for each feed

def make_feed_subfolder(feed_title):
    """Creates subfolder like 0001_FeedTitle in Notes/."""
    global _feed_counter
    safe_title = "".join(c for c in feed_title if c.isalnum() or c in "-_ ")
    safe_title = safe_title.strip().replace(" ", "_")
    folder_name = f"{_feed_counter:04d}_{safe_title}" if safe_title else f"{_feed_counter:04d}_UnknownFeed"
    path = os.path.join(NOTES_DIR, folder_name[:60])  # limit length
    if not os.path.exists(path):
        os.makedirs(path)
    _feed_counter += 1
    return path

# We'll number the articles in each feed from 1 upwards
def make_note_filename(index):
    return f"{index:04d}.txt"

# --------------------------------
# Reaction Handling
# --------------------------------

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """
    This triggers for any reaction, including DMs and uncached messages.
    """
    if payload.user_id == bot.user.id:
        return  # ignore bot's own reactions

    msg_id = payload.message_id
    if msg_id not in message_article_map:
        return  # not one of our tracked messages

    info = message_article_map[msg_id]
    long_summary = info["long_summary"]
    entry_title = info["entry_title"]

    channel = await bot.fetch_channel(payload.channel_id)
    emoji_str = str(payload.emoji)

    if emoji_str == "👍":
        # Detailed summary
        detail_msg = f"**Detailed Summary for:** {entry_title}\n\n{long_summary}"
        await channel.send(detail_msg)
        _update_user_profile_positive(entry_title)

    elif emoji_str == "👎":
        await channel.send("Noted! We won't expand on this topic.")
        _update_user_profile_negative(entry_title)

    elif emoji_str == "🙌":
        await channel.send("Please type your feedback with `!feedback <your feedback here>`.")

def _update_user_profile_positive(topic):
    append_text_file(USER_PROFILE_FILE, f"Positive interest in: {topic}")

def _update_user_profile_negative(topic):
    append_text_file(USER_PROFILE_FILE, f"Negative interest in: {topic}")

# --------------------------------
# Sending Summaries
# --------------------------------

async def send_short_summary_dm(user, title, short_summary, long_summary):
    """
    DM the user the short summary. Add real reactions so they can click them.
    Store references in message_article_map so we can handle the feedback in on_raw_reaction_add.
    """
    msg_text = f"**Title:** {title}\n\n{short_summary}"
    sent_msg = await user.send(msg_text)

    # store article info
    message_article_map[sent_msg.id] = {
        "entry_title": title,
        "short_summary": short_summary,
        "long_summary": long_summary,
    }

    # add reaction buttons
    await sent_msg.add_reaction("👍")
    await sent_msg.add_reaction("👎")
    await sent_msg.add_reaction("🙌")

# --------------------------------
# RSS Parsing
# --------------------------------

async def process_rss_feed(feed_url, user):
    feed = feedparser.parse(feed_url)
    if feed.bozo:
        print(f"[ERROR] Could not parse feed: {feed_url}")
        return

    feed_title = getattr(feed.feed, "title", "UnknownFeed")
    print(f"Processing feed: {feed_title} (URL: {feed_url})")

    # Create subfolder for notes
    subfolder = make_feed_subfolder(feed_title)

    # load user data
    user_data = load_text_file(USER_FILE)
    user_profile = load_text_file(USER_PROFILE_FILE)

    entries = feed.entries
    note_index = 1

    for entry in entries:
        title = entry.title if hasattr(entry, 'title') else "No Title"
        content = entry.summary if hasattr(entry, 'summary') else "No Summary"
        link = entry.link if hasattr(entry, 'link') else ""

        # Summaries
        short_sum, long_sum = await summarize_with_ai(title, content)

        # Check relevance
        if check_relevance(title, short_sum, user_data, user_profile):
            # DM user the short summary
            await send_short_summary_dm(user, title, short_sum, long_sum)
        else:
            # If not relevant, do nothing (no DM).
            pass

        # Save note in subfolder (for reference)
        note_filename = make_note_filename(note_index)
        note_index += 1
        note_path = os.path.join(subfolder, note_filename)
        note_body = (
            f"Title: {title}\nLink: {link}\n\n"
            f"Short Summary:\n{short_sum}\n\n"
            f"Long Summary:\n{long_sum}\n"
        )
        with open(note_path, 'w', encoding='utf-8') as f:
            f.write(note_body)


async def process_all_feeds_for_user(user):
    """
    Reads RSS_feed_sources.txt, processes each feed in turn.
    """
    if not os.path.exists(RSS_FEED_SOURCES_FILE):
        print(f"[ERROR] {RSS_FEED_SOURCES_FILE} not found or empty.")
        return

    with open(RSS_FEED_SOURCES_FILE, 'r', encoding='utf-8') as f:
        lines = [line.strip() for line in f if line.strip()]

    for url in lines:
        await process_rss_feed(url, user)

# --------------------------------
# Bot Commands
# --------------------------------

@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    print("DM the bot `!run` to parse feeds.")

@bot.command(name="feedback")
async def custom_feedback(ctx, *, arg):
    append_text_file(USER_PROFILE_FILE, f"Custom Feedback: {arg}")
    await ctx.send("Feedback noted! user_profile.txt has been updated.")

@bot.command(name="run")
async def run_now(ctx):
    # Only allow in DM
    if ctx.guild is not None:
        await ctx.send("Please use this command in a direct message (DM) with me.")
        return

    user = ctx.author
    await ctx.send("Starting RSS feed parsing now...")
    ensure_notes_dir()
    await process_all_feeds_for_user(user)
    await ctx.send("Parsing complete!")

def run_bot():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    run_bot()
