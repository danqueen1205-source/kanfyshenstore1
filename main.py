#import asyncio
import csv
import json
import logging
import os
import random
import sqlite3
import string
import time
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
import aiofiles

from dotenv import load_dotenv
load_dotenv()

# Импорты для python-telegram-bot 20.x
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.error import TelegramError

# Импорты для matplotlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
import numpy as np

# ============ НАСТРОЙКИ ============
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise ValueError("Токен бота не найден.")

ADMIN_USERNAME = "@kanvylsia"
TESTER_USERNAME = "@kanvylsia"
ADMIN_IDS = set()
TESTER_IDS = set()
DB_FILE = "shop.db"
BACKUP_DIR = "backups"
LOG_FILE = "admin_logs.txt"
STATS_DIR = "stats"
CURRENCY = "₪"
REFERRAL_BONUS_NEW = 2
REFERRAL_BONUS_INVITER = 3


# Создаем необходимые директории
for directory in [BACKUP_DIR, STATS_DIR]:
    Path(directory).mkdir(exist_ok=True)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============ СИСТЕМА ЛОГИРОВАНИЯ АДМИНСКИХ ДЕЙСТВИЙ ============
class AdminLogger:
    def __init__(self, log_file: str = LOG_FILE):
        self.log_file = log_file
        
    def log_action(self, admin_id: int, action: str, target: str = "", details: str = ""):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        admin_info = db.fetchone("SELECT username FROM users WHERE user_id = ?", (admin_id,))
        username = f"@{admin_info['username']}" if admin_info and admin_info['username'] else f"ID:{admin_id}"
        
        log_entry = f"[{timestamp}] Admin: {username} | Action: {action}"
        
        if target:
            log_entry += f" | Target: {target}"
        if details:
            log_entry += f" | Details: {details}"
        
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry + '\n')
        except Exception as e:
            logger.error(f"Failed to write to admin log: {e}")
        
        logger.info(f"Admin Action: {action} by {username}")

admin_logger = AdminLogger()

# ============ БАЗА ДАННЫХ ============
class Database:
    def __init__(self, db_file: str = DB_FILE):
        self.db_file = db_file
        self._init_db()
        self._migrate_db()
    
    def _init_db(self):
        """Инициализация базы данных"""
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            
            conn.execute("PRAGMA foreign_keys = ON")
            
            # Таблица пользователей
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    balance INTEGER DEFAULT 0,
                    total_deposited INTEGER DEFAULT 0,
                    total_spent INTEGER DEFAULT 0,
                    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    referral_code TEXT UNIQUE,
                    referred_by INTEGER,
                    total_referrals INTEGER DEFAULT 0,
                    referral_earnings INTEGER DEFAULT 0,
                    is_banned BOOLEAN DEFAULT 0,
                    ban_reason TEXT,
                    banned_at TIMESTAMP,
                    banned_by INTEGER,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_purchase TIMESTAMP,
                    is_tester BOOLEAN DEFAULT 0,
                    tested_products INTEGER DEFAULT 0
                )
            """)
            
            # Таблица категорий
            conn.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    position INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Таблица товаров
            conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    price INTEGER NOT NULL,
                    category_id INTEGER DEFAULT 1,
                    stock INTEGER DEFAULT -1,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    image_path TEXT,
                    position INTEGER DEFAULT 0
                )
            """)
            
            # Таблица заказов
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    product_name TEXT NOT NULL,
                    quantity INTEGER DEFAULT 1,
                    amount INTEGER NOT NULL,
                    status TEXT DEFAULT 'completed',
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Таблица промокодов
            conn.execute("""
                CREATE TABLE IF NOT EXISTS promocodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE NOT NULL,
                    amount INTEGER NOT NULL,
                    discount_percent INTEGER DEFAULT 0,
                    min_order INTEGER DEFAULT 0,
                    max_uses INTEGER DEFAULT 1,
                    used_count INTEGER DEFAULT 0,
                    user_ids TEXT DEFAULT '',
                    is_active BOOLEAN DEFAULT 1,
                    expires_at TIMESTAMP,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Таблица настроек
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    description TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Создание дефолтных категорий
            default_categories = [
                (1, 'Разное', 1),
                (2, 'Гриф - Броня', 2),
                (3, 'Гриф - Кит', 3),
                (4, 'Гриф - Зелье', 4),
                (5, 'Гриф - Инструменты', 5),
                (6, 'Анархия - Броня', 6),
                (7, 'Анархия - Кит', 7),
                (8, 'Анархия - Зелье', 8),
                (9, 'Анархия - Инструменты', 9),
            ]
            
            for cat_id, cat_name, pos in default_categories:
                conn.execute("""
                    INSERT OR IGNORE INTO categories (id, name, position) 
                    VALUES (?, ?, ?)
                """, (cat_id, cat_name, pos))
            
            # Дефолтные настройки
            default_settings = [
                ('shop_name', 'Мой магазин', 'Название магазина'),
                ('welcome_message', 'Добро пожаловать в наш магазин!', 'Приветственное сообщение'),
                ('currency', '₪', 'Валюта'),
                ('min_deposit', '100', 'Минимальная сумма пополнения'),
                ('max_deposit', '10000', 'Максимальная сумма пополнения'),
                ('referral_bonus_new', '2', 'Бонус за регистрацию по реферальной ссылке'),
                ('referral_bonus_inviter', '3', 'Бонус пригласившему'),
                ('admin_notifications', '1', 'Уведомления администраторам'),
                ('maintenance_mode', '0', 'Режим техобслуживания'),
                ('support_contact', '@kanvylsia', 'Контакты поддержки'),
                ('terms_url', '', 'Ссылка на правила'),
                ('faq_url', '', 'Ссылка на FAQ'),
                ('ref_percent', '10', 'Процент с покупок реферала')
            ]
            
            for key, value, description in default_settings:
                conn.execute("""
                    INSERT OR REPLACE INTO settings (key, value, description, updated_at) 
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (key, value, description))
            
            conn.commit()
            logger.info("База данных успешно инициализирована")
    
    def _migrate_db(self):
        """Добавляем новые столбцы в существующую базу"""
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            
            # Проверяем существующие столбцы
            columns_to_add = [
                ('is_tester', 'BOOLEAN DEFAULT 0'),
                ('tested_products', 'INTEGER DEFAULT 0'),
            ]
            
            for column_name, column_type in columns_to_add:
                try:
                    cursor = conn.execute(f"PRAGMA table_info(users)")
                    existing_columns = [column[1] for column in cursor.fetchall()]
                    
                    if column_name not in existing_columns:
                        conn.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
                        logger.info(f"Добавлен столбец {column_name}")
                except Exception as e:
                    logger.error(f"Ошибка добавления {column_name}: {e}")
            
            # Создаем индексы
            indexes = [
                ("idx_users_balance", "users(balance)"),
                ("idx_orders_user_date", "orders(user_id, created_at)"),
                ("idx_products_category", "products(category_id, is_active)"),
                ("idx_promocodes_code", "promocodes(code, is_active)"),
                ("idx_users_referral", "users(referral_code)"),
                ("idx_orders_status", "orders(status)"),
                ("idx_users_last_active", "users(last_active)"),
            ]
            
            for index_name, index_columns in indexes:
                try:
                    conn.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {index_columns}")
                except Exception as e:
                    logger.error(f"Ошибка при создании индекса {index_name}: {e}")
            
            conn.commit()
            logger.info("Миграция базы данных завершена")
    
    def execute(self, query: str, params: tuple = ()):
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            conn.commit()
            return cursor
    
    def fetchone(self, query: str, params: tuple = ()):
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return cursor.fetchone()
    
    def fetchall(self, query: str, params: tuple = ()):
        with sqlite3.connect(self.db_file, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return cursor.fetchall()
    
    def get_stats(self, days: int = 30):
        """Получение статистики"""
        stats = {}
        
        try:
            stats['total_users'] = self.fetchone("SELECT COUNT(*) as count FROM users")['count']
            stats['active_users'] = self.fetchone(
                "SELECT COUNT(*) as count FROM users WHERE last_active > datetime('now', '-7 day')"
            )['count']
            stats['banned_users'] = self.fetchone(
                "SELECT COUNT(*) as count FROM users WHERE is_banned = 1"
            )['count']
            stats['total_balance'] = self.fetchone("SELECT SUM(balance) as sum FROM users")['sum'] or 0
            
            try:
                stats['testers_count'] = self.fetchone(
                    "SELECT COUNT(*) as count FROM users WHERE is_tester = 1"
                )['count']
            except:
                stats['testers_count'] = 0
            
            stats['total_products'] = self.fetchone(
                "SELECT COUNT(*) as count FROM products WHERE is_active = 1"
            )['count']
            stats['total_categories'] = self.fetchone(
                "SELECT COUNT(*) as count FROM categories WHERE is_active = 1"
            )['count']
            
            stats['total_orders'] = self.fetchone("SELECT COUNT(*) as count FROM orders")['count']
            stats['total_revenue'] = self.fetchone("SELECT SUM(amount) as sum FROM orders WHERE status = 'completed'")['sum'] or 0
            
            today = datetime.now().strftime('%Y-%m-%d')
            
            today_stats = self.fetchone("""
                SELECT 
                    COUNT(*) as orders_count,
                    SUM(amount) as revenue,
                    COUNT(DISTINCT user_id) as unique_buyers
                FROM orders 
                WHERE DATE(created_at) = ? AND status = 'completed'
            """, (today,))
            
            if today_stats:
                stats['today_orders'] = today_stats['orders_count'] or 0
                stats['today_revenue'] = today_stats['revenue'] or 0
                stats['today_buyers'] = today_stats['unique_buyers'] or 0
            else:
                stats['today_orders'] = 0
                stats['today_revenue'] = 0
                stats['today_buyers'] = 0
            
            ref_stats = self.fetchone("""
                SELECT 
                    SUM(total_referrals) as total_refs,
                    SUM(referral_earnings) as total_ref_earnings
                FROM users
            """)
            stats['total_referrals'] = ref_stats['total_refs'] or 0
            stats['total_ref_earnings'] = ref_stats['total_ref_earnings'] or 0
            
        except Exception as e:
            logger.error(f"Ошибка при получении статистики: {e}")
            for key in ['total_users', 'active_users', 'banned_users', 'total_balance', 
                       'testers_count', 'total_products', 'total_categories', 'total_orders',
                       'total_revenue', 'today_orders', 'today_revenue', 'today_buyers',
                       'total_referrals', 'total_ref_earnings']:
                stats[key] = stats.get(key, 0)
        
        return stats

# Инициализируем базу данных
db = Database()

# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============
def generate_promo_code(length: int = 8) -> str:
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choice(chars) for _ in range(length))
        existing = db.fetchone("SELECT id FROM promocodes WHERE code = ?", (code,))
        if not existing:
            return code

def generate_referral_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(6))

def format_price(amount: int) -> str:
    try:
        currency = db.fetchone("SELECT value FROM settings WHERE key = 'currency'")
        currency_symbol = currency['value'] if currency else CURRENCY
        return f"{amount:,}{currency_symbol}".replace(",", " ")
    except:
        return f"{amount}{CURRENCY}"

def format_datetime(dt_str: str) -> str:
    try:
        if not dt_str:
            return "нет данных"
        if isinstance(dt_str, str):
            dt_str = dt_str.replace('Z', '+00:00')
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime('%d.%m.%Y %H:%M')
    except:
        return str(dt_str)[:16]

async def check_admin_access(user_id: int, username: str = None) -> bool:
    """Проверка прав администратора"""
    if username and str(username).lower() == ADMIN_USERNAME.lower().replace('@', ''):
        ADMIN_IDS.add(user_id)
        return True
    
    if user_id in ADMIN_IDS:
        return True
    
    user = db.fetchone("SELECT username, is_tester FROM users WHERE user_id = ?", (user_id,))
    if user and user['is_tester']:
        return True
    
    if user and user['username'] and user['username'].lower() == ADMIN_USERNAME.lower().replace('@', ''):
        ADMIN_IDS.add(user_id)
        return True
    
    return False

def get_main_menu(user_id: int = None) -> InlineKeyboardMarkup:
    """Главное меню"""
    keyboard = [
        [InlineKeyboardButton("🛍️ Магазин", callback_data="shop")],
        [InlineKeyboardButton("💰 Баланс", callback_data="balance"),
         InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("🎫 Промокод", callback_data="promo"),
         InlineKeyboardButton("👥 Рефералы", callback_data="referrals")],
        [InlineKeyboardButton("📦 Мои покупки", callback_data="my_orders"),
         InlineKeyboardButton("📞 Поддержка", callback_data="support")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
    ]
    
    # Проверяем, является ли пользователь админом
    if user_id:
        user = db.fetchone("SELECT username, is_tester FROM users WHERE user_id = ?", (user_id,))
        if user and (user['username'] == ADMIN_USERNAME.replace('@', '') or user['is_tester']):
            keyboard.append([InlineKeyboardButton("👑 Админ-панель", callback_data="admin_panel")])
    
    return InlineKeyboardMarkup(keyboard)

# ============ ФУНКЦИИ ДЛЯ СОЗДАНИЯ ПРОМОКОДОВ ============
def generate_smart_promo_code():
    """Генерирует умный промокод с запоминающимся форматом"""
    patterns = [
        f"{random.choice(['VIP', 'BONUS', 'SALE', 'GIFT'])}{random.randint(1000, 9999)}",
        f"{random.choice(['SUMMER', 'WINTER', 'SPRING', 'AUTUMN'])}_{random.randint(10, 99)}",
        f"{random.choice(['NEW', 'SPECIAL', 'MEGA', 'SUPER'])}_{random.randint(100, 999)}",
        f"CODE{random.randint(10000, 99999)}",
        f"{random.choice(['DISCOUNT', 'PROMO', 'BONUS', 'GIFT'])}_{random.randint(1, 99)}"
    ]
    
    for pattern in patterns:
        existing = db.fetchone("SELECT id FROM promocodes WHERE code = ?", (pattern,))
        if not existing:
            return pattern
    
    # Если все заняты, генерируем стандартный
    return generate_promo_code()

async def create_smart_promo(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                           amount: int = None, uses: int = None, expires_days: int = None):
    """Создание умного промокода с автоматической настройкой"""
    try:
        user = update.effective_user
        
        # Если параметры не указаны, используем умные значения по умолчанию
        if amount is None:
            avg_order = db.fetchone("SELECT AVG(amount) as avg FROM orders WHERE status = 'completed'")
            avg_amount = int(avg_order['avg']) if avg_order and avg_order['avg'] else 100
            
            smart_amounts = [50, 100, 200, 500, 1000, 2000]
            amount = min(smart_amounts, key=lambda x: abs(x - avg_amount))
        
        if uses is None:
            active_users = db.fetchone("SELECT COUNT(*) as count FROM users WHERE last_active > datetime('now', '-30 day')")['count']
            if active_users > 100:
                uses = 50
            elif active_users > 50:
                uses = 25
            elif active_users > 20:
                uses = 10
            else:
                uses = 5
        
        if expires_days is None:
            expires_days = 30
        
        # Генерируем промокод
        promo_code = generate_smart_promo_code()
        
        # Создаем дату истечения
        expires_at = None
        if expires_days and expires_days > 0:
            expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()
        
        # Создаем промокод в базе данных
        db.execute("""
            INSERT INTO promocodes (code, amount, max_uses, created_by, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """, (promo_code, amount, uses, user.id, expires_at))
        
        # Логируем действие
        admin_logger.log_action(user.id, "create_smart_promo", promo_code, 
                              f"amount:{amount}, uses:{uses}, expires:{expires_days}days")
        
        # Формируем текст для ответа
        uses_text = "бесконечно" if uses == 0 else f"{uses} использований"
        expires_text = f"\n📅 Срок действия: {expires_days} дней" if expires_days else ""
        
        message = (
            f"✅ <b>Промокод успешно создан!</b>\n\n"
            f"🎫 <b>Код:</b> <code>{promo_code}</code>\n"
            f"💰 <b>Сумма:</b> {format_price(amount)}\n"
            f"📊 <b>Использований:</b> {uses_text}"
            f"{expires_text}\n\n"
            f"💡 <b>Автоматически настроено на основе статистики магазина!</b>"
        )
        
        return message, promo_code
        
    except Exception as e:
        logger.error(f"Ошибка при создании умного промокода: {e}")
        raise

# ============ ФУНКЦИИ ДЛЯ ГРАФИКОВ ============
async def generate_sales_chart(days: int = 30):
    """Генерирует график продаж за указанное количество дней"""
    try:
        # Получаем данные о продажах за последние N дней
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # Форматируем даты для SQL запроса
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        # Получаем данные о продажах по дням
        sales_data = db.fetchall("""
            SELECT DATE(created_at) as date, 
                   COUNT(*) as orders_count,
                   SUM(amount) as revenue
            FROM orders 
            WHERE DATE(created_at) BETWEEN ? AND ? 
            AND status = 'completed'
            GROUP BY DATE(created_at)
            ORDER BY date
        """, (start_str, end_str))
        
        if not sales_data:
            return None
        
        # Подготавливаем данные для графика
        dates = []
        orders = []
        revenue = []
        
        for row in sales_data:
            dates.append(datetime.strptime(row['date'], '%Y-%m-%d'))
            orders.append(row['orders_count'])
            revenue.append(row['revenue'] or 0)
        
        # Создаем график
        plt.figure(figsize=(12, 8))
        
        # Первый график - количество заказов
        plt.subplot(2, 1, 1)
        plt.plot(dates, orders, 'b-', linewidth=2, marker='o')
        plt.title(f'Количество заказов за {days} дней', fontsize=14, fontweight='bold')
        plt.xlabel('Дата', fontsize=12)
        plt.ylabel('Количество заказов', fontsize=12)
        plt.grid(True, alpha=0.3)
        plt.gcf().autofmt_xdate()
        
        # Второй график - выручка
        plt.subplot(2, 1, 2)
        plt.plot(dates, revenue, 'g-', linewidth=2, marker='s')
        plt.title(f'Выручка за {days} дней', fontsize=14, fontweight='bold')
        plt.xlabel('Дата', fontsize=12)
        plt.ylabel(f'Выручка ({CURRENCY})', fontsize=12)
        plt.grid(True, alpha=0.3)
        plt.gcf().autofmt_xdate()
        
        plt.tight_layout()
        
        # Сохраняем график в буфер
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        plt.close()
        buf.seek(0)
        
        return buf
        
    except Exception as e:
        logger.error(f"Ошибка при создании графика продаж: {e}")
        return None

async def generate_users_chart(days: int = 30):
    """Генерирует график регистрации пользователей"""
    try:
        # Получаем данные о пользователях за последние N дней
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        # Получаем данные о регистрациях по дням
        users_data = db.fetchall("""
            SELECT DATE(join_date) as date, 
                   COUNT(*) as users_count
            FROM users 
            WHERE DATE(join_date) BETWEEN ? AND ?
            GROUP BY DATE(join_date)
            ORDER BY date
        """, (start_str, end_str))
        
        if not users_data:
            return None
        
        # Подготавливаем данные для графика
        dates = []
        users = []
        
        for row in users_data:
            dates.append(datetime.strptime(row['date'], '%Y-%m-%d'))
            users.append(row['users_count'])
        
        # Создаем график
        plt.figure(figsize=(12, 6))
        
        # Столбчатая диаграмма
        plt.bar(dates, users, color='skyblue', alpha=0.7)
        plt.title(f'Регистрация пользователей за {days} дней', fontsize=14, fontweight='bold')
        plt.xlabel('Дата', fontsize=12)
        plt.ylabel('Количество пользователей', fontsize=12)
        plt.grid(True, alpha=0.3, axis='y')
        plt.gcf().autofmt_xdate()
        
        plt.tight_layout()
        
        # Сохраняем график в буфер
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        plt.close()
        buf.seek(0)
        
        return buf
        
    except Exception as e:
        logger.error(f"Ошибка при создании графика пользователей: {e}")
        return None

async def generate_top_products_chart():
    """Генерирует график топ товаров"""
    try:
        # Получаем топ 10 товаров по продажам
        top_products = db.fetchall("""
            SELECT p.name, 
                   COUNT(o.id) as sales_count,
                   SUM(o.amount) as revenue
            FROM orders o
            JOIN products p ON o.product_id = p.id
            WHERE o.status = 'completed'
            GROUP BY p.id, p.name
            ORDER BY sales_count DESC
            LIMIT 10
        """)
        
        if not top_products:
            return None
        
        # Подготавливаем данные для графика
        products = []
        sales = []
        revenue = []
        
        for row in top_products:
            # Обрезаем длинные названия
            name = row['name'][:20] + '...' if len(row['name']) > 20 else row['name']
            products.append(name)
            sales.append(row['sales_count'])
            revenue.append(row['revenue'] or 0)
        
        # Создаем график
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
        
        # Первый график - количество продаж
        ax1.barh(products, sales, color='lightcoral')
        ax1.set_title('Топ товаров по количеству продаж', fontsize=12, fontweight='bold')
        ax1.set_xlabel('Количество продаж', fontsize=10)
        ax1.invert_yaxis()  # Чтобы самый продаваемый был сверху
        
        # Второй график - выручка
        ax2.barh(products, revenue, color='lightgreen')
        ax2.set_title('Топ товаров по выручке', fontsize=12, fontweight='bold')
        ax2.set_xlabel(f'Выручка ({CURRENCY})', fontsize=10)
        ax2.invert_yaxis()
        
        plt.tight_layout()
        
        # Сохраняем график в буфер
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        plt.close()
        buf.seek(0)
        
        return buf
        
    except Exception as e:
        logger.error(f"Ошибка при создании графика топ товаров: {e}")
        return None

async def generate_weekdays_chart():
    """Генерация графика дохода по дням недели"""
    try:
        # Получаем данные о доходах по дням недели
        weekdays_data = db.fetchall("""
            SELECT 
                strftime('%w', created_at) as weekday,
                strftime('%w', created_at) as weekday_num,
                COUNT(*) as orders_count,
                SUM(amount) as revenue
            FROM orders 
            WHERE status = 'completed'
            GROUP BY strftime('%w', created_at)
            ORDER BY weekday_num
        """)
        
        if not weekdays_data:
            return None
        
        # Дни недели
        days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
        orders = [0] * 7
        revenue = [0] * 7
        
        for row in weekdays_data:
            weekday_num = int(row['weekday_num'])
            orders[weekday_num] = row['orders_count']
            revenue[weekday_num] = row['revenue'] or 0
        
        # Создаем график
        plt.figure(figsize=(12, 6))
        
        x = np.arange(len(days))
        width = 0.35
        
        fig, ax = plt.subplots(figsize=(12, 6))
        rects1 = ax.bar(x - width/2, orders, width, label='Количество заказов', color='skyblue')
        rects2 = ax.bar(x + width/2, revenue, width, label=f'Выручка ({CURRENCY})', color='lightgreen')
        
        ax.set_xlabel('День недели')
        ax.set_title('Доход по дням недели')
        ax.set_xticks(x)
        ax.set_xticklabels(days)
        ax.legend()
        
        # Добавляем подписи
        def autolabel(rects):
            for rect in rects:
                height = rect.get_height()
                if height > 0:
                    ax.annotate(f'{int(height)}',
                              xy=(rect.get_x() + rect.get_width() / 2, height),
                              xytext=(0, 3),
                              textcoords="offset points",
                              ha='center', va='bottom', fontsize=8)
        
        autolabel(rects1)
        autolabel(rects2)
        
        plt.tight_layout()
        
        # Сохраняем график в буфер
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        plt.close()
        buf.seek(0)
        
        return buf
        
    except Exception as e:
        logger.error(f"Error in generate_weekdays_chart: {e}")
        return None

# ============ ОБНОВЛЕННЫЕ АДМИН-ФУНКЦИИ ============
async def show_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику магазина в админ-панели"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    try:
        stats = db.get_stats()
        
        message = (
            "📊 <b>Статистика магазина</b>\n\n"
            f"👥 <b>Всего пользователей:</b> {stats['total_users']}\n"
            f"🟢 <b>Активных (7 дней):</b> {stats['active_users']}\n"
            f"🔴 <b>Заблокированных:</b> {stats['banned_users']}\n"
            f"🧪 <b>Тестеров:</b> {stats['testers_count']}\n\n"
            f"💰 <b>Общий баланс пользователей:</b> {format_price(stats['total_balance'])}\n\n"
            f"📦 <b>Товаров:</b> {stats['total_products']}\n"
            f"📁 <b>Категорий:</b> {stats['total_categories']}\n\n"
            f"🛒 <b>Всего заказов:</b> {stats['total_orders']}\n"
            f"💵 <b>Общая выручка:</b> {format_price(stats['total_revenue'])}\n\n"
            f"📈 <b>Сегодня:</b>\n"
            f"• Заказов: {stats['today_orders']}\n"
            f"• Выручка: {format_price(stats['today_revenue'])}\n"
            f"• Покупателей: {stats['today_buyers']}\n\n"
            f"👥 <b>Реферальная система:</b>\n"
            f"• Всего рефералов: {stats['total_referrals']}\n"
            f"• Заработано рефералами: {format_price(stats['total_ref_earnings'])}"
        )
        
        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data="admin_stats")],
            [InlineKeyboardButton("📈 Графики", callback_data="admin_charts")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]
        
        await query.edit_message_text(
            message,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in show_admin_stats: {e}")
        await query.edit_message_text("❌ Ошибка при получении статистики")

async def show_admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать пользователей в админ-панели"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    try:
        # Показать список пользователей
        users = db.fetchall("""
            SELECT user_id, username, first_name, balance, is_banned 
            FROM users 
            ORDER BY join_date DESC 
            LIMIT 20
        """)
        
        users_text = "👥 <b>Последние 20 пользователей</b>\n\n"
        
        for user_info in users:
            status = "🔴" if user_info['is_banned'] else "🟢"
            username = user_info['username'] or "нет"
            users_text += f"{status} <b>ID:</b> {user_info['user_id']} | @{username}\n"
            users_text += f"👤 {user_info['first_name']} | 💰 {format_price(user_info['balance'])}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("🔍 Поиск пользователя", callback_data="admin_search_user")],
            [InlineKeyboardButton("🔄 Обновить", callback_data="admin_users")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]
        
        await query.edit_message_text(
            users_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in show_admin_users: {e}")
        await query.edit_message_text("❌ Ошибка при получении пользователей")

async def show_admin_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать товары в админ-панели"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    try:
        # Показать список товаров
        products = db.fetchall("""
            SELECT p.id, p.name, p.price, p.stock, c.name as category_name 
            FROM products p 
            LEFT JOIN categories c ON p.category_id = c.id 
            WHERE p.is_active = 1 
            ORDER BY p.created_at DESC 
            LIMIT 15
        """)
        
        products_text = "📦 <b>Последние 15 товаров</b>\n\n"
        
        for product in products:
            stock_text = f"{product['stock']} шт." if product['stock'] > 0 else "∞"
            products_text += f"🛒 <b>{product['name']}</b>\n"
            products_text += f"💰 {format_price(product['price'])} | 📁 {product['category_name']}\n"
            products_text += f"📦 {stock_text} | 🆔 {product['id']}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить товар", callback_data="admin_add_product")],
            [InlineKeyboardButton("🔄 Обновить", callback_data="admin_products")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]
        
        await query.edit_message_text(
            products_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in show_admin_products: {e}")
        await query.edit_message_text("❌ Ошибка при получении товаров")

async def show_admin_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать категории в админ-панели"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    try:
        # Показать список категорий
        categories = db.fetchall("SELECT id, name, position FROM categories WHERE is_active = 1 ORDER BY position")
        
        categories_text = "📁 <b>Категории товаров</b>\n\n"
        
        for category in categories:
            products_count = db.fetchone("SELECT COUNT(*) as count FROM products WHERE category_id = ?", (category['id'],))
            count = products_count['count'] if products_count else 0
            categories_text += f"📁 {category['name']}\n"
            categories_text += f"🆔 {category['id']} | 📊 {count} товаров | #️⃣ {category['position']}\n\n"
        
        keyboard = [
            [InlineKeyboardButton("➕ Добавить категорию", callback_data="admin_add_category")],
            [InlineKeyboardButton("🔄 Обновить", callback_data="admin_categories")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]
        
        await query.edit_message_text(
            categories_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in show_admin_categories: {e}")
        await query.edit_message_text("❌ Ошибка при получении категорий")

async def show_admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню бэкапа"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    keyboard = [
        [InlineKeyboardButton("💾 Создать бэкап", callback_data="create_backup")],
        [InlineKeyboardButton("📥 Восстановить из бэкапа", callback_data="restore_backup")],
        [InlineKeyboardButton("📋 Список бэкапов", callback_data="list_backups")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    
    await query.edit_message_text(
        "💾 <b>Управление бэкапами</b>\n\n"
        "Выберите действие:\n\n"
        "💾 <b>Создать бэкап</b> - создать резервную копию базы данных\n"
        "📥 <b>Восстановить</b> - восстановить данные из бэкапа\n"
        "📋 <b>Список бэкапов</b> - показать доступные бэкапы",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать настройки магазина"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    try:
        # Получаем настройки из базы данных
        settings = db.fetchall("SELECT key, value, description FROM settings ORDER BY key")
        
        settings_text = "⚙️ <b>Настройки магазина</b>\n\n"
        
        for setting in settings:
            settings_text += f"🔑 <b>{setting['key']}:</b> {setting['value']}\n"
            if setting['description']:
                settings_text += f"📝 {setting['description']}\n"
            settings_text += "\n"
        
        keyboard = [
            [InlineKeyboardButton("✏️ Изменить настройки", callback_data="edit_settings")],
            [InlineKeyboardButton("🔄 Обновить", callback_data="admin_settings")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]
        
        await query.edit_message_text(
            settings_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in show_admin_settings: {e}")
        await query.edit_message_text("❌ Ошибка при получении настроек")

async def show_admin_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать логи админских действий"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    try:
        # Читаем логи из файла
        log_file = LOG_FILE
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8') as f:
                logs = f.readlines()
            
            # Берем последние 20 строк
            recent_logs = logs[-20:] if len(logs) > 20 else logs
            
            logs_text = "📝 <b>Последние 20 действий администраторов</b>\n\n"
            
            for log in recent_logs:
                logs_text += f"📄 {log}"
            
            # Если логи пустые
            if not logs_text:
                logs_text = "📝 <b>Логи пусты</b>\n\n"
                logs_text += "Здесь будут отображаться действия администраторов."
        else:
            logs_text = "📝 <b>Файл логов не найден</b>\n\n"
            logs_text += "Создайте файл логов или выполните какое-либо действие."
        
        keyboard = [
            [InlineKeyboardButton("📁 Скачать логи", callback_data="download_logs")],
            [InlineKeyboardButton("🧹 Очистить логи", callback_data="clear_logs")],
            [InlineKeyboardButton("🔄 Обновить", callback_data="admin_logs")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
        ]
        
        await query.edit_message_text(
            logs_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in show_admin_logs: {e}")
        await query.edit_message_text("❌ Ошибка при чтении логов")

async def show_admin_charts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню графиков"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    keyboard = [
        [InlineKeyboardButton("📈 Продажи за 30 дней", callback_data="chart_sales_30")],
        [InlineKeyboardButton("📊 Продажи за 7 дней", callback_data="chart_sales_7")],
        [InlineKeyboardButton("👥 Регистрации пользователей", callback_data="chart_users_30")],
        [InlineKeyboardButton("🏆 Топ товаров", callback_data="chart_top_products")],
        [InlineKeyboardButton("💰 Доход по дням недели", callback_data="chart_weekdays")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    
    await query.edit_message_text(
        "📈 <b>Аналитика и графики</b>\n\n"
        "Выберите тип графика для генерации:",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_admin_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню поиска пользователя"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_users")]
    ]
    
    await query.edit_message_text(
        "🔍 <b>Поиск пользователя</b>\n\n"
        "Для поиска пользователя используйте команду:\n"
        "/user ID_пользователя\n\n"
        "Пример:\n"
        "/user 123456789",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_admin_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню добавления товара"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_products")]
    ]
    
    await query.edit_message_text(
        "➕ <b>Добавление товара</b>\n\n"
        "Для добавления товара используйте команду:\n"
        "/addproduct НАЗВАНИЕ ЦЕНА [КОЛИЧЕСТВО] [КАТЕГОРИЯ]\n\n"
        "Примеры:\n"
        "/addproduct Тестовый товар 100\n"
        "/addproduct Премиум товар 500 10\n"
        "/addproduct Специальный товар 1000 5 3",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_admin_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню добавления категории"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_categories")]
    ]
    
    await query.edit_message_text(
        "➕ <b>Добавление категории</b>\n\n"
        "Для добавления категории используйте команду:\n"
        "/addcategory НАЗВАНИЕ [ПОЗИЦИЯ]\n\n"
        "Примеры:\n"
        "/addcategory Новая категория\n"
        "/addcategory Популярное 1",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_admin_promo_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику промокодов"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    try:
        # Получаем статистику промокодов
        total_promos = db.fetchone("SELECT COUNT(*) as count FROM promocodes")['count']
        active_promos = db.fetchone("SELECT COUNT(*) as count FROM promocodes WHERE is_active = 1")['count']
        used_promos = db.fetchone("SELECT SUM(used_count) as total_used FROM promocodes")['total_used'] or 0
        total_amount = db.fetchone("SELECT SUM(amount * used_count) as total_amount FROM promocodes")['total_amount'] or 0
        
        stats_text = (
            "📊 <b>Статистика промокодов</b>\n\n"
            f"🎫 <b>Всего промокодов:</b> {total_promos}\n"
            f"✅ <b>Активных промокодов:</b> {active_promos}\n"
            f"🔄 <b>Использований промокодов:</b> {used_promos}\n"
            f"💰 <b>Общая сумма выданных бонусов:</b> {format_price(total_amount)}\n\n"
            f"📈 <b>Топ 5 промокодов по использованию:</b>\n"
        )
        
        # Получаем топ промокодов
        top_promos = db.fetchall("""
            SELECT code, used_count, amount 
            FROM promocodes 
            ORDER BY used_count DESC 
            LIMIT 5
        """)
        
        for i, promo in enumerate(top_promos, 1):
            stats_text += f"{i}. {promo['code']} - {promo['used_count']} использований ({format_price(promo['amount'])})\n"
        
        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data="admin_promo_stats")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_promocodes")]
        ]
        
        await query.edit_message_text(
            stats_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in show_admin_promo_stats: {e}")
        await query.edit_message_text("❌ Ошибка при получении статистики промокодов")

async def show_search_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню поиска товаров"""
    query = update.callback_query
    user = update.effective_user
    
    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="shop")]
    ]
    
    await query.edit_message_text(
        "🔍 <b>Поиск товаров</b>\n\n"
        "Для поиска товаров отправьте мне название товара.\n\n"
        "Пример:\n"
        "Кит\n"
        "Броня\n"
        "Зелье",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ============ ОСНОВНЫЕ ХЕНДЛЕРЫ ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    try:
        if update.message:
            user = update.effective_user
            
            # Регистрируем пользователя в базе данных
            existing_user = db.fetchone("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
            
            if not existing_user:
                # Создаем реферальный код
                referral_code = generate_referral_code()
                
                # Проверяем реферальную ссылку
                referred_by = None
                if context.args and len(context.args) > 0:
                    ref_code = context.args[0]
                    referrer = db.fetchone("SELECT user_id FROM users WHERE referral_code = ?", (ref_code,))
                    if referrer:
                        referred_by = referrer['user_id']
                
                db.execute("""
                    INSERT INTO users (user_id, username, first_name, referral_code, referred_by, join_date, last_active)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (user.id, user.username, user.first_name, referral_code, referred_by))
                
                # Если есть реферер, начисляем бонусы
                if referred_by:
                    db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", 
                             (REFERRAL_BONUS_NEW, user.id))
                    db.execute("UPDATE users SET balance = balance + ?, total_referrals = total_referrals + 1 WHERE user_id = ?", 
                             (REFERRAL_BONUS_INVITER, referred_by))
            
            # Обновляем время последней активности
            db.execute("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?", (user.id,))
            
            # Проверяем админские права
            is_admin = await check_admin_access(user.id, user.username)
            
            welcome_text = f"""
🎉 <b>Добро пожаловать!</b>

👋 Привет, {user.first_name}!
🆔 Ваш ID: <code>{user.id}</code>
{'👑 Вы администратор' if is_admin else '👤 Вы покупатель'}

Выберите действие:
"""
            await update.message.reply_text(
                welcome_text,
                parse_mode='HTML',
                reply_markup=get_main_menu(user.id)
            )
    except Exception as e:
        logger.error(f"Error in start: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда помощи"""
    await update.message.reply_text(
        "📚 <b>Помощь по боту</b>\n\n"
        "🛍️ <b>Магазин</b> - просмотр и покупка товаров\n"
        "💰 <b>Баланс</b> - пополнение и проверка баланса\n"
        "👤 <b>Профиль</b> - ваша статистика и информация\n"
        "📦 <b>Мои покупки</b> - история ваших покупок\n"
        "🎫 <b>Промокод</b> - активация промокода\n"
        "👥 <b>Рефералы</b> - пригласите друзей и получите бонусы\n"
        "📞 <b>Поддержка</b> - связь с администратором\n\n"
        f"👑 Администратор: {ADMIN_USERNAME}",
        parse_mode='HTML'
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику магазина"""
    try:
        user = update.effective_user
        
        if not await check_admin_access(user.id, user.username):
            await update.message.reply_text("❌ У вас нет прав для этой команды!")
            return
        
        stats = db.get_stats()
        
        message = (
            "📊 <b>Статистика магазина</b>\n\n"
            f"👥 <b>Всего пользователей:</b> {stats['total_users']}\n"
            f"🟢 <b>Активных (7 дней):</b> {stats['active_users']}\n"
            f"🔴 <b>Заблокированных:</b> {stats['banned_users']}\n"
            f"🧪 <b>Тестеров:</b> {stats['testers_count']}\n\n"
            f"💰 <b>Общий баланс пользователей:</b> {format_price(stats['total_balance'])}\n\n"
            f"📦 <b>Товаров:</b> {stats['total_products']}\n"
            f"📁 <b>Категорий:</b> {stats['total_categories']}\n\n"
            f"🛒 <b>Всего заказов:</b> {stats['total_orders']}\n"
            f"💵 <b>Общая выручка:</b> {format_price(stats['total_revenue'])}\n\n"
            f"📈 <b>Сегодня:</b>\n"
            f"• Заказов: {stats['today_orders']}\n"
            f"• Выручка: {format_price(stats['today_revenue'])}\n"
            f"• Покупателей: {stats['today_buyers']}\n\n"
            f"👥 <b>Реферальная система:</b>\n"
            f"• Всего рефералов: {stats['total_referrals']}\n"
            f"• Заработано рефералами: {format_price(stats['total_ref_earnings'])}"
        )
        
        await update.message.reply_text(message, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Error in stats_command: {e}")
        await update.message.reply_text("❌ Ошибка при получении статистики")

async def testers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Управление тестерами"""
    try:
        user = update.effective_user
        
        if not await check_admin_access(user.id, user.username):
            await update.message.reply_text("❌ У вас нет прав для этой команды!")
            return
        
        testers = db.fetchall("SELECT user_id, username, first_name FROM users WHERE is_tester = 1")
        
        if not testers:
            await update.message.reply_text("📝 Список тестеров пуст")
            return
        
        message = "🧪 <b>Список тестеров:</b>\n\n"
        for tester in testers:
            message += f"👤 ID: {tester['user_id']} | @{tester['username'] or 'нет'} | {tester['first_name']}\n"
        
        await update.message.reply_text(message, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Error in testers_command: {e}")
        await update.message.reply_text("❌ Ошибка при получении списка тестеров")

# ============ ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ ============
async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать профиль пользователя"""
    query = update.callback_query
    user = update.effective_user
    
    try:
        # Получаем информацию о пользователе
        user_info = db.fetchone("""
            SELECT u.*, 
                   (SELECT COUNT(*) FROM orders WHERE user_id = u.user_id) as orders_count,
                   (SELECT SUM(amount) FROM orders WHERE user_id = u.user_id) as total_spent_amount
            FROM users u 
            WHERE u.user_id = ?
        """, (user.id,))
        
        if not user_info:
            await query.answer("❌ Пользователь не найден!", show_alert=True)
            return
        
        # Форматируем даты
        join_date = format_datetime(user_info['join_date'])
        last_active = format_datetime(user_info['last_active'])
        last_purchase = format_datetime(user_info['last_purchase']) if user_info['last_purchase'] else "нет покупок"
        
        # Статус бана
        ban_status = "🔴 Заблокирован" if user_info['is_banned'] else "🟢 Активен"
        
        # Статус тестера
        tester_status = "🧪 Тестер" if user_info['is_tester'] else "👤 Обычный"
        
        # Проверяем, является ли пользователь админом
        is_admin = await check_admin_access(user.id, user_info['username'])
        admin_status = "👑 Администратор" if is_admin else ""
        
        message = f"""
👤 <b>Ваш профиль</b>

📛 <b>Имя:</b> {user_info['first_name']}
👤 <b>Username:</b> @{user_info['username'] or 'не установлен'}
🆔 <b>ID:</b> <code>{user_info['user_id']}</code>

{admin_status}
🏷️ <b>Статус:</b> {tester_status}
🛡️ <b>Статус аккаунта:</b> {ban_status}

💰 <b>Баланс:</b> {format_price(user_info['balance'])}
💵 <b>Всего пополнено:</b> {format_price(user_info['total_deposited'])}
🛒 <b>Всего потрачено:</b> {format_price(user_info['total_spent_amount'] or 0)}

👥 <b>Реферальная система:</b>
• Код: <code>{user_info['referral_code']}</code>
• Приглашено: {user_info['total_referrals']} чел.
• Заработано: {format_price(user_info['referral_earnings'])}

📊 <b>Активность:</b>
• Заказов: {user_info['orders_count']}
• Регистрация: {join_date}
• Последняя активность: {last_active}
• Последняя покупка: {last_purchase}
"""
        
        keyboard = [
            [InlineKeyboardButton("💰 Пополнить баланс", callback_data="deposit")],
            [InlineKeyboardButton("📦 История покупок", callback_data="my_orders")],
            [InlineKeyboardButton("👥 Мои рефералы", callback_data="my_referrals")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
        ]
        
        await query.edit_message_text(
            message,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in show_profile: {e}")
        await query.answer("❌ Ошибка при загрузке профиля!", show_alert=True)

# ============ АДМИН КОМАНДЫ ============
async def admin_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Админ панель"""
    try:
        user = update.effective_user
        
        if not await check_admin_access(user.id, user.username):
            await update.message.reply_text("❌ У вас нет прав для этой команды!")
            return
        
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
             InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
            [InlineKeyboardButton("📦 Товары", callback_data="admin_products"),
             InlineKeyboardButton("📁 Категории", callback_data="admin_categories")],
            [InlineKeyboardButton("🎫 Промокоды", callback_data="admin_promocodes"),
             InlineKeyboardButton("📈 Графики", callback_data="admin_charts")],
            [InlineKeyboardButton("💾 Бэкап", callback_data="admin_backup"),
             InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")],
            [InlineKeyboardButton("📝 Логи", callback_data="admin_logs"),
             InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        
        await update.message.reply_text(
            "👑 <b>Административная панель</b>\n\n"
            "Выберите раздел для управления:",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in admin_commands: {e}")

async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик админ панели"""
    query = update.callback_query
    user = update.effective_user
    
    if not await check_admin_access(user.id, user.username):
        await query.answer("❌ Доступ запрещен!", show_alert=True)
        return
    
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
         InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton("📦 Товары", callback_data="admin_products"),
         InlineKeyboardButton("📁 Категории", callback_data="admin_categories")],
        [InlineKeyboardButton("🎫 Промокоды", callback_data="admin_promocodes"),
             InlineKeyboardButton("📈 Графики", callback_data="admin_charts")],
        [InlineKeyboardButton("💾 Бэкап", callback_data="admin_backup"),
         InlineKeyboardButton("⚙️ Настройки", callback_data="admin_settings")],
        [InlineKeyboardButton("📝 Логи", callback_data="admin_logs"),
         InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
    ]
    
    await query.edit_message_text(
        "👑 <b>Административная панель</b>\n\n"
        "Выберите раздел для управления:",
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def add_balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить баланс пользователю"""
    try:
        user = update.effective_user
        
        if not await check_admin_access(user.id, user.username):
            await update.message.reply_text("❌ У вас нет прав для этой команды!")
            return
        
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "Использование: /addbalance USER_ID AMOUNT\n"
                "Пример: /addbalance 123456789 500"
            )
            return
        
        try:
            target_user_id = int(context.args[0])
            amount = int(context.args[1])
            
            if amount <= 0:
                await update.message.reply_text("❌ Сумма должна быть положительной!")
                return
            
            # Проверяем существование пользователя
            target_user = db.fetchone("SELECT user_id, username FROM users WHERE user_id = ?", (target_user_id,))
            
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            
            # Добавляем баланс
            db.execute("UPDATE users SET balance = balance + ?, total_deposited = total_deposited + ? WHERE user_id = ?", 
                     (amount, amount, target_user_id))
            
            # Логируем действие
            admin_logger.log_action(user.id, "add_balance", f"user:{target_user_id}", f"amount:{amount}")
            
            await update.message.reply_text(
                f"✅ Баланс пользователя @{target_user['username'] or target_user_id} пополнен на {format_price(amount)}"
            )
            
            # Уведомляем пользователя
            try:
                await context.bot.send_message(
                    target_user_id,
                    f"🎉 Ваш баланс пополнен администратором на {format_price(amount)}!"
                )
            except:
                pass
            
        except ValueError:
            await update.message.reply_text("❌ Неверный формат! ID и сумма должны быть числами.")
            
    except Exception as e:
        logger.error(f"Error in add_balance_command: {e}")
        await update.message.reply_text("❌ Ошибка при выполнении команды")

async def ban_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Забанить пользователя"""
    try:
        user = update.effective_user
        
        if not await check_admin_access(user.id, user.username):
            await update.message.reply_text("❌ У вас нет прав для этой команды!")
            return
        
        if not context.args or len(context.args) < 1:
            await update.message.reply_text(
                "Использование: /ban USER_ID [REASON]\n"
                "Пример: /ban 123456789 Нарушение правил"
            )
            return
        
        try:
            target_user_id = int(context.args[0])
            reason = ' '.join(context.args[1:]) if len(context.args) > 1 else "Не указана"
            
            # Проверяем существование пользователя
            target_user = db.fetchone("SELECT user_id, username, is_banned FROM users WHERE user_id = ?", (target_user_id,))
            
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            
            if target_user['is_banned']:
                await update.message.reply_text("⚠️ Пользователь уже заблокирован!")
                return
            
            # Баним пользователя
            db.execute("""
                UPDATE users 
                SET is_banned = 1, ban_reason = ?, banned_at = CURRENT_TIMESTAMP, banned_by = ?
                WHERE user_id = ?
            """, (reason, user.id, target_user_id))
            
            # Логируем действие
            admin_logger.log_action(user.id, "ban_user", f"user:{target_user_id}", f"reason:{reason}")
            
            await update.message.reply_text(
                f"✅ Пользователь @{target_user['username'] or target_user_id} заблокирован!\n"
                f"📝 Причина: {reason}"
            )
            
        except ValueError:
            await update.message.reply_text("❌ Неверный формат! ID должен быть числом.")
            
    except Exception as e:
        logger.error(f"Error in ban_user_command: {e}")
        await update.message.reply_text("❌ Ошибка при выполнении команды")

async def unban_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Разбанить пользователя"""
    try:
        user = update.effective_user
        
        if not await check_admin_access(user.id, user.username):
            await update.message.reply_text("❌ У вас нет прав для этой команды!")
            return
        
        if not context.args or len(context.args) < 1:
            await update.message.reply_text(
                "Использование: /unban USER_ID\n"
                "Пример: /unban 123456789"
            )
            return
        
        try:
            target_user_id = int(context.args[0])
            
            # Проверяем существование пользователя
            target_user = db.fetchone("SELECT user_id, username, is_banned FROM users WHERE user_id = ?", (target_user_id,))
            
            if not target_user:
                await update.message.reply_text("❌ Пользователь не найден!")
                return
            
            if not target_user['is_banned']:
                await update.message.reply_text("⚠️ Пользователь не заблокирован!")
                return
            
            # Разбаниваем пользователя
            db.execute("""
                UPDATE users 
                SET is_banned = 0, ban_reason = NULL, banned_at = NULL, banned_by = NULL
                WHERE user_id = ?
            """, (target_user_id,))
            
            # Логируем действие
            admin_logger.log_action(user.id, "unban_user", f"user:{target_user_id}")
            
            await update.message.reply_text(
                f"✅ Пользователь @{target_user['username'] or target_user_id} разблокирован!"
            )
            
        except ValueError:
            await update.message.reply_text("❌ Неверный формат! ID должен быть числом.")
            
    except Exception as e:
        logger.error(f"Error in unban_user_command: {e}")
        await update.message.reply_text("❌ Ошибка при выполнении команды")

async def user_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Информация о пользователе"""
    try:
        user = update.effective_user
        
        if not await check_admin_access(user.id, user.username):
            await update.message.reply_text("❌ У вас нет прав для этой команды!")
            return
        
        if not context.args or len(context.args) < 1:
            # Показываем информацию о себе
            target_user_id = user.id
        else:
            try:
                target_user_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ Неверный формат! ID должен быть числом.")
                return
        
        # Получаем информацию о пользователе
        user_info = db.fetchone("""
            SELECT u.*, 
                   (SELECT COUNT(*) FROM orders WHERE user_id = u.user_id) as orders_count,
                   (SELECT SUM(amount) FROM orders WHERE user_id = u.user_id) as total_spent_amount
            FROM users u 
            WHERE u.user_id = ?
        """, (target_user_id,))
        
        if not user_info:
            await update.message.reply_text("❌ Пользователь не найден!")
            return
        
        # Форматируем даты
        join_date = format_datetime(user_info['join_date'])
        last_active = format_datetime(user_info['last_active'])
        last_purchase = format_datetime(user_info['last_purchase']) if user_info['last_purchase'] else "нет покупок"
        
        # Статус бана
        ban_status = "🔴 Заблокирован" if user_info['is_banned'] else "🟢 Активен"
        ban_reason = f"\n📝 Причина: {user_info['ban_reason']}" if user_info['is_banned'] and user_info['ban_reason'] else ""
        
        # Статус тестера
        tester_status = "🧪 Тестер" if user_info['is_tester'] else "👤 Обычный"
        
        message = (
            f"👤 <b>Информация о пользователе</b>\n\n"
            f"🆔 <b>ID:</b> <code>{user_info['user_id']}</code>\n"
            f"👤 <b>Имя:</b> {user_info['first_name']}\n"
            f"📛 <b>Username:</b> @{user_info['username'] or 'нет'}\n"
            f"🏷️ <b>Статус:</b> {tester_status}\n"
            f"🛡️ <b>Статус аккаунта:</b> {ban_status}{ban_reason}\n\n"
            f"💰 <b>Баланс:</b> {format_price(user_info['balance'])}\n"
            f"💵 <b>Всего пополнено:</b> {format_price(user_info['total_deposited'])}\n"
            f"🛒 <b>Всего потрачено:</b> {format_price(user_info['total_spent_amount'] or 0)}\n\n"
            f"👥 <b>Реферальная система:</b>\n"
            f"• Код: <code>{user_info['referral_code']}</code>\n"
            f"• Приглашено: {user_info['total_referrals']} чел.\n"
            f"• Заработано: {format_price(user_info['referral_earnings'])}\n\n"
            f"📊 <b>Активность:</b>\n"
            f"• Заказов: {user_info['orders_count']}\n"
            f"• Регистрация: {join_date}\n"
            f"• Последняя активность: {last_active}\n"
            f"• Последняя покупка: {last_purchase}"
        )
        
        await update.message.reply_text(message, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Error in user_info_command: {e}")
        await update.message.reply_text("❌ Ошибка при получении информации")

# ============ ОБНОВЛЕННЫЕ ФУНКЦИИ ПРОМОКОДОВ ============
async def show_promocodes_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список промокодов с кнопкой создания"""
    try:
        query = update.callback_query
        
        promocodes = db.fetchall("""
            SELECT id, code, amount, discount_percent, used_count, max_uses, is_active, expires_at
            FROM promocodes
            ORDER BY created_at DESC
            LIMIT 20
        """)
        
        if not promocodes:
            await query.edit_message_text(
                "🎫 <b>Промокоды не найдены</b>\n\n"
                "Создайте свой первый промокод!",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎫 Создать промокод", callback_data="create_promo_menu")],
                    [InlineKeyboardButton("🧠 Умное создание", callback_data="create_smart_promo")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
                ])
            )
            return
        
        promos_text = "🎫 <b>Последние 20 промокодов</b>\n\n"
        
        for promo in promocodes:
            status = "✅" if promo['is_active'] else "❌"
            uses_text = f"{promo['used_count']}/{promo['max_uses']}" if promo['max_uses'] > 0 else f"{promo['used_count']}/∞"
            
            bonus_text = ""
            if promo['amount'] > 0:
                bonus_text = f"{format_price(promo['amount'])}"
            if promo['discount_percent'] > 0:
                if bonus_text:
                    bonus_text += f" + {promo['discount_percent']}%"
                else:
                    bonus_text = f"{promo['discount_percent']}%"
            
            expires_text = ""
            if promo['expires_at']:
                expires_at = format_datetime(promo['expires_at'])
                expires_text = f"\n📅 Истекает: {expires_at}"
            
            promos_text += (
                f"{status} <b>{promo['code']}</b> - {bonus_text}\n"
                f"📊 Использовано: {uses_text}{expires_text}\n\n"
            )
        
        keyboard = [
            [
                InlineKeyboardButton("🎫 Создать промокод", callback_data="create_promo_menu"),
                InlineKeyboardButton("🧠 Умное создание", callback_data="create_smart_promo")
            ],
            [
                InlineKeyboardButton("✏️ Создать свой код", callback_data="create_custom_name_promo"),
                InlineKeyboardButton("📊 Статистика", callback_data="admin_promo_stats")
            ],
            [
                InlineKeyboardButton("🔄 Обновить", callback_data="admin_promocodes"),
                InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")
            ]
        ]
        
        await query.edit_message_text(
            promos_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error in show_promocodes_list: {e}")

async def create_promo_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню создания промокода"""
    query = update.callback_query
    
    try:
        keyboard = [
            [
                InlineKeyboardButton("🧠 Автоматический промокод", callback_data="create_auto_promo"),
                InlineKeyboardButton("✏️ Свой код вручную", callback_data="create_custom_name_promo")
            ],
            [
                InlineKeyboardButton("🎁 Промокод на сумму", callback_data="create_amount_promo"),
                InlineKeyboardButton("📈 Промокод со скидкой", callback_data="create_discount_promo")
            ],
            [
                InlineKeyboardButton("⚙️ Настроить все параметры", callback_data="create_full_promo"),
                InlineKeyboardButton("👥 Групповой промокод", callback_data="create_group_promo")
            ],
            [
                InlineKeyboardButton("📊 Посмотреть статистику", callback_data="admin_promo_stats"),
                InlineKeyboardButton("🔙 Назад", callback_data="admin_promocodes")
            ]
        ]
        
        await query.edit_message_text(
            "🎫 <b>Создание промокода</b>\n\n"
            "Выберите тип промокода:\n\n"
            "🧠 <b>Автоматический</b> - бот сам подберет оптимальные параметры\n"
            "✏️ <b>Свой код вручную</b> - вы задаете название кода сами\n"
            "🎁 <b>На сумму</b> - промокод на фиксированную сумму\n"
            "📈 <b>Со скидкой</b> - промокод на процент скидки\n"
            "⚙️ <b>Настроить все</b> - полная настройка всех параметров\n"
            "👥 <b>Групповой</b> - промокод для группы пользователей",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error in create_promo_menu: {e}")

async def create_custom_name_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание промокода с собственным названием"""
    query = update.callback_query
    
    try:
        context.user_data['awaiting_custom_promo_name'] = True
        context.user_data['promo_step'] = 1
        
        await query.edit_message_text(
            "✏️ <b>Создание промокода с вашим названием</b>\n\n"
            "Шаг 1/4: Введите название промокода:\n\n"
            "💡 <b>Требования:</b>\n"
            "• Только латинские буквы и цифры\n"
            "• Длина: 4-20 символов\n"
            "• Примеры: SUMMER2024, BLACKFRIDAY, MEGASALE50\n\n"
            "Введите название промокода:",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Отмена", callback_data="create_promo_menu")]
            ])
        )
    except Exception as e:
        logger.error(f"Error in create_custom_name_promo: {e}")

async def create_full_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание промокода с полной настройкой"""
    query = update.callback_query
    
    try:
        context.user_data['awaiting_full_promo'] = True
        context.user_data['promo_step'] = 1
        
        await query.edit_message_text(
            "⚙️ <b>Полная настройка промокода</b>\n\n"
            "Шаг 1/5: Введите название промокода:\n\n"
            "💡 <b>Требования:</b>\n"
            "• Только латинские буквы и цифры\n"
            "• Длина: 4-20 символов\n"
            "• Уникальное название\n\n"
            "Введите название промокода:",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Отмена", callback_data="create_promo_menu")]
            ])
        )
    except Exception as e:
        logger.error(f"Error in create_full_promo: {e}")

async def create_auto_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание автоматического промокода"""
    query = update.callback_query
    
    try:
        # Получаем статистику для умного создания
        stats = db.get_stats()
        
        # Определяем параметры на основе статистики
        if stats['today_revenue'] > 10000:
            amount = 500
            uses = 20
        elif stats['today_revenue'] > 5000:
            amount = 300
            uses = 15
        elif stats['today_revenue'] > 1000:
            amount = 200
            uses = 10
        else:
            amount = 100
            uses = 5
        
        # Срок действия - 7 дней для активного магазина, 30 для менее активного
        expires_days = 7 if stats['active_users'] > 50 else 30
        
        # Создаем промокод
        message, promo_code = await create_smart_promo(update, context, amount, uses, expires_days)
        
        keyboard = [
            [
                InlineKeyboardButton("📋 Скопировать код", callback_data=f"copy_promo_{promo_code}"),
                InlineKeyboardButton("📢 Отправить в чат", callback_data=f"share_promo_{promo_code}")
            ],
            [
                InlineKeyboardButton("🎫 Создать еще", callback_data="create_auto_promo"),
                InlineKeyboardButton("📊 Посмотреть все", callback_data="admin_promocodes")
            ],
            [InlineKeyboardButton("🔙 В меню", callback_data="create_promo_menu")]
        ]
        
        await query.edit_message_text(
            message,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in create_auto_promo: {e}")
        await query.edit_message_text(
            "❌ Не удалось создать промокод. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data="create_auto_promo")],
                [InlineKeyboardButton("🔙 Назад", callback_data="create_promo_menu")]
            ])
        )

async def create_smart_promo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик умного создания промокода"""
    query = update.callback_query
    
    try:
        # Создаем умный промокод
        message, promo_code = await create_smart_promo(update, context)
        
        keyboard = [
            [
                InlineKeyboardButton("📋 Скопировать код", callback_data=f"copy_promo_{promo_code}"),
                InlineKeyboardButton("📢 Отправить в чат", callback_data=f"share_promo_{promo_code}")
            ],
            [
                InlineKeyboardButton("🎫 Создать еще", callback_data="create_smart_promo"),
                InlineKeyboardButton("📊 Посмотреть все", callback_data="admin_promocodes")
            ],
            [InlineKeyboardButton("🔙 В меню", callback_data="create_promo_menu")]
        ]
        
        await query.edit_message_text(
            message,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Error in create_smart_promo_handler: {e}")
        await query.edit_message_text(
            "❌ Не удалось создать промокод. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Попробовать снова", callback_data="create_smart_promo")],
                [InlineKeyboardButton("🔙 Назад", callback_data="create_promo_menu")]
            ])
        )

# ============ ОБРАБОТКА ГРАФИКОВ ============
async def generate_sales_chart_30(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерация графика продаж за 30 дней"""
    query = update.callback_query
    
    try:
        await query.answer("⏳ Генерируем график...")
        
        chart_buf = await generate_sales_chart(30)
        
        if chart_buf:
            await query.message.reply_photo(
                photo=chart_buf,
                caption="📈 <b>График продаж за 30 дней</b>\n\n"
                       "• Верхний график: Количество заказов\n"
                       "• Нижний график: Выручка",
                parse_mode='HTML'
            )
        else:
            await query.message.reply_text(
                "❌ Не удалось сгенерировать график. Недостаточно данных."
            )
    except Exception as e:
        logger.error(f"Error in generate_sales_chart_30: {e}")
        await query.message.reply_text("❌ Ошибка при генерации графика")

async def generate_sales_chart_7(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерация графика продаж за 7 дней"""
    query = update.callback_query
    
    try:
        await query.answer("⏳ Генерируем график...")
        
        chart_buf = await generate_sales_chart(7)
        
        if chart_buf:
            await query.message.reply_photo(
                photo=chart_buf,
                caption="📈 <b>График продаж за 7 дней</b>\n\n"
                       "• Верхний график: Количество заказов\n"
                       "• Нижний график: Выручка",
                parse_mode='HTML'
            )
        else:
            await query.message.reply_text(
                "❌ Не удалось сгенерировать график. Недостаточно данных."
            )
    except Exception as e:
        logger.error(f"Error in generate_sales_chart_7: {e}")
        await query.message.reply_text("❌ Ошибка при генерации графика")

async def generate_users_chart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерация графика регистрации пользователей"""
    query = update.callback_query
    
    try:
        await query.answer("⏳ Генерируем график...")
        
        chart_buf = await generate_users_chart(30)
        
        if chart_buf:
            await query.message.reply_photo(
                photo=chart_buf,
                caption="👥 <b>График регистрации пользователей за 30 дней</b>\n\n"
                       "Отображена динамика регистрации новых пользователей",
                parse_mode='HTML'
            )
        else:
            await query.message.reply_text(
                "❌ Не удалось сгенерировать график. Недостаточно данных."
            )
    except Exception as e:
        logger.error(f"Error in generate_users_chart_handler: {e}")
        await query.message.reply_text("❌ Ошибка при генерации графика")

async def generate_top_products_chart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерация графика топ товаров"""
    query = update.callback_query
    
    try:
        await query.answer("⏳ Генерируем график...")
        
        chart_buf = await generate_top_products_chart()
        
        if chart_buf:
            await query.message.reply_photo(
                photo=chart_buf,
                caption="🏆 <b>Топ 10 товаров</b>\n\n"
                       "• Левый график: По количеству продаж\n"
                       "• Правый график: По выручке",
                parse_mode='HTML'
            )
        else:
            await query.message.reply_text(
                "❌ Не удалось сгенерировать график. Недостаточно данных."
            )
    except Exception as e:
        logger.error(f"Error in generate_top_products_chart_handler: {e}")
        await query.message.reply_text("❌ Ошибка при генерации графика")

async def generate_weekdays_chart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Генерация графика дохода по дням недели"""
    query = update.callback_query
    
    try:
        await query.answer("⏳ Генерируем график...")
        
        chart_buf = await generate_weekdays_chart()
        
        if chart_buf:
            await query.message.reply_photo(
                photo=chart_buf,
                caption="📊 <b>Доход по дням недели</b>\n\n"
                       "• Синие столбцы: Количество заказов\n"
                       "• Зеленые столбцы: Выручка",
                parse_mode='HTML'
            )
        else:
            await query.message.reply_text(
                "❌ Не удалось сгенерировать график. Недостаточно данных."
            )
    except Exception as e:
        logger.error(f"Error in generate_weekdays_chart_handler: {e}")
        await query.message.reply_text("❌ Ошибка при генерации графика")

# ============ ОБРАБОТКА КОЛЛБЭКОВ ============
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback-запросов"""
    query = update.callback_query
    
    if not query or not query.data:
        return
    
    await query.answer()
    
    user = update.effective_user
    data = query.data
    
    logger.info(f"Callback data: {data} from user {user.id}")
    
    try:
        # Основные callback-обработчики
        if data == "main_menu":
            await query.edit_message_text(
                "🏠 <b>Главное меню</b>\n\n"
                "Выберите действие:",
                parse_mode='HTML',
                reply_markup=get_main_menu(user.id)
            )
        
        elif data == "admin_panel":
            await admin_panel_handler(update, context)
        
        elif data == "admin_stats":
            if await check_admin_access(user.id, user.username):
                await show_admin_stats(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "admin_users":
            if await check_admin_access(user.id, user.username):
                await show_admin_users(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "admin_products":
            if await check_admin_access(user.id, user.username):
                await show_admin_products(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "admin_categories":
            if await check_admin_access(user.id, user.username):
                await show_admin_categories(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "admin_promocodes":
            if await check_admin_access(user.id, user.username):
                await show_promocodes_list(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "admin_charts":
            if await check_admin_access(user.id, user.username):
                await show_admin_charts(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "admin_backup":
            if await check_admin_access(user.id, user.username):
                await show_admin_backup(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "admin_settings":
            if await check_admin_access(user.id, user.username):
                await show_admin_settings(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "admin_logs":
            if await check_admin_access(user.id, user.username):
                await show_admin_logs(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "admin_promo_stats":
            if await check_admin_access(user.id, user.username):
                await show_admin_promo_stats(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "admin_search_user":
            if await check_admin_access(user.id, user.username):
                await show_admin_search_user(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "admin_add_product":
            if await check_admin_access(user.id, user.username):
                await show_admin_add_product(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "admin_add_category":
            if await check_admin_access(user.id, user.username):
                await show_admin_add_category(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        # Графики
        elif data == "chart_sales_30":
            if await check_admin_access(user.id, user.username):
                await generate_sales_chart_30(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "chart_sales_7":
            if await check_admin_access(user.id, user.username):
                await generate_sales_chart_7(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "chart_users_30":
            if await check_admin_access(user.id, user.username):
                await generate_users_chart_handler(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "chart_top_products":
            if await check_admin_access(user.id, user.username):
                await generate_top_products_chart_handler(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "chart_weekdays":
            if await check_admin_access(user.id, user.username):
                await generate_weekdays_chart_handler(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        # Промокоды
        elif data == "create_promo_menu":
            if await check_admin_access(user.id, user.username):
                await create_promo_menu(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "create_smart_promo":
            if await check_admin_access(user.id, user.username):
                await create_smart_promo_handler(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "create_auto_promo":
            if await check_admin_access(user.id, user.username):
                await create_auto_promo(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "create_custom_name_promo":
            if await check_admin_access(user.id, user.username):
                await create_custom_name_promo(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data == "create_full_promo":
            if await check_admin_access(user.id, user.username):
                await create_full_promo(update, context)
            else:
                await query.answer("❌ Доступ запрещен!", show_alert=True)
        
        elif data.startswith("copy_promo_"):
            promo_code = data.split("_")[2]
            await query.answer(f"Код {promo_code} скопирован!", show_alert=True)
        
        elif data.startswith("share_promo_"):
            promo_code = data.split("_")[2]
            await share_promo_to_chat(update, context, promo_code)
        
        # Бэкапы
        elif data in ["create_backup", "restore_backup", "list_backups",
                     "download_logs", "clear_logs", "edit_settings"]:
            await query.answer("⏳ Эта функция в разработке!", show_alert=True)
        
        # Разные типы промокодов
        elif data in ["create_amount_promo", "create_discount_promo",
                     "create_group_promo"]:
            await query.answer("⏳ Эта функция в разработке!", show_alert=True)
        
        # Поиск товаров
        elif data == "search_products":
            await show_search_products(update, context)
        
        # Обработка профиля
        elif data == "profile":
            await show_profile(update, context)
        
        elif data == "shop":
            categories = db.fetchall("SELECT id, name FROM categories WHERE is_active = 1 ORDER BY position")
            
            keyboard = []
            for category in categories:
                keyboard.append([InlineKeyboardButton(f"📁 {category['name']}", callback_data=f"category_{category['id']}")])
            
            keyboard.append([InlineKeyboardButton("🔍 Поиск товаров", callback_data="search_products")])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
            
            await query.edit_message_text(
                "🛍️ <b>Магазин</b>\n\n"
                "Выберите категорию:",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data == "balance":
            user_info = db.fetchone("SELECT balance FROM users WHERE user_id = ?", (user.id,))
            balance = user_info['balance'] if user_info else 0
            
            keyboard = [
                [InlineKeyboardButton("💳 Пополнить", callback_data="deposit")],
                [InlineKeyboardButton("📊 История", callback_data="balance_history")],
                [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
            ]
            
            await query.edit_message_text(
                f"💰 <b>Ваш баланс:</b> {format_price(balance)}\n\n"
                "Выберите действие:",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data == "promo":
            context.user_data['awaiting_promo'] = True
            await query.edit_message_text(
                "🎫 <b>Активация промокода</b>\n\n"
                "Введите промокод в чат:\n\n"
                "Пример: <code>SUMMER50</code>",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
                ])
            )
        
        elif data == "referrals":
            user_info = db.fetchone("SELECT referral_code, total_referrals, referral_earnings FROM users WHERE user_id = ?", (user.id,))
            
            if user_info:
                bot_username = context.bot.username
                referral_link = f"https://t.me/{bot_username}?start={user_info['referral_code']}"
                
                keyboard = [
                    [InlineKeyboardButton("📋 Скопировать ссылку", callback_data=f"copy_ref_{user_info['referral_code']}")],
                    [InlineKeyboardButton("👥 Мои рефералы", callback_data="my_referrals")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
                ]
                
                await query.edit_message_text(
                    f"👥 <b>Реферальная система</b>\n\n"
                    f"📊 <b>Статистика:</b>\n"
                    f"• Приглашено: {user_info['total_referrals']} чел.\n"
                    f"• Заработано: {format_price(user_info['referral_earnings'])}\n\n"
                    f"🔗 <b>Ваша реферальная ссылка:</b>\n"
                    f"<code>{referral_link}</code>\n\n"
                    f"🎁 <b>Бонусы:</b>\n"
                    f"• Новый пользователь: {format_price(REFERRAL_BONUS_NEW)}\n"
                    f"• Вам за приглашение: {format_price(REFERRAL_BONUS_INVITER)}\n\n"
                    f"💡 <b>Как работает:</b>\n"
                    f"1. Отправьте ссылку другу\n"
                    f"2. Он переходит и регистрируется\n"
                    f"3. Вы оба получаете бонусы!",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        elif data.startswith("copy_ref_"):
            ref_code = data.split("_")[2]
            bot_username = context.bot.username
            referral_link = f"https://t.me/{bot_username}?start={ref_code}"
            
            # Копируем в буфер обмена
            await context.bot.send_message(
                user.id,
                f"🔗 Ваша реферальная ссылка:\n\n"
                f"<code>{referral_link}</code>\n\n"
                f"📋 Ссылка скопирована! Отправьте ее другу.",
                parse_mode='HTML'
            )
            await query.answer("✅ Ссылка скопирована!", show_alert=True)
        
        elif data == "my_orders":
            orders = db.fetchall("""
                SELECT product_name, amount, quantity, created_at 
                FROM orders 
                WHERE user_id = ? 
                ORDER BY created_at DESC 
                LIMIT 10
            """, (user.id,))
            
            if not orders:
                await query.edit_message_text(
                    "📦 <b>История покупок</b>\n\n"
                    "У вас еще нет покупок.",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🛍️ В магазин", callback_data="shop")],
                        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
                    ])
                )
                return
            
            total_spent = sum(order['amount'] for order in orders)
            
            orders_text = "📦 <b>Последние 10 покупок</b>\n\n"
            for order in orders:
                order_date = format_datetime(order['created_at'])
                orders_text += f"🛒 <b>{order['product_name']}</b>\n"
                orders_text += f"💰 {format_price(order['amount'])}"
                if order['quantity'] > 1:
                    orders_text += f" (×{order['quantity']})"
                orders_text += f"\n📅 {order_date}\n\n"
            
            orders_text += f"💵 <b>Всего потрачено:</b> {format_price(total_spent)}"
            
            keyboard = [
                [InlineKeyboardButton("🔄 Обновить", callback_data="my_orders")],
                [InlineKeyboardButton("🛍️ В магазин", callback_data="shop")],
                [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
            ]
            
            await query.edit_message_text(
                orders_text,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data == "my_referrals":
            referrals = db.fetchall("""
                SELECT user_id, username, first_name, join_date 
                FROM users 
                WHERE referred_by = ? 
                ORDER BY join_date DESC
            """, (user.id,))
            
            if not referrals:
                await query.edit_message_text(
                    "👥 <b>Мои рефералы</b>\n\n"
                    "У вас еще нет рефералов.\n\n"
                    "Приглашайте друзей и получайте бонусы!",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("👥 Рефералы", callback_data="referrals")],
                        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
                    ])
                )
                return
            
            refs_text = f"👥 <b>Мои рефералы ({len(referrals)})</b>\n\n"
            
            for i, ref in enumerate(referrals, 1):
                join_date = format_datetime(ref['join_date'])
                refs_text += f"{i}. {ref['first_name']} (@{ref['username'] or 'нет'})\n"
                refs_text += f"   🆔 {ref['user_id']} | 📅 {join_date}\n\n"
            
            keyboard = [
                [InlineKeyboardButton("👥 Рефералы", callback_data="referrals")],
                [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
            ]
            
            await query.edit_message_text(
                refs_text,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data == "support":
            keyboard = [
                [InlineKeyboardButton("✉️ Написать", url=f"https://t.me/{ADMIN_USERNAME.replace('@', '')}")],
                [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
            ]
            
            await query.edit_message_text(
                f"📞 <b>Поддержка</b>\n\n"
                f"По всем вопросам обращайтесь к администратору:\n"
                f"{ADMIN_USERNAME}\n\n"
                f"⏰ Время ответа: 24/7",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data == "help":
            keyboard = [
                [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
            ]
            
            await query.edit_message_text(
                "📚 <b>Помощь по боту</b>\n\n"
                "🛍️ <b>Магазин</b> - просмотр и покупка товаров\n"
                "💰 <b>Баланс</b> - пополнение и проверка баланса\n"
                "👤 <b>Профиль</b> - ваша статистика и информация\n"
                "📦 <b>Мои покупки</b> - история ваших покупок\n"
                "🎫 <b>Промокод</b> - активация промокода\n"
                "👥 <b>Рефералы</b> - пригласите друзей и получите бонусы\n"
                "📞 <b>Поддержка</b> - связь с администратором\n\n"
                f"👑 Администратор: {ADMIN_USERNAME}",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data == "deposit":
            keyboard = [
                [InlineKeyboardButton("💳 100₪", callback_data="deposit_100"),
                 InlineKeyboardButton("💵 500₪", callback_data="deposit_500")],
                [InlineKeyboardButton("💰 1000₪", callback_data="deposit_1000"),
                 InlineKeyboardButton("💎 5000₪", callback_data="deposit_5000")],
                [InlineKeyboardButton("🎯 Другая сумма", callback_data="deposit_custom")],
                [InlineKeyboardButton("🔙 Назад", callback_data="balance")]
            ]
            
            await query.edit_message_text(
                "💰 <b>Пополнение баланса</b>\n\n"
                "Выберите сумму для пополнения:\n\n"
                "💡 После выбора суммы вы получите реквизиты для оплаты.",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data.startswith("deposit_"):
            amount_str = data.split("_")[1]
            
            if amount_str == "custom":
                context.user_data['awaiting_deposit_amount'] = True
                await query.edit_message_text(
                    "💵 Введите сумму для пополнения:\n\n"
                    "Примеры:\n"
                    "• 150\n"
                    "• 750\n"
                    "• 1200\n\n"
                    "Минимальная сумма: 100₪",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад", callback_data="deposit")]
                    ])
                )
                return
            
            try:
                amount = int(amount_str)
                if amount < 100:
                    await query.answer("❌ Минимальная сумма - 100₪!", show_alert=True)
                    return
                
                # Здесь должна быть интеграция с платежной системой
                # Покажем временные реквизиты
                payment_info = (
                    f"💰 <b>Пополнение на {format_price(amount)}</b>\n\n"
                    f"🆔 Ваш ID: <code>{user.id}</code>\n"
                    f"💵 Сумма: {format_price(amount)}\n\n"
                    f"📋 <b>Реквизиты для оплаты:</b>\n"
                    f"• Карта: 1234 5678 9012 3456\n"
                    f"• Получатель: Иван Иванов\n"
                    f"• Комментарий: <code>{user.id}</code>\n\n"
                    f"💡 <b>Инструкция:</b>\n"
                    f"1. Переведите {format_price(amount)} на указанные реквизиты\n"
                    f"2. В комментарии укажите ваш ID: {user.id}\n"
                    f"3. Ожидайте зачисления (до 15 минут)\n\n"
                    f"📞 При проблемах: {ADMIN_USERNAME}"
                )
                
                keyboard = [
                    [InlineKeyboardButton("✅ Я оплатил", callback_data=f"confirm_payment_{amount}")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="deposit")]
                ]
                
                await query.edit_message_text(
                    payment_info,
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
            except ValueError:
                await query.answer("❌ Неверная сумма!", show_alert=True)
        
        elif data.startswith("confirm_payment_"):
            amount_str = data.split("_")[2]
            amount = int(amount_str)
            
            keyboard = [
                [InlineKeyboardButton("🔄 Проверить статус", callback_data=f"check_payment_{amount}")],
                [InlineKeyboardButton("📞 Поддержка", callback_data="support")],
                [InlineKeyboardButton("🔙 Назад", callback_data="deposit")]
            ]
            
            await query.edit_message_text(
                f"✅ <b>Запрос на пополнение принят!</b>\n\n"
                f"💰 Сумма: {format_price(amount)}\n"
                f"🆔 Ваш ID: {user.id}\n\n"
                f"⏳ Платеж проверяется администратором.\n"
                f"Обычно это занимает до 15 минут.\n\n"
                f"📞 При проблемах: {ADMIN_USERNAME}",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data == "balance_history":
            # Покажем историю операций
            user_info = db.fetchone("SELECT total_deposited, total_spent FROM users WHERE user_id = ?", (user.id,))
            
            history_text = (
                f"📊 <b>История операций</b>\n\n"
                f"💵 <b>Всего пополнено:</b> {format_price(user_info['total_deposited'])}\n"
                f"🛒 <b>Всего потрачено:</b> {format_price(user_info['total_spent'])}\n"
                f"💰 <b>Текущий баланс:</b> {format_price(user_info['total_deposited'] - user_info['total_spent'])}\n\n"
                f"📈 <b>Детальная история:</b>\n"
                f"Здесь будет детальная история операций..."
            )
            
            keyboard = [
                [InlineKeyboardButton("🔄 Обновить", callback_data="balance_history")],
                [InlineKeyboardButton("🔙 Назад", callback_data="balance")]
            ]
            
            await query.edit_message_text(
                history_text,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data.startswith("category_"):
            category_id = int(data.split("_")[1])
            
            # Получаем товары из категории
            products = db.fetchall("""
                SELECT id, name, price, stock 
                FROM products 
                WHERE category_id = ? AND is_active = 1 
                ORDER BY position
                LIMIT 20
            """, (category_id,))
            
            if not products:
                await query.edit_message_text(
                    "📦 <b>Товары не найдены</b>\n\n"
                    "В этой категории пока нет товаров.",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🛍️ В магазин", callback_data="shop")],
                        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
                    ])
                )
                return
            
            category = db.fetchone("SELECT name FROM categories WHERE id = ?", (category_id,))
            category_name = category['name'] if category else "Категория"
            
            products_text = f"🛍️ <b>{category_name}</b>\n\n"
            
            keyboard = []
            for product in products:
                stock_text = f"({product['stock']} шт.)" if product['stock'] > 0 else "✔️ В наличии"
                products_text += f"📦 {product['name']}\n"
                products_text += f"💰 {format_price(product['price'])} {stock_text}\n\n"
                
                keyboard.append([
                    InlineKeyboardButton(
                        f"🛒 {product['name']} - {format_price(product['price'])}", 
                        callback_data=f"view_product_{product['id']}"
                    )
                ])
            
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="shop")])
            
            await query.edit_message_text(
                products_text,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data.startswith("view_product_"):
            product_id = int(data.split("_")[2])
            
            product = db.fetchone("""
                SELECT p.*, c.name as category_name 
                FROM products p 
                LEFT JOIN categories c ON p.category_id = c.id 
                WHERE p.id = ? AND p.is_active = 1
            """, (product_id,))
            
            if not product:
                await query.answer("❌ Товар не найден!", show_alert=True)
                return
            
            stock_text = f"📦 <b>Остаток:</b> {product['stock']} шт." if product['stock'] > 0 else "✅ <b>В наличии</b>"
            if product['stock'] == 0:
                stock_text = "❌ <b>Нет в наличии</b>"
            
            description = product['description'] or "Описание отсутствует"
            
            product_text = (
                f"📦 <b>{product['name']}</b>\n\n"
                f"📁 <b>Категория:</b> {product['category_name']}\n"
                f"💰 <b>Цена:</b> {format_price(product['price'])}\n"
                f"{stock_text}\n\n"
                f"📝 <b>Описание:</b>\n{description}\n\n"
                f"🆔 <b>ID товара:</b> <code>{product['id']}</code>"
            )
            
            keyboard = []
            if product['stock'] != 0:
                keyboard.append([InlineKeyboardButton("🛒 Купить", callback_data=f"buy_product_{product['id']}")])
            
            keyboard.append([InlineKeyboardButton("🔙 Назад в магазин", callback_data="shop")])
            
            await query.edit_message_text(
                product_text,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
        elif data.startswith("buy_product_"):
            product_id = int(data.split("_")[2])
            
            product = db.fetchone("SELECT id, name, price, stock FROM products WHERE id = ? AND is_active = 1", (product_id,))
            
            if not product:
                await query.answer("❌ Товар не найден!", show_alert=True)
                return
            
            user_info = db.fetchone("SELECT balance FROM users WHERE user_id = ?", (user.id,))
            balance = user_info['balance'] if user_info else 0
            
            if balance < product['price']:
                await query.answer(f"❌ Недостаточно средств! Нужно {format_price(product['price'])}", show_alert=True)
                return
            
            if product['stock'] == 0:
                await query.answer("❌ Товар закончился!", show_alert=True)
                return
            
            # Покупка товара
            db.execute("UPDATE users SET balance = balance - ?, total_spent = total_spent + ? WHERE user_id = ?", 
                     (product['price'], product['price'], user.id))
            
            if product['stock'] > 0:
                db.execute("UPDATE products SET stock = stock - 1 WHERE id = ?", (product['id'],))
            
            db.execute("""
                INSERT INTO orders (user_id, product_id, product_name, amount, quantity)
                VALUES (?, ?, ?, ?, 1)
            """, (user.id, product['id'], product['name'], product['price']))
            
            # Обновляем время последней покупки
            db.execute("UPDATE users SET last_purchase = CURRENT_TIMESTAMP WHERE user_id = ?", (user.id,))
            
            await query.answer(f"✅ Товар '{product['name']}' куплен!", show_alert=True)
            
            # Возвращаем в магазин
            await query.edit_message_text(
                f"✅ <b>Покупка успешна!</b>\n\n"
                f"📦 <b>Товар:</b> {product['name']}\n"
                f"💰 <b>Цена:</b> {format_price(product['price'])}\n"
                f"💵 <b>Новый баланс:</b> {format_price(balance - product['price'])}\n\n"
                f"Детали покупки будут отправлены вам в личные сообщения.",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛍️ Продолжить покупки", callback_data="shop")],
                    [InlineKeyboardButton("📦 Мои покупки", callback_data="my_orders")],
                    [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
                ])
            )
            
            # Отправляем детали покупки
            try:
                await context.bot.send_message(
                    user.id,
                    f"📦 <b>Чек покупки</b>\n\n"
                    f"🛒 <b>Товар:</b> {product['name']}\n"
                    f"💰 <b>Стоимость:</b> {format_price(product['price'])}\n"
                    f"📅 <b>Дата:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                    f"🆔 <b>ID покупки:</b> {product['id']}\n\n"
                    f"💵 <b>Новый баланс:</b> {format_price(balance - product['price'])}",
                    parse_mode='HTML'
                )
            except:
                pass
        
        else:
            logger.warning(f"Unknown callback data: {data}")
            await query.answer("⚠️ Неизвестная команда!")
            
    except Exception as e:
        logger.error(f"Error in handle_callback: {e}")
        try:
            await query.edit_message_text("❌ Произошла ошибка при обработке запроса")
        except:
            pass

async def share_promo_to_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, promo_code: str):
    """Отправка промокода в чат"""
    query = update.callback_query
    
    try:
        # Получаем информацию о промокоде
        promo = db.fetchone("SELECT amount, expires_at FROM promocodes WHERE code = ?", (promo_code,))
        
        if not promo:
            await query.answer("❌ Промокод не найден!", show_alert=True)
            return
        
        amount = promo['amount']
        expires_at = promo['expires_at']
        
        # Формируем сообщение
        expires_text = ""
        if expires_at:
            expires_date = format_datetime(expires_at)
            expires_text = f"\n⏰ Срок действия: до {expires_date}"
        
        share_text = (
            f"🎉 <b>НОВЫЙ ПРОМОКОД!</b>\n\n"
            f"🎫 Код: <code>{promo_code}</code>\n"
            f"💰 Сумма: {format_price(amount)}\n"
            f"{expires_text}\n\n"
            f"💡 <b>Как активировать:</b>\n"
            f"1. Нажмите на кнопку '🎫 Промокод'\n"
            f"2. Введите код: {promo_code}\n"
            f"3. Получите {format_price(amount)} на баланс!\n\n"
            f"🎁 Успейте воспользоваться!"
        )
        
        await context.bot.send_message(
            query.message.chat_id,
            share_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎫 Активировать промокод", callback_data="promo")],
                [InlineKeyboardButton("🛍️ Перейти в магазин", callback_data="shop")]
            ])
        )
        
        await query.answer("✅ Промокод отправлен в чат!", show_alert=True)
        
    except Exception as e:
        logger.error(f"Error in share_promo_to_chat: {e}")
        await query.answer("❌ Ошибка при отправке!", show_alert=True)

# ============ ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ ============
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    try:
        user = update.effective_user
        text = update.message.text.strip()
        
        # Проверяем, ожидаем ли мы промокод от пользователя
        if context.user_data.get('awaiting_promo'):
            context.user_data['awaiting_promo'] = False
            
            # Проверяем промокод
            promo = db.fetchone("""
                SELECT code, amount, max_uses, used_count, is_active, expires_at
                FROM promocodes 
                WHERE code = ? AND is_active = 1
            """, (text.upper(),))
            
            if not promo:
                await update.message.reply_text(
                    "❌ Промокод не найден или неактивен!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
                    ])
                )
                return
            
            # Проверяем срок действия
            if promo['expires_at']:
                expires_at = datetime.fromisoformat(promo['expires_at'].replace('Z', '+00:00'))
                if expires_at < datetime.now():
                    await update.message.reply_text(
                        "❌ Промокод истек!",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
                        ])
                    )
                    return
            
            # Проверяем количество использований
            if promo['max_uses'] > 0 and promo['used_count'] >= promo['max_uses']:
                await update.message.reply_text(
                    "❌ Промокод уже использован максимальное количество раз!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
                    ])
                )
                return
            
            # Активируем промокод
            db.execute("UPDATE promocodes SET used_count = used_count + 1 WHERE code = ?", (text.upper(),))
            db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", 
                     (promo['amount'], user.id))
            
            await update.message.reply_text(
                f"✅ Промокод активирован!\n"
                f"🎫 Код: <code>{text.upper()}</code>\n"
                f"💰 Начислено: {format_price(promo['amount'])}\n\n"
                f"💸 Ваш баланс пополнен!",
                parse_mode='HTML',
                reply_markup=get_main_menu(user.id)
            )
            return
        
        # Обработка создания промокода с собственным названием
        elif context.user_data.get('awaiting_custom_promo_name') and await check_admin_access(user.id, user.username):
            step = context.user_data.get('promo_step', 1)
            
            if step == 1:  # Название промокода
                promo_code = text.upper()
                
                # Проверяем формат
                if not all(c.isalnum() for c in promo_code):
                    await update.message.reply_text("❌ Используйте только латинские буквы и цифры!")
                    return
                
                if len(promo_code) < 4 or len(promo_code) > 20:
                    await update.message.reply_text("❌ Длина кода должна быть от 4 до 20 символов!")
                    return
                
                # Проверяем, не занят ли код
                existing = db.fetchone("SELECT id FROM promocodes WHERE code = ?", (promo_code,))
                if existing:
                    await update.message.reply_text(f"❌ Промокод {promo_code} уже существует!")
                    return
                
                context.user_data['promo_code'] = promo_code
                context.user_data['promo_step'] = 2
                
                await update.message.reply_text(
                    "✅ Название промокода принято!\n\n"
                    "Шаг 2/4: Введите сумму промокода:\n\n"
                    "💡 Примеры:\n"
                    "• 100 - промокод на 100₪\n"
                    "• 500 - промокод на 500₪\n"
                    "• 0 - промокод только на скидку\n\n"
                    "Введите число:",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Отмена", callback_data="create_promo_menu")]
                    ])
                )
            
            elif step == 2:  # Сумма
                try:
                    amount = int(text)
                    if amount < 0:
                        await update.message.reply_text("❌ Сумма не может быть отрицательной!")
                        return
                    
                    context.user_data['promo_amount'] = amount
                    context.user_data['promo_step'] = 3
                    
                    await update.message.reply_text(
                        "✅ Сумма принята!\n\n"
                        "Шаг 3/4: Введите количество использований:\n\n"
                        "💡 Примеры:\n"
                        "• 1 - одноразовый промокод\n"
                        "• 10 - 10 использований\n"
                        "• 0 - без ограничений\n\n"
                        "Введите число:",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 Отмена", callback_data="create_promo_menu")]
                        ])
                    )
                except ValueError:
                    await update.message.reply_text("❌ Введите число!")
            
            elif step == 3:  # Количество использований
                try:
                    uses = int(text)
                    if uses < 0:
                        await update.message.reply_text("❌ Количество не может быть отрицательным!")
                        return
                    
                    context.user_data['promo_uses'] = uses
                    context.user_data['promo_step'] = 4
                    
                    await update.message.reply_text(
                        "✅ Количество использований принято!\n\n"
                        "Шаг 4/4: Введите срок действия в днях:\n\n"
                        "💡 Примеры:\n"
                        "• 7 - на 7 дней\n"
                        "• 30 - на 30 дней\n"
                        "• 0 - без срока\n\n"
                        "Введите число:",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 Отмена", callback_data="create_promo_menu")]
                        ])
                    )
                except ValueError:
                    await update.message.reply_text("❌ Введите число!")
            
            elif step == 4:  # Срок действия
                try:
                    days = int(text)
                    if days < 0:
                        await update.message.reply_text("❌ Срок не может быть отрицательным!")
                        return
                    
                    # Получаем данные
                    promo_code = context.user_data['promo_code']
                    amount = context.user_data['promo_amount']
                    uses = context.user_data['promo_uses']
                    
                    # Очищаем временные данные
                    for key in ['awaiting_custom_promo_name', 'promo_step', 'promo_code', 'promo_amount', 'promo_uses']:
                        if key in context.user_data:
                            del context.user_data[key]
                    
                    # Создаем промокод
                    expires_at = None
                    if days > 0:
                        expires_at = (datetime.now() + timedelta(days=days)).isoformat()
                    
                    db.execute("""
                        INSERT INTO promocodes (code, amount, max_uses, created_by, expires_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (promo_code, amount, uses, user.id, expires_at))
                    
                    admin_logger.log_action(user.id, "create_custom_promo", promo_code, 
                                          f"amount:{amount}, uses:{uses}, expires:{days}days")
                    
                    uses_text = "бесконечно" if uses == 0 else f"{uses} использований"
                    expires_text = f"\n📅 Срок действия: {days} дней" if days else ""
                    
                    message = (
                        f"✅ <b>Промокод успешно создан!</b>\n\n"
                        f"🎫 <b>Код:</b> <code>{promo_code}</code>\n"
                        f"💰 <b>Сумма:</b> {format_price(amount)}\n"
                        f"📊 <b>Использований:</b> {uses_text}"
                        f"{expires_text}"
                    )
                    
                    keyboard = [
                        [
                            InlineKeyboardButton("📋 Скопировать код", callback_data=f"copy_promo_{promo_code}"),
                            InlineKeyboardButton("📢 Отправить в чат", callback_data=f"share_promo_{promo_code}")
                        ],
                        [
                            InlineKeyboardButton("🎫 Создать еще", callback_data="create_custom_name_promo"),
                            InlineKeyboardButton("📊 Посмотреть все", callback_data="admin_promocodes")
                        ],
                        [InlineKeyboardButton("🔙 В меню", callback_data="create_promo_menu")]
                    ]
                    
                    await update.message.reply_text(
                        message,
                        parse_mode='HTML',
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    
                except ValueError:
                    await update.message.reply_text("❌ Введите число!")
                except Exception as e:
                    logger.error(f"Ошибка при создании промокода: {e}")
                    await update.message.reply_text("❌ Не удалось создать промокод. Попробуйте позже.")
        
        # Обработка полного создания промокода
        elif context.user_data.get('awaiting_full_promo') and await check_admin_access(user.id, user.username):
            step = context.user_data.get('promo_step', 1)
            
            if step == 1:  # Название промокода
                promo_code = text.upper()
                
                # Проверяем формат
                if not all(c.isalnum() for c in promo_code):
                    await update.message.reply_text("❌ Используйте только латинские буквы и цифры!")
                    return
                
                if len(promo_code) < 4 or len(promo_code) > 20:
                    await update.message.reply_text("❌ Длина кода должна быть от 4 до 20 символов!")
                    return
                
                # Проверяем, не занят ли код
                existing = db.fetchone("SELECT id FROM promocodes WHERE code = ?", (promo_code,))
                if existing:
                    await update.message.reply_text(f"❌ Промокод {promo_code} уже существует!")
                    return
                
                context.user_data['promo_code'] = promo_code
                context.user_data['promo_step'] = 2
                
                await update.message.reply_text(
                    "✅ Название промокода принято!\n\n"
                    "Шаг 2/5: Введите сумму промокода:\n\n"
                    "💡 Примеры:\n"
                    "• 100 - промокод на 100₪\n"
                    "• 500 - промокод на 500₪\n"
                    "• 0 - промокод только на скидку\n\n"
                    "Введите число:",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Отмена", callback_data="create_promo_menu")]
                    ])
                )
            
            elif step == 2:  # Сумма
                try:
                    amount = int(text)
                    if amount < 0:
                        await update.message.reply_text("❌ Сумма не может быть отрицательной!")
                        return
                    
                    context.user_data['promo_amount'] = amount
                    context.user_data['promo_step'] = 3
                    
                    await update.message.reply_text(
                        "✅ Сумма принята!\n\n"
                        "Шаг 3/5: Введите процент скидки (0 если только сумма):\n\n"
                        "💡 Примеры:\n"
                        "• 0 - без скидки\n"
                        "• 10 - 10% скидка\n"
                        "• 50 - 50% скидка\n\n"
                        "Введите число от 0 до 100:",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 Отмена", callback_data="create_promo_menu")]
                        ])
                    )
                except ValueError:
                    await update.message.reply_text("❌ Введите число!")
            
            elif step == 3:  # Процент скидки
                try:
                    discount = int(text)
                    if discount < 0 or discount > 100:
                        await update.message.reply_text("❌ Процент скидки должен быть от 0 до 100!")
                        return
                    
                    context.user_data['promo_discount'] = discount
                    context.user_data['promo_step'] = 4
                    
                    await update.message.reply_text(
                        "✅ Процент скидки принят!\n\n"
                        "Шаг 4/5: Введите количество использований:\n\n"
                        "💡 Примеры:\n"
                        "• 1 - одноразовый промокод\n"
                        "• 10 - 10 использований\n"
                        "• 0 - без ограничений\n\n"
                        "Введите число:",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 Отмена", callback_data="create_promo_menu")]
                        ])
                    )
                except ValueError:
                    await update.message.reply_text("❌ Введите число!")
            
            elif step == 4:  # Количество использований
                try:
                    uses = int(text)
                    if uses < 0:
                        await update.message.reply_text("❌ Количество не может быть отрицательным!")
                        return
                    
                    context.user_data['promo_uses'] = uses
                    context.user_data['promo_step'] = 5
                    
                    await update.message.reply_text(
                        "✅ Количество использований принято!\n\n"
                        "Шаг 5/5: Введите срок действия в днях:\n\n"
                        "💡 Примеры:\n"
                        "• 7 - на 7 дней\n"
                        "• 30 - на 30 дней\n"
                        "• 0 - без срока\n\n"
                        "Введите число:",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔙 Отмена", callback_data="create_promo_menu")]
                        ])
                    )
                except ValueError:
                    await update.message.reply_text("❌ Введите число!")
            
            elif step == 5:  # Срок действия
                try:
                    days = int(text)
                    if days < 0:
                        await update.message.reply_text("❌ Срок не может быть отрицательным!")
                        return
                    
                    # Получаем данные
                    promo_code = context.user_data['promo_code']
                    amount = context.user_data['promo_amount']
                    discount = context.user_data['promo_discount']
                    uses = context.user_data['promo_uses']
                    
                    # Очищаем временные данные
                    for key in ['awaiting_full_promo', 'promo_step', 'promo_code', 'promo_amount', 'promo_discount', 'promo_uses']:
                        if key in context.user_data:
                            del context.user_data[key]
                    
                    # Создаем промокод
                    expires_at = None
                    if days > 0:
                        expires_at = (datetime.now() + timedelta(days=days)).isoformat()
                    
                    db.execute("""
                        INSERT INTO promocodes (code, amount, discount_percent, max_uses, created_by, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (promo_code, amount, discount, uses, user.id, expires_at))
                    
                    admin_logger.log_action(user.id, "create_full_promo", promo_code, 
                                          f"amount:{amount}, discount:{discount}%, uses:{uses}, expires:{days}days")
                    
                    uses_text = "бесконечно" if uses == 0 else f"{uses} использований"
                    expires_text = f"\n📅 Срок действия: {days} дней" if days else ""
                    
                    bonus_text = ""
                    if amount > 0:
                        bonus_text = f"{format_price(amount)}"
                    if discount > 0:
                        if bonus_text:
                            bonus_text += f" + {discount}% скидка"
                        else:
                            bonus_text = f"{discount}% скидка"
                    
                    message = (
                        f"✅ <b>Промокод успешно создан!</b>\n\n"
                        f"🎫 <b>Код:</b> <code>{promo_code}</code>\n"
                        f"🎁 <b>Бонус:</b> {bonus_text}\n"
                        f"📊 <b>Использований:</b> {uses_text}"
                        f"{expires_text}"
                    )
                    
                    keyboard = [
                        [
                            InlineKeyboardButton("📋 Скопировать код", callback_data=f"copy_promo_{promo_code}"),
                            InlineKeyboardButton("📢 Отправить в чат", callback_data=f"share_promo_{promo_code}")
                        ],
                        [
                            InlineKeyboardButton("🎫 Создать еще", callback_data="create_full_promo"),
                            InlineKeyboardButton("📊 Посмотреть все", callback_data="admin_promocodes")
                        ],
                        [InlineKeyboardButton("🔙 В меню", callback_data="create_promo_menu")]
                    ]
                    
                    await update.message.reply_text(
                        message,
                        parse_mode='HTML',
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    
                except ValueError:
                    await update.message.reply_text("❌ Введите число!")
                except Exception as e:
                    logger.error(f"Ошибка при создании промокода: {e}")
                    await update.message.reply_text("❌ Не удалось создать промокод. Попробуйте позже.")
        
        # Обработка кастомной суммы пополнения
        elif context.user_data.get('awaiting_deposit_amount'):
            context.user_data['awaiting_deposit_amount'] = False
            
            try:
                amount = int(text)
                if amount < 100:
                    await update.message.reply_text("❌ Минимальная сумма - 100₪!")
                    return
                
                # Показываем реквизиты для оплаты
                payment_info = (
                    f"💰 <b>Пополнение на {format_price(amount)}</b>\n\n"
                    f"🆔 Ваш ID: <code>{user.id}</code>\n"
                    f"💵 Сумма: {format_price(amount)}\n\n"
                    f"📋 <b>Реквизиты для оплаты:</b>\n"
                    f"• Карта: 1234 5678 9012 3456\n"
                    f"• Получатель: Иван Иванов\n"
                    f"• Комментарий: <code>{user.id}</code>\n\n"
                    f"💡 <b>Инструкция:</b>\n"
                    f"1. Переведите {format_price(amount)} на указанные реквизиты\n"
                    f"2. В комментарии укажите ваш ID: {user.id}\n"
                    f"3. Ожидайте зачисления (до 15 минут)\n\n"
                    f"📞 При проблемах: {ADMIN_USERNAME}"
                )
                
                keyboard = [
                    [InlineKeyboardButton("✅ Я оплатил", callback_data=f"confirm_payment_{amount}")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="deposit")]
                ]
                
                await update.message.reply_text(
                    payment_info,
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
            except ValueError:
                await update.message.reply_text("❌ Введите число!")
        
        else:
            # Если сообщение не обработано, показываем главное меню
            await update.message.reply_text(
                "🏠 <b>Главное меню</b>\n\n"
                "Выберите действие:",
                parse_mode='HTML',
                reply_markup=get_main_menu(user.id)
            )
            
    except Exception as e:
        logger.error(f"Error in handle_text_message: {e}")

# ============ КОМАНДА СОЗДАНИЯ ПРОМОКОДА ============
async def create_promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда создания промокода"""
    try:
        user = update.effective_user
        
        if not await check_admin_access(user.id, user.username):
            await update.message.reply_text("❌ У вас нет прав для этой команды!")
            return
        
        if not context.args:
            # Если аргументов нет, показываем меню выбора
            keyboard = [
                [
                    InlineKeyboardButton("🧠 Автоматический", callback_data="create_auto_promo"),
                    InlineKeyboardButton("✏️ Свой код", callback_data="create_custom_name_promo")
                ],
                [
                    InlineKeyboardButton("🎁 На сумму", callback_data="create_amount_promo"),
                    InlineKeyboardButton("📈 Со скидкой", callback_data="create_discount_promo")
                ],
                [InlineKeyboardButton("📊 Статистика", callback_data="admin_promo_stats")]
            ]
            
            await update.message.reply_text(
                "🎫 <b>Создание промокода</b>\n\n"
                "Выберите тип создания:\n\n"
                "🧠 <b>Автоматический</b> - бот сам подберет параметры\n"
                "✏️ <b>Свой код</b> - вы задаете название промокода\n"
                "🎁 <b>На сумму</b> - промокод на фиксированную сумму\n"
                "📈 <b>Со скидкой</b> - промокод на процент скидки\n\n"
                "💡 <b>Или используйте:</b>\n"
                "/promo SUMMER50 100 10 - код SUMMER50 на 100₪, 10 использований\n"
                "/promo MEGASALE 500 0 30 - код MEGASALE на 500₪, без ограничений, 30 дней",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        # Новый формат команды: /promo КОД СУММА [ИСПОЛЬЗОВАНИЯ] [ДНИ]
        if len(context.args) < 2:
            await update.message.reply_text(
                "Использование: /promo КОД СУММА [ИСПОЛЬЗОВАНИЯ] [СРОК_ДНЕЙ]\n"
                "Пример: /promo SUMMER50 100 10 - код SUMMER50 на 100₪, 10 использований\n"
                "/promo MEGASALE 500 0 - код MEGASALE на 500₪, бесконечно\n"
                "/promo WINTER100 200 5 30 - код WINTER100 на 200₪, 5 использований, срок 30 дней"
            )
            return
        
        try:
            promo_code = context.args[0].upper()
            amount = int(context.args[1])
            uses = int(context.args[2]) if len(context.args) > 2 else 1
            expires_days = int(context.args[3]) if len(context.args) > 3 else 30
            
            if amount <= 0:
                await update.message.reply_text("❌ Сумма должна быть положительной!")
                return
            
            # Проверяем, не занят ли код
            existing = db.fetchone("SELECT id FROM promocodes WHERE code = ?", (promo_code,))
            if existing:
                await update.message.reply_text(f"❌ Промокод {promo_code} уже существует!")
                return
            
            expires_at = None
            if expires_days and expires_days > 0:
                expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()
            
            db.execute("""
                INSERT INTO promocodes (code, amount, max_uses, created_by, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """, (promo_code, amount, uses, user.id, expires_at))
            
            admin_logger.log_action(user.id, "create_promo", promo_code, f"amount:{amount}, uses:{uses}")
            
            uses_text = "бесконечно" if uses == 0 else f"{uses} использований"
            expires_text = f"\n📅 Срок действия: {expires_days} дней" if expires_days else ""
            
            await update.message.reply_text(
                f"✅ Промокод создан!\n\n"
                f"🎫 Код: <code>{promo_code}</code>\n"
                f"💰 Сумма: {format_price(amount)}\n"
                f"📊 Использований: {uses_text}"
                f"{expires_text}",
                parse_mode='HTML'
            )
            
        except ValueError:
            await update.message.reply_text("❌ Неверный формат! Сумма и количество должны быть числами.")
        except Exception as e:
            logger.error(f"Ошибка при создании промокода: {e}")
            await update.message.reply_text(f"❌ Произошла ошибка: {str(e)}")
        
    except Exception as e:
        logger.error(f"Error in create_promo_command: {e}")

# ============ ОСНОВНАЯ ФУНКЦИЯ ============
def main():
    """Основная функция запуска бота"""
    
    if not BOT_TOKEN or BOT_TOKEN == "ВАШ_ТОКЕН_ЗДЕСЬ":
        logger.error("❌ Укажите BOT_TOKEN в коде!")
        print("=" * 60)
        print("⚠️  ВНИМАНИЕ: Токен бота виден в коде!")
        print("1. Отзовите текущий токен через @BotFather")
        print("2. Создайте новый токен")
        print("3. Вставьте его в переменную BOT_TOKEN")
        print("=" * 60)
        return
    
    try:
        print("✅ База данных успешно инициализирована")
    except Exception as e:
        print(f"❌ Ошибка инициализации базы данных: {e}")
        return
    
    try:
        from telegram.ext import ApplicationBuilder
        application = ApplicationBuilder().token(BOT_TOKEN).build()
        
        # Добавляем обработчики команд
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("admin", admin_commands))
        application.add_handler(CommandHandler("addbalance", add_balance_command))
        application.add_handler(CommandHandler("ban", ban_user_command))
        application.add_handler(CommandHandler("unban", unban_user_command))
        application.add_handler(CommandHandler("promo", create_promo_command))
        application.add_handler(CommandHandler("user", user_info_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("testers", testers_command))
        
        # Добавляем обработчик callback-запросов
        application.add_handler(CallbackQueryHandler(handle_callback))
        
        # Добавляем обработчик текстовых сообщений
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
        
        logger.info("🤖 Бот запускается...")
        print("=" * 60)
        print("✅ Бот успешно запущен!")
        print(f"🎫 Система промокодов готова!")
        print(f"📈 Система графиков готова!")
        print(f"👑 Администратор: {ADMIN_USERNAME}")
        print(f"📁 База данных: {DB_FILE}")
        print("=" * 60)
        print("📝 Логи сохраняются в bot.log")
        print("🔄 Для остановки нажмите Ctrl+C")
        print("=" * 60)
        
        # Запуск бота
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        print("=" * 60)
        print("❌ Ошибка при запуске бота!")
        print(f"Ошибка: {e}")
        print("=" * 60)

if __name__ == "__main__":
    if BOT_TOKEN == "8261940208:AAF31P8If9iZCmUP6mEsojgK2T61Ko7_YVA":
        print("=" * 60)
        print("⚠️  ВНИМАНИЕ: Используется тестовый токен!")
        print("⚠️  Рекомендуется создать новый токен через @BotFather")
        print("=" * 60)
    
    try:
        main()
    except KeyboardInterrupt:
        logger.info("✅ Бот остановлен пользователем")
        print("\n✅ Бот остановлен")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        print("\n❌ Бот завершил работу с ошибкой")