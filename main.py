import os
import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ================================================
# НАСТРОЙКИ
# ================================================
BOT_TOKEN = "8985257496:AAHeUUkzZQ8nrj3s5Zy5o4UNXJ1nQM5Rkag"
ADMIN_GROUP_ID = -5136108392
OWNER_IDS = {963341281, 8207913329}  # список ID владельцев бота (видят "Очистить месяц" и "Добавить баланс")
# ================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
DB_NAME = "reports.db"


# Функция получения текущего времени по Москве (UTC+3)
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
    conn.commit()
    conn.close()


class ShiftState(StatesGroup):
    waiting_for_earnings = State()
    waiting_for_info = State()
    waiting_for_screenshot = State()


class AddBalanceState(StatesGroup):
    waiting_for_name = State()
    waiting_for_username = State()
    waiting_for_amount = State()
    waiting_for_comment = State()


# Тексты главных кнопок меню — используются, чтобы понять,
# что пользователь хочет "выйти" из текущего процесса (например, из ввода заработка)
MENU_PREFIXES = ("🟢", "🔴", "📊", "🧹", "➕")


def is_menu_button(text: str) -> bool:
    if not text:
        return False
    return any(text.startswith(p) for p in MENU_PREFIXES)


async def route_menu_button(message: types.Message, state: FSMContext):
    """Вызывается, когда пользователь нажал одну из кнопок главного меню,
    находясь в середине другого процесса (ввод заработка/комментария/скриншота/баланса).
    Сбрасывает текущий процесс и обрабатывает нажатую кнопку как обычно."""
    await state.clear()
    if message.text.startswith("🟢"):
        await process_shift_start(message, state)
    elif message.text.startswith("🔴"):
        await process_shift_end_start(message, state)
    elif message.text.startswith("📊"):
        await process_statistics(message)
    elif message.text.startswith("🧹"):
        await process_clear_database_request(message)
    elif message.text.startswith("➕"):
        await process_add_balance_start(message, state)


# Главная клавиатура
def get_main_keyboard(user_id: int):
    buttons = [
        [types.KeyboardButton(text="🟢 Пост принял")],
        [types.KeyboardButton(text="🔴 Пост сдал")],
        [types.KeyboardButton(text="📊 Инфо за месяц")]
    ]
    if user_id in OWNER_IDS:
        buttons.append([types.KeyboardButton(text="➕ Добавить баланс")])
        buttons.append([types.KeyboardButton(text="🧹 Очистить месяц")])

    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


# Inline-клавиатура для подтверждения очистки
def get_confirm_keyboard():
    buttons = [
        [
            types.InlineKeyboardButton(text="✅ Да, очистить базу", callback_data="db_confirm_clear"),
            types.InlineKeyboardButton(text="❌ Отмена", callback_data="db_cancel_clear")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    is_owner = message.from_user.id in OWNER_IDS
    owner_note = "\n\n✅ Ты в списке владельцев." if is_owner else "\n\n⚠️ Ты НЕ в списке владельцев (нет доступа к «Очистить месяц» и «Добавить баланс»)."
    await message.answer(
        f"Привет, {message.from_user.full_name}! Я бот для отчетов Fansly (Время: МСК).\n"
        "Используй кнопки ниже для управления сменой.\n\n"
        f"🆔 Твой Telegram ID: `{message.from_user.id}`"
        f"{owner_note}",
        reply_markup=get_main_keyboard(message.from_user.id),
        parse_mode="Markdown"
    )


@dp.message(Command("id"))
async def cmd_id(message: types.Message):
    await message.answer(f"🆔 Твой Telegram ID: `{message.from_user.id}`", parse_mode="Markdown")


@dp.message(F.text.startswith("🟢"))
async def process_shift_start(message: types.Message, state: FSMContext):
    await state.clear()  # на случай если человек был в середине другого процесса
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

    text_admin = f"🟢 **Пост принял**\n👤 Чаттер: {user.full_name} (@{user.username})\n🕐 Время: {now.strftime('%d.%m.%Y %H:%M')} МСК"
    await bot.send_message(chat_id=ADMIN_GROUP_ID, text=text_admin, parse_mode="Markdown")
    await message.answer("✅ Вход на смену зафиксирован! Удачной работы.")


@dp.message(F.text.startswith("🔴"))
async def process_shift_end_start(message: types.Message, state: FSMContext):
    await state.clear()  # на случай если человек уже был в середине другого процесса
    await message.answer("Сколько ты заработал на смене (введи только число, например: 150 или 75.5)?")
    await state.set_state(ShiftState.waiting_for_earnings)


@dp.message(ShiftState.waiting_for_earnings)
async def process_earnings(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return

    try:
        earnings = float(message.text.replace(",", "."))
        await state.update_data(earnings=earnings)
        await message.answer("Напиши важную информацию/комментарий по смене:")
        await state.set_state(ShiftState.waiting_for_info)
    except (ValueError, TypeError):
        await message.answer("Пожалуйста, введи корректное число (заработок).")


@dp.message(ShiftState.waiting_for_info)
async def process_info(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return

    await state.update_data(comment=message.text)
    await message.answer("Отправь скриншот(ы) продаж.")
    await state.set_state(ShiftState.waiting_for_screenshot)


# Буфер для сборки альбома скриншотов: когда пользователь прикрепляет несколько фото
# сразу, Telegram присылает их как отдельные апдейты с одним и тем же media_group_id.
# Мы копим все фото с одним media_group_id и через небольшую паузу отправляем один отчёт.
album_buffers: dict = {}
ALBUM_WAIT_SECONDS = 1.5


async def send_shift_report(message: types.Message, state: FSMContext, photo_ids: list):
    user = message.from_user
    now = get_moscow_time()
    data = await state.get_data()

    earnings = data.get('earnings')
    comment = data.get('comment')

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
        f"🕐 Время: {now.strftime('%d.%m.%Y %H:%M')} МСК\n"
        f"💰 Заработал: ${earnings}\n"
        f"📝 Важная инфа: {comment}"
    )

    if len(photo_ids) == 1:
        await bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=photo_ids[0], caption=text_admin, parse_mode="Markdown")
    else:
        media = [types.InputMediaPhoto(media=photo_ids[0], caption=text_admin, parse_mode="Markdown")]
        media += [types.InputMediaPhoto(media=pid) for pid in photo_ids[1:]]
        await bot.send_media_group(chat_id=ADMIN_GROUP_ID, media=media)

    await message.answer("✅ Отчет успешно отправлен руководству! Спасибо за смену.", reply_markup=get_main_keyboard(user.id))
    await state.clear()


async def _finalize_album(media_group_id: str):
    await asyncio.sleep(ALBUM_WAIT_SECONDS)
    buf = album_buffers.pop(media_group_id, None)
    if not buf:
        return  # альбом уже обработан другой задачей
    await send_shift_report(buf["message"], buf["state"], buf["photos"])


@dp.message(ShiftState.waiting_for_screenshot, F.photo)
async def process_screenshot(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    mgid = message.media_group_id

    if mgid is None:
        # Обычное одиночное фото — отправляем отчёт сразу
        await send_shift_report(message, state, [photo_id])
        return

    # Фото — часть альбома: копим все фото этого альбома
    if mgid not in album_buffers:
        album_buffers[mgid] = {"photos": [], "message": message, "state": state}
    album_buffers[mgid]["photos"].append(photo_id)
    album_buffers[mgid]["message"] = message
    asyncio.create_task(_finalize_album(mgid))


@dp.message(ShiftState.waiting_for_screenshot)
async def process_screenshot_wrong_content(message: types.Message, state: FSMContext):
    # Сюда попадают любые НЕ-фото сообщения в состоянии ожидания скриншота
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return

    await message.answer("Пожалуйста, отправь именно фото (скриншот) продаж.")


# ---------- Добавление баланса вручную (кнопкой) ----------

@dp.message(F.text.startswith("➕"))
async def process_add_balance_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        await message.answer("🔴 У вас нет прав для этой команды.")
        return
    await state.clear()
    await message.answer("Введи имя чаттера, кому добавить баланс:")
    await state.set_state(AddBalanceState.waiting_for_name)


@dp.message(AddBalanceState.waiting_for_name)
async def process_add_balance_name(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return
    await state.update_data(add_name=message.text)
    await message.answer("Введи username чаттера без @ (или отправь «-», если username нет):")
    await state.set_state(AddBalanceState.waiting_for_username)


@dp.message(AddBalanceState.waiting_for_username)
async def process_add_balance_username(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return
    raw = message.text.strip().lstrip("@")
    username = None if raw == "-" else raw
    await state.update_data(add_username=username)
    await message.answer("Введи сумму заработка (число, например 150 или 75.5):")
    await state.set_state(AddBalanceState.waiting_for_amount)


@dp.message(AddBalanceState.waiting_for_amount)
async def process_add_balance_amount(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return
    try:
        earnings = float(message.text.replace(",", "."))
    except (ValueError, TypeError):
        await message.answer("Пожалуйста, введи корректное число.")
        return
    await state.update_data(add_amount=earnings)
    await message.answer("Комментарий к этой записи (или отправь «-», если не нужен):")
    await state.set_state(AddBalanceState.waiting_for_comment)


@dp.message(AddBalanceState.waiting_for_comment)
async def process_add_balance_comment(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return

    data = await state.get_data()
    name = data['add_name']
    username = data.get('add_username')
    earnings = data['add_amount']
    comment = "" if message.text.strip() == "-" else message.text

    now = get_moscow_time()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO shifts (user_id, username, full_name, action, timestamp, earnings, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (0, username, name, "сдал", now.strftime("%Y-%m-%d %H:%M:%S"), earnings, comment)
    )
    conn.commit()
    conn.close()

    await message.answer(
        f"✅ Добавлено вручную: {name} — ${earnings:.2f}\n(попадёт в «Инфо за месяц»)",
        reply_markup=get_main_keyboard(message.from_user.id)
    )
    await state.clear()


@dp.message(F.text.in_({"📊 Инфо за month", "📊 Инфо за месяц"}))
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

    response += "\n🕘 **Последние действия на сменах:**\n"
    if not recent_actions:
        response += "История пуста.\n"
    for name, action, dt_str, earn in recent_actions:
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            dt_formatted = dt.strftime("%d.%m %H:%M")
        except ValueError:
            dt_formatted = dt_str

        if action == "принял":
            response += f"🟢 {dt_formatted} - {name} принял пост\n"
        else:
            response += f"🔴 {dt_formatted} - {name} сдал пост (Заработано: ${earn})\n"

    await message.answer(response, parse_mode="Markdown")


@dp.message(F.text.startswith("🧹"))
async def process_clear_database_request(message: types.Message):
    if message.from_user.id not in OWNER_IDS:
        await message.answer("🔴 У вас нет прав для выполнения этой команды.")
        return

    await message.answer(
        "⚠️ **ВНИМАНИЕ!** Вы собираетесь полностью очистить базу данных за месяц.\n"
        "Все балансы сотрудников и история смен будут безвозвратно удалены. Вы уверены?",
        reply_markup=get_confirm_keyboard(),
        parse_mode="Markdown"
    )


@dp.callback_query(F.data == "db_confirm_clear")
async def callback_confirm_clear(callback: types.CallbackQuery):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("🔴 Отказано в доступе.", show_alert=True)
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shifts")
    conn.commit()
    conn.close()

    await callback.message.edit_text("🧹 **База данных успешно очищена!** Все балансы за месяц и история смен сброшены до нуля.", parse_mode="Markdown")
    await callback.answer("База данных успешно очищена!")


@dp.callback_query(F.data == "db_cancel_clear")
async def callback_cancel_clear(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Очистка базы данных отменена. Данные чаттеров в безопасности.")
    await callback.answer("Действие отменено.")


# --- ПРАВИЛЬНЫЙ АСИНХРОННЫЙ ЗАПУСК ДЛЯ AIOGRAM 3 ---
async def main():
    init_db()
    print("Бот успешно запущен на московском времени (период: месяц)...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
