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

# =====================================================================
# НАСТРОЙКИ: Замените значения на свои данные
# =====================================================================
BOT_TOKEN = "8985257496:AAHeUUzkZQ8nrj3s5Zy5o4UNXJ1nQM5RKag"
ADMIN_GROUP_ID = -5136108392
OWNER_TELEGRAM_ID = 963341281
DB_NAME = "reports.db"

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Временное хранилище для альбомов со скриншотами
media_storage = {}

# Класс состояний FSM
class ShiftState(StatesGroup):
    waiting_for_earnings = State()
    waiting_for_info = State()
    waiting_for_screenshot = State()

# Функция получения текущего времени по Москве (UTC+3)
async def get_moscow_time():
    tz_moscow = timezone(timedelta(hours=3))
    return datetime.now(tz_moscow)

# Инициализация базы данных
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
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

# Главная клавиатура
def get_main_keyboard(user_id: int):
    buttons = [
        [types.KeyboardButton(text="🟢 Пост принял"), types.KeyboardButton(text="🔴 Пост сдал")],
        [types.KeyboardButton(text="📊 Инфо за месяц")]
    ]
    if user_id == OWNER_TELEGRAM_ID:
        buttons.append([types.KeyboardButton(text="🧹 Очистить месяц")])
        
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# Inline-клавиатура для подтверждения очистки
def get_confirm_keyboard():
    buttons = [
        [
            types.InlineKeyboardButton(text="🗑️ Да, очистить базу", callback_data="db_confirm_clear"),
            types.InlineKeyboardButton(text="❌ Отмена", callback_data="db_cancel_clear")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)

# =====================================================================
# ХЭНДЛЕРЫ И ЛОГИКА БОТА
# =====================================================================

# 1. Защита от зависания FSM (Сброс состояний при нажатии главных кнопок)
@dp.message(F.text.in_({"🟢 Пост принял", "🔴 Пост сдал", "📊 Инфо за месяц", "📊 Инфо за month", "🧹 Очистить месяц"}))
async def handle_menu_buttons_interrupt(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()  # Аннулируем старый зависший ввод данных
    
    # Перенаправляем выполнение на нужный хэндлер
    if message.text == "🟢 Пост принял":
        await process_shift_start(message)
    elif message.text == "🔴 Пост сдал":
        await process_shift_end_start(message, state)
    elif message.text in ["📊 Инфо за месяц", "📊 Инфо за month"]:
        await process_statistics(message)
    elif message.text == "🧹 Очистить месяц":
        await process_clear_database_request(message)

# Старт бота
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        f"Привет, {message.from_user.full_name}! Я бот для отчетов Fansly (Время: МСК).\n"
        f"Используй кнопки ниже для управления сменой.",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# Пост принял
async def process_shift_start(message: types.Message):
    user = message.from_user
    now = await get_moscow_time()
    formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO shifts (user_id, username, full_name, action, timestamp) VALUES (?, ?, ?, ?, ?)",
            (user.id, user.username, user.full_name, "принял", formatted_time)
        )
        conn.commit()

    username_text = f"@{user.username}" if user.username else "нет юзернейма"
    text_admin = f"<b>🟢 Пост принял</b>\n👤 Чаттер: {user.full_name} ({username_text})"
    
    await bot.send_message(chat_id=ADMIN_GROUP_ID, text=text_admin, parse_mode="HTML")
    await message.answer("✅ Вход на смену зафиксирован! Удачной работы.")

# Пост сдал (Начало опроса FSM)
async def process_shift_end_start(message: types.Message, state: FSMContext):
    await message.answer("Сколько ты заработал на смене (введи только число, например: 150 или 75.50)?")
    await state.set_state(ShiftState.waiting_for_earnings)

# FSM: Получение заработка
@dp.message(ShiftState.waiting_for_earnings)
async def process_earnings(message: types.Message, state: FSMContext):
    try:
        clean_text = message.text.replace(" ", "").replace(",", ".")
        earnings = float(clean_text)
        
        await state.update_data(earnings=earnings)
        await message.answer("Напиши важную информацию/комментарий по смене (если нет, напиши 'нет' или '-'):")
        await state.set_state(ShiftState.waiting_for_info)
    except ValueError:
        await message.answer("Пожалуйста, введи корректное число (заработок). Пример: 120.50")

# FSM: Получение комментария
@dp.message(ShiftState.waiting_for_info)
async def process_info(message: types.Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await message.answer("Отправь скриншот(ы) продаж:")
    await state.set_state(ShiftState.waiting_for_screenshot)

# FSM: Финал. Получение скриншотов (Одиночные + Альбомы)
@dp.message(ShiftState.waiting_for_screenshot, F.photo)
async def process_screenshot(message: types.Message, state: FSMContext):
    user = message.from_user
    photo_id = message.photo[-1].file_id
    media_group_id = message.media_group_id
    
    # Логика сборщика картинок, отправленных альбомом
    if media_group_id:
        if media_group_id not in media_storage:
            media_storage[media_group_id] = []
        media_storage[media_group_id].append(photo_id)
        
        # Микропауза для склейки всех входящих потоков картинок от Telegram
        await asyncio.sleep(0.6)
        
        if media_group_id not in media_storage:
            return
        all_photos = media_storage.pop(media_group_id)
    else:
        all_photos = [photo_id]

    # --- ЕДИНОКРАТНАЯ ЗАПИСЬ В БД И ОТПРАВКА ОТЧЕТА ---
    now = await get_moscow_time()
    formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")
    
    data = await state.get_data()
    earnings = data.get('earnings', 0.0)
    comment = data.get('comment', '-')
    
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO shifts (user_id, username, full_name, action, timestamp, earnings, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user.id, user.username, user.full_name, "сдал", formatted_time, earnings, comment)
        )
        conn.commit()
        
    username_text = f"@{user.username}" if user.username else "нет юзернейма"
    text_admin = (
        f"<b>🔴 ПОСТ СДАЛ (ОТЧЕТ)</b>\n"
        f"👤 Чаттер: {user.full_name} ({username_text})\n"
        f"⏰ Время: {now.strftime('%d.%m.%Y %H:%M')} МСК\n"
        f"💰 Заработал: ${earnings:.2f}\n"
        f"📝 Важная инфа: {comment}"
    )
    
    # Отправка контента руководству
    if len(all_photos) == 1:
        await bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=all_photos[0], caption=text_admin, parse_mode="HTML")
    else:
        media_group = MediaGroupBuilder(caption=text_admin, parse_mode="HTML")
        for p_id in all_photos:
            media_group.add_photo(media=p_id)
        await bot.send_media_group(chat_id=ADMIN_GROUP_ID, media=media_group.build())
        
    await message.answer("✅ Отчет успешно отправлен руководству! Спасибо за смену.", reply_markup=get_main_keyboard(user.id))
    await state.clear()

# Вывод статистики за месяц
async def process_statistics(message: types.Message):
    now = await get_moscow_time()
    one_month_ago = now - timedelta(days=30)
    one_month_ago_str = one_month_ago.strftime("%Y-%m-%d %H:%M:%S")
    
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        
        # Сводный баланс
        cursor.execute('''
            SELECT full_name, username, SUM(earnings) 
            FROM shifts 
            WHERE timestamp >= ? AND action = 'сдал' 
            GROUP BY user_id
        ''', (one_month_ago_str,))
        balances = cursor.fetchall()
        
        # История логов (20 последних действий)
        cursor.execute('''
            SELECT full_name, action, timestamp, earnings 
            FROM shifts 
            WHERE timestamp >= ? 
            ORDER BY timestamp DESC 
            LIMIT 20
        ''', (one_month_ago_str,))
        recent_actions = cursor.fetchall()

    response = "<b>📊 ОТЧЕТ ЗА ПОСЛЕДНИЙ МЕСЯЦ (МСК)</b>\n\n"
    response += "<b>👥 Баланс чаттеров (Общий заработок):</b>\n"
    
    if not balances:
        response += "Нет данных о заработке.\n"
    else:
        for name, username, total in balances:
            user_link = f"@{username}" if username else "нет юзернейма"
            response += f"• {name} ({user_link}): <b>${total:.2f}</b>\n"
            
    response += "\n<b>🕒 Последние действия на сменах:</b>\n"
    if not recent_actions:
        response += "История пуста.\n"
    else:
        for name, action, dt_str, earn in recent_actions:
            try:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
