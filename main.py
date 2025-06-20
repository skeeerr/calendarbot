import logging
import asyncio
import sqlite3
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.utils import executor
from datetime import datetime, timedelta
import pytz
import os

from config import API_TOKEN, ADMIN_ID

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=MemoryStorage())

conn = sqlite3.connect('reminders.db')
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    reminder_text TEXT,
    remind_at TEXT,
    tz TEXT
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    timezone TEXT DEFAULT 'UTC'
)
''')
conn.commit()

class ReminderState(StatesGroup):
    waiting_for_datetime = State()
    waiting_for_text = State()

class TimezoneState(StatesGroup):
    waiting_for_timezone = State()

@dp.message_handler(commands=['start'])
async def start_handler(message: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📝 Добавить напоминание", callback_data="add_reminder"))
    kb.add(InlineKeyboardButton("📋 Мои напоминания", callback_data="list_reminders"))
    kb.add(InlineKeyboardButton("⚙️ Настройки", callback_data="settings"))
    kb.add(InlineKeyboardButton("✒️ Купить подписку", callback_data="buy_subscription"))

    await message.answer(
        "Добро пожаловать в бот-напоминалку! 🚀✨\n\n"
        "Здесь ты можешь сохранять важные дела, события и задачи, а я буду вовремя присылать тебе напоминания!\n\n"
        "📌 Как это работает?\n"
        "Добавь напоминание командой /new (или кнопкой ниже).\n"
        "Укажи дату и время.\n"
        "Получай уведомление в нужный момент!\n\n"
        "Ты также можешь:\n"
        "🔹 Просматривать свои напоминания — /list\n"
        "🔹 Удалять старые — /delete\n"
        "🔹 Настраивать время уведомлений — /settings\n\n"
        "Начни прямо сейчас, и ничего не забудь! 😉",
        reply_markup=kb
    )

@dp.callback_query_handler(lambda c: c.data == 'add_reminder')
async def process_add_reminder(callback_query: types.CallbackQuery):
    await bot.send_message(callback_query.from_user.id, "Введите дату и время напоминания в формате ГГГГ-ММ-ДД ЧЧ:ММ")
    await ReminderState.waiting_for_datetime.set()

@dp.message_handler(state=ReminderState.waiting_for_datetime)
async def reminder_get_datetime(message: types.Message, state: FSMContext):
    try:
        user_id = message.from_user.id
        cursor.execute("SELECT timezone FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        tz = pytz.timezone(row[0]) if row else pytz.UTC
        remind_time = datetime.strptime(message.text, "%Y-%m-%d %H:%M")
        remind_time = tz.localize(remind_time)
        await state.update_data(remind_time=remind_time)
        await message.answer("Теперь введите текст напоминания")
        await ReminderState.waiting_for_text.set()
    except Exception as e:
        await message.answer("Неверный формат даты. Попробуйте еще раз: ГГГГ-ММ-ДД ЧЧ:ММ")

@dp.message_handler(state=ReminderState.waiting_for_text)
async def reminder_get_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    cursor.execute("SELECT timezone FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    tz = row[0] if row else 'UTC'
    cursor.execute("INSERT INTO reminders (user_id, reminder_text, remind_at, tz) VALUES (?, ?, ?, ?)",
                   (user_id, message.text, data['remind_time'].isoformat(), tz))
    conn.commit()
    await message.answer("Напоминание сохранено!")
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == 'list_reminders')
async def list_reminders(callback_query: types.CallbackQuery):
    cursor.execute("SELECT reminder_text, remind_at FROM reminders WHERE user_id = ?", (callback_query.from_user.id,))
    reminders = cursor.fetchall()
    if not reminders:
        await bot.send_message(callback_query.from_user.id, "У вас нет активных напоминаний.")
    else:
        msg = "Ваши напоминания:\n"
        for r in reminders:
            dt = datetime.fromisoformat(r[1])
            msg += f"\n🕒 {dt.strftime('%Y-%m-%d %H:%M')} — {r[0]}"
        await bot.send_message(callback_query.from_user.id, msg)

@dp.callback_query_handler(lambda c: c.data == 'settings')
async def settings_handler(callback_query: types.CallbackQuery):
    await bot.send_message(callback_query.from_user.id, "Введите ваш часовой пояс (например, Europe/Moscow)")
    await TimezoneState.waiting_for_timezone.set()

@dp.message_handler(state=TimezoneState.waiting_for_timezone)
async def set_timezone(message: types.Message, state: FSMContext):
    try:
        pytz.timezone(message.text)
        cursor.execute("REPLACE INTO users (user_id, timezone) VALUES (?, ?)", (message.from_user.id, message.text))
        conn.commit()
        await message.answer("Часовой пояс успешно обновлен!")
        await state.finish()
    except pytz.UnknownTimeZoneError:
        await message.answer("Неверный часовой пояс. Попробуйте снова")

@dp.message_handler(commands=['broadcast'])
async def broadcast_handler(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    text = message.text.split(' ', 1)[1] if ' ' in message.text else ""
    if not text:
        await message.answer("Введите текст рассылки после команды /broadcast")
        return
    cursor.execute("SELECT DISTINCT user_id FROM reminders")
    users = cursor.fetchall()
    for u in users:
        try:
            await bot.send_message(u[0], text)
        except:
            pass
    await message.answer("Рассылка завершена.")

async def reminder_checker():  # Запуск напоминаний
    while True:
        now = datetime.utcnow()
        cursor.execute("SELECT id, user_id, reminder_text, remind_at, tz FROM reminders")
        for row in cursor.fetchall():
            r_id, user_id, text, remind_at, tz = row
            remind_time = pytz.timezone(tz).localize(datetime.fromisoformat(remind_at).replace(tzinfo=None))
            if now >= remind_time.astimezone(pytz.UTC):
                try:
                    await bot.send_message(user_id, f"Сегодня вы собирались: *{text}*", parse_mode="Markdown")
                except:
                    pass
                cursor.execute("DELETE FROM reminders WHERE id = ?", (r_id,))
        week_ago = datetime.utcnow() - timedelta(days=7)
        cursor.execute("DELETE FROM reminders WHERE remind_at < ?", (week_ago.isoformat(),))
        conn.commit()
        await asyncio.sleep(30)

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.create_task(reminder_checker())
    executor.start_polling(dp, skip_updates=True)