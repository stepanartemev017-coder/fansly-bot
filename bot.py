import os
import sqlite3
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ========================================================
# НАСТРОЙКИ: Замените значения на свои данные
# ========================================================
BOT_TOKEN = "8985257496:AAFg99so12mVX6jR3HwzsoG77A6kBEoF2nE"  # Кавычки оставляем
ADMIN_GROUP_ID = -5136108392  # ID группы отчетов (без кавычек!)
OWNER_TELEGRAM_ID = 963341281  # ВСТАВЬТЕ СЮДА ВАШ ЛИЧНЫЙ ID ИЗ @myidbot (число без кавычек)
SECRET_CODE = "315699"  # СЕКРЕТНОЕ СЛОВО ДЛЯ ЧАТТЕРОВ
# ========================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
DB_NAME = "reports.db"

class AuthState(StatesGroup):
    waiting_for_code = State()

class ShiftState(StatesGroup):
    waiting_for_earnings = State()
    waiting_for_info = State()
    waiting_for_screenshot = State()

def get_moscow_time():
    tz_moscow = timezone(timedelta(hours=3))
    return datetime.now(tz_moscow)

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            action TEXT,
            timestamp DATETIME,
            earnings REAL DEFAULT 0,
            comment TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT
        )
    ''')
    conn.commit()
    conn.close()

def is_user_allowed(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user is not None or user_id == OWNER_TELEGRAM_ID

def get_main_keyboard(user_id: int):
    buttons = [
        [types.KeyboardButton(text="🟢 Пост принял")],
        [types.KeyboardButton(text="🔴 Пост сдал")],
        [types.KeyboardButton(text="📊 Инфо за месяц")]
    ]
    # Только для владельца добавляем кнопки управления
    if user_id == OWNER_TELEGRAM_ID:
        buttons.append([types.KeyboardButton(text="👥 Участники")])
        buttons.append([types.KeyboardButton(text="🧹 Очистить месяц")])
        
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_confirm_keyboard():
    buttons = [
        [
            types.InlineKeyboardButton(text="⚠️ Подтвердить удаление", callback_data="db_confirm_clear"),
            types.InlineKeyboardButton(text="❌ Отмена", callback_data="db_cancel_clear")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if is_user_allowed(user_id):
        await message.answer(
            f"Привет, {message.from_user.full_name}! Рад видеть тебя снова.\n"
            "Используй кнопки ниже для управления сменой (Время: МСК).",
            reply_markup=get_main_keyboard(user_id)
        )
    else:
        await message.answer("введите код входа")
        await state.set_state(AuthState.waiting_for_code)

@dp.message(AuthState.waiting_for_code)
async def process_auth_code(message: types.Message, state: FSMContext):
    if message.text == SECRET_CODE:
        user = message.from_user
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        # Сохраняем ID, юзернейм и имя чаттера для списка участников
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
            (user.id, user.username, user.full_name)
        )
        conn.commit()
        conn.close()
        await message.answer("✅ **Доступ успешно открыт!**\nТеперь вы можете полноценно использовать бота.", reply_markup=get_main_keyboard(user.id))
        await state.clear()
    else:
        await message.answer("❌ Неверный код активации. Попробуйте еще раз или обратитесь к администратору:")

@dp.message(F.text == "🟢 Пост принял")
async def process_shift_start(message: types.Message):
    if not is_user_allowed(message.from_user.id):
        await message.answer("⛔ У вас нет доступа. Нажмите /start для активации.")
        return
        
    user = message.from_user
    now = get_moscow_time()
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO shifts (user_id, username, full_name, action, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user.id, user.username, user.full_name, "принял", now.strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    
    text_admin = f"🟢 **Пост принял**\n👤 Чаттер: {user.full_name} (@{user.username})\n⏰ Время: {now.strftime('%d.%m.%Y %H:%M')} МСК"
    await bot.send_message(chat_id=ADMIN_GROUP_ID, text=text_admin, parse_mode="Markdown")
    await message.answer("✅ Вход на смену зафиксирован! Удачной работы.")

@dp.message(F.text == "🔴 Пост сдал")
async def process_shift_end_start(message: types.Message, state: FSMContext):
    if not is_user_allowed(message.from_user.id):
        await message.answer("⛔ У вас нет доступа. Нажмите /start для активации.")
        return
        
    await message.answer("Сколько ты заработал на смене (введи только число, например: 150 или 75.5)?")
    await state.set_state(ShiftState.waiting_for_earnings)

@dp.message(ShiftState.waiting_for_earnings)
async def process_earnings(message: types.Message, state: FSMContext):
    try:
        earnings = float(message.text.replace(",", "."))
        await state.update_data(earnings=earnings)
        await message.answer("Напиши важную информацию/комментарий по смене:")
        await state.set_state(ShiftState.waiting_for_info)
    except ValueError:
        await message.answer("Пожалуйста, введи корректное число (заработок).")

@dp.message(ShiftState.waiting_for_info)
async def process_info(message: types.Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await message.answer("Отправь скриншот(ы) продаж.")
    await state.set_state(ShiftState.waiting_for_screenshot)

@dp.message(ShiftState.waiting_for_screenshot, F.photo)
async def process_screenshot(message: types.Message, state: FSMContext):
    user = message.from_user
    now = get_moscow_time()
    data = await state.get_data()
    
    earnings = data['earnings']
    comment = data['comment']
    photo_id = message.photo[-1].file_id
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO shifts (user_id, username, full_name, action, timestamp, earnings, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user.id, user.username, user.full_name, "сдал", now.strftime("%Y-%m-%d %H:%M:%S"), earnings, comment)
    )
    conn.commit()
    conn.close()
    
    text_admin = (
        f"🔴 **Пост сдал (ОТЧЕТ)**\n"
        f"👤 Чаттер: {user.full_name} (@{user.username})\n"
        f"⏰ Время: {now.strftime('%d.%m.%Y %H:%M')} МСК\n"
        f"💰 Заработал: ${earnings}\n"
        f"📝 Важная инфа: {comment}"
    )
    
    await bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=photo_id, caption=text_admin, parse_mode="Markdown")
    await message.answer("✅ Отчет успешно отправлен руководством! Спасибо за смену.", reply_markup=get_main_keyboard(user.id))
    await state.clear()

@dp.message(F.text == "📊 Инфо за месяц")
async def process_statistics(message: types.Message):
    if not is_user_allowed(message.from_user.id):
        await message.answer("⛔ У вас нет доступа.")
        return
        
    one_month_ago = get_moscow_time() - timedelta(days=30)
    one_month_ago_str = one_month_ago.strftime("%Y-%m-%d %H:%M:%S")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT full_name, username, SUM(earnings) 
        FROM shifts 
        WHERE timestamp >= ? AND action = 'сдал'
        GROUP BY user_id
    ''', (one_month_ago_str,))
    balances = cursor.fetchall()
    
    cursor.execute('''
        SELECT full_name, action, timestamp, earnings 
        FROM shifts 
        WHERE timestamp >= ? 
        ORDER BY timestamp DESC 
        LIMIT 20
    ''', (one_month_ago_str,))
    recent_actions = cursor.fetchall()
    conn.close()
    
    response = "📊 **ОТЧЕТ ЗА ПОСЛЕДНИЙ МЕСЯЦ (МСК)**\n\n"
    response += "💰 **Баланс чаттеров (Общий заработок):**\n"
    if not balances:
        response += "Нет данных о заработке.\n"
    for name, username, total in balances:
        user_link = f"@{username}" if username else "нет юзернейма"
        response += f"• {name} ({user_link}): **${total:.2f}**\n"
        
    response += "\n🕒 **Последние действия на сменах:**\n"
    if not recent_actions:
        response += "История пуста.\n"
    for name, action, dt_str, earn in recent_actions:
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            dt_formatted = dt.strftime("%d.%m %H:%M")
        except ValueError:
            dt_formatted = dt_str
        
        if action == "принял":
            response += f"🟢 {dt_formatted} — {name} принял пост\n"
        else:
            response += f"🔴 {dt_formatted} — {name} сдал пост (Заработано: ${earn})\n"
            
    await message.answer(response, parse_mode="Markdown")

# --- ВКУЛАДКА СПИСКА УЧАСТНИКОВ (ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА) ---
@dp.message(F.text == "👥 Участники")
async def process_view_users(message: types.Message):
    if message.from_user.id != OWNER_TELEGRAM_ID:
        await message.answer("⛔ У вас нет прав для выполнения этой команды.")
        return
        
