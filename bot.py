import os
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ========================================================
# НАСТРОЙКИ: Замените значения внутри кавычек на свои данные
# ========================================================
BOT_TOKEN = "8985257496:AAFg99so12mVX6jR3HwzsoG77A6kBEoF2nE"  # Кавычки оставляем
ADMIN_GROUP_ID = -5136108392  # Сюда ID группы из Избранного (без кавычек!)
# ========================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
DB_NAME = "reports.db"

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
    conn.commit()
    conn.close()

class ShiftState(StatesGroup):
    waiting_for_earnings = State()
    waiting_for_info = State()
    waiting_for_screenshot = State()

def get_main_keyboard():
    buttons = [
        [types.KeyboardButton(text="🟢 Пост принял")],
        [types.KeyboardButton(text="🔴 Пост сдал")],
        [types.KeyboardButton(text="📊 Инфо за 2 недели")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        f"Привет, {message.from_user.full_name}! Я бот для отчетов Fansly.\n"
        "Используй кнопки ниже для управления сменой.",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "🟢 Пост принял")
async def process_shift_start(message: types.Message):
    user = message.from_user
    now = datetime.now()
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO shifts (user_id, username, full_name, action, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user.id, user.username, user.full_name, "принял", now)
    )
    conn.commit()
    conn.close()
    
    text_admin = f"🟢 **Пост принял**\n👤 Чаттер: {user.full_name} (@{user.username})\n⏰ Время: {now.strftime('%d.%m.%Y %H:%M')}"
    await bot.send_message(chat_id=ADMIN_GROUP_ID, text=text_admin, parse_mode="Markdown")
    await message.answer("✅ Вход на смену зафиксирован! Удачной работы.")

@dp.message(F.text == "🔴 Пост сдал")
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
    await message.answer("Отправь скриншот(ы) продаж (одним фото).")
    await state.set_state(ShiftState.waiting_for_screenshot)

@dp.message(ShiftState.waiting_for_screenshot, F.photo)
async def process_screenshot(message: types.Message, state: FSMContext):
    user = message.from_user
    now = datetime.now()
    data = await state.get_data()
    
    earnings = data['earnings']
    comment = data['comment']
    photo_id = message.photo[-1].file_id
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO shifts (user_id, username, full_name, action, timestamp, earnings, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user.id, user.username, user.full_name, "сдал", now, earnings, comment)
    )
    conn.commit()
    conn.close()
    
    text_admin = (
        f"🔴 **Пост сдал (ОТЧЕТ)**\n"
        f"👤 Чаттер: {user.full_name} (@{user.username})\n"
        f"⏰ Время: {now.strftime('%d.%m.%Y %H:%M')}\n"
        f"💰 Заработал: ${earnings}\n"
        f"📝 Важная инфа: {comment}"
    )
    
    await bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=photo_id, caption=text_admin, parse_mode="Markdown")
    await message.answer("✅ Отчет успешно отправлен руководству! Спасибо за смену.", reply_markup=get_main_keyboard())
    await state.clear()

@dp.message(F.text == "📊 Инфо за 2 недели")
async def process_statistics(message: types.Message):
    two_weeks_ago = datetime.now() - timedelta(days=14)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT full_name, username, SUM(earnings) 
        FROM shifts 
        WHERE timestamp >= ? AND action = 'сдал'
        GROUP BY user_id
    ''', (two_weeks_ago,))
    balances = cursor.fetchall()
    
    cursor.execute('''
        SELECT full_name, action, timestamp, earnings 
        FROM shifts 
        WHERE timestamp >= ? 
        ORDER BY timestamp DESC 
        LIMIT 20
    ''', (two_weeks_ago,))
    recent_actions = cursor.fetchall()
    conn.close()
    
    response = "📊 **ОТЧЕТ ЗА ПОСЛЕДНИЕ 2 НЕДЕЛИ**\n\n"
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
        # Корректное чтение формата даты SQLite
        try:
            dt = datetime.strptime(dt_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            dt = datetime.now()
        dt_formatted = dt.strftime("%d.%m %H:%M")
        
        if action == "принял":
            response += f"🟢 {dt_formatted} — {name} принял пост\n"
        else:
            response += f"🔴 {dt_formatted} — {name} сдал пост (Заработано: ${earn})\n"
            
    await message.answer(response, parse_mode="Markdown")

if __name__ == '__main__':
    init_db()
    print("Бот успешно запущен...")
    dp.run_polling(bot)
