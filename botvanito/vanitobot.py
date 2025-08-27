import os
import re
import logging
import asyncio
import time
import random
import socket
from typing import Dict, Optional, List
import telegram
from telegram.error import RetryAfter, NetworkError
from dotenv import load_dotenv  # Добавьте этот импорт
from telegram.error import TimedOut
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters
)
from datetime import datetime, timedelta
from telegram.constants import ChatAction
import pytz
import json

# Загружаем переменные из .env файла
load_dotenv()

# Получаем токен из переменных окружения
TOKEN = os.getenv('BOT_TOKEN')

# Проверяем, что токен загружен
if not TOKEN:
    raise ValueError("Токен бота не найден! Проверьте файл .env")

# Минимальное логирование - только ошибки и критические проблемы
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING  # Только WARNING и ERROR
)

logger = logging.getLogger(__name__)

# Сильно ограничиваем шумные модули
logging.getLogger("httpx").setLevel(logging.ERROR)          # Только ошибки HTTP
logging.getLogger("httpcore").setLevel(logging.ERROR)       # Только ошибки HTTP core
logging.getLogger("apscheduler").setLevel(logging.ERROR)    # Только ошибки планировщика
logging.getLogger("telegram.ext").setLevel(logging.WARNING) # Предупреждения и ошибки Telegram

# Дополнительно можно отключить совсем ненужные модули
logging.getLogger("asyncio").setLevel(logging.ERROR)
logging.getLogger("telegram.bot").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.dispatcher").setLevel(logging.WARNING)

# Разрешаем INFO только для нашего основного логгера
logger.setLevel(logging.INFO)

socket.setdefaulttimeout(30)

class BananaTracker:
    BANANA_COOLDOWN = 60 * 180
    @staticmethod
    def can_get_banana(user_id: str) -> bool:
        """Проверяет, можно ли получить банан (прошел ли кулдаун)"""
        BananaTracker._init_user(user_id)
        last_time = BANANA_STATS[user_id].get('last_banana', 0)
        return time.time() - last_time >= BananaTracker.BANANA_COOLDOWN

    @staticmethod
    def get_remaining_cooldown(user_id: str) -> str:
        """Возвращает оставшееся время кулдауна в формате HH:MM:SS"""
        BananaTracker._init_user(user_id)
        last_time = BANANA_STATS[user_id].get('last_banana', 0)
        remaining = max(0, BananaTracker.BANANA_COOLDOWN - (time.time() - last_time))
        
        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        seconds = int(remaining % 60)
        
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def initialize():
        """Инициализирует статистику при старте бота"""
        try:
            if os.path.exists(BANANA_STATS_FILE):
                with open(BANANA_STATS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Ошибка загрузки banana_stats.json: {e}")
            return {}

    @staticmethod
    def save_stats():
        """Сохраняет статистику в JSON-файл"""
        try:
            with open(BANANA_STATS_FILE, 'w', encoding='utf-8') as f:
                json.dump(BANANA_STATS, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Ошибка сохранения статистики: {e}")

    @staticmethod
    def _init_user(user_id: str):
        """Инициализирует все необходимые поля пользователя"""
        if user_id not in BANANA_STATS:
            BANANA_STATS[user_id] = {
                'bananas': 0,
                'total_earned': 0,
                'wins': 0,
                'losses': 0,
                'achievements': [],
                'current_streak': 0,
                'max_streak': 0,
                'flags': [],
                'last_banana': 0  # Добавляем поле для хранения времени последнего получения банана
            }
        else:
            # Добавляем отсутствующие поля для существующих пользователей
            defaults = {
                'bananas': 0,
                'total_earned': BANANA_STATS[user_id].get('bananas', 0),
                'wins': 0,
                'losses': 0,
                'achievements': [],
                'current_streak': 0,
                'max_streak': 0,
                'flags': [],
                'last_banana': 0
            }
            for key, value in defaults.items():
                if key not in BANANA_STATS[user_id]:
                    BANANA_STATS[user_id][key] = value
        
        BananaTracker.save_stats()

    @staticmethod
    def add_bananas(user_id: str, amount: int) -> int:
        """Добавляет бананы и возвращает новый баланс"""
        BananaTracker._init_user(user_id)
        BANANA_STATS[user_id]['bananas'] += amount
        if amount > 0:
            BANANA_STATS[user_id]['total_earned'] += amount
        BananaTracker.save_stats()
        return BANANA_STATS[user_id]['bananas']

    @staticmethod
    def update_streak(user_id: str, is_win: bool):
        """Обновляет игровой стрик (только победы/поражения)"""
        BananaTracker._init_user(user_id)
        
        if is_win:
            BANANA_STATS[user_id]['current_streak'] += 1
            BANANA_STATS[user_id]['wins'] += 1
            
            # Обновляем максимальный стрик
            if BANANA_STATS[user_id]['current_streak'] > BANANA_STATS[user_id]['max_streak']:
                BANANA_STATS[user_id]['max_streak'] = BANANA_STATS[user_id]['current_streak']
        else:
            BANANA_STATS[user_id]['current_streak'] = 0  # Полный сброс при проигрыше
            BANANA_STATS[user_id]['losses'] += 1
        
        BananaTracker.save_stats()

    @staticmethod
    def check_achievements(user_id: str):
        """Проверяет и выдает достижения по текущему стрику"""
        BananaTracker._init_user(user_id)
        streak = BANANA_STATS[user_id]['current_streak']
        achievements = BANANA_STATS[user_id]['achievements']
        
        for wins, data in ACHIEVEMENTS.items():
            if wins <= streak and data['name'] not in achievements:
                achievements.append(data['name'])
                BananaTracker.add_bananas(user_id, data['reward'])
                return data['msg']
        return None

    def check_streak_achievements(self, user_id):
            user = self.get_user(user_id)  # Получаем данные пользователя
            if not user:
                return []

            new_achievements = []
            for wins, data in ACHIEVEMENTS["streak"].items():
                if user['current_streak'] >= wins and data['name'] not in user['achievements']:
                    # Добавляем достижение
                    user['achievements'].append(data['name'])
                    user['bananas'] += data['reward']
                    new_achievements.append(data['msg'])
            
            if new_achievements:
                self.save_user(user_id, user)  # Сохраняем обновленные данные
            
            return new_achievements

    @staticmethod
    def check_all_achievements(user_id: str):
        """Проверяет все типы достижений"""
        user = BananaTracker.get_stats(user_id)
        if not user:
            return []
        
        unlocked = []
        
        # Проверка стриков
        for wins, data in ACHIEVEMENTS["streak"].items():
            if user['current_streak'] >= wins and data['name'] not in user['achievements']:
                unlocked.append(data)
        
        # Проверка коллекции
        for amount, data in ACHIEVEMENTS["collection"].items():
            if user['total_earned'] >= amount and data['name'] not in user['achievements']:
                unlocked.append(data)
        
        # Проверка специальных
        for ach_id, data in ACHIEVEMENTS["special"].items():
            if data['name'] not in user['achievements']:
                if ach_id == "diamond" and user.get('diamond_bananas', 0) > 0:
                    unlocked.append(data)
                elif ach_id == "event_winner" and user.get('event_wins', 0) > 0:
                    unlocked.append(data)
        
        return unlocked


    @staticmethod
    def check_achievements(user_id: str):
        """Проверяет и выдает достижения по текущему стрику"""
        BananaTracker._init_user(user_id)
        streak = BANANA_STATS[user_id]['current_streak']
        achievements = BANANA_STATS[user_id]['achievements']
        
        # Исправленная проверка достижений
        for wins, data in ACHIEVEMENTS.get("streak", {}).items():
            try:
                wins_int = int(wins)
                if wins_int <= streak and data['name'] not in achievements:
                    achievements.append(data['name'])
                    BananaTracker.add_bananas(user_id, data['reward'])
                    return data['msg']
            except (ValueError, KeyError):
                continue
        return None

    @staticmethod
    def unlock_achievement(user_id: str, achievement_data: dict):
        BananaTracker._init_user(user_id)
        if achievement_data['name'] not in BANANA_STATS[user_id]['achievements']:
            BANANA_STATS[user_id]['achievements'].append(achievement_data['name'])
            if achievement_data.get('reward', 0) > 0:
                BananaTracker.add_bananas(user_id, achievement_data['reward'])
            BananaTracker.save_stats()
            return achievement_data.get('msg', f"Получено достижение: {achievement_data['name']}")
        return None

    @staticmethod
    def migrate_all_users():
        """Переносит старые данные пользователей в новую структуру"""
        global BANANA_STATS  # Добавляем эту строку для доступа к глобальной переменной
        
        try:
            if BANANA_STATS is None:  # Добавляем проверку на None
                BANANA_STATS = {}
                
            for user_id in list(BANANA_STATS.keys()):
                # Если данные пользователя - строка (старый формат)
                if isinstance(BANANA_STATS[user_id], str):
                    try:
                        # Пробуем преобразовать строку в число (бананы)
                        bananas = int(BANANA_STATS[user_id])
                        BANANA_STATS[user_id] = {
                            'bananas': bananas,
                            'total_earned': bananas,
                            'wins': 0,
                            'losses': 0,
                            'achievements': [],
                            'current_streak': 0,
                            'max_streak': 0,
                            'flags': []
                        }
                    except ValueError:
                        # Если не число - создаём пустую запись
                        BANANA_STATS[user_id] = {
                            'bananas': 0,
                            'total_earned': 0,
                            'wins': 0,
                            'losses': 0,
                            'achievements': [],
                            'current_streak': 0,
                            'max_streak': 0,
                            'flags': []
                        }
                # Если данные уже в новом формате - просто добавляем недостающие поля
                elif isinstance(BANANA_STATS[user_id], dict):
                    defaults = {
                        'bananas': 0,
                        'total_earned': BANANA_STATS[user_id].get('bananas', 0),
                        'wins': 0,
                        'losses': 0,
                        'achievements': [],
                        'current_streak': 0,
                        'max_streak': 0,
                        'flags': []
                    }
                    for key, value in defaults.items():
                        if key not in BANANA_STATS[user_id]:
                            BANANA_STATS[user_id][key] = value

            BananaTracker.save_stats()
            logger.info("Миграция данных пользователей завершена")
        except Exception as e:
            logger.error(f"Ошибка миграции данных: {e}")

    @staticmethod
    def get_stats(user_id: str) -> dict:
        """Возвращает статистику пользователя"""
        BananaTracker._init_user(user_id)
        return BANANA_STATS.get(user_id, {})

    @staticmethod
    def get_top_users(limit=10):
        """Возвращает топ пользователей по количеству бананов"""
        users = []
        for user_id, data in BANANA_STATS.items():
            try:
                users.append({
                    'id': user_id,
                    'bananas': data['bananas'],
                    'total_earned': data['total_earned']
                })
            except KeyError:
                continue
        
        # Сортируем по убыванию бананов
        sorted_users = sorted(users, key=lambda x: x['bananas'], reverse=True)
        return sorted_users[:limit]


# 1. Сначала объявляем все константы
ADMIN_IDS = [1282672403, 1308588259, 5920802640, 5647757355, 425442049, 1776681047, 5176508798, 7827374847]
BANNED_PLAYER_ID = {425442049}
BANNED_PLAYER_IDs = 425442049
MOSCOW_TZ = pytz.timezone("Europe/Moscow")
BANANA_STATS_FILE = "banana_stat.json"
BANANA_STATS = BananaTracker.initialize()
if "active_events" not in BANANA_STATS:
    BANANA_STATS["active_events"] = {}
    BananaTracker.save_stats()  # Сохраняем изменения
MULTIPLIER_HOUR = random.randint(0, 23)
EVENT_CHAT_ID = -1002443160040
EVENT_TOPIC_ID = 6

BANANA_STATS = BananaTracker.initialize()
if not isinstance(BANANA_STATS, dict):
    BANANA_STATS = {}
if "active_events" not in BANANA_STATS:
    BANANA_STATS["active_events"] = {}
    with open(BANANA_STATS_FILE, 'w', encoding='utf-8') as f:
        json.dump(BANANA_STATS, f, indent=4, ensure_ascii=False)

# Игровые данные
active_games = {}
pending_games = {} 
user_games = {}
active_knb_games = {}  
user_knb_games = {}  
pending_knb_games = {}  

CONFIRM_DATA = {}

# Достижения
ACHIEVEMENTS = {
    "wins": {  # За победы в играх
        5: {"name": "🍌 Миньон-новичок", "reward": 1, "msg": "Ба-на-на! 5 побед! +1 банан в корзинку!"},
        10: {"name": "🔥 Огненный банан", "reward": 2, "msg": "Ура-ура! 10 побед! +2 банана! Бе-бе-бе!"},
        25: {"name": "🦍 Горилла-чемпион", "reward": 5, "msg": "БОМБА! 25 побед! Целых 5 бананов!"},
        50: {"name": "🏆 Король джунглей", "reward": 10, "msg": "БАНАНА-ПАУ! 50 ПОБЕД! 10 БАНАНОВ ТВОИ!"},
        100: {"name": "👑 БОГ МИНЬОНОВ", "reward": 20, "msg": "БА-БА-БУМ! 100 ПОБЕД! ТЫ ЛЕГЕНДА! ДЕРЖИ 20 БАНАНОВ!"}
    },     "collection": {  # За сбор бананов
        100: {"name": "🏦 Банановый вкладчик", "reward": 5, "msg": "Накопил 100 бананов! +5!"},
        500: {"name": "💰 Банановый олигарх", "reward": 10, "msg": "500 бананов! Ты богач! +10!"}
    },
    "special": {  # Уникальные ачивки
        "diamond": {"name": "💎 Алмазный собиратель", "reward": 20, "msg": "Нашел алмазный банан! +20!", "condition": "diamond_bananas > 0"},
        "event_winner": {"name": "🏆 Победитель ивента", "reward": 15, "msg": "Победил в чат-ивенте! +15!"}
    }, "shop": {
        "1": {"name": "🍌 Банановый новичок", "reward": 0, "msg": "Первый шаг в мир банановых достижений!"},
        "2": {"name": "🍌 Опытный банановед", "reward": 0, "msg": "Теперь ты знаток бананов!"},
        "3": {"name": "🍌 Повелитель связок", "reward": 0, "msg": "Целые связки бананов твои!"},
        "4": {"name": "🍌 Банановый магнат", "reward": 0, "msg": "Вершина банановой карьеры!"}
    }
}

EMOJI_OPTIONS = ["🍌", "🍉", "🍎", "🍐", "🍇"]
polls = {}  # {chat_id: {message_id: {question, options, votes, creator_id, ended}}}

ACTIVE_CHATS = set()  # Для хранения ID чатов, где бот активен


async def event_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая отправка сообщений без удаления исходного с сохранением форматирования"""
    try:
        # Проверяем наличие текста
        if not update.message.text:
            return  # Молча игнорируем пустые команды

        # Конфигурация чата (ваши значения)
        TARGET_CHAT_ID = -1002443160040
        TARGET_TOPIC_ID = 6

        # Получаем полный текст сообщения (после команды)
        full_text = update.message.text
        # Удаляем саму команду (/event или что вы используете)
        command = update.message.entities[0]  # Первая entity - это команда
        raw_text = full_text[command.length + 1:]  # +1 для пробела после команды
        
        # Отправляем сообщение с сохранением форматирования
        await context.bot.send_message(
            chat_id=TARGET_CHAT_ID,
            message_thread_id=TARGET_TOPIC_ID,
            text=raw_text,
            parse_mode=None  # Отключаем Markdown для сохранения оригинального форматирования
        )

        # Можно добавить скрытое подтверждение
        await update.message.reply_text(
            "Отправил в чат, хдхд лолкек, типа ты от меня говоришь",
            reply_to_message_id=update.message.message_id
        )

    except Exception as e:
        logger.error(f"Stealth error: {str(e)[:50]}")

async def channel_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скрытая отправка сообщений без удаления исходного с сохранением форматирования"""
    try:
        # Проверяем наличие текста
        if not update.message.text:
            return  # Молча игнорируем пустые команды

        # Конфигурация чата (ваши значения)
        TARGET_CHANNEL_ID = -1002799989868

        # Получаем полный текст сообщения (после команды)
        full_text = update.message.text
        # Удаляем саму команду (/event или что вы используете)
        command = update.message.entities[0]  # Первая entity - это команда
        raw_text = full_text[command.length + 1:]  # +1 для пробела после команды
        
        # Отправляем сообщение с сохранением форматирования
        await context.bot.send_message(
            chat_id=TARGET_CHANNEL_ID,
            text=raw_text,
            parse_mode=None  # Отключаем Markdown для сохранения оригинального форматирования
        )

        # Можно добавить скрытое подтверждение
        await update.message.reply_text(
            "Отправил в чат, хдхд лолкек, типа ты от меня говоришь",
            reply_to_message_id=update.message.message_id
        )

    except Exception as e:
        logger.error(f"Stealth error: {str(e)[:50]}")

def parse_poll_args(text: str):
    question_part = text.split("<")[0].strip()
    options = re.findall(r"<([^<>]+)>", text)
    return question_part, options

def build_poll_message(question, options, votes):
    total_votes = len(votes)
    counts = [0]*len(options)
    for v in votes.values():
        counts[v] += 1
    lines = [f"*{question}*"]
    for i, option in enumerate(options):
        pct = (counts[i] / total_votes * 100) if total_votes else 0
        lines.append(f"{EMOJI_OPTIONS[i]} - {option} — {counts[i]} голосов ({pct:.0f}%)")
    lines.append(f"\nПроголосовали: {total_votes}")
    return "\n".join(lines)

async def pollcreate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Получаем полный текст после команды, включая вопрос и варианты
    text = update.message.text[len("/pollcreate"):].strip()
    question, options = parse_poll_args(text)

    if not question or len(options) < 2 or len(options) > 5:
        await update.message.reply_text(
            "Использование:\n/pollcreate Вопрос <Вариант1> <Вариант2> [<Вариант3>]...[<Вариант5>]\n"
            "Минимум 2 варианта, максимум 5."
        )
        return

    keyboard = [[InlineKeyboardButton(EMOJI_OPTIONS[i], callback_data=f"vote_{i}")] for i in range(len(options))]
    keyboard.append([InlineKeyboardButton("Завершить опрос", callback_data="end_poll")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    text_message = build_poll_message(question, options, {})
    sent = await update.message.reply_text(text_message, reply_markup=reply_markup, parse_mode="Markdown")

    chat_id = update.effective_chat.id
    polls.setdefault(chat_id, {})[sent.message_id] = {
        "question": question,
        "options": options,
        "votes": {},
        "creator_id": update.effective_user.id,
        "ended": False
    }

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    message_id = query.message.message_id


    poll = polls[chat_id][message_id]

    if poll["ended"]:
        await query.answer("Ой, банан! Опрос завершён, голосовать нельзя 🛑", show_alert=True)
        return

    data = query.data

    if data == "end_poll":
        is_admin = user_id in ADMIN_IDS
        logger.info(f"User {user_id} пытается завершить опрос. Создатель: {poll['creator_id']}, is_admin={is_admin}")

        if user_id != poll["creator_id"] and not is_admin:
            await query.answer("Вии-ду, вии-ду! Только создатель или босс-админ может закрыть банановый опрос 🍌👑", show_alert=True)
            return

        poll["ended"] = True
        text = build_poll_message(poll["question"], poll["options"], poll["votes"])
        text += "\n\n💥 Опрос завершён! Бананы в безопасности! 🍌🍌🍌"
        await query.edit_message_text(text, parse_mode="Markdown")
        logger.info(f"Опрос в чате {chat_id} сообщении {message_id} завершён пользователем {user_id}")
        return

    if data.startswith("vote_"):
        if poll["ended"]:
            await query.answer("Опрос завершён, голосовать нельзя.", show_alert=True)
            return

        option_index = int(data.split("_")[1])
        poll["votes"][user_id] = option_index
        text = build_poll_message(poll["question"], poll["options"], poll["votes"])
        keyboard = [[InlineKeyboardButton(EMOJI_OPTIONS[i], callback_data=f"vote_{i}")] for i in range(len(poll["options"]))]
        keyboard.append([InlineKeyboardButton("Завершить опрос 🍌", callback_data="end_poll")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает ID пользователя только по ответу на сообщение (для админов)."""
    msg = update.message
    sender_id = update.effective_user.id

    # Проверка прав
    if sender_id not in ADMIN_IDS:
        await msg.reply_text("🚫 Эта команда только для админов!")
        return

    # Проверка, что есть ответ на сообщение
    if not msg.reply_to_message:
        await msg.reply_text("❌ Нужно ответить на сообщение пользователя, чтобы узнать его ID.")
        return

    target_user = msg.reply_to_message.from_user

    # Проверка на бан
    if target_user.id in BANNED_PLAYER_ID:
        await msg.reply_text("❌ Не смог найти айди/ник человека, либо вы не указали его. Я не работаю если написать /getid @чей-то_тег.")
        return

    # Выводим результат
    await msg.reply_text(
        f"👤 Ник: @{target_user.username or '—'}\n🆔 ID: <code>{target_user.id}</code>",
        parse_mode="HTML"
    )


Owner_ID = {1282672403}

async def add_bananas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админская команда для выдачи бананов (/addbananas <user_id> <amount>)"""
    try:
        # Проверка прав администратора
        if update.effective_user.id not in Owner_ID:
            await update.message.reply_text(
                "🚫 Только для Главных Бананов! 🍌\n"
                "Бе-бе-бе! Эта команда только для админов!",
                parse_mode="HTML"
            )
            return

        # Проверка наличия аргументов
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "ℹ️ <b>Использование команды:</b>\n"
                "<code>/addbananas &lt;user_id&gt; &lt;количество&gt;</code>\n\n"
                "📌 <b>Пример:</b>\n"
                "<code>/addbananas 123456789 100</code>\n\n"
                "Выдаст 100 бананов пользователю с ID 123456789",
                parse_mode="HTML"
            )
            return

        # Получаем и проверяем ID пользователя
        target_id = str(context.args[0]).strip()
        if not target_id.isdigit():
            await update.message.reply_text(
                "❌ <b>Ошибка!</b> ID пользователя должен быть числом!\n"
                "Пример правильного ID: <code>123456789</code>",
                parse_mode="HTML"
            )
            return

        # Получаем и проверяем количество бананов
        try:
            amount = int(context.args[1])
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "❌ <b>Ошибка!</b> Количество бананов должно быть положительным числом!\n"
                "Пример: <code>/addbananas 123456789 50</code>",
                parse_mode="HTML"
            )
            return

        # Выдаем бананы и получаем новый баланс
        new_balance = BananaTracker.add_bananas(target_id, amount)
        
        # Определяем правильное склонение слова "банан"
        banana_word = "банан" + (
            "ов" if amount % 10 in {0,5,6,7,8,9} or 11 <= amount % 100 <= 14 
            else "а" if amount % 10 == 1 
            else "ов"
        )
        
        # Формируем и отправляем ответ
        await update.message.reply_text(
            f"🎉 <b>Администратор выдал бананы!</b>\n\n"
            f"👤 <b>Получатель:</b> <code>{target_id}</code>\n"
            f"🍌 <b>Количество:</b> <code>{amount}</code> {banana_word}\n"
            f"💰 <b>Новый баланс:</b> <code>{new_balance}</code>\n\n"
            f"Ба-на-на! Миньоны довольны! 🎵",
            parse_mode="HTML"
        )

        # Логируем действие
        logger.info(f"Admin {update.effective_user.id} added {amount} bananas to user {target_id}")

    except Exception as e:
        logger.error(f"Error in add_bananas: {e}", exc_info=True)
        await update.message.reply_text(
            "💥 <b>Банановая катастрофа!</b>\n\n"
            "Что-то пошло не так при выдаче бананов...\n"
            "Попробуйте еще раз или проверьте логи.",
            parse_mode="HTML"
        )


BananaTracker.migrate_all_users()  

class ChatEventManager:
    EVENT_TYPES = {
        "banana_rain": {
            "goal": 50,
            "description": "☔️ Банановый дождь!\nСоберите 50🍌 всем чатом!",
            "reward": 5,
            "duration": 3600  # 60 минут
        },
        "banana_fest": {
            "goal": 100,
            "description": "🎪 Банановый фестиваль!\nСоберите 100🍌 для мега-награды (10 🍌)!",
            "reward": 10,
            "duration": 7200  # 2 часа
        }
    }

    @staticmethod
    def start_event(chat_id: int, event_type: str = None):
        """Запускает ивент (если event_type не указан — случайный)"""
        if not event_type or event_type not in ChatEventManager.EVENT_TYPES:
            event_type = random.choice(list(ChatEventManager.EVENT_TYPES.keys()))

        event_config = ChatEventManager.EVENT_TYPES[event_type]
        event_data = {
            "type": event_type,
            "goal": event_config["goal"],
            "progress": 0,
            "participants": [],
            "start_time": int(time.time()),
            "duration": event_config["duration"]
        }
        BANANA_STATS.setdefault("active_events", {})[str(chat_id)] = event_data
        BananaTracker.save_stats()
        return event_data

    @staticmethod
    def update_event_progress(chat_id: int, user_id: str, amount: int = 1):
        """Обновляет прогресс ивента"""
        event = BANANA_STATS.get("active_events", {}).get(str(chat_id))
        if not event:
            return False

        # Проверка на время
        if int(time.time()) - event["start_time"] > event["duration"]:
            BANANA_STATS["active_events"].pop(str(chat_id), None)
            BananaTracker.save_stats()
            return False

        event["progress"] += amount
        if user_id not in event["participants"]:
            event["participants"].append(user_id)

        BananaTracker.save_stats()
        return True

    @staticmethod
    def get_event_status(chat_id: int):
        """Возвращает статус текущего ивента"""
        return BANANA_STATS.get("active_events", {}).get(str(chat_id))


async def start_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запускает ивент (только для админов)"""
    if update.effective_user.id not in Owner_ID:
        await update.message.reply_text("🚫 Только для Главных Бананов!")
        return

    chat_id = update.effective_chat.id
    if str(chat_id) in BANANA_STATS.get("active_events", {}):
        await update.message.reply_text("⚠️ В этом чате уже идет ивент!")
        return

    # Можно указать тип ивента: /start_event banana_fest
    chosen_event = context.args[0] if context.args else None
    event = ChatEventManager.start_event(chat_id, chosen_event)
    description = ChatEventManager.EVENT_TYPES[event["type"]]["description"]

    await update.message.reply_text(
        f"🎉 Новый ивент начался!\n\n{description}\n"
        f"Прогресс: 0/{event['goal']}\n"
        f"Время на выполнение: {event['duration'] // 60} минут"
    )


async def event_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущий статус ивента"""
    chat_id = update.effective_chat.id
    event = ChatEventManager.get_event_status(chat_id)

    if not event:
        await update.message.reply_text("ℹ️ Сейчас нет активных ивентов.")
        return

    event_info = ChatEventManager.EVENT_TYPES[event["type"]]
    time_left = max(0, event['duration'] - (int(time.time()) - event["start_time"]))

    await update.message.reply_text(
        f"📊 **Текущий ивент:** {event_info['description']}\n"
        f"Прогресс: {event['progress']}/{event['goal']}\n"
        f"Участников: {len(event['participants'])}\n"
        f"Осталось времени: {time_left // 60} минут"
    )

async def end_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершает ивент и выдаёт бананы участникам (только для админов)"""
    if update.effective_user.id not in Owner_ID:
        await update.message.reply_text("🚫 Только для Главных Бананов!")
        return

    chat_id = update.effective_chat.id
    event = BANANA_STATS.get("active_events", {}).get(str(chat_id))

    if not event:
        await update.message.reply_text("ℹ️ В этом чате нет активного ивента.")
        return

    reward_per_participant = ChatEventManager.EVENT_TYPES[event["type"]]["reward"]
    participants = event.get("participants", {})

    if not participants:
        await update.message.reply_text("⚠️ Никто не участвовал в ивенте. Ивент завершён без наград.")
    else:
        for user_id, contribution in participants.items():
            BananaTracker.add_bananas(user_id, reward_per_participant)

        await update.message.reply_text(
            f"🎉 Ивент завершён!\n"
            f"Каждый из {len(participants)} участников получил по {reward_per_participant} бананов!"
        )

    # Удаляем ивент
    del BANANA_STATS["active_events"][str(chat_id)]
    BananaTracker.save_stats()


class LawEnforcer:
    def __init__(self):
        self.active_law = None
        self.end_time = None
        self.appeals = {}  # Словарь для хранения обжалований
        self.laws = self.load_laws()  # Загружаем законы при инициализации
        
    def load_laws(self):
        try:
            with open('laws.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('laws', [])
        except (FileNotFoundError, json.JSONDecodeError):
            # Возвращаем стандартные законы если файл не найден
            return [
                "Все сообщения должны заканчиваться знаком вопроса?",
                "Запрещено использовать букву 'Е'",
                "Нужно отправить случайное число от 1 до 100",
                "Писать только шёпотом (все буквы lowercase)",
                "Использовать только слова короче 5 букв",
                "Запрещены все цифры, кроме 7",
                "Буквы только из слова 'миньон'"
            ]
    
    def get_random_law(self):
        """Выбирает случайный закон и устанавливает его на 30 минут"""
        self.active_law = random.choice(self.laws)
        self.end_time = datetime.now() + timedelta(minutes=30)
        return self.active_law
    
    def check_violation(self, message):
        """Проверяет сообщение на нарушение текущего закона"""
        if not self.active_law or datetime.now() > self.end_time:
            return False
            
        text = message.text
        user_id = str(message.from_user.id)
        
        # 1. Завершение знаком вопроса
        if "знаком вопроса" in self.active_law and not text.endswith('?'):
            return True
            
        # 2. Запрет буквы 'Е'
        elif "букву 'Е'" in self.active_law and any(c.lower() == 'е' for c in text):
            return True
            
        # 3. Случайное число 1-100
        elif "случайное число от 1 до 100" in self.active_law:
            if not any(num.isdigit() and 1 <= int(num) <= 100 for num in re.findall(r'\d+', text)):
                return True
                
        # 4. Только lowercase
        elif "шёпотом" in self.active_law and text != text.lower():
            return True
            
        # 5. Слова короче 5 букв
        elif "слова короче 5 букв" in self.active_law:
            if any(len(word) >= 5 for word in re.findall(r'\w+', text)):
                return True
                
        # 6. Цифры кроме 7
        elif "цифры, кроме 7" in self.active_law:
            if any(c.isdigit() and c != '7' for c in text):
                return True
                
        # 7. Буквы из слова 'миньон'
        elif "буквы, которые есть в слове 'миньон'" in self.active_law:
            allowed_letters = {'м', 'и', 'н', 'ь', 'о', ' '}
            if any(c.lower() not in allowed_letters for c in text if c.isalpha()):
                return True
                
        # 8. Только гласные буквы
        elif "гласные буквы" in self.active_law:
            vowels = {'а', 'е', 'ё', 'и', 'о', 'у', 'ы', 'э', 'ю', 'я'}
            if any(c.lower() not in vowels and c.isalpha() for c in text):
                return True
                
        # 9. Только согласные буквы
        elif "согласные буквы" in self.active_law:
            consonants = {'б', 'в', 'г', 'д', 'ж', 'з', 'й', 'к', 'л', 'м', 
                         'н', 'п', 'р', 'с', 'т', 'ф', 'х', 'ц', 'ч', 'ш', 'щ'}
            if any(c.lower() not in consonants and c.isalpha() for c in text):
                return True
                
        # 10. Без пробелов
        elif "без пробелов" in self.active_law and ' ' in text:
            return True
            
        # 11. Только эмодзи
        elif "только эмодзи" in self.active_law:
            emoji_pattern = re.compile("["
                u"\U0001F600-\U0001F64F"  # emoticons
                u"\U0001F300-\U0001F5FF"  # symbols & pictographs
                u"\U0001F680-\U0001F6FF"  # transport & map symbols
                u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                u"\U00002702-\U000027B0"
                u"\U000024C2-\U0001F251"
                "]+", flags=re.UNICODE)
            if not emoji_pattern.fullmatch(text):
                return True
                
        # 12. Каждое слово с заглавной
        elif "каждое слово с заглавной буквы" in self.active_law:
            words = text.split()
            if any(word and not word[0].isupper() for word in words):
                return True
                
        # 13. Без повторяющихся букв
        elif "без повторяющихся букв" in self.active_law:
            chars = [c.lower() for c in text if c.isalpha()]
            if len(chars) != len(set(chars)):
                return True
                
        # 14. Только знаки препинания
        elif "только знаки препинания" in self.active_law:
            if any(c.isalnum() for c in text):
                return True
                
        # 15. Четное количество символов
        elif "четное количество символов" in self.active_law and len(text) % 2 != 0:
            return True
            
        # 16. Палиндром
        elif "палиндром" in self.active_law:
            clean_text = re.sub(r'[^а-яА-ЯёЁ]', '', text.lower())
            if clean_text != clean_text[::-1]:
                return True
                
        # 17. Без гласных
        elif "без гласных букв" in self.active_law:
            vowels = {'а', 'е', 'ё', 'и', 'о', 'у', 'ы', 'э', 'ю', 'я'}
            if any(c.lower() in vowels for c in text if c.isalpha()):
                return True
                
        # 18. Только русские буквы
        elif "только русские буквы" in self.active_law:
            if any(not (c.isalpha() and c.lower() in 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя') for c in text if c.isalpha()):
                return True
                
        # 19. Чередование регистра
        elif "чередование регистра" in self.active_law:
            for i, c in enumerate(text):
                if c.isalpha():
                    if (i % 2 == 0 and not c.islower()) or (i % 2 != 0 and not c.isupper()):
                        return True
                        
        # 20. Только математические символы
        elif "математические символы" in self.active_law:
            allowed = set('+-*/=()0123456789 ')
            if any(c not in allowed for c in text):
                return True
                
        return False
        async def punish_violation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Наказывает пользователя за нарушение закона"""
            user = update.effective_user
            message = update.message
            fine = random.randint(1, 3)  # Штраф 1-3 банана
            
            # Вычитаем бананы
            BananaTracker.add_bananas(str(user.id), -fine)
            
            # Мут на 5 минут
            until_date = int(time.time()) + 300  # 5 минут
            try:
                await context.bot.restrict_chat_member(
                    chat_id=update.effective_chat.id,
                    user_id=user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until_date
                )
            except Exception as e:
                logger.error(f"Ошибка при муте пользователя: {e}")
            
            # Отправляем сообщение о нарушении
            keyboard = [
                [InlineKeyboardButton("🚨 Обжаловать", callback_data=f"appeal_{message.message_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await message.reply_text(
                f"🚨 {user.first_name} нарушил закон!\n"
                f"📜 Закон: {self.active_law}\n"
                f"💸 Штраф: {fine}🍌 + мут 5 минут\n\n"
                "Если это ошибка, нажмите кнопку ниже",
                reply_markup=reply_markup
            )
            
            # Сохраняем информацию об обжаловании
            self.appeals[message.message_id] = {
                'user_id': str(user.id),
                'user_name': user.full_name,
                'law': self.active_law,
                'message_text': message.text,
                'fine': fine
            }
        
    async def handle_appeal(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает обжалование нарушения"""
        query = update.callback_query
        await query.answer()
        
        if query.from_user.id not in ADMIN_IDS:
            await query.answer("Только админы могут рассматривать обжалования!", show_alert=True)
            return
            
        message_id = int(query.data.split('_')[1])
        appeal = self.appeals.get(message_id)
        
        if not appeal:
            await query.answer("Обжалование уже рассмотрено!", show_alert=True)
            return
            
        # Создаем клавиатуру для админа
        keyboard = [
            [
                InlineKeyboardButton("✅ Отменить наказание", callback_data=f"appeal_approve_{message_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"appeal_reject_{message_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🚨 Обжалование нарушения\n\n"
            f"👤 Пользователь: {appeal['user_name']}\n"
            f"📜 Закон: {appeal['law']}\n"
            f"✉️ Сообщение: {appeal['message_text']}\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )
        
    async def process_appeal_decision(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обрабатывает решение админа по обжалованию"""
        query = update.callback_query
        await query.answer()
        
        data = query.data.split('_')
        message_id = int(data[2])
        decision = data[1]
        
        appeal = self.appeals.get(message_id)
        if not appeal:
            await query.answer("Обжалование уже рассмотрено!", show_alert=True)
            return
            
        user_id = appeal['user_id']
        fine = appeal['fine']
        
        if decision == "approve":
            # Возвращаем бананы и снимаем мут
            BananaTracker.add_bananas(user_id, fine)
            
            try:
                await context.bot.restrict_chat_member(
                    chat_id=query.message.chat_id,
                    user_id=user_id,
                    permissions=ChatPermissions(
                        can_send_messages=True,
                        can_send_media_messages=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True
                    )
                )
            except Exception as e:
                logger.error(f"Ошибка при снятии мута: {e}")
            
            await query.edit_message_text(
                f"✅ Наказание отменено!\n"
                f"👤 {appeal['user_name']} получил назад {fine}🍌\n"
                f"Приносим извинения за ошибку!"
            )
        else:
            await query.edit_message_text(
                f"❌ Обжалование отклонено!\n"
                f"👤 {appeal['user_name']} остается наказанным\n"
                f"Закон есть закон!"
            )
        
        del self.appeals[message_id]

async def law_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /law - показывает текущий закон или устанавливает новый"""
    try:
        if not context.args:
            # Показать текущий закон
            if law_enforcer.active_law:
                time_left = law_enforcer.end_time - datetime.now()
                minutes = int(time_left.total_seconds() // 60)
                await update.message.reply_text(
                    f"📜 <b>Текущий закон:</b>\n{law_enforcer.active_law}\n\n"
                    f"⏳ <b>Осталось времени:</b> {minutes} минут\n\n"
                    "Чтобы предложить новый закон, используйте /law [текст закона]",
                    parse_mode="HTML"
                )
            else:
                await update.message.reply_text(
                    "ℹ️ Сейчас нет активного закона.\n"
                    "Админы могут установить новый с помощью /setlaw\n"
                    "Или вы можете предложить закон: /law [текст]"
                )
            return

        # Предложение нового закона (для обычных пользователей)
        if update.effective_user.id not in ADMIN_IDS:
            proposed_law = ' '.join(context.args)
            await update.message.reply_text(
                f"📜 Ваше предложение закона:\n\n{proposed_law}\n\n"
                "Отправлено администраторам на рассмотрение!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Принять", callback_data=f"accept_law_{update.message.message_id}"),
                     InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_law_{update.message.message_id}")]
                ])
            )
            return

        # Установка нового закона (для админов)
        new_law = ' '.join(context.args)
        law_enforcer.active_law = new_law
        law_enforcer.end_time = datetime.now() + timedelta(minutes=30)
        
        await update.message.reply_text(
            f"📜 <b>Установлен новый закон:</b>\n\n{new_law}\n\n"
            f"⏳ <b>Действует до:</b> {law_enforcer.end_time.strftime('%H:%M')}\n"
            "Ба-на-на! Соблюдайте!",
            parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Ошибка в law_command: {e}")
        await update.message.reply_text("🍌 Ой, что-то пошло не так с законом!")

law_enforcer = LawEnforcer()
async def handle_law_proposal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка предложений законов от пользователей"""
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("Только админы могут принимать законы!", show_alert=True)
        return
        
    action, message_id = query.data.split('_')[1], int(query.data.split('_')[2])
    
    if action == "accept":
        # Получаем текст предложенного закона из сообщения
        try:
            message = await context.bot.get_message(
                chat_id=query.message.chat_id,
                message_id=message_id
            )
            new_law = message.text.split("\n\n")[0]
            
            law_enforcer.active_law = new_law
            law_enforcer.end_time = datetime.now() + timedelta(minutes=30)
            
            await query.edit_message_text(
                f"✅ Закон принят администратором!\n\n"
                f"📜 <b>Новый закон:</b>\n{new_law}\n\n"
                f"⏳ <b>Действует до:</b> {law_enforcer.end_time.strftime('%H:%M')}",
                parse_mode="HTML"
            )
            
            # Отправляем уведомление в чат
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"📢 <b>Объявление!</b>\n\n"
                     f"Администратор принял новый закон:\n\n"
                     f"{new_law}\n\n"
                     f"Действует до {law_enforcer.end_time.strftime('%H:%M')}\n"
                     f"Ба-на-на! Соблюдайте!",
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"Ошибка при принятии закона: {e}")
            await query.answer("Ошибка при принятии закона!", show_alert=True)
            
    elif action == "reject":
        await query.edit_message_text("❌ Предложение закона отклонено администратором")

# В обработчике сообщений добавляем проверку:
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем на нарушение закона
    if law_enforcer.check_violation(update.message):
        await law_enforcer.punish_violation(update, context)
        return

class QuestSystem:
    def __init__(self):
        self.active_quests = {}
        self.quest_jobs = {}
        self.QUEST_DURATION = 300  # 5 минут в секундах
        self.clues = [
            "На месте преступления найдены жёлтые следы",
            "Слышали странный звук 'Ба-на-на'",
            "Камеры зафиксировали движение в 3:15",
            "Обнаружены следы кожуры"
        ]
        self.scenarios = [
            {
                "crime": "Украдено 100🍌 из бананового хранилища!",
                "suspects": ["@Мистер_Жёлтый", "@Банана_Джо", "@Миньон_Гарри"],
                "culprit": "@Банана_Джо",
                "solution": "Он единственный знал код от хранилища"
            }
        ]

    def is_quest_active(self, chat_id):
        """Проверяет, активен ли квест в чате"""
        return chat_id in self.active_quests

    async def start_quest(self, chat_id, context, trigger_message_id=None, manual=False, thread_id=None):
        """Запуск квеста с привязкой к сообщению-триггеру"""
        try:
            if not chat_id:
                logger.error("Не указан chat_id для запуска квеста")
                return

            if self.is_quest_active(chat_id):
                if manual:
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="🔍 Квест уже идет! Используйте /clue, /vote и /ask",
                            reply_to_message_id=trigger_message_id,
                            message_thread_id=thread_id
                        )
                    except Exception as e:
                        logger.error(f"Ошибка при отправке сообщения: {e}")
                return

            scenario = random.choice(self.scenarios)
            self.active_quests[chat_id] = {
                "scenario": scenario,
                "votes": {},
                "found_clues": [],
                "start_time": datetime.now(),
                "culprit": scenario["culprit"],
                "trigger_message_id": trigger_message_id,
                "thread_id": thread_id,
                "is_active": True  # Флаг активности квеста
            }

            msg = f"""🔍 ДЕТЕКТИВНЫЙ КВЕСТ!

🛑 {scenario['crime']}
🔎 Подозреваемые: {', '.join(scenario['suspects'])}

Используйте:
/clue - получить улику
/vote @ник - проголосовать
/ask @ник вопрос - допросить
/stop_quest - остановить квест

На разгадку 5 минут!"""
        
            try:
                sent_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    reply_to_message_id=trigger_message_id,
                    message_thread_id=thread_id
                )
                self.quest_jobs[chat_id] = asyncio.create_task(
                    self.end_quest(chat_id, context),
                    name=f"quest_{chat_id}_{time.time()}"
                )
            except Exception as e:
                logger.error(f"Ошибка запуска квеста: {e}")
                await self.cleanup_quest(chat_id)
        except Exception as e:
            logger.error(f"Общая ошибка в start_quest: {e}")

    async def stop_quest(self, chat_id):
        """Остановка активного квеста"""
        if not self.is_quest_active(chat_id):
            return False
            
        # Помечаем квест как неактивный
        self.active_quests[chat_id]["is_active"] = False
        await self._end_quest_internal(chat_id)
        return True

    async def stop_quest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /stop_quest"""
        chat_id = update.effective_chat.id
        if await self.stop_quest(chat_id):
            await update.message.reply_text("🛑 Квест успешно остановлен!")
        else:
            await update.message.reply_text("ℹ️ Активный квест не найден")

    async def cleanup_quest(self, chat_id):
        """Очистка данных квеста"""
        try:
            if chat_id in self.quest_jobs:
                self.quest_jobs[chat_id].cancel()
                del self.quest_jobs[chat_id]
            if chat_id in self.active_quests:
                del self.active_quests[chat_id]
        except Exception as e:
            logger.error(f"Ошибка в cleanup_quest: {e}")

    async def end_quest(self, chat_id, context):
        """Завершение квеста по таймеру"""
        try:
            await asyncio.sleep(self.QUEST_DURATION)
            
            if not self.is_quest_active(chat_id):
                return
                
            await self._end_quest_internal(chat_id, context)
        except Exception as e:
            logger.error(f"Ошибка в end_quest: {e}")
        finally:
            await self.cleanup_quest(chat_id)

    async def _end_quest_internal(self, chat_id, context=None):
        """Внутренняя логика завершения квеста"""
        if not self.is_quest_active(chat_id):
            return
            
        quest = self.active_quests[chat_id]
        scenario = quest['scenario']
        
        # Определяем победителя по голосам
        votes = {}
        for voter_id, suspect in quest['votes'].items():
            votes[suspect] = votes.get(suspect, 0) + 1
        
        if votes:
            winner_suspect = max(votes.items(), key=lambda x: x[1])[0]
            is_correct = (winner_suspect == scenario['culprit'])
        else:
            winner_suspect = "никто"
            is_correct = False
        
        result_text = (
            f"🕵️‍♂️ Квест завершен!\n\n"
            f"🔍 Преступление: {scenario['crime']}\n"
            f"🦹‍♂️ Преступник: {scenario['culprit']}\n"
            f"💡 Решение: {scenario['solution']}\n\n"
            f"🏆 Голоса выбрали: {winner_suspect} ({'верно' if is_correct else 'неверно'})\n\n"
            f"Спасибо за участие! Ба-на-на! 🎵"
        )
        
        await self.send_quest_response(chat_id, context, result_text)
        self.cleanup_quest(chat_id)

    async def send_quest_response(self, chat_id, context, text):
        """Отправка результата квеста"""
        try:
            if not self.is_quest_active(chat_id):
                return
                
            quest = self.active_quests.get(chat_id)
            if not quest:
                return
                
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=quest['trigger_message_id'],
                message_thread_id=quest['thread_id']
            )
        except Exception as e:
            logger.error(f"Ошибка отправки результата квеста: {e}")

    async def process_clue(self, chat_id):
        """Обработка команды /clue"""
        try:
            if not self.is_quest_active(chat_id):
                return "🔍 Сейчас нет активных квестов!"
                
            quest = self.active_quests[chat_id]
            available_clues = [c for c in self.clues if c not in quest["found_clues"]]
            
            if not available_clues:
                return "ℹ️ Все улики уже собраны!"
            
            clue = random.choice(available_clues)
            quest["found_clues"].append(clue)
            return f"🔎 Улика:\n{clue}"
        except Exception as e:
            logger.error(f"Ошибка в process_clue: {e}")
            return "🍌 Ой, что-то пошло не так!"

    async def process_vote(self, chat_id, user_id, suspect):
        """Обработка команды /vote"""
        try:
            if not self.is_quest_active(chat_id):
                return "ℹ️ Сейчас нет активного квеста!"
                
            quest = self.active_quests[chat_id]
            
            if suspect not in quest['scenario']['suspects']:
                return f"❌ {suspect} нет среди подозреваемых!"
            
            quest['votes'][user_id] = suspect
            return f"✅ Ваш голос за {suspect} учтён!"
        except Exception as e:
            logger.error(f"Ошибка в process_vote: {e}")
            return "🍌 Ой, что-то пошло не так!"

    async def process_ask(self, chat_id, suspect, question):
        """Обработка команды /ask"""
        try:
            if not self.is_quest_active(chat_id):
                return "ℹ️ Сейчас нет активного квеста!"
                
            quest = self.active_quests[chat_id]
            
            if suspect not in quest['scenario']['suspects']:
                return f"❌ {suspect} нет среди подозреваемых!"
            
            answers = {
                "@Мистер_Жёлтый": ["Я был в банановой лавке!", "Не трогайте меня!", "Я невиновен!"],
                "@Банана_Джо": ["Эээ... я... ничего не брал!", "*нервно почесался*", "Может быть да, может быть нет..."],
                "@Миньон_Гарри": ["Я спал!", "Я маленький, я не мог!", "Спросите у Бананы Джо!"]
            }
            
            response = random.choice(answers.get(suspect, ["Не знаю такого"]))
            return f"{suspect}: {response}"
        except Exception as e:
            logger.error(f"Ошибка в process_ask: {e}")
            return "🍌 Ой, что-то пошло не так!"

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает топ-5 пользователей по бананам без упоминаний"""
    try:
        # Получаем всех пользователей для определения позиции
        all_users = BananaTracker.get_top_users(limit=1000)  # Большое число чтобы получить всех
        top_users = all_users[:5]  # Берем только топ-5 для отображения
        
        if not top_users:
            await update.message.reply_text("🍌 Пока никто не собрал бананов! Бе-бе-бе!")
            return
        
        # Эмодзи для позиций
        position_emojis = {1: "🥇", 2: "🥈", 3: "🥉", 4: "4️⃣", 5: "5️⃣"}
        
        leaderboard_text = (
            "🏆 <b>ТОП-5 БАНАНОВЫХ МИНЬОНОВ</b> 🏆\n"
            "────────────────────\n\n"
        )
        
        for i, user in enumerate(top_users, 1):
            try:
                chat_member = await context.bot.get_chat_member(update.effective_chat.id, int(user['id']))
                # Используем только имя без тега
                username = chat_member.user.full_name
                
                # Проверяем статус Золотого Миньона
                user_stats = BananaTracker.get_stats(user['id'])
                if user_stats.get('golden_minion', False):
                    username = f"👑 {username}"  # Добавляем корону для Золотого Миньона
                    
            except:
                username = f"Миньон #{user['id'][-4:]}"  # Показываем только последние 4 цифры ID
            
            emoji = position_emojis.get(i, "🍌")
            leaderboard_text += (
                f"{emoji} <b>{username}</b>\n"
                f"   🍌 Баланс: <code>{user['bananas']}</code>\n"
                f"   💎 Всего: <code>{user['total_earned']}</code>\n\n"
            )
        
        # Добавляем информацию о текущем пользователе
        user_id = str(update.effective_user.id)
        user_stats = BananaTracker.get_stats(user_id)
        
        # Находим позицию текущего пользователя в общем рейтинге
        user_position = None
        total_players = len(all_users)
        
        for idx, user in enumerate(all_users, 1):
            if user['id'] == user_id:
                user_position = idx
                break
        
        # Проверяем статус Золотого Миньона для текущего пользователя
        current_user_name = update.effective_user.full_name
        if user_stats.get('golden_minion', False):
            current_user_name = f"👑 {current_user_name}"
        
        if user_position is not None:
            # Определяем эмодзи для позиции
            position_emoji = position_emojis.get(user_position, f"{user_position}️⃣")
            
            leaderboard_text += (
                f"\n────────────────────\n"
                f"<b>Ваша позиция:</b>\n"
                f"{position_emoji} <b>{current_user_name}</b>\n"
                f"🍌 Баланс: <code>{user_stats.get('bananas', 0)}</code>\n"
                f"💎 Всего: <code>{user_stats.get('total_earned', 0)}</code>\n"
                f"📊 Место: <code>{user_position}</code> из <code>{total_players}</code>\n"
                f"👥 Всего миньонов: <code>{total_players}</code>"
            )
        else:
            # Если пользователь не найден в рейтинге (новый игрок)
            leaderboard_text += (
                f"\n────────────────────\n"
                f"<b>Ваша позиция:</b>\n"
                f"{current_user_name}\n"
                f"🍌 Баланс: <code>{user_stats.get('bananas', 0)}</code>\n"
                f"💎 Всего: <code>{user_stats.get('total_earned', 0)}</code>\n"
                f"📊 Вы ещё не в рейтинге!\n"
                f"👥 Всего миньонов: <code>{total_players}</code>"
            )
        
        # Добавляем кнопку обновления
        keyboard = [[InlineKeyboardButton("🔄 Обновить", callback_data="refresh_leaderboard")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            leaderboard_text,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"Ошибка в leaderboard_command: {e}")
        await update.message.reply_text(
            "🍌 Ой, банановая катастрофа! Не удалось загрузить лидерборд...",
            parse_mode="HTML"
        )

def is_vanito_banana_message(update):
    text = update.message.text.lower() if update.message and update.message.text else ""
    return "ванито банан" in text

# 4. Определяем обработчики команд
async def banana_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id

    try:
        # Инициализация пользователя
        BananaTracker._init_user(user_id)

        # Проверяем активные бусты
        boosts = BANANA_STATS[user_id].get('boosts', {})
        midas_multiplier = 3.0 if boosts.get('midas_touch', 0) > 0 else 1.0
        banana_multiplier = 2.0 if boosts.get('multiplier', 0) > 0 else 1.0

        # Определяем кулдаун (базовый 3 часа)
        cooldown_seconds = 60 * 180  # 3 часа по умолчанию

        # Проверка ускорителя времени
        if "time_accelerator" in boosts:
            accelerator_time = boosts["time_accelerator"]
            if time.time() < accelerator_time:
                cooldown_seconds = 900  # 15 минут при ускорителе
                logger.info(f"Ускоритель времени активен для пользователя {user_id}")

        # Проверка машины времени (пропускает кулдаун)
        machine_time_used = False
        if "time_machine_used" in BANANA_STATS[user_id]:
            # Машина времени уже использована в этом цикле
            pass
        elif "time_machine" in boosts and boosts["time_machine"] > 0:
            # Используем машину времени
            BANANA_STATS[user_id]["boosts"]["time_machine"] -= 1
            BANANA_STATS[user_id]["time_machine_used"] = True
            machine_time_used = True
            logger.info(f"Машина времени использована для пользователя {user_id}")

        last_time = BANANA_STATS[user_id].get('last_banana', 0)
        elapsed = time.time() - last_time

        # Проверка кулдауна (если не использована машина времени)
        if not machine_time_used and elapsed < cooldown_seconds:
            remaining = cooldown_seconds - elapsed
            hours = int(remaining // 3600)
            minutes = int((remaining % 3600) // 60)
            seconds = int(remaining % 60)
            cooldown_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

            reply = await update.message.reply_text(
                f"⏳ Рано! Следующая команда через {cooldown_str}\n"
                f"⌛ Последний раз: {datetime.fromtimestamp(last_time).strftime('%H:%M')}"
            )
            await _delete_messages_after_delay(update.message, reply, context)
            return

        # Основная логика начисления бананов
        base = random.randint(1, 3)
        hour_multiplier = 1.5 if datetime.now().hour == MULTIPLIER_HOUR else 1.0
        
        # Применяем множители
        bananas = int(base * hour_multiplier * banana_multiplier)

        # Добавление бананов из мешка (используем новые константы)
        bag_level = BANANA_STATS[user_id].get('banana_bag_level', 0)
        if bag_level > 0:
            bananas += UPGRADES['banana_bag']['effects'][bag_level-1]
            logger.info(f"Бонус от бананового мешка: +{UPGRADES['banana_bag']['effects'][bag_level-1]} бананов")

        # Базовые шансы
        gold_chance = 2.0 * midas_multiplier
        diamond_chance = 0.33

        # Модификация шансов с помощью тотема (используем новые константы)
        totem_level = BANANA_STATS[user_id].get('banana_totem_level', 0)
        if totem_level > 0:
            gold_bonus, diamond_bonus = UPGRADES['banana_totem']['effects'][totem_level-1]
            gold_chance += gold_bonus
            diamond_chance += diamond_bonus
            logger.info(f"Бонус от бананового тотема: +{gold_bonus}% к золотому, +{diamond_bonus}% к алмазному")

        special_event = None
        rand = random.random() * 100  # от 0 до 100

        if rand < diamond_chance:  # Шанс на алмазный банан
            special_event = "алмазный"
            bananas = 50
            if 'diamond_bananas' not in BANANA_STATS[user_id]:
                BANANA_STATS[user_id]['diamond_bananas'] = 0
            BANANA_STATS[user_id]['diamond_bananas'] += 1
        elif rand < diamond_chance + gold_chance:  # Шанс на золотой банан
            special_event = "золотой"
            bananas = 10 * banana_multiplier
        elif rand < 22.33:  # 20% шанс кражи
            stolen = random.randint(1, 3)
            current_bananas = BANANA_STATS[user_id]['bananas']
            stolen_amount = min(stolen, current_bananas)
            if stolen_amount > 0:
                BananaTracker.add_bananas(user_id, -stolen_amount)
                special_event = f"миньоны украли {stolen_amount}🍌"

        # Начисляем бананы
        new_balance = BananaTracker.add_bananas(user_id, bananas)
        BANANA_STATS[user_id]['last_banana'] = time.time()
        
        # Сбрасываем флаг машины времени после успешного использования
        if machine_time_used:
            BANANA_STATS[user_id]["time_machine_used"] = False

        # Уменьшаем счетчики бустов
        if 'boosts' in BANANA_STATS[user_id]:
            if 'midas_touch' in BANANA_STATS[user_id]['boosts']:
                BANANA_STATS[user_id]['boosts']['midas_touch'] -= 1
                if BANANA_STATS[user_id]['boosts']['midas_touch'] <= 0:
                    del BANANA_STATS[user_id]['boosts']['midas_touch']
            
            if 'multiplier' in BANANA_STATS[user_id]['boosts']:
                BANANA_STATS[user_id]['boosts']['multiplier'] -= 1
                if BANANA_STATS[user_id]['boosts']['multiplier'] <= 0:
                    del BANANA_STATS[user_id]['boosts']['multiplier']

        # Обновляем стрик
        BananaTracker.update_streak(user_id, is_win=True)

        # Обновляем ивент (если активен)
        if "active_events" in BANANA_STATS and str(chat_id) in BANANA_STATS["active_events"]:
            ChatEventManager.update_event_progress(chat_id, user_id, amount=bananas)

        # Формируем сообщение
        message = []

        if special_event:
            if special_event.startswith("миньоны"):
                message.append(f"⚠️ О нет! {special_event.capitalize()}!")
            else:
                message.append(f"🎖 ВАУ! Вы получили {special_event} банан!")
            message.append(f"💎 Итог: +{bananas} бананов")
        else:
            multiplier_text = []
            if hour_multiplier > 1:
                multiplier_text.append("часовой x1.5")
            if banana_multiplier > 1:
                multiplier_text.append(f"буст x{banana_multiplier}")
            if bag_level > 0:
                multiplier_text.append(f"мешок +{UPGRADES['banana_bag']['effects'][bag_level-1]}")
            
            if multiplier_text:
                message.append(f"🍌 Получено +{bananas} банан(а) ({' + '.join(multiplier_text)})")
            else:
                message.append(f"🍌 Получено +{bananas} банан(а)")

        message.extend([
            f"💰 Теперь у вас: {new_balance} бананов",
            f"🤑 Всего заработано: {BANANA_STATS[user_id]['total_earned']}",
            f"🚀 Таким темпом в космос улетим!"
        ])

        # Показываем активные бусты и улучшения
        active_boosts = []
        if 'boosts' in BANANA_STATS[user_id]:
            if 'midas_touch' in BANANA_STATS[user_id]['boosts']:
                active_boosts.append(f"Касание Мидаса: {BANANA_STATS[user_id]['boosts']['midas_touch']} раз")
            if 'multiplier' in BANANA_STATS[user_id]['boosts']:
                active_boosts.append(f"Множитель: {BANANA_STATS[user_id]['boosts']['multiplier']} раз")
            if 'time_accelerator' in BANANA_STATS[user_id]['boosts']:
                remaining_time = BANANA_STATS[user_id]['boosts']['time_accelerator'] - time.time()
                if remaining_time > 0:
                    minutes = int(remaining_time // 60)
                    active_boosts.append(f"Ускоритель: {minutes} мин")
            if 'time_machine' in BANANA_STATS[user_id]['boosts']:
                active_boosts.append(f"Машина времени: {BANANA_STATS[user_id]['boosts']['time_machine']} раз")
        
        # Добавляем перманентные улучшения
        if bag_level > 0:
            active_boosts.append(f"Банановый мешок: ур. {bag_level} (+{UPGRADES['banana_bag']['effects'][bag_level-1]})")
        if totem_level > 0:
            gold_bonus, diamond_bonus = UPGRADES['banana_totem']['effects'][totem_level-1]
            active_boosts.append(f"Банановый тотем: ур. {totem_level} (+{gold_bonus}%/+{diamond_bonus}%)")
        
        if active_boosts:
            message.append(f"\n🔮 Активные улучшения: {', '.join(active_boosts)}")

        # Проверка достижений
        try:
            achievement_msg = BananaTracker.check_achievements(user_id)
            if achievement_msg:
                message.append(f"\n🎉 {achievement_msg}")
        except Exception as e:
            logger.error(f"Ошибка при проверке достижений: {e}")

        # Сохраняем изменения
        BananaTracker.save_stats()

        # Отправляем сообщение
        reply = await update.message.reply_text("\n".join(message))
        await _delete_messages_after_delay(update.message, reply, context)

    except telegram.error.TimedOut:
        logger.warning("Таймаут при выполнении banana_command")
        reply = await update.message.reply_text("⏳ Бананы загружаются... Попробуйте позже!")
        await _delete_messages_after_delay(update.message, reply, context)
    except Exception as e:
        logger.error(f"Ошибка в banana_command: {e}\nДанные пользователя: {BANANA_STATS.get(user_id)}", exc_info=True)
        reply = await update.message.reply_text(
            "🍌 Ой, банановая ошибка! Но твой кулдаун сохранен.\n"
            "Попробуй снова через 3 часа."
        )
        await _delete_messages_after_delay(update.message, reply, context)

async def _delete_messages_after_delay(user_message, bot_message, context, delay=30):
    """Удаляет сообщения пользователя и бота через указанную задержку."""   
    async def delete():
        await asyncio.sleep(delay)
        try:
            await user_message.delete()
        except Exception as e:
            logger.error(f"Не удалось удалить сообщение пользователя: {e}")
        try:
            await bot_message.delete()
        except Exception as e:
            logger.error(f"Не удалось удалить сообщение бота: {e}")
    
    # Запускаем в фоне без ожидания
    context.application.create_task(delete())
    
VANITO_PHRASES = [
    "Ванито на посту! Бананы под охраной, сэр! 💂🍌",
    "Кто посмел тронуть бананы?! Я слежу! 👀🍌",
    "Бананы под замком, сигнализация включена! 🔒🍌",
    "Спокойно, я на дежурстве! Бананы в безопасности. 🛡️🍌",
    "Если кто-то украдёт банан, я его найду… и съем улики. 😏🍌",
    "Банановая охрана прибыла! 🚓🍌",
    "Ванито докладывает: банановый периметр чист! ✅🍌",
    "Я видел воришку… но это был я в зеркале. 😳🍌"
]

def setup_handlers(app):
    # Регистрируем реакции на фразы
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))



ADMIN_IDS = [1282672403, 1308588259, 5920802640, 5647757355, 425442049, 1776681047, 5176508798, 7827374847]
MOD_COMMANDS = {"мут", "фри", "варн", "анварн", "бан", "кик"}
CONFIRM_COMMANDS = {"бан", "кик"}
CONFIRM_TIMEOUT = 300  # 5 минут в секундах

AUTOHELLO_CONFIG = {
    "enabled": True,
    "use_topic": False,
    "instructions": "Будь активным и собирай бананы каждый день!",
    "links": "- [Наш сайт](https://example.com)\n- [FAQ](https://t.me/examplefaq)",
    "rules": "1. Не флудить\n2. Не оскорблять\n3. Любить бананы 🍌",
    "extra": "Миньоны любят веселье, а ты?",
}

async def autohello_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Безопасная команда автохеллоу"""
    try:
        if context is None:
            return
            
        await update.message.reply_text(
            "💛 *БАНАНА-МЕНЮ ПРИВЕТСТВИЙ* 💛\n\n"
            f"📌 Автопривет: {'✅ Вкл' if AUTOHELLO_CONFIG['enabled'] else '❌ Выкл'}\n"
            f"📂 Топик: {'📌 Вкл' if AUTOHELLO_CONFIG['use_topic'] else '🚫 Выкл'}\n\n"
            "Используйте /set_welcome для настройки",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка в autohello_command: {e}")

async def set_welcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Упрощенная настройка приветствия"""
    try:
        if context is None or not context.args:
            await update.message.reply_text(
                "ℹ️ Использование: /set_welcome [текст]\n"
                "Пример: /set_welcome Добро пожаловать! 🍌"
            )
            return
            
        new_welcome = ' '.join(context.args)
        AUTOHELLO_CONFIG['instructions'] = new_welcome
        
        await update.message.reply_text(
            "✅ Приветствие обновлено!\n"
            f"Новый текст: {new_welcome}"
        )
    except Exception as e:
        logger.error(f"Ошибка в set_welcome_command: {e}")

async def send_welcome_message(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_name: str, user_id: int):
    """Безопасная отправка приветствия"""
    try:
        if not AUTOHELLO_CONFIG["enabled"] or context is None:
            return
        
        welcome_text = (
            f"✨ **Добро пожаловать, {user_name}!** ✨\n\n"
            f"{AUTOHELLO_CONFIG['instructions']}\n\n"
            f"🔗 **Ссылки:**\n{AUTOHELLO_CONFIG['links']}\n\n"
            f"📜 **Правила:**\n{AUTOHELLO_CONFIG['rules']}\n\n"
            f"🍌 **Чтобы начать:** пропиши /banana"
        )
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=welcome_text,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка отправки приветствия: {e}")

async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Безопасный обработчик новых участников"""
    try:
        if context is None or not update.message:
            return
            
        for member in update.message.new_chat_members:
            if not member.is_bot:
                await send_welcome_message(
                    chat_id=update.effective_chat.id,
                    context=context,
                    user_name=member.first_name,
                    user_id=member.id
                )
    except Exception as e:
        logger.error(f"Ошибка в handle_new_members: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Безопасный обработчик текста"""
    try:
        # Защита от None context
        if context is None:
            return
            
        if not update.message or not update.message.text:
            return
            
        text = update.message.text.lower().strip()
    
        # Обработка фраз "ванито ..."
        triggers = {
            'ванито банан': banana_command,
            'ванито стата': stats_command,
            'ванито топ': leaderboard_command,
            'ванито лидерборд': leaderboard_command,
            'ванито игра': banana_game,
            'ванито ваняня баняня': banana_game,
            'ванито ваняня-баняня': banana_game
        }
        
        for trigger, handler in triggers.items():
            if text.startswith(trigger):
                await handler(update, context)
                return
        
        # Обработка банано-бомбы (проверяем активные бомбы в чате)
        await handle_banana_bomb(update, context)
        
        # Обработка модерационных команд без "/"
        for cmd in MOD_COMMANDS:
            if text.startswith(cmd):
                # Проверка прав администратора
                if update.effective_user.id not in ADMIN_IDS:
                    await update.message.reply_text("🚫 Только админы могут использовать модерационные команды!")
                    return
                    
                # Проверка что это ответ на сообщение
                if not update.message.reply_to_message:
                    await update.message.reply_text(f"ℹ️ Используйте команду '{cmd}' в ответ на сообщение пользователя")
                    return
                    
                target = update.message.reply_to_message.from_user
                target_id = target.id
                target_name = target.full_name
                
                if target_id in ADMIN_IDS:
                    await update.message.reply_text("👑 Нельзя применять команды к другим админам!")
                    return
                
                args = text[len(cmd):].strip().split()
                
                # Если команда требует подтверждения
                if cmd in CONFIRM_COMMANDS:
                    key = f"{update.effective_user.id}_{update.effective_chat.id}_{time.time()}"
                    context.bot_data.setdefault('confirm_data', {})[key] = {
                        "command": cmd,
                        "target_id": target_id,
                        "target_name": target_name,
                        "args": args,
                        "expires": time.time() + CONFIRM_TIMEOUT
                    }
                    
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Да", callback_data=f"confirm_yes_{key}"),
                        InlineKeyboardButton("❌ Нет", callback_data=f"confirm_no_{key}")
                    ]])
                    
                    await update.message.reply_text(
                        f"⚠️ Вы уверены, что хотите выполнить '{cmd}' для {target_name}?\n"
                        f"⏳ Подтверждение действительно 5 минут",
                        reply_markup=keyboard
                    )
                    
                    # Запланируем очистку данных после истечения времени
                    context.job_queue.run_once(
                        callback=cleanup_confirm_data,
                        when=CONFIRM_TIMEOUT,
                        data={"key": key},
                        name=f"confirm_cleanup_{key}"
                    )
                    return
                
                # Непосредственное выполнение команд без подтверждения
                try:
                    if cmd == "мут":
                        minutes = 60
                        reason = "Причина не указана"
                        if args:
                            if args[0].isdigit():
                                minutes = min(max(1, int(args[0])), 43200)
                                reason = " ".join(args[1:]) if len(args) > 1 else reason
                            else:
                                reason = " ".join(args)
                        
                        until_date = int(time.time()) + minutes * 60
                        release_time = datetime.now() + timedelta(minutes=minutes)
                        
                        # Правильное склонение слова "минута"
                        if minutes % 10 == 1 and minutes % 100 != 11:
                            time_word = "минуту"
                        elif 2 <= minutes % 10 <= 4 and (minutes % 100 < 10 or minutes % 100 >= 20):
                            time_word = "минуты"
                        else:
                            time_word = "минут"
                        
                        await context.bot.restrict_chat_member(
                            chat_id=update.effective_chat.id,
                            user_id=target_id,
                            permissions=ChatPermissions(can_send_messages=False),
                            until_date=until_date
                        )
                        await update.message.reply_text(
                            f"🔒 {target_name} отправлен в банановую тюрьму на {minutes} {time_word}!\n"
                            f"🧐 Причина: {reason}\n"
                            f"🔓 Освобождение: {release_time.strftime('%d.%m в %H:%M')}\n\n"
                            "Бе-бе-бе-дум! 🎵"
                        )
                    
                    elif cmd == "фри":
                        await context.bot.restrict_chat_member(
                            chat_id=update.effective_chat.id,
                            user_id=target_id,
                            permissions=ChatPermissions(can_send_messages=True)
                        )
                        await update.message.reply_text(f"🍏 {target_name} освобождён!\nМожно снова есть бананы! 🎉")
                    
                    elif cmd == "варн":
                        reason = " ".join(args) if args else "Нарушение правил"
                        # Здесь можно добавить логику варнов
                        await update.message.reply_text(
                            f"⚠️ {target_name} получил предупреждение.\n"
                            f"🍌 Причина: {reason}"
                        )
                    
                    elif cmd == "анварн":
                        # Здесь можно добавить логику снятия предупреждения
                        await update.message.reply_text(
                            f"🍏 С {target_name} снято предупреждение!\n"
                            "Миньон стал немного лучше! 🎉"
                        )
                
                except Exception as e:
                    logger.error(f"Ошибка выполнения команды {cmd}: {e}")
                    await update.message.reply_text(f"❌ Ошибка: {str(e)}")
                return

    # ДОБАВЬТЕ ЭТОТ EXCEPT БЛОК
    except Exception as e:
        logger.error(f"Ошибка в handle_text: {e}")

async def handle_banana_bomb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик банано-бомбы - выдает бананы за сообщения"""
    try:
        if not update.message or not update.message.text:
            return
            
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        # Проверяем активные бомбы в чате
        if "active_bombs" not in BANANA_STATS:
            return
            
        bomb_info = BANANA_STATS["active_bombs"].get(str(chat_id))
        if not bomb_info or time.time() > bomb_info["end_time"]:
            # Удаляем просроченную бомбу
            if str(chat_id) in BANANA_STATS["active_bombs"]:
                del BANANA_STATS["active_bombs"][str(chat_id)]
                BananaTracker.save_stats()
            return
            
        # Проверка кулдауна для пользователя (не чаще чем раз в 30 секунд)
        last_bomb_reward = bomb_info.get("last_rewards", {}).get(str(user_id), 0)
        if time.time() - last_bomb_reward < 30:
            return
            
        # Выдаем случайное количество бананов (3-5)
        bomb_reward = random.randint(3, 5)
        new_balance = BananaTracker.add_bananas(str(user_id), bomb_reward)
        
        # Обновляем время последней награды
        if "last_rewards" not in BANANA_STATS["active_bombs"][str(chat_id)]:
            BANANA_STATS["active_bombs"][str(chat_id)]["last_rewards"] = {}
        BANANA_STATS["active_bombs"][str(chat_id)]["last_rewards"][str(user_id)] = time.time()
        BananaTracker.save_stats()
        
        # Отправляем уведомление (редко, чтобы не спамить)
        if random.random() < 0.1:  # 10% шанс
            user_name = update.effective_user.username or update.effective_user.first_name
            await update.message.reply_text(
                f"💣 Банано-бомба! +{bomb_reward}🍌 для @{user_name}",
                parse_mode=None
            )
            
    except Exception as e:
        logger.error(f"Ошибка в обработчике банано-бомбы: {e}")

async def cleanup_confirm_data(context: ContextTypes.DEFAULT_TYPE):
    """Очистка данных подтверждения по истечении времени"""
    key = context.job.data["key"]
    if key in context.bot_data.get('confirm_data', {}):
        del context.bot_data['confirm_data'][key]
        logger.info(f"Очищены данные подтверждения для ключа {key}")

async def confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий кнопок подтверждения"""
    query = update.callback_query
    await query.answer()
    
    try:
        if not query.data.startswith(('confirm_yes_', 'confirm_no_')):
            return
            
        action, key = query.data.split('_')[1], query.data.split('_')[2]
        confirm_data = context.bot_data.get('confirm_data', {}).get(key)
        
        if not confirm_data:
            await query.edit_message_text("⏳ Время подтверждения истекло")
            return
            
        # Удаляем запланированную очистку
        current_jobs = context.job_queue.get_jobs_by_name(f"confirm_cleanup_{key}")
        for job in current_jobs:
            job.schedule_removal()
            
        if action == 'no':
            await query.edit_message_text("❌ Действие отменено")
            if key in context.bot_data.get('confirm_data', {}):
                del context.bot_data['confirm_data'][key]
            return
            
        # Выполнение команда
        try:
            if confirm_data['command'] == 'бан':
                await context.bot.ban_chat_member(
                    chat_id=query.message.chat_id,
                    user_id=confirm_data['target_id']
                )
                await query.edit_message_text(f"🚫 {confirm_data['target_name']} забанен навсегда!")
                
            elif confirm_data['command'] == 'кик':
                await context.bot.ban_chat_member(
                    chat_id=query.message.chat_id,
                    user_id=confirm_data['target_id'],
                    until_date=int(time.time()) + 60
                )
                await context.bot.unban_chat_member(
                    chat_id=query.message.chat_id,
                    user_id=confirm_data['target_id']
                )
                await query.edit_message_text(f"👢 {confirm_data['target_name']} выгнан из чата!")
        
        except Exception as e:
            logger.error(f"Ошибка выполнения команды {confirm_data['command']}: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)}")
        
        # Удаляем данные подтверждения
        if key in context.bot_data.get('confirm_data', {}):
            del context.bot_data['confirm_data'][key]
    
    except Exception as e:
        logger.error(f"Ошибка в confirm_callback: {e}")
        await query.answer("Произошла ошибка, попробуйте еще раз")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    BananaTracker._init_user(user_id)
    stats = BananaTracker.get_stats(user_id)
    
    # Добавляем проверку купленных ачивок
    if "inventory" in BANANA_STATS.get(user_id, {}):
        for item_id in BANANA_STATS[user_id]["inventory"]:
            item = BananaShop.get_item_info(item_id)
            if item and item.get("effect") == "achievement":
                ach_name = item["name"]
                if ach_name not in stats['achievements']:
                    stats['achievements'].append(ach_name)

    # Прогресс до следующего достижения
    next_achievement = None
    for wins in sorted(ACHIEVEMENTS.keys()):
        if wins > stats['current_streak']:
            left = wins - stats['current_streak']
            next_achievement = f"🏆 До '{ACHIEVEMENTS[wins]['name']}': {left} побед {'🍌'*left}\nНаграда: +{ACHIEVEMENTS[wins]['reward']} бананов"
            break

    msg = (
        f"🎮 <b>БАНАНА-СТАТИСТИКА МИНЬОНА:</b>\n\n"
        f"🍌 <u>Бананов:</u> {stats['bananas']}\n"
        f"🔥 <u>Текущий стрик</u>: {stats['current_streak']} побед подряд!\n"
        f"🏅 <u>Рекорд</u>: {stats['max_streak']} побед! {'👑' if stats['max_streak'] >= 100 else '💪'}\n"
        f"✅ <u>Побед</u>: {stats['wins']} {'🎯' if stats['wins'] > 50 else '👍'}\n"
        f"❌ <u>Поражений</u>: {stats['losses']} {'💩' if stats['losses'] > stats['wins'] else '🤷'}\n\n"
        f"🏅 <b>Достижения</b>:\n"
        f"{' | '.join(stats['achievements']) if stats['achievements'] else 'Пока пусто... Бе-бе-бе!'}"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


# async def minion_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     user_id = str(update.effective_user.id)
    
#     # Проверяем, первый ли раз игрок использует команду
#     if "secret_winner" not in BANANA_STATS:
#         BANANA_STATS["secret_winner"] = user_id
#         BananaTracker.add_bananas(user_id, 10)  # Начисляем 10 бананов
#         await update.message.reply_text(
#             "🍌 Поздравляю! Ты нашёл секретную команду и выиграл 10 бананов!\n"
#             "💰 Твой баланс: " + str(BananaTracker.get_stats(user_id)["bananas"]) + "\n\n"
#             "В скором времени @EasyMain лично проверит твою удачу!",
#             parse_mode="HTML"
#         )
#     else:
#         await update.message.reply_text(
#             "🍌 Упс! Кто-то уже использовал этот секретный код...\n"
#             "Но не расстраивайся — попробуй другие команды!",
#             parse_mode="HTML"
#         )
async def countdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Точный таймер с автоудалением и защитой от ошибок"""
    try:
        # Проверка аргументов
        if not context.args:
            reply = await update.message.reply_text(
                "ℹ️ Использование:\n/countdown [секунды]\nПример: /countdown 30"
            )
            await _delete_messages_after_delay(update.message, reply, context, 30)
            return

        total_seconds = int(context.args[0])
        if total_seconds > 3600:
            reply = await update.message.reply_text("🚫 Максимальное время - 1 час (3600 секунд)!")
            await _delete_messages_after_delay(update.message, reply, context, 30)
            return
        if total_seconds <= 0:
            reply = await update.message.reply_text("❌ Время должно быть больше 0 секунд!")
            await _delete_messages_after_delay(update.message, reply, context, 30)
            return

        creator = update.effective_user
        creator_ref = f"@{creator.username}" if creator.username else creator.first_name

        # Начальное сообщение
        msg = await update.message.reply_text(
            f"⏳ Таймер запущен на {total_seconds} сек.\n"
            f"👤 Создатель: {creator_ref}"
        )

        # Удаление через 30 сек после завершения
        await _delete_messages_after_delay(update.message, msg, context, 30)

        # Основной цикл с точным временем
        start_time = time.monotonic()
        last_shown = total_seconds + 1  # Гарантированно обновим первый раз
        
        while True:
            elapsed = time.monotonic() - start_time
            remaining = max(0, total_seconds - int(elapsed))
            
            # Показываем только важные моменты
            if remaining != last_shown and (
                remaining <= 10 
                or remaining in [30, 15, 5, 3, 2, 1]
                or remaining == total_seconds
            ):
                try:
                    text = f"⏳ Осталось: {remaining} сек."
                    if remaining <= 5:
                        text += "\n" + ["💥 Готово!", "⚡ Почти!", "🔥 Летим!", "🚀 Последние секунды!"][remaining % 4]
                        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
                    
                    await msg.edit_text(text)
                    last_shown = remaining
                except Exception as e:
                    if "not modified" not in str(e):
                        logger.error(f"Ошибка обновления: {e}")

            if remaining <= 0:
                break
                
            # Точная пауза (0.1 сек для быстрого реагирования)
            await asyncio.sleep(0.1)

        # Финальное сообщение
        await msg.edit_text("🎉 Время вышло! " + random.choice(["💥", "🎊", "🚀", "🍌"]))
        
        # Финальный звук через 1 сек
        await asyncio.sleep(1)
        final_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=random.choice(["Бам!", "Пум!", "Вжух!", "Готово!"]),
            reply_to_message_id=msg.message_id
        )
        await _delete_messages_after_delay(None, final_msg, context, 30)

    except (ValueError, IndexError):
        reply = await update.message.reply_text("❌ Используйте: /countdown [секунды]")
        await _delete_messages_after_delay(update.message, reply, context, 30)
    except Exception as e:
        logger.error(f"Ошибка в таймере: {e}")
        reply = await update.message.reply_text("⏱ Ошибка таймера, попробуйте снова")
        await _delete_messages_after_delay(update.message, reply, context, 30)

async def _delete_messages_after_delay(user_message, bot_message, context, delay=30):
    """Удаляет сообщения через указанное время (по умолчанию 30 сек)"""
    async def delete():
        await asyncio.sleep(delay)
        try:
            if user_message:
                await user_message.delete()
        except Exception as e:
            logger.error(f"Не удалось удалить сообщение пользователя: {e}")
        try:
            if bot_message:
                await bot_message.delete()
        except Exception as e:
            logger.error(f"Не удалось удалить сообщение бота: {e}")
    
    context.application.create_task(delete())

def escape_markdown(text):
    """Экранирует спецсимволы MarkdownV2"""
    escape_chars = '_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)


async def achievements_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    stats = BananaTracker.get_stats(user_id)
    
    unlocked = stats.get('achievements', [])
    locked = []
    
    # Проверяем все возможные ачивки
    for category in ACHIEVEMENTS.values():
        for ach in category.values():
            if isinstance(ach, dict) and ach['name'] not in unlocked:
                locked.append(ach['name'])
    
    response = (
        "🏆 <b>Ваши достижения:</b>\n\n"
        f"🔓 <b>Получено:</b> {len(unlocked)}\n"
        f"{', '.join(unlocked) if unlocked else 'Пока пусто...'}\n\n"
        f"🔒 <b>Не получено:</b> {len(locked)}\n"
        f"{', '.join(locked[:5]) + ('...' if len(locked) > 5 else '') if locked else 'Вы получили всё!'}"
    )
    
    await update.message.reply_text(response, parse_mode="HTML")

# Определяем уровни для прогресс-бара
BANANA_LEVELS = [
    {"min": 0, "max": 9, "name": "Миньон-подмастерье", "emoji": "🥚"},
    {"min": 10, "max": 49, "name": "Бананоносец", "emoji": "🍌"},
    {"min": 50, "max": 119, "name": "Сборщик связок", "emoji": "🧺"},
    {"min": 120, "max": 199, "name": "Банановед", "emoji": "📚"},
    {"min": 200, "max": 309, "name": "Горилла-учёный", "emoji": "🦍"},
    {"min": 310, "max": 499, "name": "Хранитель огненного банана", "emoji": "🔥"},
    {"min": 500, "max": 749, "name": "Король бананов", "emoji": "👑"},
    {"min": 750, "max": 999, "name": "Банановый герой", "emoji": "🚀"},
    {"min": 1000, "max": 1499, "name": "Легендарный миньон", "emoji": "💫"},
    {"min": 1500, "max": 2499, "name": "Повелитель джунглей", "emoji": "🪐"},
    {"min": 2500, "max": 3999, "name": "Эволюционировавший миньон", "emoji": "🧬"},
    {"min": 4000, "max": 9999, "name": "Бессмертный банановед", "emoji": "🌌"},
    {"min": 10000, "max": 99999999999999999999999, "name": "Ты... Как... Читер...", "emoji": "😶"},
]

def get_level_info(bananas: int):
    for level in BANANA_LEVELS[::-1]:
        if bananas >= level['min']:
            min_b = level['min']
            max_b = level['max']
            progress_ratio = (bananas - min_b) / (max_b - min_b)
            progress_ratio = max(0, min(progress_ratio, 1.0))  # от 0 до 1
            filled = int(progress_ratio * 10)
            progress_bar = "▰" * filled + "▱" * (10 - filled)
            return {
                "name": level['name'],
                "emoji": level['emoji'],
                "progress_bar": progress_bar,
                "progress_percent": int(progress_ratio * 100),
                "max_level": max_b == 9999999
            }
    return {"name": "Миньон-подмастерье", "emoji": "🥚", "progress_bar": "▱"*10, "progress_percent": 0, "max_level": False}


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    stats = BananaTracker.get_stats(user_id)
    
    # Определяем уровень пользователя
    user_bananas = int(stats.get('bananas', 0))
    for lvl in reversed(BANANA_LEVELS):
        if user_bananas >= lvl['min']:
            level = lvl
            break
    
    # Прогресс до следующего уровня
    next_lvl_min = next((l['min'] for l in BANANA_LEVELS if l['min'] > user_bananas), None)
    if next_lvl_min:
        progress_percent = int((user_bananas - level['min']) / (next_lvl_min - level['min']) * 100)
        progress_blocks = int(progress_percent / 10)
        progress_bar = '▰' * progress_blocks + '▱' * (10 - progress_blocks)
        left_bananas = next_lvl_min - user_bananas
        progress_text = f"{progress_percent}% — до след. уровня - {left_bananas} 🍌!"
        max_level_text = ""
    else:
        progress_bar = '▰' * 10
        progress_text = "100%"
        max_level_text = "🏆 Макс. уровень!"

    # Разбиваем длинное название уровня на 2 строки с тире при переносе
    level_name = f"{level['emoji']} {level['name']}"
    if len(level_name) > 16:
        level_display = level_name
    else:
        level_display = level_name

    # Достижения
    achievements = stats.get('achievements', [])
    if not achievements:
        achievements_text = "Пока пусто… Бе-бе-бе!"
    else:
        achievements_text = '\n║'.join(achievements)

    msg = (
        "╔═════════════════╗\n"
        "║ 🎮 БАНАНА-СТАТИСТИКА\n"
        "╠═════════════════╣\n"
        f"║ 🍌 Бананов: {user_bananas}\n"
        f"║ 🎖 Уровень: {level_display}\n"
        f"{'║ '+max_level_text + chr(10) if max_level_text else ''}"
        f"║ 📊 Прогресс:\n"
        f"║ {progress_bar} ({progress_text})\n"
        "╠═════════════════╣\n"
        f"║ 🔥 Текущий стрик: {stats.get('current_streak',0)}\n"
        f"║ побед подряд!\n"
        f"║ 🏅 Рекорд: {stats.get('max_streak',0)} побед! 💪\n"
        f"║ ✅ Побед: {stats.get('wins',0)} 🎯\n"
        f"║ ❌ Поражений: {stats.get('losses',0)} 🤷\n"
        "╠═════════════════╣\n"
        "║ 🏅 Достижения:\n"
        f"║ {achievements_text}\n"
        "╚═════════════════╝"
    )
    await update.message.reply_text(msg, parse_mode="HTML")


class KNBGame:
    def __init__(self, chat_id, creator_id, creator_name, opponent_id, opponent_name, thread_id=None):
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.players = {
            creator_id: {"name": creator_name, "choice": None, "emoji": "😃", "score": 0},
            opponent_id: {"name": opponent_name, "choice": None, "emoji": "😎", "score": 0}
        }
        self.message_id = None
        self.round = 1
        self.game_state = "waiting"
        self.rules = {
            '🪨': ['✂️'],  # Камень бьет ножницы
            '✂️': ['🍌'],  # Ножницы бьют бумагу
            '🍌': ['🪨']   # Бумага бьет камень
        }
        
        # Миньон-фразы
        self.phrases = {
            "start": [
                "Бана-бана-бана! 🎵",
                "Боп-боп-банана! 🎶",
                "Ми-ньо-ны в деле! 🍌"
            ],
            "choices": {
                '🪨': "камешек! Тя-же-лый!",
                '✂️': "ножнички! Щелк-щелк!",
                '🍌': "банану! Ням-ням!"
            }
        }

    async def send_game_message(self, bot, text, reply_markup=None):
        """Отправка/обновление игрового сообщения"""
        try:
            if not self.message_id:
                msg = await bot.send_message(
                    chat_id=self.chat_id,
                    text=f"🍌 {random.choice(self.phrases['start'])}\n\n{text}",
                    reply_markup=reply_markup,
                    message_thread_id=self.thread_id
                )
                self.message_id = msg.message_id
            else:
                await bot.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    text=f"🍌 {random.choice(self.phrases['start'])}\n\n{text}",
                    reply_markup=reply_markup
                )
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {e}")

    def get_choices_keyboard(self):
        """Клавиатура для выбора"""
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🪨 Камешек", callback_data="knb_🪨")],
            [InlineKeyboardButton("✂️ Ножнички", callback_data="knb_✂️")],
            [InlineKeyboardButton("🍌 Банана", callback_data="knb_🍌")],
            [InlineKeyboardButton("🎵 Баняня-песня", callback_data="knb_song")]
        ])

    async def process_choice(self, bot, player_id, choice):
        """Обработка выбора игрока"""
        if player_id not in self.players:
            return False
            
        if self.players[player_id]["choice"] is not None:
            return False
            
        self.players[player_id]["choice"] = choice
        
        # Уведомление без раскрытия выбора
        await self.send_game_message(
            bot,
            f"{self.players[player_id]['emoji']} {self.players[player_id]['name']} готов!\n"
            f"Раунд {self.round}\n"
            "Ожидаем второго миньона...",
            self.get_choices_keyboard()
        )
        
        # Проверка на завершение раунда
        if all(p["choice"] is not None for p in self.players.values()):
            await self.show_result(bot)
            
        return True

    async def show_result(self, bot):
        """Определение и отображение результата раунда"""
        p1, p2 = list(self.players.values())
        logger.info(f"Определение результата: {p1['name']} ({p1['choice']}) vs {p2['name']} ({p2['choice']})")
        
        if p1["choice"] == p2["choice"]:
            result_text = "🤝 Ничья! Миньоны в замешательстве!"
            logger.info("Результат: ничья")
        else:
            if p2["choice"] in self.rules[p1["choice"]]:
                winner, loser = p1, p2
            else:
                winner, loser = p2, p1
            
            winner["score"] += 1
            result_text = f"🎉 {winner['name']} побеждает!\n🏆 Счет: {p1['name']} {p1['score']}-{p2['score']} {p2['name']}"
            logger.info(f"Победитель: {winner['name']} (выбор: {winner['choice']})")
            
            # Обновляем стрики
            winner_id = next(uid for uid, p in self.players.items() if p['name'] == winner['name'])
            loser_id = next(uid for uid, p in self.players.items() if p['name'] == loser['name'])
            
            BananaTracker.update_streak(str(winner_id), is_win=True)
            BananaTracker.update_streak(str(loser_id), is_win=False)
            
            # Проверяем достижения
            achievement_msg = BananaTracker.check_achievements(str(winner_id))
            if achievement_msg:
                result_text += f"\n\n{achievement_msg}"
                logger.info(f"Достижение разблокировано: {achievement_msg}")
        
        await self.send_game_message(bot, result_text, reply_markup=None)
        
        # Сброс выбора для следующего раунда
        for player in self.players.values():
            player["choice"] = None
        self.round += 1
        logger.info(f"Начинаем раунд {self.round}")

async def process_choice(self, bot, player_id, choice):
    """Обработка выбора с обязательным логированием"""
    if player_id not in self.players:
        logger.error(f"Игрок {player_id} не найден в текущей игре")
        return False
        
    if self.players[player_id]["choice"] is not None:
        logger.warning(f"Игрок {player_id} уже сделал выбор")
        return False
        
    self.players[player_id]["choice"] = choice
    
    # Обязательное логирование выбора
    choices_ru = {
        '🪨': 'камень',
        '✂️': 'ножницы', 
        '🍌': 'бумага (банан)'
    }
    
    player = self.players[player_id]
    logger.info(
        f"=== НАЖАТИЕ КНОПКИ ===\n"
        f"Игрок: {player['name']}\n"
        f"Выбрал: {choices_ru.get(choice, choice)}\n"
        f"Раунд: {self.round}\n"
        f"Время: {datetime.now().strftime('%H:%M:%S')}\n"
        f"======================"
    )
    
    await self.send_game_message(
        bot,
        f"{player['emoji']} {player['name']} готов!\n"
        f"Раунд {self.round}\n"
        "Ожидаем второго игрока...",
        self.get_choices_keyboard()
    )
    
    if all(p["choice"] is not None for p in self.players.values()):
        await self.show_result(bot)
        
    return True

async def knb_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback'ов"""
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    
    # Обработка отказа от игры
    if data == "knb_decline":
        if chat_id in pending_knb_games and user_id == pending_knb_games[chat_id]['opponent_id']:
            await query.edit_message_text(
                "🍌 Миньон испугался и убежал! Бе-бе-бе!",
                reply_markup=None
            )
            del pending_knb_games[chat_id]
        return
    
    # Обработка принятия игры
    # Обработка принятия игры
    if data.startswith("knb_accept"):
        creator_id = int(data.split('_')[2])
        invite = pending_knb_games.get(chat_id)
        
        if not invite or invite['creator_id'] != creator_id:
            await query.answer("🍌 Вызов устарел! Бе-бе-бе!")
            return
        
        if user_id != invite['opponent_id']:
            await query.answer("🍌 Не для тебя, миньон!")
            return
        
        game = KNBGame(
            chat_id=chat_id,
            creator_id=creator_id,
            creator_name=invite['creator_name'],
            opponent_id=user_id,
            opponent_name=invite['opponent_name'],
            thread_id=invite['thread_id']
        )
        
        active_knb_games[chat_id] = game
        del pending_knb_games[chat_id]
        
        await game.send_game_message(
            context.bot,
            f"🍌 БАНАНОВЫЙ БОЙ!\n\n"
            f"😃 {invite['creator_name']} vs {invite['opponent_name']} 😎\n\n"
            "Выбирайте, миньоны!",
            game.get_choices_keyboard()
        )
    
    # Обработка выбора в игре
    elif data in ["knb_🪨", "knb_✂️", "knb_🍌"]:
        game = active_knb_games.get(chat_id)
        if not game:
            await query.answer("🍌 Игра не найдена! Ой-ой!")
            return
            
        if user_id not in game.players:
            await query.answer("🍌 Ты не участник!")
            return
            
        choice = data.split('_')[1]
        await game.process_choice(context.bot, user_id, choice)
    
    # Обработка баняня-песни
    elif data == "knb_song":
        await query.answer("🎵 Ба-на-на-на-на-нааа! 🎶")

# Перенесите определение функции start_knb перед setup_knb_handlers

async def start_knb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /knb"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "🍌 Ба-на-на! Чтобы играть, ответь на сообщение миньона командой /knb\n\n"
            "Правила миньонов:\n"
            "🪨 Камешек > ✂️ Ножнички\n"
            "✂️ Ножнички > 🍌 Банана\n"
            "🍌 Банана > 🪨 Камешек"
        )
        return
    
    opponent = update.message.reply_to_message.from_user
    if opponent.id == user_id:
        await update.message.reply_text("🍌 Миньон не может играть сам с собой! Бе-бе-бе!")
        return
    
    if opponent.id == BANNED_PLAYER_IDs:
        await update.message.reply_text("🍌 Попробуй ответить на сообщение другого человека!")
        return
        
    message_thread_id = update.message.message_thread_id if update.message.is_topic_message else None
    
    creator_name = update.effective_user.full_name
    if update.effective_user.username:
        creator_name = f"@{update.effective_user.username}"
    
    opponent_name = opponent.full_name
    if opponent.username:
        opponent_name = f"@{opponent.username}"
    
    msg = await update.message.reply_text(
        f"🍌 {creator_name} вызывает {opponent_name} на банановый поединок!\n\n"
        "Примешь вызов, миньон?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🍌 Дааа!", callback_data=f"knb_accept_{user_id}")],
            [InlineKeyboardButton("😱 Нет!", callback_data="knb_decline")]
        ]),
        message_thread_id=message_thread_id
    )
    
    pending_knb_games[chat_id] = {
        'message_id': msg.message_id,
        'creator_id': user_id,
        'opponent_id': opponent.id,
        'thread_id': message_thread_id,
        'creator_name': creator_name,
        'opponent_name': opponent_name
    }

def setup_knb_handlers(app: Application):
    """Регистрация обработчиков"""
    app.add_handler(CommandHandler("knb", start_knb))
    app.add_handler(CallbackQueryHandler(knb_callback, pattern="^knb_"))

async def log_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.text:
        print(f"LOG: Сообщение от {update.effective_user.id}: {update.message.text}")

# ========== КОМАНДЫ МОДЕРАЦИИ ==========
async def extract_minion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Извлекает информацию о пользователе из сообщения"""
    try:
        # Проверка, является ли сообщение ответом
        if not update.message or not update.message.reply_to_message:
            await update.message.reply_text("🚫 Нужно ответить на сообщение пользователя!")
            return None, None

        minion = update.message.reply_to_message.from_user
        
        # Проверка на бота
        if minion.is_bot:
            await update.message.reply_text("🤖 Нельзя взаимодействовать с ботами!")
            return None, None

        # Формирование имени
        minion_name = f"@{minion.username}" if minion.username else minion.full_name
        
        # Специальный случай (можно удалить если не нужно)
        if minion.username and minion.username.lower() == "wh1sky666":
            minion_name = "Ростислав"

        return minion.id, minion_name

    except Exception as e:
        logger.error(f"Ошибка в extract_minion: {e}")
        await update.message.reply_text("🍌 Ошибка при определении пользователя!")
        return None, None

async def banana_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🍌 Ты не Главный Банан!")
        return

    minion_id, minion_name = await extract_minion(update, context)
    if not minion_id:
        return

    try:
        if minion_id in ADMIN_IDS:
            await update.message.reply_text("👑 Нельзя предупреждать Вождей!")
            return

        uid = str(minion_id)
        if uid not in BANANA_STATS:
            BANANA_STATS[uid] = {
                'bananas': 0,
                'total_earned': 0,
                'wins': 0,
                'losses': 0,
                'achievements': [],
                'current_streak': 0,
                'max_streak': 0,
                'flags': [],
                'warns': 0
            }

        # Увеличиваем варны
        BANANA_STATS[uid]['warns'] = BANANA_STATS[uid].get('warns', 0) + 1
        warn_count = BANANA_STATS[uid]['warns']

        # Сохраняем в файл
        BananaTracker.save_stats()

        if warn_count % 10 == 1 and warn_count % 100 != 11:
            warn_word = "предупреждение"
        elif 2 <= warn_count % 10 <= 4 and (warn_count % 100 < 10 or warn_count % 100 >= 20):
            warn_word = "предупреждения"
        else:
            warn_word = "предупреждений"

        if warn_count == 1:
            response = (f"⚠️ Миньон получил 1-е предупреждение!\n"
                       "Пока без наказания, но будь осторожен!\n"
                       "Бе-бе-бе-бе-дум! 🎶")
        elif warn_count == 2:
            mute_minutes = 30
            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=minion_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int(time.time()) + mute_minutes * 60
            )
            response = (f"🔇 Миньон получил 2-е предупреждение!\n"
                       f"Мут на 30 минут за плохое поведение!\n"
                       "Боп-боп-боп-тихо! 🤫")
        elif warn_count == 3:
            mute_minutes = 60 * 2
            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=minion_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int(time.time()) + mute_minutes * 60
            )
            response = (f"🔕 Миньон получил 3-е предупреждение!\n"
                       f"Мут на 2 часа за банановый беспредел!\n"
                       "Бе-бе-бе-молчи! 🤐")
        elif warn_count == 4:
            mute_minutes = 60 * 4
            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=minion_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int(time.time()) + mute_minutes * 60
            )
            response = (f"🔕 Миньон получил 4-е предупреждение!\n"
                       f"Мут на 4 часа за повторные нарушения!\n"
                       "Банановая изоляция! 🍌🔇")
        elif warn_count == 5:
            mute_minutes = 60 * 6
            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=minion_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int(time.time()) + mute_minutes * 60
            )
            response = (f"🔕 Миньон получил 5-е предупреждение!\n"
                       f"Мут на 6 часов за упрямство!\n"
                       "Банановая тишина! 🍌🔇")
        elif warn_count == 6:
            mute_minutes = 60 * 12
            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=minion_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int(time.time()) + mute_minutes * 60
            )
            response = (f"🔕 Миньон получил 6-е предупреждение!\n"
                       f"Мут на 12 часов за систематические нарушения!\n"
                       "Полдня без бананов! 🕛🍌")
        elif warn_count == 7:
            mute_minutes = 60 * 24
            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=minion_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int(time.time()) + mute_minutes * 60
            )
            response = (f"🔕 Миньон получил 7-е предупреждение!\n"
                       f"Мут на 24 часа за хронические нарушения!\n"
                       "Целый день в банановой изоляции! 🌞➡️🌜")
        elif warn_count == 8:
            mute_minutes = 60 * 48
            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=minion_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int(time.time()) + mute_minutes * 60
            )
            response = (f"🔕 Миньон получил 8-е предупреждение!\n"
                       f"Мут на 2 дня за особо злостные нарушения!\n"
                       "Два дня без бананового веселья! 🚫🍌")
        elif warn_count == 9:
            mute_minutes = 60 * 72
            await context.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=minion_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=int(time.time()) + mute_minutes * 60
            )
            response = (f"🔕 Миньон получил 9-е предупреждение!\n"
                       f"Мут на 3 дня - последний шанс исправиться!\n"
                       "Подумай о своем поведении! 🤔🍌")
        elif warn_count >= 10:
            await context.bot.ban_chat_member(
                chat_id=update.effective_chat.id,
                user_id=minion_id
            )
            del context.chat_data[f"warns_{minion_id}"]
            response = (f"💀 Миньон собрал 10 предупреждений!\n"
                       "🚀 Перманентный БАН за хронические нарушения!\n"
                       "Банановый ад тебе! 🔥🍌")
        else:
            response = (f"⚠️ Миньон теперь имеет {warn_count} {warn_word}!\n"
                       f"Следующее наказание будет строже!\n"
                       "Бе-бе-бе-осторожно! 🎵")

        await update.message.reply_text(response)
    except Exception as e:
        await update.message.reply_text(f"💥 Ошибка: {e}")

async def banana_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверить количество предупреждений"""
    try:
        # Загружаем варны из файла
        try:
            with open(BANANA_STATS_FILE, "r", encoding="utf-8") as f:
                warns = json.load(f)
        except FileNotFoundError:
            warns = {}

        # Определяем, чьи варны проверяем
        if update.message.reply_to_message:
            minion = update.message.reply_to_message.from_user
            minion_id = minion.id
            minion_name = minion.full_name
        else:
            minion_id = update.effective_user.id
            minion_name = update.effective_user.full_name

        # Получаем количество варнов
        warn_count = warns.get(str(minion_id), 0)

        # Склонение слова "предупреждение"
        if warn_count % 10 == 1 and warn_count % 100 != 11:
            warn_word = "предупреждение"
        elif 2 <= warn_count % 10 <= 4 and (warn_count % 100 < 10 or warn_count % 100 >= 20):
            warn_word = "предупреждения"
        else:
            warn_word = "предупреждений"

        # Формируем ответ
        if warn_count == 0:
            response = f"✅ {minion_name} не имеет предупреждений!\nЧист как банан! 🍌"
        else:
            response = (f"⚠️ {minion_name} имеет {warn_count} {warn_word}!\n"
                        f"Следующее наказание при {warn_count + 1} предупреждении.\n"
                        "Бе-бе-бе-осторожно! 🎵")

        await update.message.reply_text(response)

    except Exception as e:
        await update.message.reply_text(f"💥 Ошибка: {e}")
        logger.error(f"Ошибка в banana_warns: {e}")


# -------------------- /unwarn --------------------
async def banana_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Снять 1 предупреждение"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🍌 Ты не Главный Банан!")
        return

    # Определяем, на кого снять варн
    if update.message.reply_to_message:
        minion = update.message.reply_to_message.from_user
        minion_id = minion.id
        minion_name = minion.full_name
    else:
        await update.message.reply_text("⚠️ Снять предупреждение можно только ответом на сообщение игрока.")
        return

    try:
        # Загружаем варны
        try:
            with open(BANANA_STATS_FILE, "r", encoding="utf-8") as f:
                warns = json.load(f)
        except FileNotFoundError:
            warns = {}

        # Снимаем варн
        current_warns = warns.get(str(minion_id), 0)
        if current_warns > 0:
            warns[str(minion_id)] = current_warns - 1
            new_count = warns[str(minion_id)]

            # Сохраняем обратно
            with open(BANANA_STATS_FILE, "w", encoding="utf-8") as f:
                json.dump(warns, f, indent=4, ensure_ascii=False)

            # Склонение слова
            if new_count % 10 == 1 and new_count % 100 != 11:
                warn_word = "предупреждение"
            elif 2 <= new_count % 10 <= 4 and (new_count % 100 < 10 or new_count % 100 >= 20):
                warn_word = "предупреждения"
            else:
                warn_word = "предупреждений"

            await update.message.reply_text(
                f"🍏 Снято 1 предупреждение {minion_name}! Теперь {new_count} {warn_word}\n"
                "Миньон стал немного лучше! 🎉"
            )
        else:
            await update.message.reply_text(f"✅ У {minion_name} нет предупреждений!\nОн чист как банан! 🍌")

    except Exception as e:
        await update.message.reply_text(f"💥 Ошибка: {e}")
        logger.error(f"Ошибка в banana_unwarn: {e}")

async def banana_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Ты не Вождь Миньонов!")
        return

    minion_id, minion_name = await extract_minion(update, context)
    if not minion_id:
        return

    try:
        if minion_id in ADMIN_IDS:
            await update.message.reply_text("👑 Нельзя банить других Вождей!")
            return

        await context.bot.ban_chat_member(chat_id=update.effective_chat.id, user_id=minion_id)
        if f"warns_{minion_id}" in context.chat_data:
            del context.chat_data[f"warns_{minion_id}"]
        await update.message.reply_text("🍌 БА-БАХ! Миньон улетел в банановый космос!\nБАН-АН-АН! 🚀")
    except Exception as e:
        await update.message.reply_text(f"💥 Ошибка: {e}")

async def minion_jail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Посадить пользователя в тюрьму на время с указанием причины"""
    try:
        # Проверка прав администратора
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("👮 Ты не Надзиратель!")
            return

        # Получаем данные пользователя
        minion_id, minion_name = await extract_minion(update, context)
        if minion_id is None:
            return

        # Проверка на админа
        if minion_id in ADMIN_IDS:
            await update.message.reply_text("👑 Нельзя сажать в тюрьму Вождей!")
            return

        # Время по умолчанию (60 минут)
        minutes = 60
        reason = "Причина не указана"

        if context.args:
            # Если первый аргумент — число, это время
            if context.args[0].isdigit():
                minutes = min(max(1, int(context.args[0])), 43200)  # от 1 мин до 30 дней
                if len(context.args) > 1:
                    reason = " ".join(context.args[1:])
            else:
                # Если нет числа, значит всё — причина
                reason = " ".join(context.args)

        # Применяем ограничения
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=minion_id,
            permissions=ChatPermissions(
                can_send_messages=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False
            ),
            until_date=int(time.time()) + minutes * 60
        )

        # Выбор правильного слова
        if minutes % 10 == 1 and minutes % 100 != 11:
            time_word = "минуту"
        elif 2 <= minutes % 10 <= 4 and (minutes % 100 < 10 or minutes % 100 >= 20):
            time_word = "минуты"
        else:
            time_word = "минут"

        release_time = datetime.now() + timedelta(minutes=minutes)

        await update.message.reply_text(
            f"🔒 {minion_name} отправлен в банановую тюрьму на {minutes} {time_word}!\n"
            f"🧐 Причина: {reason}\n"
            f"🔓 Освобождение: {release_time.strftime('%d.%m в %H:%M')}\n\n"
            "Бе-бе-бе-дум! 🎵"
        )

    except Exception as e:
        logger.error(f"Ошибка в minion_jail: {e}")
        await update.message.reply_text("🍌 Ошибка! Не удалось посадить в тюрьму.")
       
async def banana_free(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🔓 Ты не Хранитель Ключей!")
        return

    minion_id, minion_name = await extract_minion(update, context)
    if not minion_id:
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=minion_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
        )
        await update.message.reply_text("🍏 Миньон освобождён!\nМожно снова есть бананы! 🎉")
    except Exception as e:
        await update.message.reply_text(f"💥 Ошибка: {e}")

async def minion_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("👢 Тебе нельзя пинать миньонов!")
        return

    minion_id, minion_name = await extract_minion(update, context)
    if not minion_id:
        return

    try:
        if minion_id in ADMIN_IDS:
            await update.message.reply_text("👑 Нельзя пинать Вождей!")
            return

        await context.bot.ban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=minion_id,
            until_date=int(time.time()) + 60
        )
        await context.bot.unban_chat_member(chat_id=update.effective_chat.id, user_id=minion_id)
        await update.message.reply_text("👢 Миньон получил пинка!\nПусть остынет снаружи! 🌬️")
    except Exception as e:
        await update.message.reply_text(f"💥 Ошибка: {e}")

async def zov_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Вызов администрации! @vanobanan @minevik @zombirar @EasyMain   ")

# ========== ИГРА ВАНЯНЯ-БАНЯНЯ ==========
class TicTacToeGame:
    def __init__(self, creator_id, creator_name, opponent_id, opponent_name, chat_id, message_thread_id=None):
        self.creator_id = creator_id
        self.creator_name = creator_name
        self.opponent_id = opponent_id
        self.opponent_name = opponent_name
        self.chat_id = chat_id
        self.thread_id = message_thread_id
        self.board = [[' ' for _ in range(3)] for _ in range(3)]
        self.current_player = '🅱️'  # Ваняня ходит первым
        self.game_message_id = None
        self.last_move_time = time.time()

    def get_board_buttons(self):
        """Создает клавиатуру с текущим состоянием доски"""
        keyboard = []
        for i in range(3):
            row = []
            for j in range(3):
                row.append(InlineKeyboardButton(
                    self.board[i][j] if self.board[i][j] != ' ' else ' ',
                    callback_data=f"move_{i}_{j}"
                ))
            keyboard.append(row)
        return keyboard

    def make_move(self, row, col, player):
        """Выполняет ход, если он допустим"""
        if 0 <= row < 3 and 0 <= col < 3 and self.board[row][col] == ' ':
            self.board[row][col] = player
            self.current_player = '🍌' if player == '🅱️' else '🅱️'
            self.last_move_time = time.time()
            return True
        return False

    def check_winner(self):
        """Проверяет, есть ли победитель"""
        # Проверка строк
        for row in self.board:
            if row[0] != ' ' and row[0] == row[1] == row[2]:
                return row[0]
        
        # Проверка столбцов
        for col in range(3):
            if self.board[0][col] != ' ' and self.board[0][col] == self.board[1][col] == self.board[2][col]:
                return self.board[0][col]
        
        # Проверка диагоналей
        if self.board[0][0] != ' ' and self.board[0][0] == self.board[1][1] == self.board[2][2]:
            return self.board[0][0]
        if self.board[0][2] != ' ' and self.board[0][2] == self.board[1][1] == self.board[2][0]:
            return self.board[0][2]
        
        # Проверка на ничью
        if all(cell != ' ' for row in self.board for cell in row):
            return 'draw'
        
        return None

async def delete_pending_game_callback(context: ContextTypes.DEFAULT_TYPE):
    """Удаляет ожидающую игру по таймеру"""
    job = context.job
    chat_id = job.data['chat_id']
    message_id = job.data['message_id']
    creator_id = job.data['creator_id']
    
    if chat_id in pending_games and pending_games[chat_id]['message_id'] == message_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")
        
        del pending_games[chat_id]
        if creator_id in user_games:
            del user_games[creator_id]

async def cleanup_game(chat_id, context):
    """Очищает данные игры после завершения"""
    if chat_id in active_games:
        game = active_games[chat_id]
        if game.creator_id in user_games:
            del user_games[game.creator_id]
        if game.opponent_id in user_games:
            del user_games[game.opponent_id]
        del active_games[chat_id]

async def banana_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🍌 Начать игру в Ваняня-Баняня (крестики-нолики)"""
    try:
        # Проверка типа чата
        if update.message.chat.type == "private":
            await update.message.reply_text("🍌 Игра работает только в группах и супергруппах! Бе-бе-бе!")
            return

        creator_id = update.effective_user.id
        creator_name = update.effective_user.first_name


        # Проверка формата команды
        if not update.message.reply_to_message:
            await update.message.reply_text(
                "🍌 Чтобы начать игру, ответь на сообщение соперника командой /game\n\n"
                "Как играть:\n"
                "1. Найди соперника\n"
                "2. Ответь на его сообщение\n"
                "3. Напиши /game\n\n"
                "Бана-на-на! 🎶"
            )
            return

        opponent = update.message.reply_to_message.from_user
        opponent_id = opponent.id
        opponent_name = opponent.first_name

        # Проверка валидности соперника
        if opponent_id == creator_id:
            await update.message.reply_text("🍌 Нельзя играть сам с собой! Найди другого миньона!")
            return

        if opponent_id == context.bot.id:
            await update.message.reply_text("🍌 Я всего лишь бот, не могу играть с тобой! Бе-бе-бе!")
            return


        if opponent_id == BANNED_PLAYER_IDs:
            await update.message.reply_text("🍌 Попробуй ответить на сообщение другого человека, так получится с кем-нибудь поиграть!")
            return 
            
        # Получаем ID топика (если есть)
        message_thread_id = update.message.message_thread_id if update.message.is_topic_message else None

        # Формируем клавиатуру
        keyboard = [
            [
                InlineKeyboardButton("🍌 Принять вызов!", callback_data=f"accept_{creator_id}_{opponent_id}"),
                InlineKeyboardButton("😱 Отказаться", callback_data=f"cancel_{creator_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Формируем имена с @username если есть
        creator_display = f"@{update.effective_user.username}" if update.effective_user.username else creator_name
        opponent_display = f"@{opponent.username}" if opponent.username else opponent_name

        # Отправляем предложение игры
        message = await update.message.reply_text(
            f"🍌 {creator_display} вызывает {opponent_display} на банановую дуэль!\n"
            "🎮 Ваняня-Баняня (крестики-нолики)\n\n"
            "🅱️ - Ваняня (ходит первым)\n"
            "🍌 - Баняня\n\n"
            "У тебя 3 минуты чтобы принять вызов!\n"
            "Бана-бана-бана! 🎵",
            reply_markup=reply_markup,
            message_thread_id=message_thread_id
        )

        # Сохраняем ожидающую игру
        pending_games[update.effective_chat.id] = {
            'message_id': message.message_id,
            'creator_id': creator_id,
            'opponent_id': opponent_id,
            'thread_id': message_thread_id,
            'creator_name': creator_name,
            'opponent_name': opponent_name
        }
        user_games[creator_id] = update.effective_chat.id

        # Удаляем команду /game
        try:
            await update.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

        # Устанавливаем таймер на удаление предложения игры (3 минуты)
        if context.job_queue:
            context.job_queue.run_once(
                delete_pending_game_callback, 
                180,
                chat_id=update.effective_chat.id, 
                data={
                    'chat_id': update.effective_chat.id,
                    'message_id': message.message_id,
                    'creator_id': creator_id,
                    'thread_id': message_thread_id
                },
                name=f"pending_{update.effective_chat.id}_{message.message_id}"
            )

    except Exception as e:
        logger.error(f"Ошибка в banana_game: {e}")
        await update.message.reply_text("🍌 Ой, что-то пошло не так! Попробуй еще раз позже. Бе-бе-бе!")

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    data = query.data

    try:
        if data.startswith("accept_"):
            # Обработка принятия игры
            _, creator_id, opponent_id = data.split('_')
            creator_id = int(creator_id)
            opponent_id = int(opponent_id)

            if user_id != opponent_id:
                await query.answer("Этот вызов не для тебя! 🍌")
                return

            # Получаем информацию об игроках
            try:
                creator = await context.bot.get_chat(creator_id)
                opponent = await context.bot.get_chat(opponent_id)
            except Exception as e:
                logger.error(f"Ошибка получения данных игроков: {e}")
                await query.answer("🍌 Ошибка при запуске игры")
                return

            # Создаем новую игру
            game = TicTacToeGame(
                creator_id=creator_id,
                creator_name=creator.first_name,
                opponent_id=opponent_id,
                opponent_name=opponent.first_name,
                chat_id=chat_id,
                message_thread_id=query.message.message_thread_id
            )
            
            active_games[chat_id] = game
            user_games[creator_id] = chat_id
            user_games[opponent_id] = chat_id

            # Обновляем сообщение
            try:
                await query.edit_message_text(
                    text=f"🍌 Игра началась!\n\n"
                         f"🅱️ {game.creator_name} (Ваняня)\n"
                         f"🍌 {game.opponent_name} (Баняня)\n\n"
                         f"Сейчас ходит: {game.creator_name} (🅱️)",
                    reply_markup=InlineKeyboardMarkup(game.get_board_buttons())
                )
                game.game_message_id = query.message.message_id
            except Exception as e:
                logger.error(f"Ошибка при обновлении сообщения: {e}")
                await query.answer("🍌 Ошибка при запуске игры")

        elif data.startswith("cancel_"):
            # Обработка отмены игры
            creator_id = int(data.split('_')[1])
            if user_id != creator_id:
                await query.answer("Только создатель может отменить игру!")
                return

            try:
                if chat_id in pending_games:
                    del pending_games[chat_id]
                if creator_id in user_games:
                    del user_games[creator_id]

                await query.edit_message_text("🍌 Игра отменена! Баняня испугался и убежал! 🏃‍♂️‍➡️")
            except Exception as e:
                logger.error(f"Ошибка при отмене игры: {e}")
                await query.answer("🍌 Ошибка при отмене игры")

        elif data.startswith("move_"):
            # Обработка хода в игре
            game = active_games.get(chat_id)
            if not game:
                await query.answer("Игра не найдена! 🍌")
                return

            current_player_id = game.creator_id if game.current_player == '🅱️' else game.opponent_id
            if user_id != current_player_id:
                await query.answer("Сейчас не твой ход! 🕒")
                return

            try:
                _, row, col = data.split('_')
                row, col = int(row), int(col)

                if game.make_move(row, col, game.current_player):
                    winner = game.check_winner()
                    keyboard = game.get_board_buttons()

                    if winner is not None:
                        if winner == 'draw':
                            result_text = "🍌 Ничья! Бананы остаются целыми! 🤝"
                        else:
                            is_creator_win = winner == '🅱️'
                            winner_id = game.creator_id if is_creator_win else game.opponent_id
                            winner_name = game.creator_name if is_creator_win else game.opponent_name
                            loser_id = game.opponent_id if is_creator_win else game.creator_id
                            
                            try:
                                BananaTracker.update_streak(str(winner_id), is_win=True)
                                BananaTracker.update_streak(str(loser_id), is_win=False)
                                achievement_msg = BananaTracker.check_achievements(str(winner_id))
                                result_text = (
                                    f"🍌 Победил {winner_name}! "
                                    f"{winner} торжествует! 🎉"
                                    f"{'\n\n' + achievement_msg if achievement_msg else ''}"
                                )
                            except Exception as e:
                                logger.error(f"Ошибка обновления статистики: {e}")
                                result_text = f"🍌 Победил {winner_name}! {winner} торжествует! 🎉"

                        await query.edit_message_text(
                            text=(
                                f"🍌 Битва завершена!\n\n"
                                f"{result_text}\n\n"
                                f"🅱️ {game.creator_name}\n🍌 {game.opponent_name}"
                            ),
                            reply_markup=None
                        )
                        await cleanup_game(chat_id, context)
                    else:
                        current_player_id = game.creator_id if game.current_player == '🅱️' else game.opponent_id
                        current_player_name = game.creator_name if current_player_id == game.creator_id else game.opponent_name
                        current_symbol = game.current_player
                        
                        await query.edit_message_text(
                            text=f"🍌 Битва продолжается!\n\n"
                                 f"🅱️ {game.creator_name}\n"
                                 f"🍌 {game.opponent_name}\n\n"
                                 f"Сейчас ходит: {current_player_name} ({current_symbol})",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                else:
                    await query.answer("Неверный ход! Попробуй другую клетку! ❌")
            except Exception as e:
                logger.error(f"Ошибка обработки хода: {e}")
                await query.answer("🍌 Ошибка при обработке хода")

    except Exception as e:
        logger.error(f"Критическая ошибка в button_click: {e}")
        try:
            await query.answer("🍌 Произошла ошибка, попробуйте позже")
        except:
            pass

# Система улучшений
UPGRADES = {
    "banana_bag": {
        "name": "📦 Банановый мешок",
        "description": "Постоянно +{}🍌 к каждому сбору",
        "max_level": 3,
        "prices": [100, 250, 500],
        "effects": [1, 2, 3]  # +1, +2, +3 банана
    },
    "banana_totem": {
        "name": "🏆 Банановый тотем", 
        "description": "Увеличивает шансы редких бананов: золотой +{}%, алмазный +{}%",
        "max_level": 3,
        "prices": [150, 400, 1000],
        "effects": [
            (4.0, 0.66),   # Уровень 1: +4% золотой, +0.66% алмазный
            (8.0, 1.32),   # Уровень 2: +8% золотой, +1.32% алмазный  
            (12.0, 1.98)   # Уровень 3: +12% золотой, +1.98% алмазный
        ]
    }
}

async def upgrades_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает и позволяет покупать улучшения"""
    user_id = str(update.effective_user.id)
    user_stats = BananaTracker.get_stats(user_id)
    
    # Текущие уровни улучшений
    bag_level = user_stats.get('banana_bag_level', 0)
    totem_level = user_stats.get('banana_totem_level', 0)
    
    # Сообщение с текущими улучшениями
    message = (
        "🔧 <b>Система улучшений</b>\n\n"
        f"📦 <b>Банановый мешок:</b> Уровень {bag_level}/{UPGRADES['banana_bag']['max_level']}\n"
    )
    
    if bag_level > 0:
        message += f"   → +{UPGRADES['banana_bag']['effects'][bag_level-1]}🍌 к каждому сбору\n"
    
    message += f"\n🏆 <b>Банановый тотем:</b> Уровень {totem_level}/{UPGRADES['banana_totem']['max_level']}\n"
    
    if totem_level > 0:
        gold_bonus, diamond_bonus = UPGRADES['banana_totem']['effects'][totem_level-1]
        message += f"   → Золотой банан: +{gold_bonus}%\n"
        message += f"   → Алмазный банан: +{diamond_bonus}%\n"
    
    # Кнопки для покупки улучшений
    keyboard = []
    
    # Кнопки для бананового мешка
    if bag_level < UPGRADES['banana_bag']['max_level']:
        next_level = bag_level + 1
        price = UPGRADES['banana_bag']['prices'][next_level-1]
        keyboard.append([InlineKeyboardButton(
            f"📦 Купить {next_level} уровень мешка - {price}🍌", 
            callback_data=f"buy_upgrade_banana_bag_{next_level}"
        )])
    
    # Кнопки для бананового тотема
    if totem_level < UPGRADES['banana_totem']['max_level']:
        next_level = totem_level + 1
        price = UPGRADES['banana_totem']['prices'][next_level-1]
        keyboard.append([InlineKeyboardButton(
            f"🏆 Купить {next_level} уровень тотема - {price}🍌",
            callback_data=f"buy_upgrade_banana_totem_{next_level}"
        )])
    
    keyboard.append([InlineKeyboardButton("❌ Закрыть", callback_data="close_upgrades")])
    
    await update.message.reply_text(
        message,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def upgrades_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок улучшений"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "close_upgrades":
        await query.message.delete()
        return
    
    if query.data.startswith("buy_upgrade_"):
        try:
            _, _, upgrade_type, level = query.data.split("_")
            level = int(level)
            user_id = str(query.from_user.id)
            
            upgrade = UPGRADES.get(upgrade_type)
            if not upgrade:
                await query.answer("❌ Улучшение не найдено!", show_alert=True)
                return
            
            # Проверяем можно ли купить этот уровень
            current_level = BANANA_STATS[user_id].get(f"{upgrade_type}_level", 0)
            if level != current_level + 1:
                await query.answer("❌ Сначала купите предыдущие уровни!", show_alert=True)
                return
            
            price = upgrade['prices'][level-1]
            balance = BANANA_STATS[user_id]['bananas']
            
            if balance < price:
                await query.answer(f"❌ Не хватает {price - balance}🍌!", show_alert=True)
                return
            
            # Покупаем улучшение
            BananaTracker.add_bananas(user_id, -price)
            BANANA_STATS[user_id][f"{upgrade_type}_level"] = level
            BananaTracker.save_stats()
            
            # Сообщение об успехе с правильными эффектами
            if upgrade_type == "banana_bag":
                effect = f"+{upgrade['effects'][level-1]}🍌 к каждому сбору"
            elif upgrade_type == "banana_totem":
                # Правильная распаковка для тотема
                gold_bonus, diamond_bonus = upgrade['effects'][level-1]
                effect = f"+{gold_bonus}% к золотому, +{diamond_bonus}% к алмазному"
            else:
                effect = "неизвестный эффект"
            
            await query.edit_message_text(
                f"🎉 <b>Улучшение куплено!</b>\n\n"
                f"🛒 {upgrade['name']} Уровень {level}\n"
                f"💡 {effect}\n"
                f"💰 Спиcано: {price}🍌\n\n"
                f"Ба-на-на! Миньоны стали сильнее! 🍌",
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"Ошибка покупки улучшения: {e}")
            await query.answer("❌ Ошибка при покупке!", show_alert=True)

class BananaShop:
    SHOP_ITEMS = {
        "achievements": {
            "1": {
                "name": "🍌 Банановый новичок",
                "price": 5,
                "description": "Твой первый шаг в мир банановых достижений!",
                "effect": "achievement",
                "stock": 50,
                "max_stock": 10
            },
            "2": {
                "name": "🍌 Опытный банановед",
                "price": 10,
                "description": "Теперь ты знаешь о бананах чуть больше других!",
                "effect": "achievement",
                "stock": 30,
                "max_stock": 5
            },
            "3": {
                "name": "🍌 Повелитель связок",
                "price": 25,
                "description": "Целые связки бананов склоняются перед тобой!",
                "effect": "achievement",
                "stock": 20,
                "max_stock": 3
            },
            "4": {
                "name": "🍌 Банановый магнат",
                "price": 100,
                "description": "Вершина банановой карьеры! Но что это за секретный эффект?..",
                "effect": "secret_bonus",
                "stock": 10,
                "max_stock": 1
            }
        },
        "boosts": {
            "5": {
                "name": "✨ Касание Мидаса (3 использования)",
                "price": 5,
                "description": "Увеличивает шанс на золотой банан в 3 раза на 3 следующих /banana!",
                "effect": "midas_touch",
                "stock": 25,
                "max_stock": 25
            },
            "6": {
                "name": "🌀 Манипулятор умножений (5x)",
                "price": 15,
                "description": "Удваивает количество бананов в следующие 5 /banana!",
                "effect": "multiplier",
                "stock": 15,
                "max_stock": 15
            },
            "10": {
                "name": "⏳ Ускоритель времени (1 час)",
                "price": 25,
                "description": "Сокращает КД /banana до 15 минут на 1 час!",
                "effect": "time_accelerator",
                "stock": 20,
                "max_stock": 20
            },
            "11": {
                "name": "🌀 Машина времени (1 раз)",
                "price": 40,
                "description": "Мгновенно убирает кулдаун для одного использования /banana!",
                "effect": "time_machine",
                "stock": 15,
                "max_stock": 15
            }
        },
        "fun": {
            "8": {
                "name": "🏷️ Приставка «🍌» в топе",
                "price": 5,
                "description": "Добавляет банан перед твоим именем в /top на 1 день",
                "effect": "prefix_top",
                "stock": 40,
                "max_stock": 40
            },
            "9": {
                "name": "🥁 Мистический барабан",
                "price": 3,
                "description": "Бам-бам-бам! Что же будет? Никто не знает! Может богатство, может ничего... Ничего не гарантирую!",
                "effect": "mystic_drum",
                "stock": -1,
                "max_stock": -1
            },
            "12": {
                "name": "💣 Банано-бомба",
                "price": 15,
                "description": "Всем в чате +3-5🍌 за любое сообщение в течение 1 минуты!",
                "effect": "banana_bomb",
                "stock": 10,
                "max_stock": 10
            }
        },
        "prestige": {
            "19": {
                "name": "👑 Золотой Миньон",
                "price": 300,
                "description": "Особый значок 🥇 в /leaderboard! Чистый статус!",
                "effect": "golden_minion",
                "stock": -1,    
                "max_stock": -1
            }
        }
    }

    @staticmethod
    def get_shop_keyboard(selected_category=None, user_id=None):
        """Генерирует клавиатуру магазина с учетом остатков"""
        keyboard = []
        
        if selected_category is None:
            # Главное меню магазина
            keyboard.append([InlineKeyboardButton("🏆 Достижения", callback_data="shop_category_achievements")])
            keyboard.append([InlineKeyboardButton("⚡ Бусты", callback_data="shop_category_boosts")])
            keyboard.append([InlineKeyboardButton("🎭 Весёлые фишки", callback_data="shop_category_fun")])
            keyboard.append([InlineKeyboardButton("👑 Статусы", callback_data="shop_category_prestige")])
        else:
            # Меню конкретной категории
            category_items = BananaShop.SHOP_ITEMS.get(selected_category, {})
            
            if not category_items:
                # Если категория пуста
                keyboard.append([InlineKeyboardButton("🛒 Товаров нет", callback_data="no_items")])
            else:
                # Добавляем кнопки для каждого товара в категории
                for item_id, item in category_items.items():
                    # Пропускаем товары с stock = -1 (бесконечные)
                    if item['stock'] == 0:
                        continue
                        
                    btn_text = f"{item['name']} - {item['price']}🍌"
                    
                    # Для достижений показываем статус "куплено"
                    if selected_category == "achievements" and item_id in {"1", "2", "3"}:
                        if user_id and "inventory" in BANANA_STATS.get(str(user_id), {}) and item_id in BANANA_STATS[str(user_id)]["inventory"]:
                            btn_text = f"✅ {item['name']} (куплено)"
                    
                    keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"shop_item_{item_id}")])
            
            # Всегда добавляем кнопку "Назад"
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="shop_back")])
        
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def get_item_info(item_id: str):
        """Ищет товар по ID во всех категориях"""
        for category in BananaShop.SHOP_ITEMS.values():
            if item_id in category:
                return category[item_id]
        return None

    @staticmethod
    def has_achievement(user_id_str: str, achievement_id: str) -> bool:
        """Проверяет, есть ли у пользователя достижение"""
        return user_id_str in BANANA_STATS and "inventory" in BANANA_STATS[user_id_str] and achievement_id in BANANA_STATS[user_id_str]["inventory"]

    @staticmethod
    def restock_all_items():
        """Восстанавливает все товары до максимального количества"""
        for category in BananaShop.SHOP_ITEMS.values():
            for item in category.values():
                if item['stock'] != -1:
                    item['stock'] = item['max_stock']
        BananaTracker.save_stats()
        return "🛒 Все товары успешно пополнены! Ба-на-на!"

    @staticmethod
    async def _return_bananas(context: ContextTypes.DEFAULT_TYPE):
        """Возвращает бананы через 30 секунд"""
        job = context.job
        user_id = job.user_id
        chat_id = job.chat_id
        bananas = job.data
        
        user_id_str = str(user_id)
        new_balance = BananaTracker.add_bananas(user_id_str, bananas)
        
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"@{context.bot.get_chat(user_id).username}, ладно, шутка! 😄\n\nДержи свои {bananas}🍌 обратно!\n💰 Новый баланс: {new_balance}",
                disable_notification=True
            )
        except Exception as e:
            logger.error(f"Ошибка возврата бананов: {e}")

    @staticmethod
    async def handle_shop_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок магазина с подтверждением покупки"""
        query = update.callback_query
        await query.answer()
        
        if not query.message.reply_to_message or query.from_user.id != query.message.reply_to_message.from_user.id:
            await query.answer("🦍 Ой-ой! Это не твой магазин!", show_alert=True)
            return
        
        data = query.data
        user_id = str(query.from_user.id)
        BananaTracker._init_user(user_id)
        balance = BANANA_STATS[user_id]["bananas"]
        
        try:
            if data.startswith("shop_category_"):
                category = data.split("_")[2]
                category_names = {
                    "achievements": "🏆 Достижения",
                    "boosts": "⚡ Бусты", 
                    "fun": "🎭 Весёлые фишки"
                }
                
                await query.edit_message_text(
                    text=f"🛒 *{category_names.get(category, 'Категория')}*\n\n💰 Твой баланс: *{balance}🍌*\n\nВыбери товар:",
                    parse_mode="Markdown",
                    reply_markup=BananaShop.get_shop_keyboard(category, query.from_user.id)
                )
            
            elif data.startswith("shop_item_"):
                item_id = data.split("_")[2]
                item = BananaShop.get_item_info(item_id)
                
                if not item:
                    await query.answer("🍌 Товар не найден!")
                    return
                    
                # НАХОДИМ ПРАВИЛЬНУЮ КАТЕГОРИЮ ДЛЯ КНОПКИ "НАЗАД"
                item_category = None
                for category_name, category_items in BananaShop.SHOP_ITEMS.items():
                    if item_id in category_items:
                        item_category = category_name
                        break
                
                if not item_category:
                    await query.answer("🍌 Ошибка: категория не найдена!", show_alert=True)
                    return
                    
                stock_text = f"\n📦 Остаток: {item['stock']} шт." if item['stock'] != -1 else ""
                keyboard = [
                    [InlineKeyboardButton(f"🛒 Купить за {item['price']}🍌", callback_data=f"buy_{item_id}")],
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"shop_category_{item_category}")]  # ПРАВИЛЬНЫЙ callback
                ]
                
                await query.edit_message_text(
                    text=f"🛍️ *{item['name']}*\n\n💡 *Описание*: {item['description']}\n💰 *Цена*: {item['price']}🍌{stock_text}\n\n💰 Твой баланс: *{balance}🍌*",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            
            elif data == "shop_back":
                # Возврат в главное меню магазина
                await query.edit_message_text(
                    text=f"🛒 *Банановый магазин* 🍌\n\n💰 Твой баланс: *{balance}🍌*\n\nВыбери категорию:",
                    parse_mode="Markdown",
                    reply_markup=BananaShop.get_shop_keyboard()
                )
                return
            
            elif data.startswith("buy_"):
                item_id = data.split("_")[1]
                item = BananaShop.get_item_info(item_id)
                
                if not item:
                    await query.answer("🍌 Упс! Товар исчез!", show_alert=True)
                    return
                
                if item['stock'] != -1 and item['stock'] <= 0:
                    await query.answer("🍌 Миньоны всё раскупили!", show_alert=True)
                    return
                
                if balance < item["price"]:
                    await query.answer(f"🍌 Нужно ещё {item['price']-balance}🍌!", show_alert=True)
                    return
                
                keyboard = [
                    [
                        InlineKeyboardButton("Да! Хочу!", callback_data=f"confirm_buy_{item_id}"),
                        InlineKeyboardButton("Нет, передумал", callback_data=f"shop_item_{item_id}")
                    ]
                ]
                
                await query.edit_message_text(
                    f"🦍 *Миньон спрашивает:*\nТочно хочешь купить {item['name']} за {item['price']}🍌?\n\n"
                    f"{item['description']}\n\n"
                    f"💰 Твой баланс: {balance}🍌",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            
            elif data.startswith("confirm_buy_"):
                item_id = data.split("_")[2]
                item = BananaShop.get_item_info(item_id)
                
                if balance < item["price"]:
                    await query.answer("🍌 Ой! Бананы закончились!", show_alert=True)
                    return
                
                new_balance = BananaTracker.add_bananas(user_id, -item["price"])
                if item['stock'] != -1:
                    item['stock'] -= 1
                
                BANANA_STATS[user_id].setdefault("inventory", []).append(item_id)
                BananaTracker.save_stats()
                
                keyboard = [
                    [InlineKeyboardButton("🎒 Открыть инвентарь", callback_data="open_inv")],
                    [InlineKeyboardButton("🛒 Вернуться в магазин", callback_data="shop_back")]
                ]
                
                await query.edit_message_text(
                    f"🎉 *Ура! Миньоны завершили покупку!*\n\n"
                    f"🛍️ {item['name']}\n"
                    f"💡 {item['description']}\n\n"
                    f"💰 Новый баланс: {new_balance}🍌\n\n"
                    f"Загляни в инвентарь чтобы использовать предмет!",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            
            elif data == "open_inv":
                await inv_command(update, context)
        
        except Exception as e:
            logger.error(f"Ошибка в магазине: {e}")
            await query.answer("🍌 Миньоны всё сломали! Попробуй позже", show_alert=True)
    
    @staticmethod
    def is_unique_item(item_id: str) -> bool:
        return item_id in ["1", "2", "3", "4"]  # ID уникальных товаров (достижений)

    @staticmethod
    async def apply_effect(effect: str, user_id: str, context: ContextTypes.DEFAULT_TYPE, chat_id: int = None, item_id: str = None):
        user_id_str = str(user_id)
        
        if effect == "achievement":
            achievement_data = {
                "name": BananaShop.SHOP_ITEMS["achievements"][item_id]["name"],
                "reward": 0,                            
                "msg": BananaShop.SHOP_ITEMS["achievements"][item_id]["description"]
            }
            return BananaTracker.unlock_achievement(user_id_str, achievement_data)
        
        elif effect == "midas_touch":
            BANANA_STATS[user_id_str].setdefault("boosts", {}).setdefault("midas_touch", 0)
            BANANA_STATS[user_id_str]["boosts"]["midas_touch"] += 3
            BananaTracker.save_stats()
            return "✨ Касание Мидаса активировано! Следующие 3 /banana будут с увеличенным шансом золотых бананов!"
        
        elif effect == "multiplier":
            BANANA_STATS[user_id_str].setdefault("boosts", {}).setdefault("multiplier", 0)
            BANANA_STATS[user_id_str]["boosts"]["multiplier"] += 5
            BananaTracker.save_stats()
            return "🌀 Манипулятор умножений активирован! Следующие 5 /banana принесут двойные бананы!"
        
        elif effect == "time_accelerator":
            BANANA_STATS[user_id_str].setdefault("boosts", {}).setdefault("time_accelerator", 0)
            BANANA_STATS[user_id_str]["boosts"]["time_accelerator"] = max(
                BANANA_STATS[user_id_str]["boosts"].get("time_accelerator", 0),
                int(time.time()) + 3600  # 1 час
            )
            BananaTracker.save_stats()
            return "⏳ Ускоритель времени активирован! На 1 час КД /banana = 15 минут!"
        
        elif effect == "time_machine":
            # Снимаем кулдаун для следующего использования
            if user_id_str in BANANA_STATS:
                BANANA_STATS[user_id_str]["last_banana"] = 0
                BananaTracker.save_stats()
            return "🌀 Машина времени активирована! Следующий /banana можно использовать сразу!"
        
        elif effect == "banana_bomb":
            # Проверка: не более 1 бомбы в 10 минут на чат
            current_time = time.time()
            if "active_bombs" in BANANA_STATS:
                for chat_id, bomb_data in BANANA_STATS["active_bombs"].items():
                    if current_time - bomb_data["start_time"] < 600:  # 10 минут
                        return "💣 В этом чате недавно уже была активирована банано-бомба! Попробуйте позже."
            
            # Активация банано-бомбы
            bomb_end_time = current_time + 60  # 1 минута
            if "active_bombs" not in BANANA_STATS:
                BANANA_STATS["active_bombs"] = {}
            
            BANANA_STATS["active_bombs"][str(chat_id)] = {
                "end_time": bomb_end_time,
                "start_time": current_time,
                "activator": user_id,
                "last_rewards": {}
            }
            
            BananaTracker.save_stats()
            return f"💣 Банано-бомба активирована! В течение 1 минуты все в чате получают +3-5🍌 за сообщения!"
        
        elif effect.startswith("banana_totem_"):
            try:
                level = int(effect.split("_")[2])
                BANANA_STATS[user_id_str]["banana_totem_level"] = level
                BananaTracker.save_stats()
                
                # Получаем правильные бонусы из UPGRADES
                if level <= len(UPGRADES['banana_totem']['effects']):
                    gold_bonus, diamond_bonus = UPGRADES['banana_totem']['effects'][level-1]
                    return f"🏆 Банановый тотем {level} уровня активирован! +{gold_bonus}% к золотому, +{diamond_bonus}% к алмазному банану!"
                else:
                    return f"🏆 Банановый тотем {level} уровня активирован!"
                    
            except (IndexError, ValueError) as e:
                logger.error(f"Ошибка активации тотема: {e}")
                return "🏆 Банановый тотем активирован!"
        
        elif effect == "golden_minion":
            BANANA_STATS[user_id_str]["golden_minion"] = True
            BananaTracker.save_stats()
            return "👑 Теперь ты Золотой Миньон! Особый значок 🥇 в /leaderboard!"
        
        elif effect == "storm":
            return "🌪️ Банановый шторм! Но пока ничего не произошло..."
        
        elif effect == "secret_bonus":
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"@{context.bot.get_chat(user_id).username} купил Бананового магната...\n\n🔒 Все твои бананы исчезли! Но не переживай..."
            )
            
            context.job_queue.run_once(
                callback=BananaShop._return_bananas,
                when=30,
                chat_id=chat_id,
                user_id=user_id,
                data=100,
                name=f"secret_return_{user_id}"
            )
            return ""
        
        elif effect == "mystic_drum":
            responses = [
                "🥁 Бам-бам-бам! Ничего не произошло...",
                "🥁 Ты услышал шёпот: 'Ба-на-на...'",
                "🥁 Миньоны замерли... но ничего не случилось!"
            ]
            return random.choice(responses)
        
        return "🎉 Эффект активирован!"


# Новая команда /inv для просмотра инвентаря
async def inv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /inv"""
    # Определяем, откуда пришел запрос - из команды или callback
    if update.callback_query:
        query = update.callback_query
        message = query.message
        user_id = str(query.from_user.id)
        # Сохраняем ID владельца инвентаря в контекст
        context.user_data["inventory_owner"] = user_id
    else:
        message = update.message
        user_id = str(update.effective_user.id)
        # Сохраняем ID владельца инвентаря в контекст
        context.user_data["inventory_owner"] = user_id
    
    BananaTracker._init_user(user_id)
    
    inventory = BANANA_STATS[user_id].get("inventory", [])
    boosts = BANANA_STATS[user_id].get("boosts", {})

    if not inventory and not boosts:
        if update.callback_query:
            await query.answer("📦 Ваш инвентарь пуст!", show_alert=True)
        else:
            await message.reply_text("📦 Ваш инвентарь пуст!")
        return

    # Формируем текст сообщения
    message_lines = ["*🎒 Ваш инвентарь*"]
    
    if inventory:
        message_lines.append("\n*🛍️ Купленные предметы:*")
        for item_id in inventory:
            item = BananaShop.get_item_info(item_id)
            if item:
                message_lines.append(f"• {item['name']} (ID: `{item_id}`)")

    if boosts:
        message_lines.append("\n*⚡ Активные бусты:*")
        for boost_name, count in boosts.items():
            if count > 0:
                # Правильное отображение бустов
                boost_display = {
                    "midas_touch": f"✨ Касание Мидаса: {count} использований",
                    "multiplier": f"🌀 Манипулятор умножений: {count} использований",
                    "time_accelerator": f"⏳ Ускоритель времени: до {datetime.fromtimestamp(count).strftime('%H:%M')}",
                    "time_machine": f"🌀 Машина времени: {count} использований"
                }.get(boost_name, f"{boost_name}: {count}")
                message_lines.append(f"• {boost_display}")

    keyboard = []
    for item_id in inventory:
        item = BananaShop.get_item_info(item_id)
        if item and item.get('effect') not in ['achievement', 'golden_minion']:  # Не показываем кнопки для достижений и статусов
            keyboard.append([InlineKeyboardButton(
                f"🎯 {item['name'][:20]}..." if len(item['name']) > 20 else f"🎯 {item['name']}",
                callback_data=f"use_{item_id}"
            )])
    
    keyboard.append([InlineKeyboardButton("❌ Закрыть", callback_data="close_inv")])

    try:
        text = '\n'.join(message_lines)
        if update.callback_query:
            await query.edit_message_text(
                text=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            sent_message = await message.reply_text(
                text=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            # Сохраняем ID сообщения для проверки
            context.user_data["inventory_message_id"] = sent_message.message_id
    except Exception as e:
        logger.error(f"Ошибка при отправке инвентаря: {e}")
        # Альтернативный вариант без форматирования
        plain_text = '\n'.join(line.replace('*', '') for line in message_lines)
        if update.callback_query:
            await query.edit_message_text(
                text=plain_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            sent_message = await message.reply_text(
                text=plain_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data["inventory_message_id"] = sent_message.message_id

# Новый обработчик для кнопок инвентаря
async def inv_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # ПРОВЕРКА ВЛАДЕЛЬЦА ИНВЕНТАРЯ
    owner_id = context.user_data.get("inventory_owner")
    if not owner_id or str(query.from_user.id) != owner_id:
        await query.answer("🚫 Это не ваш инвентарь!", show_alert=True)
        return
        
    if query.data == "close_inv":
        try:
            await query.message.delete()
            # Очищаем данные контекста
            context.user_data.pop("inventory_owner", None)
            context.user_data.pop("inventory_message_id", None)
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")
        return
        
    if query.data.startswith("use_"):
        item_id = query.data.split("_")[1]
        user_id = str(query.from_user.id)
        
        # ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА ВЛАДЕЛЬЦА
        if user_id != owner_id:
            await query.answer("🚫 Это не ваш инвентарь!", show_alert=True)
            return
            
        try:
            # Проверка наличия предмета
            if user_id not in BANANA_STATS or item_id not in BANANA_STATS[user_id].get("inventory", []):
                await query.answer("❌ Предмет не найден!", show_alert=True)
                return
                
            item = BananaShop.get_item_info(item_id)
            if not item:
                await query.answer("❌ Ошибка: предмет не существует!", show_alert=True)
                return
                
            # Применяем эффект
            effect_result = await BananaShop.apply_effect(
                item["effect"],
                query.from_user.id,
                context,
                chat_id=query.message.chat_id,
                item_id=item_id
            )
            
            # УДАЛЯЕМ ПРЕДМЕТ ИЗ ИНВЕНТАРЯ ТОЛЬКО ЕСЛИ ЭТО НЕ УНИКАЛЬНЫЙ ПРЕДМЕТ
            # Достижения и статусы остаются в инвентаре навсегда
            if item["effect"] not in ["achievement", "golden_minion"]:
                BANANA_STATS[user_id]["inventory"].remove(item_id)
                BananaTracker.save_stats()
            
            # Формируем сообщение без лишнего экранирования
            response_text = (
                f"🎯 Использован предмет: {item['name']}\n\n"
                f"{effect_result}\n\n"
                f"🆔 ID: {item_id}"
            )
            
            # Отправляем как обычный текст
            await query.edit_message_text(
                text=response_text,
                parse_mode=None,  # Отключаем Markdown полностью
                reply_markup=None
            )
            
        except Exception as e:
            logger.error(f"Ошибка в обработчике кнопки инвентаря: {e}")
            await query.answer("⚠️ Произошла ошибка при использовании предмета!", show_alert=True)
            await query.edit_message_text(
                "⚠️ Не удалось использовать предмет. Попробуйте позже.",
                parse_mode=None
            )

async def cleanup_expired_boosts(context: ContextTypes.DEFAULT_TYPE):
    """Очистка просроченных бустов"""
    try:
        current_time = time.time()
        for user_id, user_data in BANANA_STATS.items():
            if not isinstance(user_data, dict) or "boosts" not in user_data:
                continue
                
            # Очистка ускорителя времени
            if "time_accelerator" in user_data["boosts"] and current_time > user_data["boosts"]["time_accelerator"]:
                del user_data["boosts"]["time_accelerator"]
                logger.info(f"Ускоритель времени истек для пользователя {user_id}")
                
        BananaTracker.save_stats()
    except Exception as e:
        logger.error(f"Ошибка при очистке бустов: {e}")


async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /shop"""
    try:
        user_id = update.effective_user.id
        user_id_str = str(user_id)
        BananaTracker._init_user(user_id_str)
        balance = BANANA_STATS[user_id_str]["bananas"]
        
        # Отправляем сообщение с reply_to_message
        message = await update.message.reply_text(
            f"🛒 Банановый магазин\n\n"
            f"💰 Твой баланс: {balance} бананов\n\n"
            "Выбери категорию:",
            parse_mode="Markdown",
            reply_markup=BananaShop.get_shop_keyboard(),
            reply_to_message_id=update.message.message_id  # Важно!
        )
        
        # Сохраняем ID сообщения в контекст
        context.user_data["shop_message_id"] = message.message_id
        
    except Exception as e:
        print(f"Ошибка в shop_command: {e}")
        await update.message.reply_text("🍌 Ой, магазин временно закрыт на банановую переучётку!")
        

async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /buy"""
    # Проверяем, что команду вызвал сам пользователь
    if update.message.reply_to_message and update.message.reply_to_message.from_user.id != update.effective_user.id:
        await update.message.reply_text("❌ Это меню другого пользователя!")
        return
    
    try:
        if not context.args:
            await update.message.reply_text(
                "ℹ️ Использование: /buy <ID товара>\n"
                "Например: /buy 5\n\n"
                "Посмотреть товары: /shop"
            )
            return
            
        item_id = context.args[0]
        item = BananaShop.get_item_info(item_id)
        
        if not item:
            await update.message.reply_text("🍌 Упс! Такого товара нет в магазине!")
            return
            
        # Проверка остатка
        if item['stock'] != -1 and item['stock'] <= 0:
            await update.message.reply_text("🍌 Этот товар закончился! Попробуй позже.")
            return
            
        user_id = update.effective_user.id
        user_id_str = str(user_id)
        BananaTracker._init_user(user_id_str)
        balance = BANANA_STATS[user_id_str]["bananas"]
        
        if balance < item["price"]:
            await update.message.reply_text(
                f"🍌 Ой-ой! Не хватает бананов!\n"
                f"Нужно: {item['price']}🍌, у тебя: {balance}🍌\n\n"
                "Пополни баланс командой /banana"
            )
            return
            
        # Проверяем, есть ли уже такой товар у пользователя (для уникальных товаров)
        if BananaShop.is_unique_item(item_id) and BananaShop.has_achievement(user_id_str, item_id):
            await update.message.reply_text("🍌 У тебя уже есть этот товар!")
            return
            
        # Списываем бананы
        new_balance = BananaTracker.add_bananas(user_id_str, -item["price"])
        
        # Уменьшаем остаток
        if item['stock'] != -1:
            item['stock'] -= 1
        
        # Добавляем предмет в инвентарь
        if "inventory" not in BANANA_STATS[user_id_str]:
            BANANA_STATS[user_id_str]["inventory"] = []
        BANANA_STATS[user_id_str]["inventory"].append(item_id)
        BananaTracker.save_stats()
        
        response_text = (
            f"🎉 Поздравляем с покупкой!\n\n"
            f"🛍️ *{item['name']}*\n"
            f"💡 {item['description']}\n\n"
            f"💰 Новый баланс: *{new_balance}🍌*\n\n"
            f"📦 Товар добавлен в инвентарь! Используй /inventory"
        )
        
        await update.message.reply_text(
            response_text,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        print(f"Ошибка в buy_command: {e}")
        await update.message.reply_text("🍌 Ой, что-то пошло не так с покупкой! Попробуй позже.")

async def shop_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Унифицированный обработчик кнопок магазина"""
    query = update.callback_query
    await query.answer()
    
    # Проверка принадлежности меню
    if not query.message.reply_to_message or query.from_user.id != query.message.reply_to_message.from_user.id:
        await query.answer("🚫 Это меню другого пользователя!", show_alert=True)
        return
    
    data = query.data
    user_id = str(query.from_user.id)
    BananaTracker._init_user(user_id)
    balance = BANANA_STATS[user_id]["bananas"]
    
    try:
        if data == "shop_back":
            # Возврат в главное меню магазина
            await query.edit_message_text(
                text=f"🛒 *Банановый магазин* �\n\n💰 Твой баланс: *{balance}🍌*\n\nВыбери категорию:",
                parse_mode="Markdown",
                reply_markup=BananaShop.get_shop_keyboard()
            )
            return
            
        elif data.startswith("shop_category_"):
            category = data.split("_")[2]
            category_names = {
                "achievements": "🏆 Достижения",
                "boosts": "⚡ Бусты", 
                "fun": "🎭 Весёлые фишки",
                "prestige": "👑 Статусы"
            }
            
            await query.edit_message_text(
                text=f"🛒 *{category_names.get(category, 'Категория')}*\n\n💰 Твой баланс: *{balance}🍌*\n\nВыбери товар:",
                parse_mode="Markdown",
                reply_markup=BananaShop.get_shop_keyboard(category, query.from_user.id)
            )
            
        elif data.startswith("shop_item_"):
            item_id = data.split("_")[2]
            item = BananaShop.get_item_info(item_id)
            
            if not item:
                await query.answer("🍌 Товар не найден!")
                return
                
            stock_text = f"\n📦 Остаток: {item['stock']} шт." if item['stock'] != -1 else ""
            keyboard = [
                [InlineKeyboardButton(f"🛒 Купить за {item['price']}🍌", callback_data=f"buy_{item_id}")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"shop_category_{next((cat for cat, items in BananaShop.SHOP_ITEMS.items() if item_id in items), '')}")]
            ]
            
            await query.edit_message_text(
                text=f"🛍️ *{item['name']}*\n\n💡 *Описание*: {item['description']}\n💰 *Цена*: {item['price']}🍌{stock_text}\n\n💰 Твой баланс: *{balance}🍌*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        elif data.startswith("buy_"):
            item_id = data.split("_")[1]
            item = BananaShop.get_item_info(item_id)
            
            if not item:
                await query.answer("🍌 Товар не найден!", show_alert=True)
                return
                
            if item['stock'] != -1 and item['stock'] <= 0:
                await query.answer("🍌 Этот товар закончился!", show_alert=True)
                return
                
            if balance < item["price"]:
                await query.answer("🍌 Не хватает бананов!", show_alert=True)
                return
                
            keyboard = [
                [
                    InlineKeyboardButton("Да! Хочу!", callback_data=f"confirm_buy_{item_id}"),
                    InlineKeyboardButton("Нет, передумал", callback_data=f"shop_item_{item_id}")
                ]
            ]
            
            await query.edit_message_text(
                text=f"🦍 *Миньон спрашивает:*\nТочно хочешь купить {item['name']} за {item['price']}🍌?\n\n"
                     f"{item['description']}\n\n"
                     f"💰 Твой баланс: {balance}🍌",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        elif data.startswith("confirm_buy_"):
            item_id = data.split("_")[2]
            item = BananaShop.get_item_info(item_id)
            
            if balance < item["price"]:
                await query.answer("🍌 Ой! Бананы закончились!", show_alert=True)
                return
                
            new_balance = BananaTracker.add_bananas(user_id, -item["price"])
            if item['stock'] != -1:
                item['stock'] -= 1
                
            BANANA_STATS[user_id].setdefault("inventory", []).append(item_id)
            BananaTracker.save_stats()
            
            keyboard = [
                [InlineKeyboardButton("🎒 Открыть инвентарь", callback_data="open_inv")],
                [InlineKeyboardButton("🛒 Вернуться в магазин", callback_data="shop_back")]
            ]
            
            effect_result = await BananaShop.apply_effect(
                item["effect"],
                query.from_user.id,
                context,
                chat_id=query.message.chat_id,
                item_id=item_id
            )
            
            response_text = f"🎉 *Ура! Миньоны завершили покупку!*\n\n🛍️ {item['name']}\n💡 {item['description']}\n\n💰 Новый баланс: {new_balance}🍌"
            if effect_result:
                response_text += f"\n\n{effect_result}"
                
            await query.edit_message_text(
                text=response_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        elif data == "open_inv":
            await inv_command(update, context)
            
    except Exception as e:
        logger.error(f"Ошибка в обработчике магазина: {e}")
        await query.answer("🍌 Миньоны всё сломали! Попробуй позже", show_alert=True)

# Добавляем новую команду для админов
async def restock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для пополнения товаров (только для админов)"""
    user_id = update.effective_user.id
    
    # Проверяем, является ли пользователь админом
    if str(user_id) not in ADMIN_IDS:  # ADMINS должен быть определен где-то в вашем коде
        await update.message.reply_text("🍌 Ой, эта команда только для банановых начальников!")
        return
    
    result = BananaShop.restock_all_items()
    await update.message.reply_text(result)



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🍌 Ба-на-на! Я ВанитоБот — страж бананового порядка!\n\n"
        "Мои команды:\n"
        "/warn - Банановое предупреждение ⚠️\n"
        "/unwarn - Снять предупреждение 🍏\n"
        "/warns - Проверить кол-во предупреждений\n"
        "/ban - Банана-БАН! 🍌\n"
        "/jail - Посадить в банановую клетку 🔒\n"
        "/free - Освободить с банановой амнистией 🍏\n"
        "/kick - Пнуть миньона банановой кожурой 👢\n"
        "/game - Вызвать на банановый поединок (Ваняня-Баняня)\n"
        "Па-па-па-па-па-па-па! 🎵"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔧 Боп-боп! Вот что я умею:\n\n"
        "🍌 <b>Банановые команды:</b>\n"
        "/warn (ответ) - Выдать предупреждение (система наказаний) ⚠️\n"
        "/unwarn (ответ) - Снять предупреждение 🍏\n"
        "/warns (ответ) - просмотреть количество предупреждений\n"
        "/ban (ответ) - БАН навсегда 🍌\n"
        "/jail (ответ) 2 - Клетка на время (в минутах) 🔒\n"
        "/free (ответ) - Освободить 🍏\n"
        "/kick (ответ) - Пинок из чата 👢\n"
        "/game (ответ) - Вызвать на банановый поединок (Ваняня-Баняня)\n\n"
        "<b>Система предупреждений:</b>\n"
        "1. 1-е - предупреждение\n"
        "2. 2-е - мут 30 мин\n"
        "3. 3-е - мут 2 часа\n"
        "4. 4-е - мут 4 часа\n"
        "5. 5-е - мут 6 часов\n"
        "6. 6-е - мут 12 часов\n"
        "7. 7-е - мут 1 день\n"
        "8. 8-е - мут 2 дня\n"
        "9. 9-е - мут 3 дня\n"
        "10. 10-е - перманентный бан\n\n"
        "<b>Игра Ваняня-Баняня:</b>\n"
        "- Один пользователь = одна активная игра\n"
        "- Если игрок не ходит 5 минут - поражение\n"
        "- Все сообщения удаляются после завершения\n\n"
        "Работает через ответ на сообщение!\n"
        "Бе-бе-бе-бе! 🎶",
        parse_mode='HTML'
    )
    
# После определения класса QuestSystem
quest_system = QuestSystem()

async def start_quest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Только смотрители могут запускать квесты!")
        return
    
    # Получаем ID топика, если команда вызвана в треде
    thread_id = update.message.message_thread_id if update.message.is_topic_message else None
    
    # Передаем thread_id в start_quest
    await quest_system.start_quest(
        update.effective_chat.id, 
        context, 
        trigger_message_id=update.message.message_id,
        manual=True,
        thread_id=thread_id
    )


async def clue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in quest_system.active_quests:
        await update.message.reply_text("🔍 Сейчас нет активных квестов!")
        return
    
    quest = quest_system.active_quests[chat_id]
    available_clues = [c for c in quest_system.clues if c not in quest["found_clues"]]
    
    if not available_clues:
        await update.message.reply_text("ℹ️ Все улики уже собраны!")
        return
    clue = random.choice(available_clues)
    quest["found_clues"].append(clue)
    await update.message.reply_text(f"🔎 Улика:\n{clue}")

async def vote_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in quest_system.active_quests:
        await update.message.reply_text("ℹ️ Сейчас нет активного квеста!")
        return
    
    if not context.args:
        await update.message.reply_text("ℹ️ Используйте: /vote @ник")
        return
    
    suspect = " ".join(context.args)
    quest_system.active_quests[chat_id]["votes"][update.effective_user.id] = suspect
    await update.message.reply_text(f"✅ Вы проголосовали за {suspect}!")

async def ask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in quest_system.active_quests:
        await update.message.reply_text("ℹ️ Сейчас нет активного квеста!")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("ℹ️ Используйте: /ask @ник Ваш вопрос")
        return
    
    suspect = context.args[0]
    question = " ".join(context.args[1:])
    answers = {
        "@Мистер_Жёлтый": ["Я был в банановой лавке!", "Не трогайте меня!", "Я невиновен!"],
        "@Банана_Джо": ["Эээ... я... ничего не брал!", "*нервно почесался*", "Может быть да, может быть нет..."],
        "@Миньон_Гарри": ["Я спал!", "Я маленький, я не мог!", "Спросите у Бананы Джо!"]
    }
    response = random.choice(answers.get(suspect, ["Не знаю такого"]))
    await update.message.reply_text(f"{suspect}: {response}")

        
async def set_law(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("🚫 Только Главные Бананы могут устанавливать законы!")
        return
        
    if not context.args:
        current_law = law_enforcer.active_law
        if current_law:
            time_left = law_enforcer.end_time - datetime.now()
            minutes = int(time_left.total_seconds() // 60)
            await update.message.reply_text(
                f"📜 Текущий закон: {current_law}\n"
                f"⏳ Осталось времени: {minutes} минут\n\n"
                "Чтобы установить новый закон, используйте /setlaw [текст закона]"
            )
        else:
            await update.message.reply_text(
                "ℹ️ Сейчас нет активного закона.\n"
                "Чтобы установить новый, используйте /setlaw [текст закона]"
            )
        return
        
    new_law = ' '.join(context.args)
    law_enforcer.active_law = new_law
    law_enforcer.end_time = datetime.now() + timedelta(minutes=30)
    
    await update.message.reply_text(
        f"📜 Установлен новый закон:\n\n{new_law}\n\n"
        f"Действует до {law_enforcer.end_time.strftime('%H:%M')}\n"
        "Ба-на-на! Соблюдайте!"
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    
    # Игнорируем сетевые ошибки
    if isinstance(context.error, telegram.error.NetworkError):
        logger.warning("Сетевая ошибка, пропускаем...")
        return
    
    # Для других ошибок можно отправить сообщение
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "🍌 Ой, произошла банановая ошибка! Попробуйте позже."
            )
    except:
        pass

async def main():
    # Инициализация данных
    global BANANA_STATS
    BANANA_STATS = BananaTracker.initialize()
    BananaTracker.migrate_all_users()
    quest_system.active_quests = {}
    quest_system.quest_jobs = {}

    # Сообщение о старте бота
    logger.info("🎯 Бот запущен и работает! Ба-на-на! 🍌")
    logger.info("🚀 Режим минимального логирования - только ошибки и важные события")
    
    # Создаем приложение
    application = Application.builder().token(TOKEN).build()

    # Настройка команд меню
    commands = [
        BotCommand("start", "Запустить банановое веселье"),
        BotCommand("help", "Банановая помощь"),
        BotCommand("warn", "Банановое предупреждение ⚠️"),
        BotCommand("unwarn", "Снять предупреждение 🍏"),
        BotCommand("ban", "Банановый бан 🍌"),
        BotCommand("jail", "Тюрьма для миньонов 🔒"),
        BotCommand("free", "Освобождение бананом 🍏"),
        BotCommand("kick", "Пнуть миньона 👢"),
        BotCommand("game", "Банановый поединок 🍌🅱️"),
        BotCommand("warns", "Проверить количество предупреждений"),
    ]

    # Установка команд
    await application.initialize()
    await application.bot.set_my_commands(commands)
    
    # Улучшенная настройка polling с обработкой ошибок сети
    await application.updater.start_polling(
        poll_interval=5.0,  # Увеличенный интервал
        timeout=60,         # Увеличенный timeout
        drop_pending_updates=True,
        allowed_updates=[]
    )

    setup_knb_handlers(application)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("warn", banana_warn))
    application.add_handler(CommandHandler("unwarn", banana_unwarn))
    application.add_handler(CommandHandler("ban", banana_ban))
    application.add_handler(CommandHandler("jail", minion_jail))
    application.add_handler(CommandHandler("free", banana_free))
    application.add_handler(CommandHandler("kick", minion_kick))
    application.add_handler(CommandHandler("game", banana_game))
    application.add_handler(CommandHandler("warns", banana_warns))
    application.add_handler(CommandHandler("knb", start_knb))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("banana", banana_command))
    application.add_handler(CommandHandler("leaderboard", leaderboard_command))
    application.add_handler(CommandHandler("law", law_command))
    application.add_handler(CommandHandler("start_quest", start_quest_cmd))
    application.add_handler(CommandHandler("clue", clue_cmd))
    application.add_handler(CommandHandler("vote", vote_cmd))
    application.add_handler(CommandHandler("ask", ask_cmd))
    application.add_handler(CommandHandler("stop_quest", quest_system.stop_quest_command))
    application.add_handler(CommandHandler("achievements", achievements_command))
    application.add_handler(CommandHandler("setlaw", set_law))  
    application.add_handler(CommandHandler("countdown", countdown))
    application.add_handler(CommandHandler("event_msg", event_message))
    application.add_handler(CommandHandler("pollcreate", pollcreate))
    application.add_handler(CommandHandler("channel_send_message", channel_message))
    application.add_handler(CommandHandler("add_bananas", add_bananas))
    application.add_handler(CommandHandler("getid", get_id))
    application.add_handler(CommandHandler("start_event", start_event))
    application.add_handler(CommandHandler("event_status", event_status))
    application.add_handler(CommandHandler("end_event", end_event))
    # application.add_handler(CommandHandler("shop", shop_command))
    application.add_handler(CommandHandler("buy", buy_command))
    application.add_handler(CommandHandler("restock", restock_command))
    application.add_handler(CommandHandler("inv", inv_command))
    application.add_handler(CommandHandler("upgrades", upgrades_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.job_queue.run_repeating(cleanup_expired_boosts, interval=1800, first=10)
    pattern = re.compile(r"^(мут|фри|варн|анварн|бан|кик)(\s.*)?$", re.IGNORECASE)
    application.add_handler(CallbackQueryHandler(upgrades_button_handler, pattern="^buy_upgrade_|^close_upgrades"))
    application.add_handler(CallbackQueryHandler(inv_button_handler, pattern="^(ask_use_|use_|back_inv|close_inv|shop_back)"))
    application.add_handler(CallbackQueryHandler(shop_button_handler, pattern="^(shop_|buy_|confirm_buy_|open_inv|shop_back)"))
    application.add_handler(CallbackQueryHandler(button_handler, pattern="^vote_|^end_poll"))
    application.add_handler(CallbackQueryHandler(knb_callback, pattern="^knb_"))
    application.add_handler(CallbackQueryHandler(button_click, pattern="^accept_|^cancel_|^move_"))
    application.add_handler(CallbackQueryHandler(confirm_callback, pattern="^confirm_"))

    application.add_error_handler(error_handler)

    application.add_handler(CallbackQueryHandler(
        handle_law_proposal, 
        pattern="^(accept|reject)_law_"        
    ))
    application.add_handler(CallbackQueryHandler(
        law_enforcer.handle_appeal, 
        pattern="^appeal_[0-9]+$"
    ))
    application.add_handler(CallbackQueryHandler(
        law_enforcer.process_appeal_decision, 
        pattern="^appeal_(approve|reject)_[0-9]+$"
))

    async def quest_job_callback(context: ContextTypes.DEFAULT_TYPE):
        await quest_system.start_quest(
            chat_id=-1002443160040,  # Ваш chat_id
            context=context,
            trigger_message_id=None,
            manual=False,
            thread_id=None  # Укажите thread_id если нужно
        )

    application.job_queue.run_repeating(
        callback=quest_job_callback,
        interval=21600,  # 6 часов
        first=10
    )
    
    # Запуск основного цикла
    await application.start()
    await asyncio.Event().wait()  # Бесконечное ожидание


if __name__ == "__main__":
    import platform
    from asyncio import set_event_loop_policy, WindowsSelectorEventLoopPolicy
    
    # Настройка event loop для Windows
    if platform.system() == "Windows":
        set_event_loop_policy(WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")
    finally:
        # Гарантированное сохранение данных при завершении
        if 'BANANA_STATS' in globals():
            BananaTracker.save_stats()