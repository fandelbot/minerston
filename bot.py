import logging
import sqlite3
import time
import uuid
import random
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters, Updater

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TOKEN = "7349143840:AAGVNAySKjbcrlKBUru1AtOufcXw4eHIddU"
COLD_WALLET_ADDRESS = "UQC-ZLaJ2nwdPfo9AXsiEmcgUC7H1HZTW6Ak5IB03SGIbcJr"
CHANNELS = {
    "@fazixcheck": "Чеки FAZIX / xRocket"
}

# Подключение к базе данных
conn = sqlite3.connect('users.db', check_same_thread=False)
c = conn.cursor()

# Создание таблицы пользователей, если она не существует
c.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    balance REAL DEFAULT 0.0,
    mining_balance REAL DEFAULT 0.0,
    mining_rate REAL DEFAULT 0.0000003000,
    last_collect_time INTEGER DEFAULT 0,
    payment_comment TEXT
)
''')
conn.commit()

def ensure_columns():
    c.execute("PRAGMA table_info(users)")
    columns = [info[1] for info in c.fetchall()]

    if "last_collect_time" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN last_collect_time INTEGER DEFAULT 0")

    if "mining_balance" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN mining_balance REAL DEFAULT 0.0")

    if "mining_rate" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN mining_rate REAL DEFAULT 0.0000003000")

    if "payment_comment" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN payment_comment TEXT")

    if "referrer_id" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT NULL")

    if "referral_count" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN referral_count INTEGER DEFAULT 0")

    if "second_level_referral_count" not in columns:  # Добавляем колонку для второго уровня рефералов
        c.execute("ALTER TABLE users ADD COLUMN second_level_referral_count INTEGER DEFAULT 0")
    
    if "username" not in columns:
        c.execute("ALTER TABLE users ADD COLUMN username TEXT")

    if "mining_timer" not in columns:
        mining_timer_default = 12 * 3600  # Вычисляем значение в секундах
        c.execute(f"ALTER TABLE users ADD COLUMN mining_timer INTEGER DEFAULT {mining_timer_default}")

ensure_columns()

# Подключение к базе данных для выводов
conn_withdrawals = sqlite3.connect('withdrawals.db', check_same_thread=False)
c_withdrawals = conn_withdrawals.cursor()

# Создание таблицы для выводов, если она не существует
c_withdrawals.execute('''
CREATE TABLE IF NOT EXISTS withdrawals (
    user_id INTEGER,
    ton_address TEXT,
    amount REAL
)
''')
conn_withdrawals.commit()

# Проверка подписки
async def is_subscribed(user_id, channel, context) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logging.error(f"Ошибка при проверке подписки на канал {channel}: {e}")
        return False

# Подключение к базе данных администраторов
conn_admins = sqlite3.connect('administration.db', check_same_thread=False)
c_admins = conn_admins.cursor()

# Создание таблицы администраторов, если она не существует
c_admins.execute('''
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
)
''')
conn_admins.commit()

# Проверка подписок на все каналы
async def check_all_subscriptions(user_id, context) -> list:
    return [await is_subscribed(user_id, channel, context) for channel in CHANNELS]

# Декоратор для проверки подписки
def subscription_required(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.message.from_user if update.message else update.callback_query.from_user
        if all(await check_all_subscriptions(user.id, context)):
            return await handler(update, context)
        else:
            buttons = [[InlineKeyboardButton(text=name, url=f"https://t.me/{channel[1:]}")] for channel, name in CHANNELS.items()]
            reply_markup = InlineKeyboardMarkup(buttons)
            await update.message.reply_text(
                "❌ <b>Подпишитесь на все каналы и попробуйте снова.</b>",
                reply_markup=reply_markup, parse_mode='HTML'
            )
    return wrapper

# Проверка прав администратора
def admin_required(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        c_admins.execute('SELECT * FROM admins WHERE user_id = ?', (user_id,))
        admin = c_admins.fetchone()
        if admin:
            return await handler(update, context)
        else:
            await update.message.reply_text("❌ У вас нет прав администратора для выполнения этой команды.")
    return wrapper

# Функция старта
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.message.from_user
    args = context.args
    referrer_id = None

    if args:
        try:
            referrer_id = int(args[0])
        except ValueError:
            logging.warning(f"Invalid referrer ID: {args[0]}")

    c.execute('SELECT * FROM users WHERE user_id = ?', (user.id,))
    user_data = c.fetchone()

    if not user_data:
        logging.info(f"Регистрация нового пользователя: {user.id}")
        payment_comment = str(uuid.uuid4())
        c.execute('INSERT INTO users (user_id, username, mining_rate, last_collect_time, payment_comment, referrer_id) VALUES (?, ?, ?, ?, ?, ?)', 
                  (user.id, user.username, 0.0000003000, int(time.time()), payment_comment, referrer_id))
        conn.commit()

        if referrer_id:
            reward_referrer(referrer_id)
            c.execute('SELECT referrer_id FROM users WHERE user_id = ?', (referrer_id,))
            second_level_referrer_id = c.fetchone()[0]
            if second_level_referrer_id:
                reward_second_level_referrer(second_level_referrer_id)

        subscriptions = await check_all_subscriptions(user.id, context)
        if all(subscriptions):
            await update.message.reply_text(
                "✅ Спасибо за подписку!\n\n ⛏ Теперь вы можете майнить TON.\n\n ↳ Нажмите /menu для продолжения.\n",
            )
        else:
            buttons = [[InlineKeyboardButton(text=name, url=f"https://t.me/{channel[1:]}")] for channel, name in CHANNELS.items()]
            reply_markup = InlineKeyboardMarkup(buttons)
            await update.message.reply_text(
                f"👋 Привет! Для использования бота, необходимо подписаться на наши каналы и нажать /start после подписки.",
            )
    else:
        logging.info(f"Пользователь уже зарегистрирован: {user.id}")
        await update.message.reply_text(
            f"❌ Вы уже зарегистрированы.\n\n ↳ Нажмите /menu для продолжения.")
        
# Основное меню
@subscription_required
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("⛏ Майнинг", callback_data='mining')],
        [InlineKeyboardButton("💰 Баланс", callback_data='balance')],
        [InlineKeyboardButton("📋 Задания", callback_data='tasks')],
        [InlineKeyboardButton("👤 Друзья", callback_data='referrals')],
        [InlineKeyboardButton("ℹ️ Информация", callback_data='info')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"📃 Главное меню\n\n"
        f'⬇️ Выберите раздел для продолжения работы', reply_markup=reply_markup)

# Обработка кнопок
@subscription_required
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == 'mining':
        await update_mining_message(query, context, user_id)

    elif query.data == 'start_mining':
        await start_mining(update, context)

    if query.data == 'collect':
        logging.info(f"Collect button pressed by user_id: {user_id}")
        c.execute('SELECT mining_rate, balance, mining_balance, last_collect_time FROM users WHERE user_id = ?', (user_id,))
        user_data = c.fetchone()
        if user_data:
            logging.info(f"User data found: {user_data}")
            elapsed_time = min(12 * 3600, int(time.time()) - user_data[3])
            collected_amount = user_data[2] + user_data[0] * (elapsed_time // 10)
            new_balance = user_data[1] + collected_amount
            c.execute('UPDATE users SET balance = ?, mining_balance = 0.0, last_collect_time = ? WHERE user_id = ?', 
                      (new_balance, int(time.time()), user_id))
            conn.commit()
            logging.info(f"Updated balance for user_id {user_id}: {new_balance}")
            text = (
                f"✅ <b>Вы собрали {collected_amount:.10f} TON.\n\n"
                f"💰 Ваш новый баланс: {new_balance:.10f} TON.</b>"
            )
            keyboard = [[InlineKeyboardButton("⬅️ Вернуться в меню", callback_data='menu')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            logging.error(f"No user data found for user_id: {user_id}")
            await query.edit_message_text(text="Ошибка при сборе майнинга.")

    elif query.data == 'menu':
        await show_menu(query, context)

    if query.data == 'balance':
        c.execute('SELECT balance, username FROM users WHERE user_id = ?', (user_id,))
        balance, username = c.fetchone()
        text = (
            f"👤 <b>Профиль:\n\n"
            f"↳ 🆔 Ваш ID: {user_id}\n"
            f"↳ 🔗 Ваш username: @{username}\n"
            f"↳ 💰 Ваш Баланс: {balance:.10f} TON.</b>"
        )
        keyboard = [
            [InlineKeyboardButton("📤 Вывести", callback_data='withdraw')],
            [InlineKeyboardButton("⬅️ Назад", callback_data='menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')

    elif query.data == 'tasks':
        task = get_random_task(user_id)
        if task:
            task_text = (
                f"#️⃣ <b> Задание №{task['id']} </b>\n\n"
                f'📋 <b>Условия выполнения:</b>\n'
                f"<i>1. Нажать кнопку \"Перейти\"\n"
                f"2. Подписаться на канал и посмотреть 5-10 постов\n"
                f"3. Нажать кнопку \"Готово\" после выполнения задания.</i>\n\n"
                f"💎 <b>Задание увеличивает скорость майнинга на 0.0000001000 </b> TON"
            )
            keyboard = [
                [InlineKeyboardButton("🔗 Перейти", url=task['link'])],
                [InlineKeyboardButton("✅ Готово", callback_data=f'check_task:{task["id"]}')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text=task_text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            keyboard = [[InlineKeyboardButton("⬅️ Вернуться в меню", callback_data='menu')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text="✅ <b>Вы выполнили все задания!</b>", reply_markup=reply_markup, parse_mode='HTML')

    elif query.data.startswith('check_task:'):
        await check_task_completion(update, context)

    elif query.data == 'referrals':
        c.execute('SELECT referral_count, second_level_referral_count FROM users WHERE user_id = ?', (user_id,))
        referral_count, second_level_referral_count = c.fetchone()

        referral_link = f"https://t.me/{context.bot.username}?start={user_id}"
        text = (
            f"🤝 <b>Приглашайте друзей и получайте бонусы!\n\n"
            f"🔗 Ваша реферальная ссылка:</b> {referral_link}\n\n"
            f"📥 <b>Вы получите 0.005 TON за каждого приглашенного пользователя.\n\n"
            f"👥 Количество приглашенных вами друзей: {referral_count} </b>\n\n"
        )
        keyboard = [
            [InlineKeyboardButton("⬅️ Назад", callback_data='menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')

    elif query.data == 'info':
        text = (
        f"ℹ️ <b>Информация об выплатах, обновлениях и т.д. находится в оффициальном канале\n\n"
        f"© Ссылка на официальный канал: @minerston_official</b>\n\n"
        )
        keyboard = [
            [InlineKeyboardButton("⬅️ Назад", callback_data='menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')

    elif query.data == 'menu':
        await show_menu(query, context)

    elif query.data == 'upgrade_first':
        text = (
            f"🚀 <b>Улучшения\n\n"
            f"💎 Стоимость: 0.5 TON\n\n"
            f"⛏ Увеличивает скорость вашего майнинга на 0.0000030000 TON\n\n"
            f"❗️ Максимальный размер оплаты для пользователя: 10 TON.\n\n"
            f"❗️ Максимальное увеличение майнинга при этом составляет: 0.0000600000 TON.</b>"
        )
        keyboard = (
            [InlineKeyboardButton("🚀 Улучшить", callback_data='upgrade_second')],
            [InlineKeyboardButton("⬅️ Назад", callback_data='menu')]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')

    elif query.data == "upgrade_second":
        await handle_upgrade(update, context, user_id)

    elif query.data == 'payment_done':
        await payment_done(update, context)

    elif query.data == 'withdraw':
        c.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    balance = c.fetchone()[0]
    if balance < 0.5000000000:
        text = "❌ <b>Минимальная сумма вывода составляет 0.5 TON.</b>"
        keyboard = [
            [InlineKeyboardButton("⬅️ Назад", callback_data='balance')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        text = "💳 Укажите ваш TON адрес"
        context.user_data['awaiting_ton_address'] = True
        keyboard = [
            [InlineKeyboardButton("❌ Отменить", callback_data='balance')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')

# Функция обработки подачи заявки на вывод средств
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    message_text = update.message.text

    # Если пользователь ожидает ввода TON адреса
    if context.user_data.get('awaiting_ton_address'):
        ton_address = message_text
        
        if is_valid_ton_address(ton_address):
            c_withdrawals.execute('INSERT INTO withdrawals (user_id, ton_address) VALUES (?, ?)', (user_id, ton_address))
            conn_withdrawals.commit()
            context.user_data['awaiting_ton_address'] = False
            context.user_data['awaiting_withdraw_amount'] = True

            text = "💎 Введите сумму вывода"
            keyboard = [[InlineKeyboardButton("❌ Отменить", callback_data='balance')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(text, reply_markup=reply_markup)
        else:
            await update.message.reply_text("❌ Неверный формат адреса! Укажите ваш TON адрес:")
            
    # Если пользователь ожидает ввода суммы вывода
    elif context.user_data.get('awaiting_withdraw_amount'):
        try:
            amount = float(message_text)
            c.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,))
            balance = c.fetchone()[0]

            if amount > balance:
                await update.message.reply_text("❌ Сумма вывода превышает ваш баланс. Пожалуйста, введите корректную сумму.")
            else:
                c_withdrawals.execute('UPDATE withdrawals SET amount = ? WHERE user_id = ? AND amount IS NULL', (amount, user_id))
                c.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amount, user_id))
                conn_withdrawals.commit()
                conn.commit()
                context.user_data['awaiting_withdraw_amount'] = False
                await update.message.reply_text("✅ Заявка на вывод средств успешно принята.\n\n ↳ Нажмите /menu для продолжения.")
                await update.message.reply_text(
                    "📃Главное меню\n\n⬇️ Выберите раздел для продолжения работы", 
                    reply_markup=show_menu
                )
        except ValueError:
            await update.message.reply_text("❌ Неверный формат суммы! Пожалуйста, введите корректную сумму.")


# Функция для обновления сообщения с информацией о майнинге
async def update_mining_message(query, context, user_id):
    c.execute('SELECT mining_rate, mining_balance, last_collect_time, mining_timer FROM users WHERE user_id = ?', (user_id,))
    user_data = c.fetchone()
    if user_data:
        elapsed_time = int(time.time()) - user_data[2]
        remaining_time = user_data[3] - elapsed_time

        if remaining_time <= 0:
            # Остановить майнинг и обновить статус
            total_collected = user_data[1] + user_data[0] * (user_data[3] // 10)
            c.execute('UPDATE users SET mining_timer = 0, mining_balance = ?, last_collect_time = ? WHERE user_id = ?', 
                      (total_collected, int(time.time()), user_id))
            conn.commit()
            text = (
                f"❌ <b>Майнинг неактивен! Нажмите кнопку \"Запустить\" для возобновления.</b>\n\n"
                f"💰 <b>Баланс: {total_collected:.10f} TON</b>\n\n"
                f"⏳ <b>Вы каждые 10 секунд получаете - {user_data[0]:.10f} TON</b>\n"
            )
            keyboard = [
                [InlineKeyboardButton("♻️ Запустить", callback_data='start_mining')],
                [InlineKeyboardButton(f"📥 Собрать {total_collected:.10f} TON", callback_data='collect')],
                [InlineKeyboardButton("🚀 Улучшения", callback_data='upgrade_first')],
                [InlineKeyboardButton("⬅️ Назад", callback_data='menu')]
            ]
        else:
            hours, remainder = divmod(remaining_time, 3600)
            minutes, seconds = divmod(remainder, 60)
            time_string = f"{hours} часов {minutes} минут {seconds} секунд"
            text = (
                f"✅ <b>Майнинг активен.</b>\n\n"
                f"💰 <b>Баланс: {user_data[1] + user_data[0] * (elapsed_time // 10):.10f} TON</b>\n\n"
                f"⏳ <b>Вы каждые 10 секунд получаете - {user_data[0]:.10f} TON</b>\n\n"
                f"⏰ <b>Время до завершения майнинга: {time_string} </b>"
            )
            keyboard = [
                [InlineKeyboardButton(f"📥 Собрать {user_data[1] + user_data[0] * (elapsed_time // 10):.10f} TON", callback_data='collect')],
                [InlineKeyboardButton("🚀 Улучшения", callback_data='upgrade_first')],
                [InlineKeyboardButton("⬅️ Назад", callback_data='menu')]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')

# Функция майнинга
async def mining(context):
    job = context.job
    user_id = int(job.name)
    c.execute('SELECT mining_rate, mining_balance, last_collect_time, mining_timer FROM users WHERE user_id = ?', (user_id,))
    user_data = c.fetchone()
    if user_data:
        elapsed_time = int(time.time()) - user_data[2]
        remaining_time = user_data[3] - elapsed_time
        if remaining_time <= 0:
            context.job_queue.get_jobs_by_name(str(user_id))[0].schedule_removal()
            total_collected = user_data[1] + user_data[0] * (user_data[3] // 10)
            c.execute('UPDATE users SET mining_balance = ?, mining_timer = 0, last_collect_time = ? WHERE user_id = ?', 
                      (total_collected, int(time.time()), user_id))
            conn.commit()
        else:
            new_mining_balance = user_data[1] + user_data[0] * (elapsed_time // 10)
            c.execute('UPDATE users SET mining_balance = ?, last_collect_time = ? WHERE user_id = ?', 
                      (new_mining_balance, int(time.time()), user_id))
            conn.commit()

# Функция для обработки улучшения скорости майнинга
async def handle_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    query = update.callback_query
    c.execute('SELECT payment_comment FROM users WHERE user_id = ?', (user_id,))
    payment_comment = c.fetchone()[0]
    text = (
        f"💎 <b>Переведите 0.5 TON.\n\n"
        f"💳 Адрес для пополнения через TON:</b> <code>{COLD_WALLET_ADDRESS}</code>\n\n"
        f"💬 <b>Комментарий:</b> <code>{payment_comment}</code>\n\n"
        f"❗ <b>Обязательно указывайте комментарий к переводу, иначе деньги не дойдут! В случае неверного платежа, деньги не возвращаются!❗\n\n"
        f"После оплаты нажмите на \"✅ Я оплатил\"!</b>\n\n"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Я оплатил", callback_data='payment_done')],
        [InlineKeyboardButton("⬅️ Назад", callback_data='menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode='HTML')

# Функция для подтверждения оплаты
async def payment_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    c.execute('SELECT payment_comment FROM users WHERE user_id = ?', (user_id,))
    payment_comment = c.fetchone()[0]

    keyboard = [[InlineKeyboardButton("⬅️ Вернуться в меню", callback_data='menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.reply_text(
        f"⏳ <b>Ожидайте подтверждения оплаты.\n\n"
        f"🚀 После проверки скорость майнинга будет обновлена автоматически.</b>",
        reply_markup=reply_markup, parse_mode='HTML'
    )

# Функция для отображения главного меню
async def show_menu(query, context):
    keyboard = [
        [InlineKeyboardButton("⛏ Майнинг", callback_data='mining')],
        [InlineKeyboardButton("💰 Баланс", callback_data='balance')],
        [InlineKeyboardButton("📋 Задания", callback_data='tasks')],
        [InlineKeyboardButton("👤 Друзья", callback_data='referrals')],
        [InlineKeyboardButton("ℹ️ Информация", callback_data='info')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text=
        f"📃 Главное меню\n\n"
        f'⬇️ Выберите раздел для продолжения работы', reply_markup=reply_markup)

#команда для обновления скорости майнинга
@admin_required
async def update_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) != 1:
        await update.message.reply_text("Используйте команду в формате: /update_rate <user_id>")
        return
    user_id = context.args[0]
    increment = 0.0000030000
    
    #уведомление об увеличении скорости майнинга админу
    c.execute('SELECT mining_rate FROM users WHERE user_id = ?', (user_id,))
    current_rate = c.fetchone()
    
    if current_rate is None:
        await update.message.reply_text(f"Пользователь с ID {user_id} не найден.")
        return

    new_rate = current_rate[0] + increment
    c.execute('UPDATE users SET mining_rate = ? WHERE user_id = ?', (new_rate, user_id))
    conn.commit()
    await update.message.reply_text(f"Скорость майнинга пользователя {user_id} увеличена на {increment:.10f} TON и теперь составляет {new_rate:.10f} TON.")
    
# Список заданий
TASKS_LIST = [
    {"id": 1, "channel_link": "@fazixcheck", "link": "https://t.me/fazixcheck"},
    {"id": 3, "channel_link": "@topchecks_rocket", "link": "https://t.me/+FRm_FF6KASsxMGJi"}
]

# Словарь для хранения выполненных заданий по пользователям
USER_TASKS = {}

# Функция для получения случайного задания из списка TASKS_LIST
def get_random_task(user_id):
    # Получение ID выполненных заданий
    c.execute('SELECT task_id FROM user_tasks WHERE user_id = ?', (user_id,))
    completed_tasks = set(row[0] for row in c.fetchall())
    
    # Фильтрация невыполненных заданий
    available_tasks = [task for task in TASKS_LIST if task["id"] not in completed_tasks]
    
    if available_tasks:
        return random.choice(available_tasks)
    return None

# Проверка выполнения задания
@subscription_required
async def check_task_completion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    task_id = int(query.data.split(":")[1])
    user_id = query.from_user.id

    # Найти задание в списке по ID
    task = next((task for task in TASKS_LIST if task["id"] == task_id), None)
    if not task:
        await query.edit_message_text(text="❌ Задание не найдено.")
        return

    # Проверка подписки на канал
    if await is_subscribed(user_id, task["channel_link"], context):
        mining_rate_increase = 0.0000001000
        c.execute('UPDATE users SET mining_rate = mining_rate + ? WHERE user_id = ?', (mining_rate_increase, user_id))
        c.execute('INSERT INTO user_tasks (user_id, task_id) VALUES (?, ?)', (user_id, task_id))  # Запись выполненного задания
        conn.commit()
        keyboard = [[InlineKeyboardButton("⬅️ Вернуться в меню", callback_data='menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=
            f"✅ <b>Задание №{task_id} выполнено!\n\n 🚀 Ваша скорость майнинга увеличена на {mining_rate_increase:.10f} TON каждые 10 секунд. </b>",
            reply_markup=reply_markup, parse_mode='HTML'
        )
    else:
        keyboard = [[InlineKeyboardButton("⬅️ Вернуться в меню", callback_data='menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text=f"❌ <b>Вы не подписаны на канал! Попробуйте снова. </b>",
            reply_markup=reply_markup, parse_mode='HTML'
        )
      
# Создание таблицы выполненных заданий
c.execute('''
CREATE TABLE IF NOT EXISTS user_tasks (
    user_id INTEGER,
    task_id INTEGER,
    PRIMARY KEY (user_id, task_id)
)
''')
conn.commit()

# Вознаграждение пользователю за приглашения
def reward_referrer(referrer_id):
    reward_amount = 0.0050000000
    c.execute('UPDATE users SET balance = balance + ?, referral_count = referral_count + 1 WHERE user_id = ?', 
              (reward_amount, referrer_id))
    conn.commit()

# Функция счета 2 уровня рефералки
def reward_second_level_referrer(second_level_referrer_id):
    reward_amount = 0.0000000000  # Вознаграждение за реферала второго уровня
    c.execute('UPDATE users SET balance = balance + ?, second_level_referral_count = second_level_referral_count + 1 WHERE user_id = ?', 
              (reward_amount, second_level_referrer_id))
    conn.commit()

# Команда проверки количества активных пользователей
async def active_users_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Получаем время 24 часа назад
    last_24_hours = int(time.time()) - 24 * 3600
    
    # Считаем количество пользователей, которые собирали майнинг за последние 24 часа
    c.execute('SELECT COUNT(*) FROM users WHERE last_collect_time > ?', (last_24_hours,))
    active_users_count = c.fetchone()[0]
    
    # Выводим статистику
    text = f"📊 Статистика активных пользователей:\n\n" \
           f"👤 Активных пользователей за последние 24 часа: {active_users_count}"
    
    await update.message.reply_text(text)

# Функция для перезапуска майнинга
@subscription_required
async def start_mining(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    c.execute('UPDATE users SET last_collect_time = ?, mining_timer = ? WHERE user_id = ?', (int(time.time()), 12 * 3600, user_id))
    conn.commit()

    await update_mining_message(query, context, user_id)

# Функция проверки на TON адрес
def is_valid_ton_address(address: str) -> bool:
    return re.match(r"^[A-Za-z0-9_-]{48,64}$", address) is not None

def main() -> None:
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu))
    application.add_handler(CommandHandler("update_rate", update_rate))   
    application.add_handler(CommandHandler("active_users", admin_required(active_users_stats)))                  
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))  # Новый обработчик сообщений
    application.run_polling()
    updater = Updater("7349143840:AAGVNAySKjbcrlKBUru1AtOufcXw4eHIddU")
    dp = updater.dispatcher

    dp.add_handler(CallbackQueryHandler(button))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
