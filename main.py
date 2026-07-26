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
BOT_TOKEN = "8985257496:AAF8XxPVAA-CarYbnm8D3Pe0J65OLVOJco4"
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


class DolyotState(StatesGroup):
    waiting_for_earnings = State()
    waiting_for_comment = State()
    waiting_for_screenshot = State()


class EditBalanceState(StatesGroup):
    waiting_for_name = State()
    waiting_for_username = State()
    waiting_for_date = State()
    waiting_for_amount = State()
    waiting_for_comment = State()


# Тексты кнопок подменю "Инфо" — вынесены в константы, чтобы не дублировать строки
BTN_INFO_MAIN = "📊 Инфо"
BTN_INFO_MONTH = "📊 Инфо за месяц"
BTN_INFO_YEAR = "📅 Инфо за год"
BTN_BACK_MAIN = "🔙 Главное меню"

# Тексты главных кнопок меню — используются, чтобы понять,
# что пользователь хочет "выйти" из текущего процесса (например, из ввода заработка)
MENU_PREFIXES = ("🟢", "🔴", "🟠", "📊", "🧹", "✏️", "👥", "📅", "🔙")


def is_menu_button(text: str) -> bool:
    if not text:
        return False
    return any(text.startswith(p) for p in MENU_PREFIXES)


async def route_menu_button(message: types.Message, state: FSMContext):
    """Вызывается, когда пользователь нажал одну из кнопок главного меню,
    находясь в середине другого процесса (ввод заработка/комментария/скриншота/баланса).
    Сбрасывает текущий процесс и обрабатывает нажатую кнопку как обычно."""
    await state.clear()
    text = message.text
    if text == BTN_INFO_MAIN:
        await process_info_menu(message, state)
    elif text == BTN_INFO_MONTH:
        await process_statistics(message)
    elif text == BTN_INFO_YEAR:
        await process_yearly_stats(message)
    elif text == BTN_BACK_MAIN:
        await process_back_to_main(message, state)
    elif text.startswith("🟢"):
        await process_shift_start(message, state)
    elif text.startswith("🔴"):
        await process_shift_end_start(message, state)
    elif text.startswith("🟠"):
        await process_dolyot_start(message, state)
    elif text.startswith("🧹"):
        await process_clear_database_request(message)
    elif text.startswith("✏️"):
        await process_edit_balance_start(message, state)
    elif text.startswith("👥"):
        await process_participants_list(message)


# Главная клавиатура
def get_main_keyboard(user_id: int):
    buttons = [
        [types.KeyboardButton(text="🟢 Пост принял")],
        [types.KeyboardButton(text="🔴 Пост сдал")],
        [types.KeyboardButton(text="🟠 Долёт")],
        [types.KeyboardButton(text=BTN_INFO_MAIN)]
    ]
    if user_id in OWNER_IDS:
        buttons.append([types.KeyboardButton(text="✏️ Изменить баланс")])
        buttons.append([types.KeyboardButton(text="👥 Список участников")])
        buttons.append([types.KeyboardButton(text="🧹 Очистить месяц")])

    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


# Подменю "Инфо" — доступно всем
def get_info_keyboard():
    buttons = [
        [types.KeyboardButton(text=BTN_INFO_MONTH)],
        [types.KeyboardButton(text=BTN_INFO_YEAR)],
        [types.KeyboardButton(text=BTN_BACK_MAIN)]
    ]
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
    owner_note = "\n\n✅ Ты в списке владельцев." if is_owner else "\n\n⚠️ Ты НЕ в списке владельцев (нет доступа к «Очистить месяц» и «Изменить баланс»)."
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


@dp.message(F.text == BTN_INFO_MAIN)
async def process_info_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Выбери раздел:", reply_markup=get_info_keyboard())


@dp.message(F.text == BTN_BACK_MAIN)
async def process_back_to_main(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=get_main_keyboard(message.from_user.id))


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


@dp.message(F.text.startswith("🟠"))
async def process_dolyot_start(message: types.Message, state: FSMContext):
    await state.clear()  # на случай если человек уже был в середине другого процесса
    await message.answer("Сколько всего долетело (введи только число, например: 150 или 75.5)?")
    await state.set_state(DolyotState.waiting_for_earnings)


@dp.message(DolyotState.waiting_for_earnings)
async def process_dolyot_earnings(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return

    try:
        earnings = float(message.text.replace(",", "."))
        await state.update_data(dolyot_earnings=earnings)
        await message.answer("Комментарий по желанию (или отправь «-», если не нужен):")
        await state.set_state(DolyotState.waiting_for_comment)
    except (ValueError, TypeError):
        await message.answer("Пожалуйста, введи корректное число.")


@dp.message(DolyotState.waiting_for_comment)
async def process_dolyot_comment(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return

    comment = "" if message.text.strip() == "-" else message.text
    await state.update_data(dolyot_comment=comment)
    await message.answer("Отправь скриншот(ы) долёта.")
    await state.set_state(DolyotState.waiting_for_screenshot)


dolyot_album_buffers: dict = {}


async def send_dolyot_report(message: types.Message, state: FSMContext, photo_ids: list):
    user = message.from_user
    now = get_moscow_time()
    data = await state.get_data()

    earnings = data.get('dolyot_earnings')
    comment = data.get('dolyot_comment') or ""

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO shifts (user_id, username, full_name, action, timestamp, earnings, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user.id, user.username, user.full_name, "долет", now.strftime("%Y-%m-%d %H:%M:%S"), earnings, comment)
    )
    conn.commit()
    conn.close()

    text_admin = (
        f"🟠 **Долёт (ОТЧЕТ)**\n"
        f"👤 Чаттер: {user.full_name} (@{user.username})\n"
        f"🕐 Время: {now.strftime('%d.%m.%Y %H:%M')} МСК\n"
        f"💰 Сумма: ${earnings}\n"
    )
    if comment:
        text_admin += f"📝 Комментарий: {comment}"

    if len(photo_ids) == 1:
        await bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=photo_ids[0], caption=text_admin, parse_mode="Markdown")
    else:
        media = [types.InputMediaPhoto(media=photo_ids[0], caption=text_admin, parse_mode="Markdown")]
        media += [types.InputMediaPhoto(media=pid) for pid in photo_ids[1:]]
        await bot.send_media_group(chat_id=ADMIN_GROUP_ID, media=media)

    await message.answer("✅ Отчет по долёту отправлен руководству!", reply_markup=get_main_keyboard(user.id))
    await state.clear()


async def _finalize_dolyot_album(media_group_id: str):
    await asyncio.sleep(ALBUM_WAIT_SECONDS)
    buf = dolyot_album_buffers.pop(media_group_id, None)
    if not buf:
        return
    await send_dolyot_report(buf["message"], buf["state"], buf["photos"])


@dp.message(DolyotState.waiting_for_screenshot, F.photo)
async def process_dolyot_screenshot(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    mgid = message.media_group_id

    if mgid is None:
        await send_dolyot_report(message, state, [photo_id])
        return

    if mgid not in dolyot_album_buffers:
        dolyot_album_buffers[mgid] = {"photos": [], "message": message, "state": state}
    dolyot_album_buffers[mgid]["photos"].append(photo_id)
    dolyot_album_buffers[mgid]["message"] = message
    asyncio.create_task(_finalize_dolyot_album(mgid))


@dp.message(DolyotState.waiting_for_screenshot)
async def process_dolyot_screenshot_wrong_content(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return

    await message.answer("Пожалуйста, отправь именно фото (скриншот) долёта.")


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


# ---------- Изменение баланса вручную (кнопкой): плюс или минус ----------

@dp.message(F.text.startswith("✏️"))
async def process_edit_balance_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        await message.answer("🔴 У вас нет прав для этой команды.")
        return
    await state.clear()
    await message.answer("Введи имя чаттера, кому изменить баланс:")
    await state.set_state(EditBalanceState.waiting_for_name)


@dp.message(EditBalanceState.waiting_for_name)
async def process_edit_balance_name(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return
    await state.update_data(edit_name=message.text)
    await message.answer("Введи username чаттера без @ (или отправь «-», если username нет):")
    await state.set_state(EditBalanceState.waiting_for_username)


@dp.message(EditBalanceState.waiting_for_username)
async def process_edit_balance_username(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return
    raw = message.text.strip().lstrip("@")
    username = None if raw == "-" else raw
    await state.update_data(edit_username=username)
    await message.answer(
        "На какую дату записать изменение?\n"
        "Введи день и месяц в формате `ДД.ММ` (например `15.07`), или отправь «-», чтобы использовать сегодняшнюю дату.",
        parse_mode="Markdown"
    )
    await state.set_state(EditBalanceState.waiting_for_date)


@dp.message(EditBalanceState.waiting_for_date)
async def process_edit_balance_date(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return

    now = get_moscow_time()
    raw = message.text.strip()

    if raw == "-":
        chosen_dt = now
    else:
        parts = raw.split(".")
        if len(parts) != 2:
            await message.answer("Формат должен быть `ДД.ММ`, например `15.07`. Попробуй ещё раз, или отправь «-» для сегодняшней даты.", parse_mode="Markdown")
            return
        try:
            day = int(parts[0])
            month = int(parts[1])
            chosen_dt = now.replace(month=month, day=day)
        except (ValueError, TypeError):
            await message.answer("Не получилось распознать дату. Формат `ДД.ММ`, например `15.07`. Попробуй ещё раз.", parse_mode="Markdown")
            return

    await state.update_data(edit_date=chosen_dt.strftime("%Y-%m-%d %H:%M:%S"))
    await message.answer(
        "На сколько изменить баланс?\n"
        "Укажи знак: `+` чтобы прибавить, `-` чтобы убавить.\n"
        "Например: `+50` или `-20.5`",
        parse_mode="Markdown"
    )
    await state.set_state(EditBalanceState.waiting_for_amount)


@dp.message(EditBalanceState.waiting_for_amount)
async def process_edit_balance_amount(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return

    raw = message.text.strip().replace(",", ".")
    if not (raw.startswith("+") or raw.startswith("-")):
        await message.answer("Нужно указать знак в начале: `+50` чтобы прибавить или `-20` чтобы убавить.", parse_mode="Markdown")
        return

    sign = 1 if raw.startswith("+") else -1
    try:
        amount = float(raw[1:])
    except (ValueError, TypeError):
        await message.answer("Пожалуйста, введи корректное число, например `+50` или `-20.5`.", parse_mode="Markdown")
        return

    await state.update_data(edit_delta=sign * amount)
    await message.answer("Комментарий к этому изменению (или отправь «-», если не нужен):")
    await state.set_state(EditBalanceState.waiting_for_comment)


@dp.message(EditBalanceState.waiting_for_comment)
async def process_edit_balance_comment(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return

    data = await state.get_data()
    name = data['edit_name']
    username = data.get('edit_username')
    delta = data['edit_delta']
    date_str = data['edit_date']
    comment = "" if message.text.strip() == "-" else message.text

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO shifts (user_id, username, full_name, action, timestamp, earnings, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (0, username, name, "корректировка", date_str, delta, comment)
    )
    conn.commit()
    conn.close()

    sign_text = f"+${delta:.2f}" if delta >= 0 else f"-${abs(delta):.2f}"
    date_display = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").strftime("%d.%m")
    await message.answer(
        f"✅ Баланс изменён: {name} — {sign_text} (дата {date_display})\n(попадёт в «Инфо за месяц»)",
        reply_markup=get_main_keyboard(message.from_user.id)
    )
    await state.clear()


@dp.message(F.text == BTN_INFO_MONTH)
async def process_statistics(message: types.Message):
    now = get_moscow_time()
    # Границы текущего календарного месяца и середины (для двух периодов ЗП: 1-15 и 16-конец)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        next_month_start = month_start.replace(year=now.year + 1, month=1)
    else:
        next_month_start = month_start.replace(month=now.month + 1)

    month_start_str = month_start.strftime("%Y-%m-%d %H:%M:%S")
    next_month_start_str = next_month_start.strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, full_name, action, timestamp, earnings, comment
        FROM shifts
        WHERE timestamp >= ? AND timestamp < ?
        ORDER BY full_name, timestamp ASC
    ''', (month_start_str, next_month_start_str))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer(f"📊 **ОТЧЕТ ЗА {now.strftime('%m.%Y')} (МСК)**\n\nЗа текущий месяц данных пока нет.", parse_mode="Markdown")
        return

    # Группируем все записи по каждому чаттеру, отдельно считая период 1-15 и 16-конец месяца
    chatters = {}
    order = []
    for user_id, username, full_name, action, dt_str, earnings, comment in rows:
        key = (user_id or 0, full_name)
        if key not in chatters:
            chatters[key] = {
                "username": username, "full_name": full_name,
                "total_p1": 0.0, "total_p2": 0.0, "actions": []
            }
            order.append(key)
        if username and not chatters[key]["username"]:
            chatters[key]["username"] = username

        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            dt_formatted = dt.strftime("%d.%m %H:%M")
            date_short = dt.strftime("%d.%m")
            day = dt.day
        except ValueError:
            dt_formatted = dt_str
            date_short = dt_str
            day = 1  # fallback, если формат даты неожиданный

        period = 1 if day <= 15 else 2
        if action in ("сдал", "долет", "корректировка") and earnings:
            if period == 1:
                chatters[key]["total_p1"] += earnings
            else:
                chatters[key]["total_p2"] += earnings

        chatters[key]["actions"].append((dt_formatted, date_short, action, earnings, comment, period))

    parts = [f"📊 **ОТЧЕТ ЗА {now.strftime('%m.%Y')} (МСК)**\n"]

    for key in order:
        info = chatters[key]
        user_link = f"@{info['username']}" if info['username'] else "нет юзернейма"
        total = info["total_p1"] + info["total_p2"]
        block = (
            f"\n👤 **{info['full_name']}** ({user_link})\n"
            f"   💵 1–15 число: **${info['total_p1']:.2f}**\n"
            f"   💵 16–конец месяца: **${info['total_p2']:.2f}**\n"
            f"   💰 Итого за месяц: **${total:.2f}**\n"
        )
        for dt_formatted, date_short, action, earnings, comment, period in info["actions"]:
            if action == "принял":
                block += f"   🟢 {dt_formatted} — принял пост\n"
            elif action == "долет":
                block += f"   🟠 {dt_formatted} — долёт (${earnings:.2f})\n"
            elif action == "корректировка":
                comment_part = f" {comment}" if comment else ""
                block += f"   ✏️ Изменение баланса. {date_short}.{comment_part}\n"
            else:
                block += f"   🔴 {dt_formatted} — сдал (${earnings:.2f})\n"

        # Telegram режет сообщения на 4096 символов — если ответ разрастается, шлём частями
        if len(parts[-1]) + len(block) > 3800:
            parts.append(block)
        else:
            parts[-1] += block

    for part in parts:
        await message.answer(part, parse_mode="Markdown")


MONTH_NAMES_RU = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]


@dp.message(F.text == BTN_INFO_YEAR)
async def process_yearly_stats(message: types.Message):
    now = get_moscow_time()
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    year_end = year_start.replace(year=now.year + 1)

    year_start_str = year_start.strftime("%Y-%m-%d %H:%M:%S")
    year_end_str = year_end.strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, full_name, timestamp, earnings
        FROM shifts
        WHERE timestamp >= ? AND timestamp < ? AND action IN ('сдал', 'долет', 'корректировка')
        ORDER BY full_name
    ''', (year_start_str, year_end_str))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer(f"📅 **ГОДОВОЙ ОТЧЁТ ЗА {now.year}**\n\nЗа этот год данных пока нет.", parse_mode="Markdown")
        return

    # Группируем заработок по каждому чаттеру и по месяцам этого года
    chatters = {}
    order = []
    for user_id, username, full_name, dt_str, earnings in rows:
        key = (user_id or 0, full_name)
        if key not in chatters:
            chatters[key] = {"username": username, "full_name": full_name, "months": [0.0] * 12}
            order.append(key)
        if username and not chatters[key]["username"]:
            chatters[key]["username"] = username

        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            month_idx = dt.month - 1
        except ValueError:
            continue

        if earnings:
            chatters[key]["months"][month_idx] += earnings

    parts = [f"📅 **ГОДОВОЙ ОТЧЁТ ЗА {now.year} (по месяцам)**\n"]

    for key in order:
        info = chatters[key]
        user_link = f"@{info['username']}" if info['username'] else "нет юзернейма"
        block = f"\n👤 **{info['full_name']}** ({user_link})\n"
        for i, m_total in enumerate(info["months"]):
            block += f"   {MONTH_NAMES_RU[i]}: ${m_total:.2f}\n"

        # Telegram режет сообщения на 4096 символов — если ответ разрастается, шлём частями
        if len(parts[-1]) + len(block) > 3800:
            parts.append(block)
        else:
            parts[-1] += block

    for part in parts:
        await message.answer(part, parse_mode="Markdown")


@dp.message(F.text.startswith("👥"))
async def process_participants_list(message: types.Message):
    if message.from_user.id not in OWNER_IDS:
        await message.answer("🔴 У вас нет прав для просмотра этого списка.")
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT username, full_name,
               SUM(CASE WHEN action IN ('сдал', 'долет', 'корректировка') THEN earnings ELSE 0 END) as total,
               MAX(timestamp) as last_seen
        FROM shifts
        GROUP BY full_name
        ORDER BY full_name COLLATE NOCASE
    ''')
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer("👥 **СПИСОК УЧАСТНИКОВ**\n\nПока никто не отмечался.", parse_mode="Markdown")
        return

    response = "👥 **СПИСОК УЧАСТНИКОВ**\n\n"
    for username, full_name, total, last_seen in rows:
        user_link = f"@{username}" if username else "нет юзернейма"
        try:
            last_dt = datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S")
            last_str = last_dt.strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            last_str = last_seen or "—"
        response += f"• **{full_name}** ({user_link})\n   Всего заработано: ${total:.2f} | Последняя активность: {last_str}\n\n"

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
