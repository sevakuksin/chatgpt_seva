import asyncio
import os
from base64 import b64encode
# from io import BytesIO
from telegram import ReplyKeyboardMarkup

import nest_asyncio
from dotenv import load_dotenv
from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from collections import defaultdict

user_token_usage = defaultdict(lambda: {"input": 0, "output": 0})

# Load .env variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

openai_token = os.getenv("OPENAI_API_KEY")
telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD")

if not openai_token or not telegram_token:
    raise ValueError("❌ OPENAI_API_KEY or TELEGRAM_BOT_TOKEN not set in environment.")

# OpenAI client
client = AsyncOpenAI(api_key=openai_token)

# Auth and session
TRUSTED_USERS_FILE = "trusted_users.txt"
trusted_users = set()
chat_history = {}  # user_id -> list of messages
MAX_EXCHANGES = 10


# Load trusted users from file
def load_trusted_users():
    if os.path.exists(TRUSTED_USERS_FILE):
        with open(TRUSTED_USERS_FILE, "r") as f:
            return set(int(line.strip()) for line in f if line.strip().isdigit())
    return set()


def save_trusted_user(user_id: int):
    with open(TRUSTED_USERS_FILE, "a") as f:
        f.write(f"{user_id}\n")


trusted_users = load_trusted_users()


# # Mischief managed button
# def get_reset_button():
#     keyboard = [[InlineKeyboardButton("🗺 Mischief managed", callback_data="reset")]]
#     return InlineKeyboardMarkup(keyboard)

def format_cost(tokens_in, tokens_out):
    in_cost = tokens_in * 0.005 / 1000
    out_cost = tokens_out * 0.015 / 1000
    total = in_cost + out_cost
    return f"💰 Tokens used: {tokens_in + tokens_out} (in: {tokens_in}, out: {tokens_out})\n≈ ${total:.4f} total"


# Callback for reset button
async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_history[user_id] = []
    await context.bot.send_message(chat_id=query.message.chat.id, text="✨ Memory wiped. Mischief managed!")

async def handle_cost_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    tokens = user_token_usage[user_id]
    msg = format_cost(tokens["input"], tokens["output"])
    await context.bot.send_message(chat_id=query.message.chat.id, text=msg)


# Main message handler
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_input = update.message.text
    chat_id = update.effective_chat.id

    # 🔐 Handle authorization
    if user_id not in trusted_users:
        if user_input.strip() == AUTH_PASSWORD:
            trusted_users.add(user_id)
            save_trusted_user(user_id)
            await update.message.reply_text("✅ You are now authorized! Mischief managed.")
        else:
            await update.message.reply_text("🔒 Please enter the secret password to use this bot.")
        return

    # 🧠 Initialize or trim history
    history = chat_history.setdefault(user_id, [])
    history.append({"role": "user", "content": user_input})
    history = history[-MAX_EXCHANGES * 2:]  # Keep last N exchanges
    chat_history[user_id] = history

    if user_input == "💸 Check balance":
        tokens = user_token_usage[user_id]
        msg = format_cost(tokens["input"], tokens["output"])
        await update.message.reply_text(msg)
        return

    if user_input == "🗺 Mischief managed":
        chat_history[user_id] = []
        await update.message.reply_text("✨ Memory wiped. Mischief managed!")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=chat_history[user_id],
            temperature=0.7,
            max_tokens=1000,
        )
        reply = response.choices[0].message.content
        chat_history[user_id].append({"role": "assistant", "content": reply})
        usage = response.usage
        user_token_usage[user_id]["input"] += usage.prompt_tokens
        user_token_usage[user_id]["output"] += usage.completion_tokens

    except Exception as e:
        reply = f"⚠️ Error: {str(e)}"

    print(f"[{user_id}] User: {user_input}\nBot: {reply}")
    await update.message.reply_text(reply, reply_markup=get_persistent_keyboard())



async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    caption = update.message.caption or "What’s in this image?"

    if user_id not in trusted_users:
        await update.message.reply_text("🔒 Please enter the password first to use this feature.")
        return

    # Get highest resolution photo
    photo = update.message.photo[-1]
    file = await photo.get_file()
    file_bytes = await file.download_as_bytearray()
    encoded = b64encode(file_bytes).decode('utf-8')
    image_url = f"data:image/jpeg;base64,{encoded}"

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": caption},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
        }
    ]

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.5,
            max_tokens=1000,
        )
        reply = response.choices[0].message.content
        usage = response.usage
        user_token_usage[user_id]["input"] += usage.prompt_tokens
        user_token_usage[user_id]["output"] += usage.completion_tokens

    except Exception as e:
        reply = f"⚠️ Error processing image: {str(e)}"

    print(f"[{user_id}] Sent photo with caption: {caption}\nBot: {reply}")
    await update.message.reply_text(reply, reply_markup=get_persistent_keyboard())

def get_persistent_keyboard():
    keyboard = [["💸 Check balance", "🗺 Mischief managed"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# App entry
async def main():
    print("✅ Bot is starting...")
    app = ApplicationBuilder().token(telegram_token).build()



    app.add_handler(CallbackQueryHandler(handle_reset))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.run_polling()


# Runtime wrapper
if __name__ == '__main__':
    import sys

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(main())
    except RuntimeError:
        nest_asyncio.apply()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
