import os
import sqlite3
import asyncio
import logging
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
ADMIN_GROUP_ID = -1004458669568
OWNER_IDS = {963341281, 8207913329}  # список ID владельцев бота (видят "Очистить месяц" и "Добавить баланс")
# ================================================

# ---------- Логирование ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DB_NAME = "reports.db"


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
    waiting_for_username = State()
    waiting_for_date = State()
    waiting_for_amount = State()
    waiting_for_comment = State()


class ClearUserState(StatesGroup):
    waiting_for_username = State()


# Тексты кнопок подменю "Инфо" — вынесены в константы, чтобы не дублировать строки
BTN_INFO_MAIN = "📊 Инфо"
BTN_INFO_MONTH = "📊 Инфо за месяц"
BTN_INFO_YEAR = "📅 Инфо за год"
BTN_BACK_MAIN = "🔙 Главное меню"

# Тексты кнопок подменю "Очистка"
BTN_CLEAR_MAIN = "🧹 Очистка"
BTN_CLEAR_MONTH = "🧹 Очистить месяц"
BTN_CLEAR_YEAR = "🧹 Очистить год"
BTN_CLEAR_USER = "🗑 Удалить участника"

# Тексты главных кнопок меню — используются, чтобы понять,
# что пользователь хочет "выйти" из текущего процесса (например, из ввода заработка)
MENU_PREFIXES = ("🟢", "🔴", "🟠", "📊", "🧹", "✏️", "👥", "📅", "🔙", "🗑")


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
    elif text == BTN_CLEAR_MAIN:
        await process_clear_menu(message, state)
    elif text == BTN_CLEAR_MONTH:
        await process_clear_month_request(message)
    elif text == BTN_CLEAR_YEAR:
        await process_clear_year_request(message)
    elif text == BTN_CLEAR_USER:
        await process_clear_user_start(message, state)
    elif text == BTN_BACK_MAIN:
        await process_back_to_main(message, state)
    elif text.startswith("🟢"):
        await process_shift_start(message, state)
    elif text.startswith("🔴"):
        await process_shift_end_start(message, state)
    elif text.startswith("🟠"):
        await process_dolyot_start(message, state)
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
    buttons.append([types.KeyboardButton(text=BTN_CLEAR_MAIN)])
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


# Подменю "Инфо" — доступно всем
def get_info_keyboard():
    buttons = [
        [types.KeyboardButton(text=BTN_INFO_MONTH)],
        [types.KeyboardButton(text=BTN_INFO_YEAR)],
        [types.KeyboardButton(text=BTN_BACK_MAIN)]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


# Подменю "Очистка" — только для владельцев
def get_clear_menu_keyboard():
    buttons = [
        [types.KeyboardButton(text=BTN_CLEAR_MONTH)],
        [types.KeyboardButton(text=BTN_CLEAR_YEAR)],
        [types.KeyboardButton(text=BTN_CLEAR_USER)],
        [types.KeyboardButton(text=BTN_BACK_MAIN)]
    ]
    return types.ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


# Inline-клавиатура для подтверждения очистки. scope: "month" или "year"
def get_confirm_keyboard(scope: str):
    buttons = [
        [
            types.InlineKeyboardButton(text="✅ Да, очистить", callback_data=f"db_confirm_clear:{scope}"),
            types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"db_cancel_clear:{scope}")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


# Inline-клавиатура для подтверждения удаления конкретного участника
def get_confirm_user_keyboard(username: str):
    buttons = [
        [
            types.InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"db_confirm_clear_user:{username}"),
            types.InlineKeyboardButton(text="❌ Отмена", callback_data="db_cancel_clear_user")
        ]
    ]
    return types.InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    is_owner = message.from_user.id in OWNER_IDS
    owner_note = "\n\n✅ Ты в списке владельцев." if is_owner else "\n\n⚠️ Ты НЕ в списке владельцев (нет доступа к «Очистка» и «Изменить баланс»)."
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


@dp.message(F.text == BTN_CLEAR_MAIN)
async def process_clear_menu(message: types.Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        await message.answer("🔴 У вас нет прав для этого раздела.")
        return
    await state.clear()
    await message.answer("Выбери, что очистить:", reply_markup=get_clear_menu_keyboard())


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

    text_admin = f"🟢 **Пост принял**\n👤 Чаттер: @{user.username}\n🕐 Время: {now.strftime('%d.%m.%Y %H:%M')} МСК"

    try:
        await bot.send_message(chat_id=ADMIN_GROUP_ID, text=text_admin, parse_mode="Markdown")
        await message.answer("✅ Вход на смену зафиксирован! Удачной работы.")
    except Exception as e:
        logger.error(f"Не удалось отправить в группу (Пост принял) для @{user.username}: {e}")
        await message.answer(
            "⚠️ Смена зафиксирована, но не удалось отправить уведомление в группу (техническая ошибка). "
            "Сообщи об этом администратору."
        )


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
    await message.answer("Отправь скриншот(ы) долёта (или «-», если скриншота нет).")
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
        f"👤 Чаттер: @{user.username}\n"
        f"🕐 Время: {now.strftime('%d.%m.%Y %H:%M')} МСК\n"
        f"💰 Сумма: ${earnings}\n"
    )
    if comment:
        text_admin += f"📝 Комментарий: {comment}"

    try:
        if not photo_ids:
            await bot.send_message(chat_id=ADMIN_GROUP_ID, text=text_admin, parse_mode="Markdown")
        elif len(photo_ids) == 1:
            await bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=photo_ids[0], caption=text_admin, parse_mode="Markdown")
        else:
            media = [types.InputMediaPhoto(media=photo_ids[0], caption=text_admin, parse_mode="Markdown")]
            media += [types.InputMediaPhoto(media=pid) for pid in photo_ids[1:]]
            await bot.send_media_group(chat_id=ADMIN_GROUP_ID, media=media)
        await message.answer("✅ Отчет по долёту отправлен руководству!", reply_markup=get_main_keyboard(user.id))
    except Exception as e:
        logger.error(f"Не удалось отправить отчёт (долёт) в группу для @{user.username}: {e}")
        await message.answer(
            "⚠️ Долёт записан, но отчёт не удалось отправить в группу (техническая ошибка). "
            "Напиши об этом администратору.",
            reply_markup=get_main_keyboard(user.id)
        )

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
    if message.text and message.text.strip() == "-":
        await send_dolyot_report(message, state, [])
        return
    await message.answer("Пожалуйста, отправь фото (скриншот) долёта, либо «-», если скриншота нет.")


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
    await message.answer("Отправь скриншот(ы) продаж (или «-», если скриншота нет).")
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
        f"👤 Чаттер: @{user.username}\n"
        f"🕐 Время: {now.strftime('%d.%m.%Y %H:%M')} МСК\n"
        f"💰 Заработал: ${earnings}\n"
        f"📝 Важная инфа: {comment}"
    )

    try:
        if not photo_ids:
            await bot.send_message(chat_id=ADMIN_GROUP_ID, text=text_admin, parse_mode="Markdown")
        elif len(photo_ids) == 1:
            await bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=photo_ids[0], caption=text_admin, parse_mode="Markdown")
        else:
            media = [types.InputMediaPhoto(media=photo_ids[0], caption=text_admin, parse_mode="Markdown")]
            media += [types.InputMediaPhoto(media=pid) for pid in photo_ids[1:]]
            await bot.send_media_group(chat_id=ADMIN_GROUP_ID, media=media)
        await message.answer("✅ Отчет успешно отправлен руководству! Спасибо за смену.", reply_markup=get_main_keyboard(user.id))
    except Exception as e:
        logger.error(f"Не удалось отправить отчёт (сдал) в группу для @{user.username}: {e}")
        await message.answer(
            "⚠️ Заработок записан, но отчёт не удалось отправить в группу (техническая ошибка). "
            "Напиши об этом администратору.",
            reply_markup=get_main_keyboard(user.id)
        )

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
    if message.text and message.text.strip() == "-":
        await send_shift_report(message, state, [])
        return
    await message.answer("Пожалуйста, отправь фото (скриншот) продаж, либо «-», если скриншота нет.")


# ---------- Изменение баланса вручную (кнопкой): плюс или минус ----------
@dp.message(F.text.startswith("✏️"))
async def process_edit_balance_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        await message.answer("🔴 У вас нет прав для этой команды.")
        return
    await state.clear()
    await message.answer("Введи username чаттера без @, кому изменить баланс:")
    await state.set_state(EditBalanceState.waiting_for_username)


@dp.message(EditBalanceState.waiting_for_username)
async def process_edit_balance_username(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return
    username = message.text.strip().lstrip("@")
    if not username:
        await message.answer("Username не может быть пустым. Введи username без @.")
        return
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
    username = data['edit_username']
    delta = data['edit_delta']
    date_str = data['edit_date']
    comment = "" if message.text.strip() == "-" else message.text

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO shifts (user_id, username, full_name, action, timestamp, earnings, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (0, username, username, "корректировка", date_str, delta, comment)
    )
    conn.commit()
    conn.close()

    sign_text = f"+${delta:.2f}" if delta >= 0 else f"-${abs(delta):.2f}"
    date_display = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").strftime("%d.%m")
    await message.answer(
        f"✅ Баланс изменён: @{username} — {sign_text} (дата {date_display})\n(попадёт в «Инфо за месяц»)",
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
        SELECT user_id, username, action, timestamp, earnings, comment
        FROM shifts
        WHERE timestamp >= ? AND timestamp < ?
        ORDER BY timestamp ASC
    ''', (month_start_str, next_month_start_str))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer(f"📊 **ОТЧЕТ ЗА {now.strftime('%m.%Y')} (МСК)**\n\nЗа текущий месяц данных пока нет.", parse_mode="Markdown")
        return

    # Группируем все записи по username каждого чаттера (единственный идентификатор,
    # чтобы один и тот же человек не раздваивался из-за разных сохранённых имён)
    chatters = {}
    order = []
    for user_id, username, action, dt_str, earnings, comment in rows:
        key = username.strip().lower() if username else f"nouser_{user_id or 0}"
        if key not in chatters:
            chatters[key] = {
                "username": username, "user_id": user_id,
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
    order.sort(key=lambda k: (chatters[k]['username'] or '').lower())

    for key in order:
        info = chatters[key]
        user_link = f"@{info['username']}" if info['username'] else f"без username (ID {info['user_id']})"
        total = info["total_p1"] + info["total_p2"]
        block = (
            f"\n👤 **{user_link}**\n"
            f"   💵 1–15 число: **${info['total_p1']:.2f}**\n"
            f"   💵 16–конец месяца: **${info['total_p2']:.2f}**\n"
            f"   💰 Итого за месяц: **${total:.2f}**\n"
        )
        for dt_formatted, date_short, action, earnings, comment, period in info["actions"]:
            if action == "принял":
                block += f"     🟢 {dt_formatted} — принял пост\n"
            elif action == "долет":
                block += f"     🟠 {dt_formatted} — долёт (${earnings:.2f})\n"
            elif action == "корректировка":
                comment_part = f" {comment}" if comment else ""
                block += f"     ✏️ Изменение баланса. {date_short}.{comment_part}\n"
            else:
                block += f"     🔴 {dt_formatted} — сдал (${earnings:.2f})\n"

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
        SELECT user_id, username, timestamp, earnings
        FROM shifts
        WHERE timestamp >= ? AND timestamp < ? AND action IN ('сдал', 'долет', 'корректировка')
        ORDER BY timestamp ASC
    ''', (year_start_str, year_end_str))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer(f"📅 **ГОДОВОЙ ОТЧЁТ ЗА {now.year}**\n\nЗа этот год данных пока нет.", parse_mode="Markdown")
        return

    # Группируем заработок по username каждого чаттера и по месяцам этого года
    chatters = {}
    order = []
    for user_id, username, dt_str, earnings in rows:
        key = username.strip().lower() if username else f"nouser_{user_id or 0}"
        if key not in chatters:
            chatters[key] = {"username": username, "user_id": user_id, "months": [0.0] * 12}
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
    order.sort(key=lambda k: (chatters[k]['username'] or '').lower())

    for key in order:
        info = chatters[key]
        user_link = f"@{info['username']}" if info['username'] else f"без username (ID {info['user_id']})"
        block = f"\n👤 **{user_link}**\n"
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
        SELECT user_id, username, action, timestamp, earnings
        FROM shifts
        ORDER BY timestamp ASC
    ''')
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await message.answer("👥 **СПИСОК УЧАСТНИКОВ**\n\nПока никто не отмечался.", parse_mode="Markdown")
        return

    # Группируем по username — единственному идентификатору участника
    chatters = {}
    order = []
    for user_id, username, action, dt_str, earnings in rows:
        key = username.strip().lower() if username else f"nouser_{user_id or 0}"
        if key not in chatters:
            chatters[key] = {"username": username, "user_id": user_id, "total": 0.0, "last_seen": dt_str}
            order.append(key)
        if username and not chatters[key]["username"]:
            chatters[key]["username"] = username
        if action in ("сдал", "долет", "корректировка") and earnings:
            chatters[key]["total"] += earnings
        chatters[key]["last_seen"] = dt_str  # rows идут по возрастанию времени, так что последняя запись — самая свежая

    order.sort(key=lambda k: (chatters[k]['username'] or '').lower())

    response = "👥 **СПИСОК УЧАСТНИКОВ**\n\n"
    for key in order:
        info = chatters[key]
        user_link = f"@{info['username']}" if info['username'] else f"без username (ID {info['user_id']})"
        try:
            last_dt = datetime.strptime(info["last_seen"], "%Y-%m-%d %H:%M:%S")
            last_str = last_dt.strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            last_str = info["last_seen"] or "—"
        response += f"• **{user_link}**\n   Всего заработано: ${info['total']:.2f} | Последняя активность: {last_str}\n\n"

    await message.answer(response, parse_mode="Markdown")


@dp.message(F.text == BTN_CLEAR_MONTH)
async def process_clear_month_request(message: types.Message):
    if message.from_user.id not in OWNER_IDS:
        await message.answer("🔴 У вас нет прав для выполнения этой команды.")
        return
    await message.answer(
        "⚠️ **ВНИМАНИЕ!** Вы собираетесь очистить данные за ТЕКУЩИЙ МЕСЯЦ.\n"
        "Все действия (принял/сдал/долёт/корректировки) за этот месяц будут безвозвратно удалены. Вы уверены?",
        reply_markup=get_confirm_keyboard("month"),
        parse_mode="Markdown"
    )


@dp.message(F.text == BTN_CLEAR_YEAR)
async def process_clear_year_request(message: types.Message):
    if message.from_user.id not in OWNER_IDS:
        await message.answer("🔴 У вас нет прав для выполнения этой команды.")
        return
    await message.answer(
        "⚠️ **ВНИМАНИЕ!** Вы собираетесь очистить данные за ТЕКУЩИЙ ГОД (все 12 месяцев).\n"
        "Все действия за этот год будут безвозвратно удалены. Вы уверены?",
        reply_markup=get_confirm_keyboard("year"),
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("db_confirm_clear:"))
async def callback_confirm_clear(callback: types.CallbackQuery):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("🔴 Отказано в доступе.", show_alert=True)
        return

    scope = callback.data.split(":", 1)[1]
    now = get_moscow_time()

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if scope == "month":
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month == 12:
            period_end = period_start.replace(year=now.year + 1, month=1)
        else:
            period_end = period_start.replace(month=now.month + 1)
        scope_label = "за текущий месяц"
    else:  # "year"
        period_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        period_end = period_start.replace(year=now.year + 1)
        scope_label = "за текущий год"

    cursor.execute(
        "DELETE FROM shifts WHERE timestamp >= ? AND timestamp < ?",
        (period_start.strftime("%Y-%m-%d %H:%M:%S"), period_end.strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()

    await callback.message.edit_text(f"🧹 **Данные {scope_label} успешно очищены!**", parse_mode="Markdown")
    await callback.answer("Очищено!")


@dp.callback_query(F.data.startswith("db_cancel_clear:"))
async def callback_cancel_clear(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Очистка отменена. Данные чаттеров в безопасности.")
    await callback.answer("Действие отменено.")


@dp.message(F.text == BTN_CLEAR_USER)
async def process_clear_user_start(message: types.Message, state: FSMContext):
    if message.from_user.id not in OWNER_IDS:
        await message.answer("🔴 У вас нет прав для этой команды.")
        return
    await state.clear()
    await message.answer("Введи username участника без @, которого нужно полностью удалить из всей статистики:")
    await state.set_state(ClearUserState.waiting_for_username)


@dp.message(ClearUserState.waiting_for_username)
async def process_clear_user_username(message: types.Message, state: FSMContext):
    if is_menu_button(message.text):
        await route_menu_button(message, state)
        return
    username = message.text.strip().lstrip("@")
    if not username:
        await message.answer("Username не может быть пустым. Введи username без @.")
        return
    await state.clear()
    await message.answer(
        f"⚠️ **ВНИМАНИЕ!** Вы собираетесь полностью удалить участника @{username} из всей статистики "
        f"(все месяцы и годы, без возможности восстановить). Вы уверены?",
        reply_markup=get_confirm_user_keyboard(username),
        parse_mode="Markdown"
    )


@dp.callback_query(F.data.startswith("db_confirm_clear_user:"))
async def callback_confirm_clear_user(callback: types.CallbackQuery):
    if callback.from_user.id not in OWNER_IDS:
        await callback.answer("🔴 Отказано в доступе.", show_alert=True)
        return

    username = callback.data.split(":", 1)[1]

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM shifts WHERE LOWER(username) = LOWER(?)", (username,))
    conn.commit()
    conn.close()

    await callback.message.edit_text(f"🗑 **Участник @{username} полностью удалён из статистики.**", parse_mode="Markdown")
    await callback.answer("Удалено!")


@dp.callback_query(F.data == "db_cancel_clear_user")
async def callback_cancel_clear_user(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Удаление отменено.")
    await callback.answer("Действие отменено.")


# ---------- Обработчик "потерянных" сообщений ----------
# Если фото пришло, а бот не ожидает его ни в одном состоянии (например,
# состояние стёрлось из-за перезапуска бота), это фото раньше просто пропадало
# без следа. Теперь бот честно сообщит об этом и попросит начать заново,
# а в bot.log останется запись для диагностики.
@dp.message(F.photo)
async def process_lost_photo(message: types.Message, state: FSMContext):
    logger.warning(
        f"Получено фото вне ожидаемого состояния от @{message.from_user.username} (id {message.from_user.id})"
    )
    await message.answer(
        "⚠️ Я не ожидал сейчас скриншот — похоже, процесс отчёта прервался (например, из-за перезапуска бота).\n"
        "Пожалуйста, начни заново: нажми «🔴 Пост сдал» или «🟠 Долёт».",
        reply_markup=get_main_keyboard(message.from_user.id)
    )


# --- ПРАВИЛЬНЫЙ АСИНХРОННЫЙ ЗАПУСК ДЛЯ AIOGRAM 3 ---
async def main():
    init_db()
    logger.info("Бот успешно запущен на московском времени (период: месяц)...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
