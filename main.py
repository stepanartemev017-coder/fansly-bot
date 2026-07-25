import os
import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.media_group import MediaGroupBuilder

# Данные конфигурации
BOT_TOKEN = "8985257496:AAHeUUkzZQ8nrj3s5Zy5o4UNXJ1nQM5Rkag"
ADMIN_GROUP_ID = -5136108392
OWNER_TELEGRAM_ID = 963341281
DB_NAME = "reports.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Временный склад для скриншотов из альбома
media_storage = {}

class ShiftState(StatesGroup):
    waiting_for_earnings = State()
    waiting_for_info = State()
    waiting_for_screenshot = State()

async def get_moscow_time():
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
    conn.commit()
    conn.close()

def get_main_keyboard(user_id: int):
    buttons = [
        [types.KeyboardButton(text="🟢 Пост принял")],
        [types.KeyboardButton(text="🔴 Пост сдал")],
        [types.KeyboardButton(text="📊 Инфо за месяц")]
    ]
    if user_id == OWNER_TELEGRAM_ID:
        buttons.append([types.KeyboardButton(text="🧹 Очистить месяц")])
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_confirm_keyboard():
    buttons = [
        [types.InlineKeyboardButton(text="🗑️ Да, очистить базу", callback_data="db_confirm_clear")],
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="db_cancel_clear")]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)

# ИСПРАВЛЕНИЕ 1: Сброс зависания, если нажали кнопку меню посреди опроса
@dp.message(F.text.in_({"🟢 Пост принял", "🔴 Пост сдал", "📊 Инфо за месяц", "🧹 Очистить месяц"}))
async def handle_menu_interrupt(message: types.Message, state: FSMContext):
    await state.clear()
    if message.text == "🟢 Пост принял":
        await process_shift_start(message)
    elif message.text == "🔴 Пост сдал":
        await process_shift_end_start(message, state)
    elif message.text == "📊 Инфо за месяц":
        await process_statistics(message)
    elif message.text == "🧹 Очистить месяц":
        await process_clear_database_request(message)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        f"Привет, {message.from_user.full_name}! Я бот для отчетов Fansly (Время: МСК).\nИспользуй кнопки ниже для управления сменой.",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

async def process_shift_start(message: types.Message):
    user = message.from_user
    now = await get_moscow_time()
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO shifts (user_id, username, full_name, action, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user.id, user.username, user.full_name, "принял", now.strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    
    text_admin = f"**🟢 Пост принял**\n👤 Чаттер: {user.full_name} (@{user.username})"
    await bot.send_message(chat_id=ADMIN_GROUP_ID, text=text_admin, parse_mode="Markdown")
    await message.answer("✅ Вход на смену зафиксирован! Удачной работы.")

async def process_shift_end_start(message: types.Message, state: FSMContext):
    await message.answer("Сколько ты заработал на смене (введи только число, например: 150 или 75.50)?")
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
    await message.answer("Отправь скриншот(ы) продаж:")
    await state.set_state(ShiftState.waiting_for_screenshot)

# ИСПРАВЛЕНИЕ 2: Обработка нескольких скриншотов альбомом без спама
@dp.message(ShiftState.waiting_for_screenshot, F.photo)
async def process_screenshot(message: types.Message, state: FSMContext):
    user = message.from_user
    photo_id = message.photo[-1].file_id
    media_group_id = message.media_group_id
    
    if media_group_id:
        if media_group_id not in media_storage:
            media_storage[media_group_id] = []
        media_storage[media_group_id].append(photo_id)
        
        await asyncio.sleep(0.6)
        
        if media_group_id not in media_storage:
            return
        all_photos = media_storage.pop(media_group_id)
    else:
        all_photos = [photo_id]

    now = await get_moscow_time()
    data = await state.get_data()
    earnings = data['earnings']
    comment = data['comment']
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO shifts (user_id, username, full_name, action, timestamp, earnings, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user.id, user.username, user.full_name, "сдал", now.strftime("%Y-%m-%d %H:%M:%S"), earnings, comment)
    )
    conn.commit()
    conn.close()
    
    text_admin = (
        f"**🔴 ПОСТ СДАЛ (ОТЧЕТ)**\n"
        f"👤 Чаттер: {user.full_name} (@{user.username})\n"
        f"⏰ Время: {now.strftime('%d.%m.%Y %H:%M')} МСК\n"
        f"💰 Заработал: ${earnings}\n"
        f"📝 Важная инфа: {comment}"
    )
    
    if len(all_photos) == 1:
        await bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=all_photos[0], caption=text_admin, parse_mode="Markdown")
    else:
        media_group = MediaGroupBuilder(caption=text_admin, parse_mode="Markdown")
        for p_id in all_photos:
            media_group.add_photo(media=p_id)
        await bot.send_media_group(chat_id=ADMIN_GROUP_ID, media=media_group.build())
        
    await message.answer("✅ Отчет успешно отправлен руководство! Спасибо за смену.", reply_markup=get_main_keyboard(user.id))
    await state.clear()

async def process_statistics(message: types.Message):
    now = await get_moscow_time()
    one_month_ago = now - timedelta(days=30)
    one_month_ago_str = one_month_ago.strftime("%Y-%m-%d %H:%M:%S")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT full_name, username, SUM(earnings) FROM shifts 
        WHERE timestamp >= ? AND action = 'сдал' GROUP BY user_id
    ''', (one_month_ago_str,))
    balances = cursor.fetchall()
    
    cursor.execute('''
        SELECT full_name, action, timestamp, earnings FROM shifts 
        WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 20
    ''', (one_month_ago_str,))
    recent_actions = cursor.fetchall()
    conn.close()
    
    response = "**📊 ОТЧЕТ ЗА ПОСЛЕДНИЙ МЕСЯЦ (МСК)**\n\n**👥 Баланс чаттеров (Общий заработок):**\n"
    if not balances:
        response += "Нет данных о заработке.\n"
    else:
        for name, username, total in balances:
            response += f"• {name} (@{username}): **${total:.2f}**\n"
            
    response += "\n**🕒 Последние действия на сменах:**\n"
    if not recent_actions:
        response += "История пуста.\n"
    else:
        for name, action, dt_str, earn in recent_actions:
            try:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                dt_formatted = dt.strftime("%d.%m %H:%M")
            except ValueError:
                dt_formatted = dt_str
                
            if action == "принял":
                response += f"{dt_formatted} - {name} принял пост\n"
            else:
                response += f"{dt_formatted} - {name} сдал пост (Заработано: ${earn})\n"
                
    await message.answer(response, parse_mode="Markdown")

async def process_clear_database_request(message: types.Message):
    if message.from_user.id != OWNER_TELEGRAM_ID:
        await message.answer("🛑 У вас нет прав для выполнения этой команды.")
        return
    await message.answer(
        "⚠️ **ВНИМАНИЕ!** Вы собираетесь полностью очистить базу данных за месяц.\nВсе балансы сотрудников и история смен будут безвозвратно удалены. Вы уверены?",
        reply_markup=get_confirm_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "db_confirm_clear")
async def callback_confirm_clear(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_TELEGRAM_ID:
        await callback.answer("🛑 Отказано в доступе.", show_alert=True)
        return
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shifts")
    conn.commit()
    conn.close()
    
    await callback.message.edit_text("🧹 **База данных успешно очищена!** Все балансы сотрудников и история смен были удалены.", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "db_cancel_clear")
async def callback_cancel_clear(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Очистка базы данных отменена. Данные чаттеров остались в безопасности.", parse_mode="Markdown")
    await callback.answer()

async def main():
    init_db()
    print("Бот успешно запущен на московском времени (период: месяц)...")
