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
ADMIN_PENDING = {}

VIP_LEVELS = [(1, 2000, 200), (2, 7000, 700), (3, 20000, 2000), (4, 50000, 5000)]
VIP_NAMES = {0: '—', 1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}

def init_db():
    conn = sqlite3.connect('casino.db', timeout=15)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, username TEXT, balance REAL, total_rounds INTEGER, best_result REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, method TEXT, amount REAL, status TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS bets_history 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, game TEXT, bet REAL, win REAL, currency TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    for col, typ in [
        ('phone', 'TEXT'), ('reg_name', 'TEXT'), ('registered', 'INTEGER DEFAULT 0'), 
        ('balance_uah', 'REAL DEFAULT 0'), ('email', 'TEXT'), 
        ('kyc_name', 'TEXT'), ('kyc_country', 'TEXT'), ('kyc_status', 'TEXT DEFAULT "Not passed"'),
        ('bonus_status', 'TEXT DEFAULT "none"'),
        ('bonus_amount', 'REAL DEFAULT 0'),
        ('bonus_wager_required', 'REAL DEFAULT 0'),
        ('bonus_wager_progress', 'REAL DEFAULT 0'),
        ('bonus_currency', 'TEXT DEFAULT "USD"'),
        ('bonus_withdraw_done', 'INTEGER DEFAULT 0'),
        ('blocked', 'INTEGER DEFAULT 0'),
        ('first_deposit_done', 'INTEGER DEFAULT 0'),
        ('vip_turnover', 'REAL DEFAULT 0'),
        ('vip_level', 'INTEGER DEFAULT 0')
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

BLOCKED_WHITELIST = {'/api/save', '/api/support', '/api/kyc', '/api/register'}

@app.before_request
def check_blocked_user():
    if request.method != 'POST':
        return
    if not request.path.startswith('/api/'):
        return
    if request.path in BLOCKED_WHITELIST:
        return
    try:
        data = request.get_json(silent=True) or {}
        uid = data.get('id')
        if uid:
            conn = sqlite3.connect('casino.db', timeout=15)
            c = conn.cursor()
            c.execute("SELECT blocked FROM users WHERE id=?", (int(uid),))
            r = c.fetchone()
            conn.close()
            if r and r[0]:
                return jsonify({'error': 'Аккаунт заблокирован'}), 403
    except Exception:
        pass

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

def _track_vip(c, user_id, usd_bet):
    try:
        c.execute("SELECT vip_turnover, vip_level FROM users WHERE id=?", (user_id,))
        vr = c.fetchone()
        if not vr:
            return 0
        new_turnover = round((vr[0] or 0) + usd_bet, 2)
        current_level = vr[1] or 0
        new_level = current_level
        total_reward = 0
        for lvl, thr, reward in VIP_LEVELS:
            if new_turnover >= thr and current_level < lvl:
                new_level = lvl
                total_reward += reward
        if total_reward > 0:
            c.execute("UPDATE users SET vip_turnover=?, vip_level=?, balance=balance+? WHERE id=?",
                      (new_turnover, new_level, total_reward, user_id))
            try:
                bot.send_message(user_id,
                    f"🏆 Поздравляем! Вы достигли статуса VIP <b>{VIP_NAMES[new_level]}</b>!\n"
                    f"💰 Бонус ${total_reward:.2f} зачислен на баланс.\n"
                    f"📊 Оборот: ${new_turnover:.2f}", parse_mode='HTML')
            except Exception as e: print("VIP user notify error:", e)
            try:
                bot.send_message(STATS_CHAT_ID,
                    f"🏆 <b>VIP UPGRADE</b>\nID: {user_id}\nУровень: {VIP_NAMES[new_level]}\nБонус: ${total_reward:.2f}\nОборот: ${new_turnover:.2f}",
                    parse_mode='HTML')
            except Exception as e: print("VIP admin notify error:", e)
        else:
            c.execute("UPDATE users SET vip_turnover=? WHERE id=?", (new_turnover, user_id))
        return total_reward
    except Exception as e:
        print("VIP track error:", e)
        return 0

def _track_bonus_wager(c, user_id, amount, currency):
    try:
        c.execute("SELECT bonus_status, bonus_wager_required, bonus_wager_progress, bonus_currency FROM users WHERE id=?", (user_id,))
        br = c.fetchone()
        if br and br[0] == 'active' and br[3] == currency:
            new_progress = round((br[2] or 0) + amount, 2)
            req = br[1] or 0
            if new_progress >= req:
                c.execute("UPDATE users SET bonus_wager_progress=?, bonus_status='completed' WHERE id=?", (new_progress, user_id))
            else:
                c.execute("UPDATE users SET bonus_wager_progress=? WHERE id=?", (new_progress, user_id))
    except Exception as e:
        print("wager track error:", e)

def log_bet(c, user_id, game, bet, win, currency):
    c.execute("INSERT INTO bets_history (user_id, game, bet, win, currency) VALUES (?, ?, ?, ?, ?)", (user_id, game, bet, win, currency))
    _track_bonus_wager(c, user_id, bet, currency)
    usd_bet = bet if currency == 'USD' else round(bet / 40.0, 2)
    _track_vip(c, user_id, usd_bet)

# ============ ОБРАБОТЧИКИ КНОПОК ============
@bot.callback_query_handler(func=lambda call: call.data.startswith(('app_dep', 'rej_dep', 'app_wd', 'rej_wd')))
def handle_admin_action(call):
    try: bot.answer_callback_query(call.id, "⏳ Обработка...")
    except Exception: pass
    if str(call.from_user.id) != str(ADMIN_ID): return
    try:
        parts = call.data.split('_')
        action, tx_type, tx_id = parts[0], parts[1], int(parts[2])
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        c.execute("SELECT user_id, amount, status, currency FROM transactions WHERE id=?", (tx_id,))
        tx = c.fetchone()
        if not tx or tx[2] != 'Pending':
            conn.close(); return
        user_id, amount = int(tx[0]), float(tx[1])
        tx_curr = tx[3] if tx[3] else 'USD'
        status_line = ""; user_msg = ""
        sym = '₴' if tx_curr == 'UAH' else '$'
        if action == 'app' and tx_type == 'dep':
            if tx_curr == 'UAH':
                c.execute("UPDATE users SET balance_uah = balance_uah + ? WHERE id=?", (amount, user_id))
            else:
                c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
            c.execute("UPDATE transactions SET status = 'Completed' WHERE id=?", (tx_id,))
            status_line = f"✅ Пополнение #{tx_id} одобрено"
            user_msg = f"✅ Ваш депозит на {amount}{sym} одобрен! Баланс пополнен."
            try:
                c.execute("SELECT bonus_status, first_deposit_done FROM users WHERE id=?", (user_id,))
                br = c.fetchone()
                if br and br[0] == 'pending' and not br[1]:
                    cap = 100.0 if tx_curr == 'USD' else 4000.0
                    bonus_amount = round(min(amount, cap), 2)
                    wager_req = round(bonus_amount * 15, 2)
                    if tx_curr == 'UAH':
                        c.execute("UPDATE users SET balance_uah = balance_uah + ?, bonus_status='active', bonus_amount=?, bonus_wager_required=?, bonus_wager_progress=0, bonus_currency=?, first_deposit_done=1 WHERE id=?",
                                  (bonus_amount, bonus_amount, wager_req, tx_curr, user_id))
                    else:
                        c.execute("UPDATE users SET balance = balance + ?, bonus_status='active', bonus_amount=?, bonus_wager_required=?, bonus_wager_progress=0, bonus_currency=?, first_deposit_done=1 WHERE id=?",
                                  (bonus_amount, bonus_amount, wager_req, tx_curr, user_id))
                    user_msg += f"\n\n🎁 Приветственный бонус {bonus_amount}{sym} зачислен! Отыграйте вейджер x15 ({wager_req}{sym})."
                    status_line += f" + бонус {bonus_amount}{sym}"
            except Exception as e: print("bonus apply error:", e)
            try: c.execute("UPDATE users SET first_deposit_done=1 WHERE id=?", (user_id,))
            except Exception: pass
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
        conn.commit(); conn.close()
        try:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🎰 Открыть казино", web_app=types.WebAppInfo(url=WEBAPP_URL)))
            bot.send_message(user_id, user_msg + "\n\nОткройте казино — баланс уже обновлён.", reply_markup=markup)
        except Exception as e: print("notify user error:", e)
        try:
            new_text = call.message.text + "\n\n" + status_line
            bot.edit_message_text(new_text, call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception as e: print("edit_message error:", e)
    except Exception as e: print("Callback error:", e)

@bot.callback_query_handler(func=lambda call: call.data.startswith(('app_kyc', 'rej_kyc')))
def handle_kyc_action(call):
    try: bot.answer_callback_query(call.id, "⏳ Обработка KYC...")
    except Exception: pass
    if str(call.from_user.id) != str(ADMIN_ID): return
    try:
        parts = call.data.split('_')
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
        conn.commit(); conn.close()
        try:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🎰 Открыть казино", web_app=types.WebAppInfo(url=WEBAPP_URL)))
            bot.send_message(user_id, user_msg, reply_markup=markup)
        except Exception as e: print("notify user error (KYC):", e)
        try:
            new_text = call.message.text + "\n\n" + status_line
            bot.edit_message_text(new_text, call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception as e: print("edit_message error (KYC):", e)
    except Exception as e: print("Callback error (KYC):", e)

# ============ СТАТИСТИКА ============
@bot.message_handler(commands=['stats'])
def send_stats(message):
    if message.from_user.id != ADMIN_ID: return
    ADMIN_PENDING.pop(message.from_user.id, None)
    conn = sqlite3.connect('casino.db', timeout=15)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users"); total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE registered=1"); reg_users = c.fetchone()[0]
    c.execute("SELECT SUM(balance), SUM(balance_uah) FROM users")
    sum_bal = c.fetchone(); sum_usd = sum_bal[0] or 0; sum_uah = sum_bal[1] or 0
    c.execute("SELECT SUM(amount) FROM transactions WHERE type='Deposit' AND status='Completed' AND currency='USD'")
    dep_usd = c.fetchone()[0] or 0
    c.execute("SELECT SUM(amount) FROM transactions WHERE type='Deposit' AND status='Completed' AND currency='UAH'")
    dep_uah = c.fetchone()[0] or 0
    c.execute("SELECT SUM(amount) FROM transactions WHERE type='Withdrawal' AND status='Completed' AND currency='USD'")
    wd_usd = c.fetchone()[0] or 0
    c.execute("SELECT SUM(amount) FROM transactions WHERE type='Withdrawal' AND status='Completed' AND currency='UAH'")
    wd_uah = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM transactions WHERE type='Deposit' AND status='Pending'"); pend_dep = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM transactions WHERE type='Withdrawal' AND status='Pending'"); pend_wd = c.fetchone()[0]
    c.execute("SELECT game, COUNT(*) as cnt, SUM(bet) as total_bet FROM bets_history GROUP BY game ORDER BY cnt DESC LIMIT 5")
    top_games = c.fetchall()
    c.execute("SELECT SUM(bet), SUM(win) FROM bets_history")
    totals = c.fetchone(); total_bet = totals[0] or 0; total_win = totals[1] or 0
    casino_profit = total_bet - total_win
    c.execute("SELECT COUNT(*) FROM users WHERE vip_level >= 1"); vip_bronze = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE vip_level >= 2"); vip_silver = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE vip_level >= 3"); vip_gold = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE vip_level >= 4"); vip_plat = c.fetchone()[0]
    conn.close()
    text = f"📊 <b>СТАТИСТИКА КАЗИНО</b>\n\n"
    text += f"👥 Всего: <b>{total_users}</b> | Зарегистрировано: <b>{reg_users}</b>\n\n"
    text += f"💰 <b>Балансы:</b> USD ${sum_usd:.2f} | UAH ₴{sum_uah:.2f}\n\n"
    text += f"📥 <b>Депозиты:</b> USD ${dep_usd:.2f} | UAH ₴{dep_uah:.2f}\n"
    text += f"📤 <b>Выплаты:</b> USD ${wd_usd:.2f} | UAH ₴{wd_uah:.2f}\n\n"
    text += f"⏳ <b>Ожидают:</b> Депозиты {pend_dep} | Выводы {pend_wd}\n\n"
    text += f"🏆 <b>VIP:</b> B{vip_bronze} S{vip_silver} G{vip_gold} P{vip_plat}\n\n"
    text += f"🎮 <b>ТОП-5 игр:</b>\n"
    if top_games:
        for g in top_games: text += f"  {g[0]}: {g[1]} ст., ${g[2]:.0f}\n"
    else: text += f"  Пока нет ставок\n"
    text += f"\n💵 Оборот: ${total_bet:.2f}\n🏆 Выплачено: ${total_win:.2f}\n🏦 Профит: ${casino_profit:.2f}"
    bot.send_message(STATS_CHAT_ID, text, parse_mode='HTML')
    if message.chat.id != STATS_CHAT_ID:
        bot.reply_to(message, "✅ Отправлено!")

# ============ ADMIN PANEL ============
def show_admin_users(chat_id, page=0):
    conn = sqlite3.connect('casino.db', timeout=15)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE registered=1"); total = c.fetchone()[0]
    c.execute("""SELECT id, username, reg_name, phone, email, balance, balance_uah, blocked, bonus_status, vip_level, vip_turnover 
                 FROM users WHERE registered=1 ORDER BY id DESC LIMIT 8 OFFSET ?""", (page*8,))
    users = c.fetchall(); conn.close()
    total_pages = max(1, (total + 7) // 8)
    text = f"👥 <b>Админ-панель</b>\nВсего: <b>{total}</b> | Стр. {page+1}/{total_pages}\n\n"
    if not users:
        text += "Пусто."; bot.send_message(chat_id, text, parse_mode='HTML'); return
    markup = types.InlineKeyboardMarkup()
    for u in users:
        uid, uname, rname, phone, email, bal, bal_uah, blk, bst, vlvl, vturn = u
        status = "🚫 BLK" if blk else "✅"
        bonus_icon = ""
        if bst == 'active': bonus_icon = " 🎁"
        elif bst == 'completed': bonus_icon = " 🏆"
        elif bst == 'pending': bonus_icon = " ⏳"
        vip_icon = f" | VIP:{VIP_NAMES[vlvl or 0]}(${(vturn or 0):.0f})" if vlvl else ""
        text += f"{status} <b>ID {uid}</b>{bonus_icon}{vip_icon}\n"
        text += f"  👤 {rname or uname or '—'}\n  📱 {phone or '—'}\n"
        text += f"  💰 ${(bal or 0):.2f} / ₴{(bal_uah or 0):.2f}\n\n"
        blk_label = f"✅ {uid}" if blk else f"🚫 {uid}"
        markup.add(types.InlineKeyboardButton(blk_label, callback_data=f"togblk_{uid}"),
                   types.InlineKeyboardButton(f"💰 {uid}", callback_data=f"addbal_{uid}"))
    nav = []
    if page > 0: nav.append(types.InlineKeyboardButton("⬅️", callback_data=f"adminpage_{page-1}"))
    if page < total_pages - 1: nav.append(types.InlineKeyboardButton("➡️", callback_data=f"adminpage_{page+1}"))
    if nav: markup.add(*nav)
    markup.add(types.InlineKeyboardButton("🔄", callback_data=f"adminpage_{page}"))
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != ADMIN_ID: return
    ADMIN_PENDING.pop(message.from_user.id, None)
    show_admin_users(message.chat.id, page=0)

@bot.callback_query_handler(func=lambda call: call.data.startswith('adminpage_'))
def admin_page_cb(call):
    if call.from_user.id != ADMIN_ID: return
    try: page = int(call.data.split('_')[1])
    except: page = 0
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except: pass
    show_admin_users(call.message.chat.id, page=page)
    try: bot.answer_callback_query(call.id)
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('togblk_'))
def toggle_block_cb(call):
    if call.from_user.id != ADMIN_ID: return
    try: uid = int(call.data.split('_')[1])
    except: return
    conn = sqlite3.connect('casino.db', timeout=15)
    c = conn.cursor()
    c.execute("SELECT blocked FROM users WHERE id=?", (uid,))
    r = c.fetchone()
    if not r:
        conn.close(); bot.answer_callback_query(call.id, "❌"); return
    new_state = 0 if r[0] else 1
    c.execute("UPDATE users SET blocked=? WHERE id=?", (new_state, uid))
    conn.commit(); conn.close()
    msg = "🚫 Заблокирован" if new_state else "✅ Разблокирован"
    try: bot.answer_callback_query(call.id, msg)
    except: pass
    try: bot.send_message(call.message.chat.id, f"{msg} ID: {uid}")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('addbal_'))
def add_balance_start_cb(call):
    if call.from_user.id != ADMIN_ID: return
    try: uid = int(call.data.split('_')[1])
    except: return
    ADMIN_PENDING[call.from_user.id] = {'action': 'add_balance', 'user_id': uid}
    try: bot.answer_callback_query(call.id, "💰 Сумма?")
    except: pass
    bot.send_message(call.message.chat.id, f"💰 Сумма USD для ID <b>{uid}</b>.\nМинус = списание. /cancel — отмена.", parse_mode='HTML')

@bot.message_handler(commands=['cancel'])
def cancel_pending(message):
    if message.from_user.id != ADMIN_ID: return
    if ADMIN_PENDING.pop(message.from_user.id, None): bot.send_message(message.chat.id, "❌ Отменено.")
    else: bot.send_message(message.chat.id, "Нечего отменять.")

@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and m.from_user.id in ADMIN_PENDING and not m.text.startswith('/'))
def handle_pending_admin_action(message):
    pending = ADMIN_PENDING.get(message.from_user.id)
    if not pending: return
    if pending.get('action') == 'add_balance':
        try: amt = float((message.text or '').replace(',', '.').strip())
        except: bot.send_message(message.chat.id, "❌ Число или /cancel"); return
        uid = pending['user_id']
        conn = sqlite3.connect('casino.db', timeout=15)
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE id=?", (uid,))
        if not c.fetchone():
            conn.close(); ADMIN_PENDING.pop(message.from_user.id, None)
            bot.send_message(message.chat.id, "❌ Не найден"); return
        c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amt, uid))
        c.execute("INSERT INTO transactions (user_id, type, method, amount, status, currency) VALUES (?, 'Deposit', 'Manual', ?, 'Completed', 'USD')", (uid, amt))
        c.execute("SELECT balance FROM users WHERE id=?", (uid,))
        new_bal = c.fetchone()[0]
        conn.commit(); conn.close()
        ADMIN_PENDING.pop(message.from_user.id, None)
        bot.send_message(message.chat.id, f"✅ ${amt:.2f} → {uid}\nБаланс: ${new_bal:.2f}")
        try:
            if amt > 0: bot.send_message(uid, f"💰 Начислено ${amt:.2f}.")
            else: bot.send_message(uid, f"⚠️ Списано ${abs(amt):.2f}.")
        except: pass

# ============ API ============
@app.route('/api/user', methods=['GET'])
def get_user():
    user_id = request.args.get('id')
    username = request.args.get('username', 'Игрок')
    if not user_id: return jsonify({'error': 'No ID'}), 400
    conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
    c.execute("SELECT balance, total_rounds, best_result, phone, reg_name, registered, balance_uah, email, kyc_name, kyc_country, kyc_status, vip_turnover, vip_level FROM users WHERE id=?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT INTO users (id, username, balance, total_rounds, best_result, registered, balance_uah) VALUES (?, ?, ?, ?, ?, 0, ?)", (user_id, username, 0.0, 0, 0.0, 0.0))
        conn.commit()
        user = (0.0, 0, 0.0, None, None, 0, 0.0, None, None, None, "Not passed", 0.0, 0)
    conn.close()
    return jsonify({
        'balance': user[0], 'total_rounds': user[1], 'best_result': user[2],
        'phone': user[3], 'reg_name': user[4], 'registered': bool(user[5]),
        'balance_uah': user[6], 'email': user[7], 
        'kyc_name': user[8], 'kyc_country': user[9], 'kyc_status': user[10],
        'vip_turnover': user[11] or 0, 'vip_level': user[12] or 0
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
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        c.execute("SELECT id FROM users WHERE id=?", (user_id,))
        if not c.fetchone():
            c.execute("INSERT INTO users (id, balance, balance_uah, registered) VALUES (?, 0.0, 0.0, 0)", (user_id,))
        c.execute("SELECT id FROM users WHERE phone=? AND id!=?", (phone, user_id))
        if c.fetchone():
            conn.close(); return jsonify({'error': 'Этот номер уже зарегистрирован'}), 400
        c.execute("UPDATE users SET phone=?, reg_name=?, email=?, registered=1 WHERE id=?", (phone, name, email, user_id))
        conn.commit(); conn.close()
        try: bot.send_message(STATS_CHAT_ID, f"👤 <b>Регистрация</b>\nID: {user_id}\nИмя: {name}\nТел: {phone}\nEmail: {email}", parse_mode='HTML')
        except: pass
        return jsonify({'status': 'ok'})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/kyc', methods=['POST'])
def submit_kyc():
    try:
        data = request.json or {}
        user_id = int(data['id'])
        kyc_name = (data.get('kyc_name') or '').strip()
        kyc_country = (data.get('kyc_country') or '').strip()
        if len(kyc_name) < 3 or len(kyc_country) < 2:
            return jsonify({'error': 'Неверные данные KYC'}), 400
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        c.execute("UPDATE users SET kyc_name=?, kyc_country=?, kyc_status='Pending' WHERE id=?", (kyc_name, kyc_country, user_id))
        conn.commit(); conn.close()
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅", callback_data=f"app_kyc_{user_id}"),
                   types.InlineKeyboardButton("❌", callback_data=f"rej_kyc_{user_id}"))
        try: bot.send_message(STATS_CHAT_ID, f"🛡️ <b>KYC</b>\nID: {user_id}\nФИО: {kyc_name}\nСтрана: {kyc_country}", reply_markup=markup, parse_mode='HTML')
        except: pass
        return jsonify({'status': 'ok'})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/save', methods=['POST'])
def save_user():
    data = request.json or {}
    user_id = data['id']
    delta_usd = data.get('balance_delta_usd', None)
    delta_uah = data.get('balance_delta_uah', 0) or 0
    bet_delta_usd = float(data.get('bet_delta_usd', 0) or 0)
    conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
    if delta_usd is not None:
        if delta_usd != 0 or delta_uah != 0:
            c.execute("UPDATE users SET balance = balance + ?, balance_uah = balance_uah + ? WHERE id=?", (delta_usd, delta_uah, user_id))
    else:
        c.execute("UPDATE users SET balance=?, balance_uah=? WHERE id=?", (data.get('balance', 0), data.get('balance_uah', 0), user_id))
    if bet_delta_usd > 0:
        try: _track_vip(c, user_id, bet_delta_usd)
        except Exception as e: print("VIP track error:", e)
    c.execute("UPDATE users SET total_rounds=MAX(total_rounds, ?), best_result=MAX(best_result, ?) WHERE id=?",
              (data.get('total_rounds', 0) or 0, data.get('best_result', 0) or 0, user_id))
    conn.commit(); conn.close()
    return jsonify({'status': 'ok'})

@app.route('/api/transactions', methods=['GET'])
def get_transactions():
    user_id = request.args.get('id')
    conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
    c.execute("SELECT type, method, amount, status, date, currency FROM transactions WHERE user_id=? ORDER BY date DESC LIMIT 20", (user_id,))
    rows = c.fetchall(); conn.close()
    return jsonify([{'type': r[0], 'method': r[1], 'amount': r[2], 'status': r[3], 'date': r[4], 'currency': r[5]} for r in rows])

@app.route('/api/bets', methods=['GET'])
def get_bets():
    user_id = request.args.get('id')
    conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
    c.execute("SELECT game, bet, win, currency, date FROM bets_history WHERE user_id=? ORDER BY date DESC LIMIT 30", (user_id,))
    rows = c.fetchall(); conn.close()
    return jsonify([{'game': r[0], 'bet': r[1], 'win': r[2], 'currency': r[3], 'date': r[4]} for r in rows])

@app.route('/api/deposit', methods=['POST'])
def deposit():
    try:
        data = request.json
        user_id, amount, method = int(data['id']), float(data['amount']), data.get('method', 'USDT')
        currency = data.get('currency', 'USD')
        min_limits = {'USDT': 15.0, 'BTC': 50.0, 'ETH': 20.0, 'UAH': 500.0}
        if amount < min_limits.get(method, 15.0):
            return jsonify({'error': f'Минимум для {method}: {min_limits.get(method, 15.0)}'}), 400
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        c.execute("SELECT registered FROM users WHERE id=?", (user_id,))
        reg = c.fetchone()
        if not reg or not reg[0]:
            conn.close(); return jsonify({'error': 'Сначала зарегистрируйтесь'}), 400
        c.execute("INSERT INTO transactions (user_id, type, method, amount, status, currency) VALUES (?, 'Deposit', ?, ?, 'Pending', ?)", (user_id, method, amount, currency))
        tx_id = c.lastrowid
        conn.commit(); conn.close()
        sym = 'грн' if currency == 'UAH' else '$'
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅ Одобрить", callback_data=f"app_dep_{tx_id}"),
                   types.InlineKeyboardButton("❌ Отклонить", callback_data=f"rej_dep_{tx_id}"))
        bot.send_message(STATS_CHAT_ID, f"💰 <b>Депозит #{tx_id}</b>\nID: {user_id}\nСумма: {amount} {sym}\nСеть: {method}", reply_markup=markup, parse_mode='HTML')
        return jsonify({'status': 'ok'})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/withdraw', methods=['POST'])
def withdraw():
    try:
        data = request.json
        user_id, amount, method = int(data['id']), float(data['amount']), data.get('method', 'USDT')
        currency = data.get('currency', 'USD')
        wallet = (data.get('wallet') or '').strip()
        if len(wallet) < 5:
            return jsonify({'error': 'Укажите реквизиты'}), 400
        min_limits = {'USDT': 15.0, 'BTC': 50.0, 'ETH': 20.0, 'UAH': 500.0}
        if amount < min_limits.get(method, 15.0):
            return jsonify({'error': f'Минимум для {method}: {min_limits.get(method, 15.0)}'}), 400
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        c.execute("SELECT balance, balance_uah, registered FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        if not row: return jsonify({'error': 'Не найден'}), 404
        if not row[2]: return jsonify({'error': 'Сначала зарегистрируйтесь'}), 400
        c.execute("SELECT bonus_status, bonus_amount FROM users WHERE id=?", (user_id,))
        br = c.fetchone()
        if br and br[0] == 'active':
            conn.close(); return jsonify({'error': 'Сначала отыграйте бонус (x15)'}), 400
        if br and br[0] == 'completed':
            max_wd = (br[1] or 0) * 2
            if amount > max_wd:
                conn.close(); cap = '₴' if currency == 'UAH' else '$'
                return jsonify({'error': f'Макс. вывод после бонуса: {cap}{max_wd:.2f}'}), 400
        bal = row[1] if currency == 'UAH' else row[0]
        if bal >= amount:
            if currency == 'UAH': c.execute("UPDATE users SET balance_uah = balance_uah - ? WHERE id=?", (amount, user_id))
            else: c.execute("UPDATE users SET balance = balance - ? WHERE id=?", (amount, user_id))
            c.execute("INSERT INTO transactions (user_id, type, method, amount, status, currency, wallet) VALUES (?, 'Withdrawal', ?, ?, 'Pending', ?, ?)", (user_id, method, amount, currency, wallet))
            tx_id = c.lastrowid
            conn.commit(); conn.close()
            sym = 'грн' if currency == 'UAH' else '$'
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ Выплачено", callback_data=f"app_wd_{tx_id}"),
                       types.InlineKeyboardButton("❌ Отклонить", callback_data=f"rej_wd_{tx_id}"))
            bot.send_message(STATS_CHAT_ID, f"💸 <b>Вывод #{tx_id}</b>\nID: {user_id}\nСумма: {amount} {sym}\nСеть: {method}\nРеквизиты: <code>{wallet}</code>", reply_markup=markup, parse_mode='HTML')
            return jsonify({'status': 'ok'})
        else:
            conn.close(); return jsonify({'error': f'Недостаточно. Баланс: {bal}'}), 400
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/support', methods=['POST'])
def support():
    data = request.json
    text = f"📩 <b>Поддержка</b>\nОт: @{data.get('username')} (ID: {data.get('id')})\n\n{data.get('message')}"
    try:
        bot.send_message(STATS_CHAT_ID, text, parse_mode='HTML')
        return jsonify({'status': 'ok'})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/bonus/status', methods=['GET'])
def bonus_status():
    try:
        user_id = request.args.get('id')
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        c.execute("SELECT bonus_status, bonus_amount, bonus_wager_required, bonus_wager_progress, bonus_currency, first_deposit_done FROM users WHERE id=?", (user_id,))
        r = c.fetchone(); conn.close()
        if not r: return jsonify({'status': 'available'})
        status, amount, wager_req, wager_prog, curr, fdd = r
        if not status or status == 'none':
            if fdd: return jsonify({'status': 'unavailable'})
            return jsonify({'status': 'available'})
        return jsonify({'status': status, 'bonus_amount': amount or 0, 'wager_required': wager_req or 0, 'wager_progress': wager_prog or 0, 'currency': curr or 'USD'})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/bonus/activate', methods=['POST'])
def bonus_activate():
    try:
        data = request.json or {}
        user_id = int(data['id'])
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        c.execute("SELECT username, reg_name, phone, bonus_status, first_deposit_done FROM users WHERE id=?", (user_id,))
        r = c.fetchone()
        if not r:
            conn.close(); return jsonify({'error': 'Не найден'}), 404
        username, reg_name, phone, bstatus, fdd = r
        if fdd:
            conn.close(); return jsonify({'error': 'Только до первого депозита'}), 400
        if bstatus and bstatus not in ('none', ''):
            conn.close(); return jsonify({'error': 'Уже активирован'}), 400
        c.execute("UPDATE users SET bonus_status='pending' WHERE id=?", (user_id,))
        conn.commit(); conn.close()
        try:
            bot.send_message(STATS_CHAT_ID, f"🎁 <b>Бонус</b>\nID: {user_id}\nИмя: {reg_name or username or '—'}\nТел: {phone or '—'}", parse_mode='HTML')
        except: pass
        return jsonify({'status': 'ok'})
    except Exception as e: return jsonify({'error': str(e)}), 500

# ============ LIVE ROULETTE ============
@app.route('/api/live/roulette/spin', methods=['POST'])
def live_roulette_spin():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id'])
        bet = float(data['bet'])
        bet_type = data.get('bet_type')
        bet_value = data.get('bet_value')
        currency = data.get('currency', 'USD')
        
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        if bal < bet: 
            conn.close(); return jsonify({'error': 'Недостаточно средств'}), 400
        
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
        log_bet(c, user_id, 'Live Roulette', bet, max(0, profit+bet), currency)
        c.execute(f"SELECT {'balance_uah' if currency=='UAH' else 'balance'} FROM users WHERE id=?", (user_id,))
        final_bal = c.fetchone()[0]
        conn.commit(); conn.close()
        
        return jsonify({
            'number': result, 
            'color': color, 
            'profit': profit, 
            'balance': final_bal,
            'bet_type': bet_type,
            'bet_value': bet_value,
            'win': profit > 0
        })
    except Exception as e: return jsonify({'error': str(e)}), 500

# ============ ОБЫЧНЫЕ ИГРЫ ============
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
        c.execute(f"SELECT {'balance_uah' if currency=='UAH' else 'balance'} FROM users WHERE id=?", (user_id,))
        final_bal = c.fetchone()[0]
        conn.commit(); conn.close()
        return jsonify({'reels': reels, 'multiplier': mult, 'win': win, 'balance': final_bal})
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
        c.execute(f"SELECT {'balance_uah' if currency=='UAH' else 'balance'} FROM users WHERE id=?", (user_id,))
        final_bal = c.fetchone()[0]
        conn.commit(); conn.close()
        return jsonify({'reels': reels, 'multiplier': mult, 'win': win, 'balance': final_bal})
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
        c.execute(f"SELECT {'balance_uah' if currency=='UAH' else 'balance'} FROM users WHERE id=?", (user_id,))
        final_bal = c.fetchone()[0]
        conn.commit(); conn.close()
        return jsonify({'reels': reels, 'multiplier': mult, 'win': win, 'balance': final_bal})
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
        c.execute(f"SELECT {'balance_uah' if currency=='UAH' else 'balance'} FROM users WHERE id=?", (user_id,))
        final_bal = c.fetchone()[0]
        conn.commit(); conn.close()
        return jsonify({'number': result, 'color': color, 'profit': profit, 'balance': final_bal})
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
            rd['done'] = True; return jsonify({'mine': True, 'mines': list(rd['mines'])})
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
        c.execute(f"SELECT {'balance_uah' if rd['currency']=='UAH' else 'balance'} FROM users WHERE id=?", (user_id,))
        final_bal = c.fetchone()[0]
        conn.commit(); conn.close()
        return jsonify({'win': win, 'multiplier': mult, 'balance': final_bal, 'mines': list(rd['mines'])})
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
        c.execute(f"SELECT {'balance_uah' if currency=='UAH' else 'balance'} FROM users WHERE id=?", (user_id,))
        final_bal = c.fetchone()[0]
        conn.commit(); conn.close()
        return jsonify({'player_hand': p_cards, 'banker_hand': b_cards, 'player_score': ps, 'banker_score': bs, 'winner': winner, 'profit': profit, 'balance': final_bal})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/dice/roll', methods=['POST'])
def dice_roll():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id']); bet = float(data['bet']); target = float(data['target']); condition = data['condition']; currency = data.get('currency', 'USD')
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        if bal < bet: conn.close(); return jsonify({'error': 'Недостаточно средств'}), 400
        win_chance = (100 - target) if condition == 'over' else target
        if win_chance <= 0 or win_chance >= 100:
            conn.close(); return jsonify({'error': 'Неверная цель'}), 400
        multiplier = round(99.0 / win_chance, 2)
        result = round(random.uniform(0, 99.99), 2)
        win = 0
        if condition == 'over' and result > target: win = round(bet * multiplier, 2)
        elif condition == 'under' and result < target: win = round(bet * multiplier, 2)
        new_bal = round(bal - bet + win, 2)
        set_user_bal(c, user_id, currency, new_bal, win)
        c.execute("UPDATE users SET total_rounds = total_rounds + 1 WHERE id=?", (user_id,))
        log_bet(c, user_id, 'Dice', bet, win, currency)
        c.execute(f"SELECT {'balance_uah' if currency=='UAH' else 'balance'} FROM users WHERE id=?", (user_id,))
        final_bal = c.fetchone()[0]
        conn.commit(); conn.close()
        return jsonify({'result': result, 'multiplier': multiplier, 'win': win, 'balance': final_bal})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/keno/play', methods=['POST'])
def keno_play():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id']); bet = float(data['bet']); selected_numbers = data['numbers']; currency = data.get('currency', 'USD')
        if not selected_numbers or len(selected_numbers) > 10:
            return jsonify({'error': 'От 1 до 10 чисел'}), 400
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        if bal < bet: conn.close(); return jsonify({'error': 'Недостаточно средств'}), 400
        drawn_numbers = random.sample(range(1, 81), 20)
        matches = len(set(selected_numbers) & set(drawn_numbers))
        picked_count = len(selected_numbers)
        multipliers = {
            1: {1: 3.5}, 2: {2: 15}, 3: {2: 2, 3: 25}, 4: {2: 1.5, 3: 5, 4: 50},
            5: {3: 2, 4: 10, 5: 100}, 6: {3: 1.5, 4: 5, 5: 20, 6: 200},
            7: {4: 2, 5: 10, 6: 50, 7: 300}, 8: {4: 1.5, 5: 5, 6: 20, 7: 100, 8: 500},
            9: {5: 2, 6: 10, 7: 50, 8: 200, 9: 1000},
            10: {5: 1.5, 6: 5, 7: 20, 8: 100, 9: 500, 10: 2000}
        }
        mult = 0
        if picked_count in multipliers and matches in multipliers[picked_count]:
            mult = multipliers[picked_count][matches]
        win = round(bet * mult, 2); new_bal = round(bal - bet + win, 2)
        set_user_bal(c, user_id, currency, new_bal, win)
        c.execute("UPDATE users SET total_rounds = total_rounds + 1 WHERE id=?", (user_id,))
        log_bet(c, user_id, 'Keno', bet, win, currency)
        c.execute(f"SELECT {'balance_uah' if currency=='UAH' else 'balance'} FROM users WHERE id=?", (user_id,))
        final_bal = c.fetchone()[0]
        conn.commit(); conn.close()
        return jsonify({'drawn': drawn_numbers, 'matches': matches, 'multiplier': mult, 'win': win, 'balance': final_bal})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/api/wheel/spin', methods=['POST'])
def wheel_spin():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id']); bet = float(data['bet']); currency = data.get('currency', 'USD')
        conn = sqlite3.connect('casino.db', timeout=15); c = conn.cursor()
        bal = get_user_bal(c, user_id, currency)
        if bal < bet: conn.close(); return jsonify({'error': 'Недостаточно средств'}), 400
        segments = [1.5, 0, 2, 0, 1.5, 5, 0, 2, 0, 10]
        result_idx = random.randint(0, 9)
        mult = segments[result_idx]
        win = round(bet * mult, 2); new_bal = round(bal - bet + win, 2)
        set_user_bal(c, user_id, currency, new_bal, win)
        c.execute("UPDATE users SET total_rounds = total_rounds + 1 WHERE id=?", (user_id,))
        log_bet(c, user_id, 'Wheel', bet, win, currency)
        c.execute(f"SELECT {'balance_uah' if currency=='UAH' else 'balance'} FROM users WHERE id=?", (user_id,))
        final_bal = c.fetchone()[0]
        conn.commit(); conn.close()
        return jsonify({'index': result_idx, 'multiplier': mult, 'win': win, 'balance': final_bal})
    except Exception as e: return jsonify({'error': str(e)}), 500

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
