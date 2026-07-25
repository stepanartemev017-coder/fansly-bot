import os
import sqlite3
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ========================================================
# ЖЕСТКИЕ НАСТРОЙКИ (ВШИТЫ НАМЕРТВО)
# ========================================================
BOT_TOKEN = "8985257496:AAFg99so12mVX6jR3HwzsoG77A6kBEoF2nE"
ADMIN_GROUP_ID = -5136108392
OWNERS_IDS = [8207913329, 963341281]  # Два ваших ID
# ========================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
DB_NAME = "reports.db"

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

def get_main_keyboard(user_id: int):
    buttons = [
        [types.KeyboardButton(text="🟢 Пост принял")],
        [types.KeyboardButton(text="🔴 Пост сдал")],
        [types.KeyboardButton(text="📊 Инфо за month") or types.KeyboardButton(text="📊 Инфо за месяц")]
    ]
    # Только для владельцев добавляем админские кнопки
    if user_id in OWNERS_IDS:
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
async def cmd_start(message: types.Message):
    user = message.from_user
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)", 
        (user.id, user.username, user.full_name)
    )
    conn.commit()
    conn.close()
    
    await message.answer(
        f"Привет, {user.full_name}! Я бот для отчетов Fansly.\n"
        "Используй кнопки ниже для управления сменой (Время: МСК).",
        reply_markup=get_main_keyboard(user.id)
    )

@dp.message(lambda msg: msg.text == "🟢 Пост принял")
async def process_shift_start(message: types.Message):
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

@dp.message(lambda msg: msg.text == "🔴 Пост сдал")
async def process_shift_end_start(message: types.Message, state: FSMContext):
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

@dp.message(lambda msg: msg.text in ["📊 Инфо за month", "📊 Инфо за месяц"])
async def process_statistics(message: types.Message):
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

@dp.message(lambda msg: msg.text == "👥 Участники")
async def process_view_users(message: types.Message):
    if message.from_user.id not in OWNERS_IDS:
        await message.answer("⛔ У вас нет прав для выполнения этой команды.")
        return
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT username, full_name FROM users")
    all_users = cursor.fetchall()
    conn.close()
    
    response = "👥 **СПИСОК УЧАСТНИКОВ (Кто писал /start):**\n\n"
    if not all_users:
        response += "Пока никто не запускал бота."
    for username, full_name in all_users:
        user_link = f"@{username}" if username else "нет юзернейма"
        response += f"• {full_name} ({user_link})\n"
        
    await message.answer(response, parse_mode="Markdown")

@dp.message(lambda msg: msg.text == "🧹 Очистить месяц")
async def process_clear_database_request(message: types.Message):
    if message.from_user.id not in OWNERS_IDS:
        await message.answer("⛔ У вас нет прав для выполнения этой команды.")
        return
        
    await message.answer(
        "⚠️ **ВНИМАНИЕ!** Вы собираетесь полностью очистить базу данных за месяц.\n"
        "Все балансы сотрудников и история смен будут безвозвратно удалены. Вы уверены?",
        reply_markup=get_confirm_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "db_confirm_clear")
async def callback_confirm_clear(callback: types.CallbackQuery):
    if callback.from_user.id not in OWNERS_IDS:
        await callback.answer("⛔ Отказано в доступе.", show_alert=True)
        return
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shifts")
    conn.commit()
    conn.close()
    
    await callback.message.edit_text("🧹 **База данных успешно очищена!** Все балансы за месяц и история смен сброшены до нуля.", parse_mode="Markdown")
