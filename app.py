import telebot
from telebot import types
import os
import sqlite3
import random
import secrets
import datetime
from flask import Flask, send_file, request, jsonify

TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

ADMIN_ID = 7695713296
STATS_CHAT_ID = os.environ.get('STATS_CHAT_ID', ADMIN_ID)

WEBAPP_URL = "https://my-casino-bot-3.onrender.com" 

RED_ROULETTE_NUMBERS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
MINES_ROUNDS = {}

def init_db():
    conn = sqlite3.connect('casino.db', timeout=15)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, username TEXT, balance REAL, total_rounds INTEGER, best_result REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, method TEXT, amount REAL, status TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS bets_history 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, game TEXT, bet REAL, win REAL, currency TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Добавляем новые колонки для Бонусов и VIP
    for col, typ in [
        ('phone', 'TEXT'), ('reg_name', 'TEXT'), ('registered', 'INTEGER DEFAULT 0'), 
        ('balance_uah', 'REAL DEFAULT 0'), ('email', 'TEXT'), 
        ('kyc_name', 'TEXT'), ('kyc_country', 'TEXT'), ('kyc_status', 'TEXT DEFAULT "Not passed"'),
        ('total_wagered', 'REAL DEFAULT 0'), ('vip_level', 'INTEGER DEFAULT 0'), 
        ('bonus_wager_req', 'REAL DEFAULT 0')
    ]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
            
    try:
        c.execute("ALTER TABLE transactions ADD COLUMN currency TEXT DEFAULT 'USD'")
    except sqlite3.OperationalError:
        pass
        
    try:
        c.execute("ALTER TABLE transactions ADD COLUMN wallet TEXT")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

init_db()

def get_user_bal(c, uid, currency):
    if currency == 'UAH':
        c.execute("SELECT balance_uah FROM users WHERE id=?", (uid,))
    else:
        c.execute("SELECT balance FROM users WHERE id=?", (uid,))
    r = c.fetchone()
    return r[0] if r else 0

def set_user_bal(c, uid, currency, new_bal, best_win=None):
    if currency == 'UAH':
        c.execute("UPDATE users SET balance_uah=? WHERE id=?", (new_bal, uid))
    else:
        c.execute("UPDATE users SET balance=? WHERE id=?", (new_bal, uid))
        if best_win and best_win > 0:
            c.execute("UPDATE users SET best_result = MAX(best_result, ?) WHERE id=?", (best_win, uid))

def check_vip_level(c, user_id):
    """Автоматическая проверка и повышение VIP-статуса"""
    c.execute("SELECT total_wagered, vip_level FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    if not row: return
    wagered, current_vip = row
    
    new_vip = current_vip
    reward = 0
    
    if wagered >= 100000 and current_vip < 4: new_vip = 4; reward = 5000
    elif wagered >= 30000 and current_vip < 3: new_vip = 3; reward = 2000
    elif wagered >= 10000 and current_vip < 2: new_vip = 2; reward = 500
    elif wagered >= 2000 and current_vip < 1: new_vip = 1; reward = 200

    if new_vip > current_vip:
        c.execute("UPDATE users SET vip_level=?, balance = balance + ? WHERE id=?", (new_vip, reward, user_id))
        try:
            bot.send_message(user_id, f"👑 Поздравляем! Вы достигли нового VIP-статуса!\nВам начислен бонус: ${reward}")
        except:
            pass
        return True

def log_bet(c, user_id, game, bet, win, currency):
    c.execute("INSERT INTO bets_history (user_id, game, bet, win, currency) VALUES (?, ?, ?, ?, ?)", (user_id, game, bet, win, currency))
    if currency == 'USD':
        # Обновляем общий оборот
        c.execute("UPDATE users SET total_wagered = total_wagered + ? WHERE id=?", (bet, user_id))
        # Проверяем VIP
        check_vip_level(c, user_id)
        # Списываем отыгрыш бонуса
        c.execute("SELECT bonus_wager_req FROM users WHERE id=?", (user_id,))
        req = c.fetchone()[0]
        if req > 0:
            new_req = max(0, req - bet)
            c.execute("UPDATE users SET bonus_wager_req=? WHERE id=?", (new_req, user_id))

# ============ ОБРАБОТЧИКИ КНОПОК ДЕПОЗИТОВ/ВЫВОДОВ ============
@bot.callback_query_handler(func=lambda call: call.data.startswith(('app_dep', 'rej_dep', 'app_wd', 'rej_wd')))
def handle_admin_action(call):
    try:
        bot.answer_callback_query(call.id, "⏳ Обработка...")
    except Exception:
        pass

    if str(call.from_user.id) != str(ADMIN_ID):
        return

    try:
        parts = call.data.split('_')
        if len(parts) < 3: return
        action, tx_type, tx_id = parts[0], parts[1], int(parts[2])
        
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        c.execute("SELECT user_id, amount, status, currency FROM transactions WHERE id=?", (tx_id,))
        tx = c.fetchone()
        
        if not tx or tx[2] != 'Pending':
            conn.close()
            return
            
        user_id, amount = int(tx[0]), float(tx[1])
        tx_curr = tx[3] if tx[3] else 'USD'
        status_line = ""
        user_msg = ""
        sym = '₴' if tx_curr == 'UAH' else '$'
        
        if action == 'app' and tx_type == 'dep':
            if tx_curr == 'UAH':
                c.execute("UPDATE users SET balance_uah = balance_uah + ? WHERE id=?", (amount, user_id))
            else:
                c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
            c.execute("UPDATE transactions SET status = 'Completed' WHERE id=?", (tx_id,))
            status_line = f"✅ Пополнение #{tx_id} одобрено"
            user_msg = f"✅ Ваш депозит на {amount}{sym} одобрен! Баланс пополнен."
            
        elif action == 'rej' and tx_type == 'dep':
            c.execute("UPDATE transactions SET status = 'Rejected' WHERE id=?", (tx_id,))
            status_line = f"❌ Пополнение #{tx_id} отклонено"
            user_msg = f"❌ Ваш депозит на {amount}{sym} отклонен."
            
        elif action == 'app' and tx_type == 'wd':
            c.execute("UPDATE transactions SET status = 'Completed' WHERE id=?", (tx_id,))
            status_line = f"✅ Вывод #{tx_id} выплачен"
            user_msg = f"✅ Ваш вывод на {amount}{sym} успешно выплачен!"
            
        elif action == 'rej' and tx_type == 'wd':
            if tx_curr == 'UAH':
                c.execute("UPDATE users SET balance_uah = balance_uah + ? WHERE id=?", (amount, user_id))
            else:
                c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
            c.execute("UPDATE transactions SET status = 'Rejected' WHERE id=?", (tx_id,))
            status_line = f"❌ Вывод #{tx_id} отклонен (возврат)"
            user_msg = f"❌ Ваш вывод на {amount}{sym} отклонен. Средства возвращены."
            
        conn.commit()
        conn.close()

        try:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🎰 Открыть казино", web_app=types.WebAppInfo(url=WEBAPP_URL)))
            bot.send_message(user_id, user_msg + "\n\nОткройте казино — баланс уже обновлён.", reply_markup=markup)
        except Exception as e:
            print("notify user error:", e)

        try:
            new_text = call.message.text + "\n\n" + status_line
            bot.edit_message_text(new_text, call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception as e:
            print("edit_message error:", e)
    except Exception as e:
        print("Callback error:", e)

# ============ ОБРАБОТЧИК KYC ============
@bot.callback_query_handler(func=lambda call: call.data.startswith(('app_kyc', 'rej_kyc')))
def handle_kyc_action(call):
    try:
        bot.answer_callback_query(call.id, "⏳ Обработка KYC...")
    except Exception:
        pass

    if str(call.from_user.id) != str(ADMIN_ID):
        return

    try:
        parts = call.data.split('_')
        if len(parts) < 3: return
        action, user_id = parts[0], int(parts[2])
        
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        
        if action == 'app':
            c.execute("UPDATE users SET kyc_status = 'Approved' WHERE id=?", (user_id,))
            status_line = f"✅ KYC пользователя {user_id} одобрена"
            user_msg = f"✅ Ваша KYC верификация одобрена! Теперь вы можете выводить крупные суммы."
        else:
            c.execute("UPDATE users SET kyc_status = 'Not passed' WHERE id=?", (user_id,))
            status_line = f"❌ KYC пользователя {user_id} отклонена"
            user_msg = f"❌ Ваша KYC верификация отклонена. Проверьте данные и попробуйте снова."
            
        conn.commit()
        conn.close()

        try:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🎰 Открыть казино", web_app=types.WebAppInfo(url=WEBAPP_URL)))
            bot.send_message(user_id, user_msg, reply_markup=markup)
        except Exception as e:
            print("notify user error (KYC):", e)

        try:
            new_text = call.message.text + "\n\n" + status_line
            bot.edit_message_text(new_text, call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception as e:
            print("edit_message error (KYC):", e)
    except Exception as e:
        print("Callback error (KYC):", e)

# ============ СТАТИСТИКА ============
@bot.message_handler(commands=['stats'])
def send_stats(message):
    if message.from_user.id != ADMIN_ID: return
    
    conn = sqlite3.connect('casino.db', timeout=15)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE registered=1")
    reg_users = c.fetchone()[0]
    c.execute("SELECT SUM(balance), SUM(balance_uah), SUM(total_wagered) FROM users")
    sum_bal = c.fetchone()
    sum_usd = sum_bal[0] or 0
    sum_uah = sum_bal[1] or 0
    sum_wagered = sum_bal[2] or 0
    
    c.execute("SELECT SUM(amount) FROM transactions WHERE type='Deposit' AND status='Completed' AND currency='USD'")
    dep_usd = c.fetchone()[0] or 0
    c.execute("SELECT SUM(amount) FROM transactions WHERE type='Withdrawal' AND status='Completed' AND currency='USD'")
    wd_usd = c.fetchone()[0] or 0
    
    conn.close()
    
    text = f"📊 <b>СТАТИСТИКА КАЗИНО</b>\n\n"
    text += f"👥 Всего пользователей: <b>{total_users}</b>\n"
    text += f"✅ Регистраций: <b>{reg_users}</b>\n\n"
    text += f"💰 <b>Балансы:</b> ${sum_usd:.2f} / ₴{sum_uah:.2f}\n"
    text += f"🔥 <b>Общий оборот (Wager):</b> ${sum_wagered:.2f}\n\n"
    text += f"📥 Депозиты: ${dep_usd:.2f}\n"
    text += f"📤 Выплаты: ${wd_usd:.2f}\n"
    
    bot.send_message(STATS_CHAT_ID, text, parse_mode='HTML')
    if message.chat.id != STATS_CHAT_ID:
        bot.reply_to(message, "✅ Статистика отправлена!")

# ============ API ============
@app.route('/api/user', methods=['GET'])
def get_user():
    user_id = request.args.get('id')
    username = request.args.get('username', 'Игрок')
    if not user_id: return jsonify({'error': 'No ID'}), 400
    conn = sqlite3.connect('casino.db', timeout=15)
    c = conn.cursor()
    c.execute("SELECT balance, total_rounds, best_result, phone, reg_name, registered, balance_uah, email, kyc_name, kyc_country, kyc_status, total_wagered, vip_level, bonus_wager_req FROM users WHERE id=?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT INTO users (id, username, balance, total_rounds, best_result, registered, balance_uah) VALUES (?, ?, ?, ?, ?, 0, ?)",
                  (user_id, username, 0.0, 0, 0.0, 0.0))
        conn.commit()
        user = (0.0, 0, 0.0, None, None, 0, 0.0, None, None, None, "Not passed", 0.0, 0, 0.0)
    conn.close()
    return jsonify({
        'balance': user[0], 'total_rounds': user[1], 'best_result': user[2],
        'phone': user[3], 'reg_name': user[4], 'registered': bool(user[5]),
        'balance_uah': user[6], 'email': user[7], 
        'kyc_name': user[8], 'kyc_country': user[9], 'kyc_status': user[10],
        'total_wagered': user[11], 'vip_level': user[12], 'bonus_wager_req': user[13]
    })

@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.json or {}
        user_id = int(data['id'])
        name = (data.get('name') or '').strip()
        phone = (data.get('phone') or '').strip()
        email = (data.get('email') or '').strip()
        
        if len(phone) < 7 or len(name) < 2 or len(email) < 5:
            return jsonify({'error': 'Неверные данные'}), 400
            
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE id=?", (user_id,))
        if not c.fetchone():
            c.execute("INSERT INTO users (id, balance, balance_uah, registered) VALUES (?, 0.0, 0.0, 0)", (user_id,))
        
        c.execute("SELECT id FROM users WHERE phone=? AND id!=?", (phone, user_id))
        if c.fetchone():
            conn.close()
            return jsonify({'error': 'Этот номер уже зарегистрирован'}), 400
            
        c.execute("UPDATE users SET phone=?, reg_name=?, email=?, registered=1 WHERE id=?", (phone, name, email, user_id))
        conn.commit()
        conn.close()
        
        try: 
            bot.send_message(STATS_CHAT_ID, f"👤 <b>Новая регистрация</b>\nID: {user_id}\nИмя: {name}\nТел: {phone}\nEmail: {email}", parse_mode='HTML')
        except: 
            pass
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/kyc', methods=['POST'])
def submit_kyc():
    try:
        data = request.json or {}
        user_id = int(data['id'])
        kyc_name = (data.get('kyc_name') or '').strip()
        kyc_country = (data.get('kyc_country') or '').strip()
        
        if len(kyc_name) < 3 or len(kyc_country) < 2:
            return jsonify({'error': 'Неверные данные KYC'}), 400
            
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        c.execute("UPDATE users SET kyc_name=?, kyc_country=?, kyc_status='Pending' WHERE id=?", (kyc_name, kyc_country, user_id))
        conn.commit()
        conn.close()
        
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("✅ Одобрить", callback_data=f"app_kyc_{user_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"rej_kyc_{user_id}")
        )
        
        try:
            bot.send_message(STATS_CHAT_ID, f"🛡️ <b>Новая заявка KYC</b>\nID: {user_id}\nФИО: {kyc_name}\nСтрана: {kyc_country}", reply_markup=markup, parse_mode='HTML')
        except: 
            pass
            
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/bonus/claim_deposit', methods=['POST'])
def claim_deposit_bonus():
    try:
        data = request.json
        user_id = int(data['id'])
        
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        c.execute("SELECT bonus_wager_req, balance FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        
        if row[0] > 0:
            conn.close()
            return jsonify({'error': 'У вас уже есть активный бонус. Сначала отыграйте его.'}), 400
            
        c.execute("SELECT amount FROM transactions WHERE user_id=? AND type='Deposit' AND status='Completed' ORDER BY date DESC LIMIT 1", (user_id,))
        dep = c.fetchone()
        
        base_amount = dep[0] if dep else 50.0
        bonus_amount = min(base_amount, 500.0) # Максимум 500$
        
        c.execute("UPDATE users SET balance = balance + ?, bonus_wager_req = ? WHERE id=?", (bonus_amount, bonus_amount * 10, user_id))
        conn.commit()
        conn.close()
        
        return jsonify({'status': 'ok', 'bonus': bonus_amount, 'wager_req': bonus_amount * 10})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/bonus/claim_personal', methods=['POST'])
def claim_personal_bonus():
    try:
        data = request.json
        user_id = int(data['id'])
        
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        c.execute("SELECT bonus_wager_req FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        
        if row[0] > 0:
            conn.close()
            return jsonify({'error': 'Сначала отыграйте текущий бонус!'}), 400
            
        bonus_amount = round(random.uniform(1.0, 5.0), 2)
        
        c.execute("UPDATE users SET balance = balance + ?, bonus_wager_req = ? WHERE id=?", (bonus_amount, bonus_amount * 5, user_id))
        conn.commit()
        conn.close()
        
        return jsonify({'status': 'ok', 'bonus': bonus_amount, 'wager_req': bonus_amount * 5})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/save', methods=['POST'])
def save_user():
    data = request.json
    conn = sqlite3.connect('casino.db', timeout=15)
    c = conn.cursor()
    c.execute("UPDATE users SET balance=?, balance_uah=?, total_rounds=?, best_result=? WHERE id=?", 
              (data.get('balance', 0), data.get('balance_uah', 0), data.get('total_rounds', 0), data.get('best_result', 0), data['id']))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/api/transactions', methods=['GET'])
def get_transactions():
    user_id = request.args.get('id')
    conn = sqlite3.connect('casino.db', timeout=15)
    c = conn.cursor()
    c.execute("SELECT type, method, amount, status, date, currency FROM transactions WHERE user_id=? ORDER BY date DESC LIMIT 20", (user_id,))
    rows = c.fetchall()
    conn.close()
    return jsonify([{'type': r[0], 'method': r[1], 'amount': r[2], 'status': r[3], 'date': r[4], 'currency': r[5]} for r in rows])

@app.route('/api/bets', methods=['GET'])
def get_bets():
    user_id = request.args.get('id')
    conn = sqlite3.connect('casino.db', timeout=15)
    c = conn.cursor()
    c.execute("SELECT game, bet, win, currency, date FROM bets_history WHERE user_id=? ORDER BY date DESC LIMIT 30", (user_id,))
    rows = c.fetchall()
    conn.close()
    return jsonify([{'game': r[0], 'bet': r[1], 'win': r[2], 'currency': r[3], 'date': r[4]} for r in rows])

@app.route('/api/deposit', methods=['POST'])
def deposit():
    try:
        data = request.json
        user_id, amount, method = int(data['id']), float(data['amount']), data.get('method', 'USDT')
        currency = data.get('currency', 'USD')
        
        min_limits = {'USDT': 15.0, 'BTC': 50.0, 'ETH': 20.0, 'UAH': 500.0}
        if amount < min_limits.get(method, 15.0):
            return jsonify({'error': f'Минимальная сумма для {method} - {min_limits.get(method, 15.0)}'}), 400
        
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        c.execute("SELECT registered FROM users WHERE id=?", (user_id,))
        reg = c.fetchone()
        if not reg or not reg[0]:
            conn.close()
            return jsonify({'error': 'Сначала зарегистрируйтесь'}), 400
            
        c.execute("INSERT INTO transactions (user_id, type, method, amount, status, currency) VALUES (?, 'Deposit', ?, ?, 'Pending', ?)", (user_id, method, amount, currency))
        tx_id = c.lastrowid
        conn.commit()
        conn.close()
        
        sym = 'грн' if currency == 'UAH' else '$'
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅ Одобрить", callback_data=f"app_dep_{tx_id}"), types.InlineKeyboardButton("❌ Отклонить", callback_data=f"rej_dep_{tx_id}"))
        bot.send_message(STATS_CHAT_ID, f"💰 <b>Заявка на пополнение #{tx_id}</b>\nID: {user_id}\nСумма: {amount} {sym}\nСеть: {method}", reply_markup=markup, parse_mode='HTML')
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/withdraw', methods=['POST'])
def withdraw():
    try:
        data = request.json
        user_id, amount, method = int(data['id']), float(data['amount']), data.get('method', 'USDT')
        currency = data.get('currency', 'USD')
        wallet = (data.get('wallet') or '').strip()
        
        if len(wallet) < 5:
            return jsonify({'error': 'Укажите корректные реквизиты для вывода'}), 400
        
        min_limits = {'USDT': 15.0, 'BTC': 50.0, 'ETH': 20.0, 'UAH': 500.0}
        if amount < min_limits.get(method, 15.0):
            return jsonify({'error': f'Минимальная сумма для {method} - {min_limits.get(method, 15.0)}'}), 400
            
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        c.execute("SELECT balance, balance_uah, registered, bonus_wager_req FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        if not row: return jsonify({'error': 'Пользователь не найден'}), 404
        if not row[2]: return jsonify({'error': 'Сначала зарегистрируйтесь'}), 400
        
        if row[3] > 0:
            return jsonify({'error': f'Вы не можете выводить средства. Отыграйте бонус (осталось ${row[3]:.2f})'}), 400
        
        bal = row[1] if currency == 'UAH' else row[0]
        
        if bal >= amount:
            if currency == 'UAH':
                c.execute("UPDATE users SET balance_uah = balance_uah - ? WHERE id=?", (amount, user_id))
            else:
                c.execute("UPDATE users SET balance = balance - ? WHERE id=?", (amount, user_id))
                
            c.execute("INSERT INTO transactions (user_id, type, method, amount, status, currency, wallet) VALUES (?, 'Withdrawal', ?, ?, 'Pending', ?, ?)", (user_id, method, amount, currency, wallet))
            tx_id = c.lastrowid
            conn.commit()
            conn.close()
            
            sym = 'грн' if currency == 'UAH' else '$'
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ Выплачено", callback_data=f"app_wd_{tx_id}"), types.InlineKeyboardButton("❌ Отклонить", callback_data=f"rej_wd_{tx_id}"))
            bot.send_message(STATS_CHAT_ID, f"💸 <b>Заявка на вывод #{tx_id}</b>\nID: {user_id}\nСумма: {amount} {sym}\nСеть: {method}\nРеквизиты: <code>{wallet}</code>", reply_markup=markup, parse_mode='HTML')
            return jsonify({'status': 'ok'})
        else:
            conn.close()
            return jsonify({'error': f'Недостаточно средств. Баланс: {bal}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/support', methods=['POST'])
def support():
    data = request.json
    text = f"📩 <b>Поддержка</b>\nОт: @{data.get('username')} (ID: {data.get('id')})\n\n{data.get('message')}"
    try:
        bot.send_message(STATS_CHAT_ID, text, parse_mode='HTML')
        return jsonify({'status': 'ok'})
    except Exception as e: return jsonify({'error': str(e)}), 500

# ============ ИГРЫ ============
@app.route('/api/slots/classic', methods=['POST'])
def slots_classic():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id']); bet = float(data['bet']); currency = data.get('currency', 'USD')
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        if bal < bet: conn.close(); return jsonify({'error': 'Недостаточно средств'}), 400
        
        symbols = ['🍒', '🍋', '🍊', '🍇', '💎', '7️⃣']; weights = [30, 25, 20, 15, 8, 2]
        reels = []
        for _ in range(3):
            r = random.randint(1, 100); acc = 0
            for sym, w in zip(symbols, weights):
                acc += w
                if r <= acc: reels.append(sym); break
        if reels[0] == reels[1] == reels[2]: mult = 50 if reels[0] == '7️⃣' else 20 if reels[0] == '💎' else 10 if reels[0] == '🍇' else 5
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]: mult = 2 if ('💎' in reels or '7️⃣' in reels) else 1.5
        else: mult = 0
        win = round(bet * mult, 2); new_bal = round(bal - bet + win, 2)
        set_user_bal(c, user_id, currency, new_bal, win)
        c.execute("UPDATE users SET total_rounds = total_rounds + 1 WHERE id=?", (user_id,))
        log_bet(c, user_id, 'Classic Slots', bet, win, currency)
        conn.commit(); conn.close()
        return jsonify({'reels': reels, 'multiplier': mult, 'win': win, 'balance': new_bal})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/slots/lucky7', methods=['POST'])
def slots_lucky7():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id']); bet = float(data['bet']); currency = data.get('currency', 'USD')
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        if bal < bet: conn.close(); return jsonify({'error': 'Недостаточно средств'}), 400
        
        symbols = ['🍒', '🍋', '7️⃣']; weights = [45, 40, 15]; reels = []
        for _ in range(3):
            r = random.randint(1, 100); acc = 0
            for sym, w in zip(symbols, weights):
                acc += w
                if r <= acc: reels.append(sym); break
        sevens = reels.count('7️⃣')
        if sevens == 3: mult = 30
        elif sevens == 2: mult = 5
        elif sevens == 1: mult = 1.5
        elif reels[0] == reels[1] == reels[2]: mult = 3
        else: mult = 0
        win = round(bet * mult, 2); new_bal = round(bal - bet + win, 2)
        set_user_bal(c, user_id, currency, new_bal, win)
        c.execute("UPDATE users SET total_rounds = total_rounds + 1 WHERE id=?", (user_id,))
        log_bet(c, user_id, 'Lucky 7', bet, win, currency)
        conn.commit(); conn.close()
        return jsonify({'reels': reels, 'multiplier': mult, 'win': win, 'balance': new_bal})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/slots/bookofra', methods=['POST'])
def slots_bookofra():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id']); bet = float(data['bet']); currency = data.get('currency', 'USD')
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        if bal < bet: conn.close(); return jsonify({'error': 'Недостаточно средств'}), 400
        
        symbols = ['🐈', '👁️', '🏺', '🗿', '🐍', '📖']
        weights = [30, 25, 20, 15, 8, 2]
        reels = []
        for _ in range(5):
            r = random.randint(1, 100); acc = 0
            for sym, w in zip(symbols, weights):
                acc += w
                if r <= acc: reels.append(sym); break
                
        counts = {sym: reels.count(sym) for sym in symbols}
        mult = 0
        for sym, count in counts.items():
            if count == 5: mult += 100 if sym == '📖' else 50
            elif count == 4: mult += 20 if sym == '📖' else 10
            elif count == 3: mult += 5 if sym == '📖' else 3
            elif count == 2 and sym == '📖': mult += 2
            
        win = round(bet * mult, 2); new_bal = round(bal - bet + win, 2)
        set_user_bal(c, user_id, currency, new_bal, win)
        c.execute("UPDATE users SET total_rounds = total_rounds + 1 WHERE id=?", (user_id,))
        log_bet(c, user_id, 'Book of Ra', bet, win, currency)
        conn.commit(); conn.close()
        return jsonify({'reels': reels, 'multiplier': mult, 'win': win, 'balance': new_bal})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/roulette/spin', methods=['POST'])
def roulette_spin():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id']); bet = float(data['bet']); bet_type = data.get('bet_type'); bet_value = data.get('bet_value'); currency = data.get('currency', 'USD')
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        if bal < bet: conn.close(); return jsonify({'error': 'Недостаточно средств'}), 400
        
        result = random.randint(0, 36)
        color = 'green' if result == 0 else ('red' if result in RED_ROULETTE_NUMBERS else 'black')
        profit = -bet
        if bet_type == 'number' and result == bet_value: profit = bet * 35
        elif bet_type == 'red' and color == 'red': profit = bet
        elif bet_type == 'black' and color == 'black': profit = bet
        elif bet_type == 'odd' and result != 0 and result % 2 == 1: profit = bet
        elif bet_type == 'even' and result != 0 and result % 2 == 0: profit = bet
        elif bet_type == 'low' and 1 <= result <= 18: profit = bet
        elif bet_type == 'high' and 19 <= result <= 36: profit = bet
        elif bet_type == 'dozen1' and 1 <= result <= 12: profit = bet * 2
        elif bet_type == 'dozen2' and 13 <= result <= 24: profit = bet * 2
        elif bet_type == 'dozen3' and 25 <= result <= 36: profit = bet * 2
        elif bet_type == 'column1' and result != 0 and result % 3 == 1: profit = bet * 2
        elif bet_type == 'column2' and result != 0 and result % 3 == 2: profit = bet * 2
        elif bet_type == 'column3' and result != 0 and result % 3 == 0: profit = bet * 2
        
        new_bal = round(bal + profit, 2)
        set_user_bal(c, user_id, currency, new_bal, profit)
        c.execute("UPDATE users SET total_rounds = total_rounds + 1 WHERE id=?", (user_id,))
        log_bet(c, user_id, 'Roulette', bet, max(0, profit+bet), currency)
        conn.commit(); conn.close()
        return jsonify({'number': result, 'color': color, 'profit': profit, 'balance': new_bal})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/mines/start', methods=['POST'])
def mines_start():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id']); bet = float(data['bet']); mines = int(data['mines']); currency = data.get('currency', 'USD')
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        if bal < bet: conn.close(); return jsonify({'error': 'Недостаточно средств'}), 400
        
        new_bal = round(bal - bet, 2)
        set_user_bal(c, user_id, currency, new_bal)
        c.execute("UPDATE users SET total_rounds = total_rounds + 1 WHERE id=?", (user_id,))
        conn.commit(); conn.close()
        
        round_id = secrets.token_hex(8)
        MINES_ROUNDS[round_id] = {'user_id': user_id, 'bet': bet, 'mines': set(random.sample(range(25), mines)), 'revealed': set(), 'done': False, 'currency': currency}
        return jsonify({'round_id': round_id, 'balance': new_bal, 'multiplier': 1.0})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/mines/reveal', methods=['POST'])
def mines_reveal():
    try:
        data = request.get_json(silent=True) or {}
        round_id = data.get('round_id'); cell = int(data['cell']); user_id = int(data['id'])
        rd = MINES_ROUNDS.get(round_id)
        if not rd or rd['user_id'] != user_id or rd['done']: return jsonify({'error': 'Раунд не найден'}), 404
        if cell in rd['mines']:
            rd['done'] = True
            return jsonify({'mine': True, 'mines': list(rd['mines'])})
        rd['revealed'].add(cell)
        picks = len(rd['revealed'])
        prob = 1.0
        for i in range(picks): prob *= (25 - len(rd['mines']) - i) / (25 - i)
        mult = round(0.97 / prob, 2) if prob > 0 else 0
        return jsonify({'mine': False, 'multiplier': mult, 'picks': picks})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/mines/cashout', methods=['POST'])
def mines_cashout():
    try:
        data = request.get_json(silent=True) or {}
        round_id = data.get('round_id'); user_id = int(data['id'])
        rd = MINES_ROUNDS.get(round_id)
        if not rd or rd['user_id'] != user_id or rd['done']: return jsonify({'error': 'Раунд не найден'}), 404
        picks = len(rd['revealed'])
        if picks == 0: return jsonify({'error': 'Откройте клетку'}), 400
        rd['done'] = True
        prob = 1.0
        for i in range(picks): prob *= (25 - len(rd['mines']) - i) / (25 - i)
        mult = round(0.97 / prob, 2) if prob > 0 else 0
        win = round(rd['bet'] * mult, 2)
        
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        bal = get_user_bal(c, user_id, rd['currency'])
        new_bal = round(bal + win, 2)
        set_user_bal(c, user_id, rd['currency'], new_bal, win)
        log_bet(c, user_id, 'Mines', rd['bet'], win, rd['currency'])
        conn.commit(); conn.close()
        return jsonify({'win': win, 'multiplier': mult, 'balance': new_bal, 'mines': list(rd['mines'])})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/baccarat/play', methods=['POST'])
def baccarat_play():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id']); bet = float(data['bet']); bet_on = data.get('bet_on', 'player'); currency = data.get('currency', 'USD')
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        if bal < bet: conn.close(); return jsonify({'error': 'Недостаточно средств'}), 400
        
        p_cards = [random.choice(['2','3','4','5','6','7','8','9','10','J','Q','K','A']) for _ in range(2)]
        b_cards = [random.choice(['2','3','4','5','6','7','8','9','10','J','Q','K','A']) for _ in range(2)]
        ps = sum([1 if x=='A' else 0 if x in ('10','J','Q','K') else int(x) for x in p_cards]) % 10
        bs = sum([1 if x=='A' else 0 if x in ('10','J','Q','K') else int(x) for x in b_cards]) % 10
        winner = 'player' if ps > bs else 'banker' if bs > ps else 'tie'
        profit = 0
        if bet_on == winner: profit = bet * (8 if winner=='tie' else 0.95 if winner=='banker' else 1)
        elif winner == 'tie' and bet_on != 'tie': profit = 0
        else: profit = -bet
        
        new_bal = round(bal + profit, 2)
        set_user_bal(c, user_id, currency, new_bal, profit)
        c.execute("UPDATE users SET total_rounds = total_rounds + 1 WHERE id=?", (user_id,))
        log_bet(c, user_id, 'Baccarat', bet, max(0, profit+bet), currency)
        conn.commit(); conn.close()
        return jsonify({'player_hand': p_cards, 'banker_hand': b_cards, 'player_score': ps, 'banker_score': bs, 'winner': winner, 'profit': profit, 'balance': new_bal})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/dice/roll', methods=['POST'])
def dice_roll():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id'])
        bet = float(data['bet'])
        target = float(data['target'])
        condition = data['condition']
        currency = data.get('currency', 'USD')
        
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        
        if bal < bet: 
            conn.close()
            return jsonify({'error': 'Недостаточно средств'}), 400
        
        if condition == 'over':
            win_chance = 100 - target
        else:
            win_chance = target
            
        if win_chance <= 0 or win_chance >= 100:
            conn.close()
            return jsonify({'error': 'Неверная цель'}), 400
            
        multiplier = round(99.0 / win_chance, 2)
        result = round(random.uniform(0, 99.99), 2)
        
        win = 0
        if condition == 'over' and result > target:
            win = round(bet * multiplier, 2)
        elif condition == 'under' and result < target:
            win = round(bet * multiplier, 2)
            
        new_bal = round(bal - bet + win, 2)
        set_user_bal(c, user_id, currency, new_bal, win)
        c.execute("UPDATE users SET total_rounds = total_rounds + 1 WHERE id=?", (user_id,))
        log_bet(c, user_id, 'Dice', bet, win, currency)
        conn.commit()
        conn.close()
        
        return jsonify({
            'result': result,
            'multiplier': multiplier,
            'win': win,
            'balance': new_bal
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/keno/play', methods=['POST'])
def keno_play():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id'])
        bet = float(data['bet'])
        selected_numbers = data['numbers']
        currency = data.get('currency', 'USD')
        
        if not selected_numbers or len(selected_numbers) > 10:
            return jsonify({'error': 'Выберите от 1 до 10 чисел'}), 400
            
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        
        if bal < bet: 
            conn.close()
            return jsonify({'error': 'Недостаточно средств'}), 400
        
        drawn_numbers = random.sample(range(1, 81), 20)
        matches = len(set(selected_numbers) & set(drawn_numbers))
        picked_count = len(selected_numbers)
        
        multipliers = {
            1: {1: 3.5},
            2: {2: 15},
            3: {2: 2, 3: 25},
            4: {2: 1.5, 3: 5, 4: 50},
            5: {3: 2, 4: 10, 5: 100},
            6: {3: 1.5, 4: 5, 5: 20, 6: 200},
            7: {4: 2, 5: 10, 6: 50, 7: 300},
            8: {4: 1.5, 5: 5, 6: 20, 7: 100, 8: 500},
            9: {5: 2, 6: 10, 7: 50, 8: 200, 9: 1000},
            10: {5: 1.5, 6: 5, 7: 20, 8: 100, 9: 500, 10: 2000}
        }
        
        mult = 0
        if picked_count in multipliers and matches in multipliers[picked_count]:
            mult = multipliers[picked_count][matches]
            
        win = round(bet * mult, 2)
        new_bal = round(bal - bet + win, 2)
        
        set_user_bal(c, user_id, currency, new_bal, win)
        c.execute("UPDATE users SET total_rounds = total_rounds + 1 WHERE id=?", (user_id,))
        log_bet(c, user_id, 'Keno', bet, win, currency)
        conn.commit()
        conn.close()
        
        return jsonify({
            'drawn': drawn_numbers,
            'matches': matches,
            'multiplier': mult,
            'win': win,
            'balance': new_bal
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/wheel/spin', methods=['POST'])
def wheel_spin():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id'])
        bet = float(data['bet'])
        currency = data.get('currency', 'USD')
        
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        
        if bal < bet: 
            conn.close()
            return jsonify({'error': 'Недостаточно средств'}), 400
        
        segments = [1.5, 0, 2, 0, 1.5, 5, 0, 2, 0, 10]
        result_idx = random.randint(0, 9)
        mult = segments[result_idx]
        
        win = round(bet * mult, 2)
        new_bal = round(bal - bet + win, 2)
        
        set_user_bal(c, user_id, currency, new_bal, win)
        c.execute("UPDATE users SET total_rounds = total_rounds + 1 WHERE id=?", (user_id,))
        log_bet(c, user_id, 'Wheel', bet, win, currency)
        conn.commit()
        conn.close()
        
        return jsonify({
            'index': result_idx,
            'multiplier': mult,
            'win': win,
            'balance': new_bal
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/')
def index():
    return send_file('index.html')

@app.errorhandler(404)
def not_found(e): return jsonify({'error': 'not_found'}), 404

@bot.message_handler(commands=['start'])
def start(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💎 Открыть MADGEK CASINO", web_app=types.WebAppInfo(url=WEBAPP_URL)))
    bot.send_message(message.chat.id, "Добро пожаловать в MADGEK CASINO!", reply_markup=markup)

if __name__ == "__main__":
    import threading
    try: bot.remove_webhook()
    except: pass
    threading.Thread(target=bot.infinity_polling, kwargs={'skip_pending': True}).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), threaded=True)
