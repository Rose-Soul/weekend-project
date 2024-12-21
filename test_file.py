from dotenv import load_dotenv
import os
# ---------------------
# Configuration Section
# ---------------------

# option for using keys inside a .env file
load_dotenv()  # this reads .env and loads into environment variables
load_dotenv(dotenv_path=r"E:\smart_task_management\weekend-project\tokens.env")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

print("DISCORD_BOT_TOKEN is:", DISCORD_BOT_TOKEN)
