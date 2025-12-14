import logging
import os
import requests
from datetime import datetime, timedelta, time
from telegram import Update, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters

# Configuration
# Replace with your actual bot token

# Global state to store user's last selected country and city
# Key: user_id (int), Value: {'country': str, 'city': str}
user_locations = {}

# Global state to track users awaiting city input
# Key: user_id (int), Value: country_name (str)
users_awaiting_city = {}

# List of common Arabic-speaking countries for inline buttons
COUNTRIES = [
    "السعودية", "مصر", "الإمارات", "الكويت", "قطر", "البحرين", "عمان",
    "الأردن", "فلسطين", "لبنان", "سوريا", "العراق", "اليمن", "الجزائر",
    "المغرب", "تونس", "ليبيا", "السودان", "موريتانيا", "جيبوتي", "الصومال"
]
# NOTE: You must ask the user for their token before running the bot.
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8337412245:AAHEmWDg3EM2Hsu6aNgRWq45l1eAJXEJKSw")

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# --- Utility Functions ---

def get_prayer_times(city: str, country: str) -> dict | None:
    """Fetches prayer times for a given city and country using Aladhan API."""
    # Use the current date
    date_str = datetime.now().strftime("%d-%m-%Y")
    
    # API endpoint for a specific date
    url = f"http://api.aladhan.com/v1/timingsByCity/{date_str}"
    
    params = {
        "city": city,
        "country": country,
        "method": 5 # Egyptian General Authority of Survey - A common and reliable method
    }
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
        data = response.json()
        
        if data and data.get("data") and data["data"].get("timings"):
            return data["data"]["timings"]
        else:
            logger.error(f"API response error or missing data: {data}")
            return None
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching prayer times: {e}")
        return None

def calculate_times(timings: dict) -> dict:
    """
    Calculates Islamic Midnight and suggested sleep times based on Maghrib and Fajr.
    
    Calculations are based on the user's request:
    1. Night Duration (D) = Fajr - Maghrib.
    2. Islamic Midnight = Maghrib + (D / 2).
    3. First Sleep Suggestion: 1 hour after Isha.
    4. Second Sleep Suggestion (before Fajr): Fajr - (D / 6).
    """
    
    # Parse times from string "HH:MM" to datetime.datetime objects
    # We need to assume Maghrib and Isha are on the current day, and Fajr is on the next day
    today = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    
    try:
        maghrib_time_str = timings["Maghrib"]
        isha_time_str = timings["Isha"]
        fajr_time_str = timings["Fajr"]
        
        # Maghrib and Isha on today
        maghrib_dt = datetime.combine(today, datetime.strptime(maghrib_time_str, "%H:%M").time())
        isha_dt = datetime.combine(today, datetime.strptime(isha_time_str, "%H:%M").time())
        
        # Fajr on tomorrow
        fajr_dt = datetime.combine(tomorrow, datetime.strptime(fajr_time_str, "%H:%M").time())
        
    except (ValueError, KeyError) as e:
        logger.error(f"Error parsing time strings: {e}")
        return {}

    # 1. Calculate Night Duration (D)
    night_duration = fajr_dt - maghrib_dt
    
    # 2. Calculate Islamic Midnight (Midpoint)
    half_night = night_duration / 2
    midnight_dt = maghrib_dt + half_night
    
    # 3. Wake-up Suggestion (Islamic Midnight)
    # The user wants the wake-up time to be the Islamic Midnight
    wake_up_dt = midnight_dt
    
    # 4. Calculate Sleep Suggestion (Fajr - D/6)
    sixth_night = night_duration / 6
    sleep_dt = fajr_dt - sixth_night
    
    # Format results
    return {
        "Maghrib": maghrib_dt.strftime("%H:%M"),
        "Isha": isha_dt.strftime("%H:%M"),
        "Fajr": fajr_dt.strftime("%H:%M"),
        "Night_Duration": str(night_duration).split('.')[0], # Remove microseconds
        "Islamic_Midnight": midnight_dt.strftime("%H:%M"),
        "Wake_Up_Suggestion": wake_up_dt.strftime("%H:%M"),
        "Sleep_Suggestion": sleep_dt.strftime("%H:%M"),
    }

# --- Telegram Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message and country selection buttons when /start is issued."""
    user = update.effective_user
    
    # Create country selection buttons
    keyboard = []
    for i in range(0, len(COUNTRIES), 3):
        row = []
        for country in COUNTRIES[i:i+3]:
            row.append(InlineKeyboardButton(country, callback_data=f"country_{country}"))
        keyboard.append(row)
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_html(
        rf"مرحباً {user.mention_html()}! أنا بوت لحساب أوقات الصلاة واقتراح أوقات النوم.",
    )
    await update.message.reply_text(
        "الرجاء اختيار الدولة أولاً:",
        reply_markup=reply_markup
    )

async def times_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetches prayer times for the saved location."""
    user_id = update.effective_user.id
    if user_id not in user_locations:
        await update.message.reply_text(
            "لم تقم بحفظ موقع بعد. الرجاء استخدام /start لاختيار الدولة وإدخال المدينة أولاً."
        )
        return
    
    location = user_locations[user_id]
    city = location['city']
    country = location['country']
    
    await update.message.reply_text(f"جارٍ البحث عن أوقات الصلاة في موقعك المحفوظ: {city}, {country}...")
    await fetch_and_send_times(update, context, city, country)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles button presses for country selection."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data.startswith("country_"):
        country = query.data.split("_", 1)[1]
        users_awaiting_city[user_id] = country
        
        await query.edit_message_text(
            text=f"لقد اخترت: **{country}**.\nالآن، الرجاء إرسال اسم المدينة في **{country}** فقط.",
            parse_mode='Markdown'
        )

async def fetch_and_send_times(update: Update, context: ContextTypes.DEFAULT_TYPE, city: str, country: str) -> None:
    """Fetches prayer times and sends the formatted result."""
    timings = get_prayer_times(city, country)
    
    if not timings:
        await update.message.reply_text(
            f"عذراً، لم أتمكن من العثور على أوقات الصلاة لـ {city}, {country}. يرجى التأكد من صحة الإملاء."
        )
        return

    # Perform calculations
    results = calculate_times(timings)
    
    if not results:
        await update.message.reply_text(
            "حدث خطأ في معالجة أوقات الصلاة المسترجعة. يرجى المحاولة مرة أخرى."
        )
        return

    # Prepare the response message (Removed Islamic Midnight line as requested)
    response_text = (
        f"--- أوقات الصلاة واقتراحات النوم لـ {city}, {country} ---\n\n"
        f"🌅 وقت صلاة المغرب: {results['Maghrib']}\n"
        f"🌃 وقت صلاة العشاء: {results['Isha']}\n"
        f"🌄 وقت صلاة الفجر: {results['Fajr']} (في اليوم التالي)\n\n"
        f"⏱️ مدة الليل بين المغرب والفجر: {results['Night_Duration']}\n\n"
        f"🛌 اقتراحات:\n"
        f"1. موعد الاستيقاظ المقترح (منتصف الليل الشرعي): {results['Wake_Up_Suggestion']}\n"
        f"2. موعد النوم المقترح (بداية السدس الأخير من الليل): {results['Sleep_Suggestion']}\n\n"
        "ملاحظة: هذه الأوقات هي للتوجيه والعبادة، وقد تختلف مواعيد الصلاة الفعلية حسب طريقة الحساب المعتمدة في منطقتك.\n"
        "يمكنك استخدام الأمر /times للحصول على الأوقات لنفس الموقع لاحقاً."
    )
    
    await update.message.reply_text(response_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a help message when the command /help is issued."""
    await update.message.reply_text(
        "**كيفية استخدام البوت:**\n\n"
        "1. **لتعيين الموقع:** استخدم الأمر /start واختر الدولة من الأزرار، ثم أرسل اسم المدينة.\n"
        "2. **للحصول على الأوقات:** بعد تعيين الموقع، سيتم إرسال الأوقات تلقائيًا. لاحقًا، يمكنك استخدام الأمر /times للحصول على الأوقات لنفس الموقع المحفوظ.\n\n"
        "**ماذا يحسب البوت؟**\n"
        "1. استحضار أوقات صلاة المغرب والعشاء والفجر.\n"
        "2. حساب مدة الليل بين المغرب والفجر.\n"
        "3. اقتراح موعد للاستيقاظ (منتصف الليل الشرعي).\n"
        "4. اقتراح موعد للنوم قبل الفجر بناءً على تقسيم مدة الليل على 6."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages, either as a city name or an invalid command."""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if user_id in users_awaiting_city:
        country = users_awaiting_city.pop(user_id)
        city = text
        
        # Save location
        user_locations[user_id] = {'city': city, 'country': country}
        
        await update.message.reply_text(f"تم حفظ موقعك: {city}, {country}.")
        await update.message.reply_text(f"جارٍ البحث عن أوقات الصلاة في {city}, {country}...")
        
        await fetch_and_send_times(update, context, city, country)
        return
    
    # Fallback for general text input
    await update.message.reply_text(
        "الرجاء استخدام الأمر /start لاختيار الدولة أولاً، أو الأمر /times للحصول على الأوقات لموقعك المحفوظ."
    )


def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.


    application = Application.builder().token(BOT_TOKEN).build()

    # on different commands - answer in Telegram
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("times", times_command))

    # on button press
    application.add_handler(CallbackQueryHandler(button_callback))

    # on non command i.e message - handle the message
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run the bot until the user presses Ctrl-C
    print("Bot is running... Press Ctrl-C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
