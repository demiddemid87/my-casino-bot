import telebot
from telebot import types
import os
import sqlite3
import random
import secrets
from flask import Flask, send_file, request, jsonify

TOKEN = os.environ.get('TELEGRAM_TOKEN', '8879662302:AAGUpOTng02XIMIRZe7xf154qKaJVXakj3o')
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

ADMIN_ID = '7695713296'
WEBAPP_URL = "https://my-casino-bot-2.onrender.com"

RED_ROULETTE_NUMBERS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
MINES_ROUNDS = {}


def init_db():
    conn = sqlite3.connect('casino.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, username TEXT, balance REAL, total_rounds INTEGER, best_result REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, method TEXT, amount REAL, status TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    # Миграции: добавить поля регистрации
    for col, typ in [('phone', 'TEXT'), ('reg_name', 'TEXT'), ('registered', 'INTEGER DEFAULT 0')]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    # Старые активные пользователи считаются зарегистрированными
    try:
        c.execute("""UPDATE users SET registered=1 
                     WHERE registered=0 
                     AND (balance > 0 OR total_rounds > 0 OR id IN (
                         SELECT DISTINCT user_id FROM transactions
                     ))""")
    except Exception:
        pass
    # Чистка неиспользованных демо-балансов
    try:
        c.execute("""UPDATE users SET balance = 0 
                     WHERE balance = 10000.0 
                     AND total_rounds = 0
                     AND id NOT IN (
                         SELECT DISTINCT user_id FROM transactions 
                         WHERE type = 'Deposit' AND status IN ('Completed', 'Pending')
                     )""")
    except Exception:
        pass
    conn.commit()
    conn.close()

init_db()


@app.route('/api/ping')
def ping():
    return jsonify({'ok': True})


@app.route('/api/user', methods=['GET'])
def get_user():
    user_id = request.args.get('id')
    username = request.args.get('username', 'Игрок')
    if not user_id: return jsonify({'error': 'No ID'}), 400
    conn = sqlite3.connect('casino.db')
    c = conn.cursor()
    c.execute("SELECT balance, total_rounds, best_result, phone, reg_name, registered FROM users WHERE id=?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT INTO users (id, username, balance, total_rounds, best_result, registered) VALUES (?, ?, ?, ?, ?, 0)",
                  (user_id, username, 0.0, 0, 0.0))
        conn.commit()
        user = (0.0, 0, 0.0, None, None, 0)
    conn.close()
    return jsonify({
        'balance': user[0],
        'total_rounds': user[1],
        'best_result': user[2],
        'phone': user[3],
        'reg_name': user[4],
        'registered': bool(user[5])
    })


@app.route('/api/register', methods=['POST'])
def register():
    try:
        data = request.json
        user_id = int(data['id'])
        phone = (data.get('phone') or '').strip()
        name = (data.get('name') or '').strip()
        if len(phone) < 7:
            return jsonify({'error': 'Укажите корректный номер телефона'}), 400
        if len(name) < 2:
            return jsonify({'error': 'Укажите имя (минимум 2 символа)'}), 400
        conn = sqlite3.connect('casino.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE id=?", (user_id,))
        if not c.fetchone():
            c.execute("INSERT INTO users (id, balance, registered) VALUES (?, 0.0, 0)", (user_id,))
        c.execute("SELECT id FROM users WHERE phone=? AND id!=?", (phone, user_id))
        if c.fetchone():
            conn.close()
            return jsonify({'error': 'Этот номер уже зарегистрирован'}), 400
        c.execute("UPDATE users SET phone=?, reg_name=?, registered=1 WHERE id=?", (phone, name, user_id))
        conn.commit()
        conn.close()
        try:
            bot.send_message(ADMIN_ID, f"👤 Новая регистрация\nID: {user_id}\nИмя: {name}\nТелефон: {phone}")
        except Exception:
            pass
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/save', methods=['POST'])
def save_user():
    data = request.json
    conn = sqlite3.connect('casino.db')
    c = conn.cursor()
    c.execute("UPDATE users SET balance=?, total_rounds=?, best_result=? WHERE id=?", 
              (data['balance'], data['total_rounds'], data['best_result'], data['id']))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})


@app.route('/api/transactions', methods=['GET'])
def get_transactions():
    user_id = request.args.get('id')
    conn = sqlite3.connect('casino.db')
    c = conn.cursor()
    c.execute("SELECT type, method, amount, status, date FROM transactions WHERE user_id=? ORDER BY date DESC LIMIT 20", (user_id,))
    rows = c.fetchall()
    conn.close()
    return jsonify([{'type': r[0], 'method': r[1], 'amount': r[2], 'status': r[3], 'date': r[4]} for r in rows])


@app.route('/api/deposit', methods=['POST'])
def deposit():
    try:
        data = request.json
        user_id, amount, method = int(data['id']), float(data['amount']), data.get('method', 'USDT')
        min_limits = {'USDT': 15.0, 'BTC': 50.0, 'ETH': 20.0}
        if amount < min_limits.get(method, 15.0):
            return jsonify({'error': f'Минимальная сумма для {method} - ${min_limits.get(method, 15.0)}'}), 400
        conn = sqlite3.connect('casino.db')
        c = conn.cursor()
        c.execute("SELECT registered FROM users WHERE id=?", (user_id,))
        reg = c.fetchone()
        if not reg or not reg[0]:
            conn.close()
            return jsonify({'error': 'Сначала зарегистрируйтесь'}), 400
        c.execute("INSERT INTO transactions (user_id, type, method, amount, status) VALUES (?, 'Deposit', ?, ?, 'Pending')", (user_id, method, amount))
        tx_id = c.lastrowid
        conn.commit()
        conn.close()
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅ Одобрить", callback_data=f"app_dep_{tx_id}"), types.InlineKeyboardButton("❌ Отклонить", callback_data=f"rej_dep_{tx_id}"))
        bot.send_message(ADMIN_ID, f"💰 Заявка на пополнение #{tx_id}\nID: {user_id}\nСумма: ${amount}\nСеть: {method}", reply_markup=markup)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/withdraw', methods=['POST'])
def withdraw():
    try:
        data = request.json
        user_id, amount, method = int(data['id']), float(data['amount']), data.get('method', 'USDT')
        min_limits = {'USDT': 15.0, 'BTC': 50.0, 'ETH': 20.0}
        if amount < min_limits.get(method, 15.0):
            return jsonify({'error': f'Минимальная сумма для {method} - ${min_limits.get(method, 15.0)}'}), 400
        conn = sqlite3.connect('casino.db')
        c = conn.cursor()
        c.execute("SELECT balance, registered FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Пользователь не найден'}), 404
        if not row[1]:
            conn.close()
            return jsonify({'error': 'Сначала зарегистрируйтесь'}), 400
        bal = row[0]
        if bal >= amount:
            c.execute("UPDATE users SET balance = balance - ? WHERE id=?", (amount, user_id))
            c.execute("INSERT INTO transactions (user_id, type, method, amount, status) VALUES (?, 'Withdrawal', ?, ?, 'Pending')", (user_id, method, amount))
            tx_id = c.lastrowid
            conn.commit()
            conn.close()
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ Выплачено", callback_data=f"app_wd_{tx_id}"), types.InlineKeyboardButton("❌ Отклонить (Возврат)", callback_data=f"rej_wd_{tx_id}"))
            bot.send_message(ADMIN_ID, f"💸 Заявка на вывод #{tx_id}\nID: {user_id}\nСумма: ${amount}\nСеть: {method}", reply_markup=markup)
            return jsonify({'status': 'ok'})
        else:
            conn.close()
            return jsonify({'error': f'Недостаточно средств. Ваш баланс: ${bal}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bot.callback_query_handler(func=lambda call: call.data.startswith(('app_dep', 'rej_dep', 'app_wd', 'rej_wd')))
def handle_admin_action(call):
    try:
        if str(call.from_user.id) != ADMIN_ID:
            bot.answer_callback_query(call.id, "❌ Нет прав")
            return
        parts = call.data.split('_')
        action, tx_type, tx_id = parts[0], parts[1], int(parts[2])
        conn = sqlite3.connect('casino.db')
        c = conn.cursor()
        c.execute("SELECT user_id, amount, status FROM transactions WHERE id=?", (tx_id,))
        tx = c.fetchone()
        if not tx or tx[2] != 'Pending':
            conn.close()
            bot.answer_callback_query(call.id, "❌ Заявка уже обработана")
            return
        user_id, amount = int(tx[0]), float(tx[1])
        status_line = ""
        user_msg = ""
        if action == 'app' and tx_type == 'dep':
            c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
            c.execute("UPDATE transactions SET status = 'Completed' WHERE id=?", (tx_id,))
            status_line = f"✅ Пополнение #{tx_id} одобрено"
            user_msg = f"✅ Ваш депозит на ${amount} одобрен! Баланс пополнен."
        elif action == 'rej' and tx_type == 'dep':
            c.execute("UPDATE transactions SET status = 'Rejected' WHERE id=?", (tx_id,))
            status_line = f"❌ Пополнение #{tx_id} отклонено"
            user_msg = f"❌ Ваш депозит на ${amount} отклонен."
        elif action == 'app' and tx_type == 'wd':
            c.execute("UPDATE transactions SET status = 'Completed' WHERE id=?", (tx_id,))
            status_line = f"✅ Вывод #{tx_id} выплачен"
            user_msg = f"✅ Ваш вывод на ${amount} успешно выплачен!"
        elif action == 'rej' and tx_type == 'wd':
            c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
            c.execute("UPDATE transactions SET status = 'Rejected' WHERE id=?", (tx_id,))
            status_line = f"❌ Вывод #{tx_id} отклонен (средства возвращены)"
            user_msg = f"❌ Ваш вывод на ${amount} отклонен. Средства возвращены на баланс."
        conn.commit()
        conn.close()
        # 1) Сначала отвечаем на callback — гасит "часики"
        try:
            bot.answer_callback_query(call.id, status_line)
        except Exception as e:
            print("answer_callback error:", e)
        # 2) Потом правим сообщение (без Markdown, чтобы не падало)
        try:
            new_text = call.message.text + "\n\n" + status_line
            bot.edit_message_text(new_text, call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception as e:
            print("edit_message error:", e)
        # 3) Уведомляем игрока + кнопка открыть WebApp
        try:
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🎰 Открыть казино", web_app=types.WebAppInfo(url=WEBAPP_URL)))
            bot.send_message(user_id, user_msg + "\n\nОткройте казино — баланс уже обновлён.", reply_markup=markup)
        except Exception as e:
            print("notify user error:", e)
    except Exception as e:
        print("Callback handler error:", e)
        try:
            bot.answer_callback_query(call.id, "⚠️ Ошибка обработки")
        except Exception:
            pass


@app.route('/api/support', methods=['POST'])
def support():
    data = request.json
    text = f"📩 Обращение в поддержку\nОт: @{data.get('username')} (ID: {data.get('id')})\n\n{data.get('message')}"
    try:
        bot.send_message(ADMIN_ID, text)
        return jsonify({'status': 'ok'})
    except Exception as e: return jsonify({'error': str(e)}), 500


# ============ SLOTS ============
@app.route('/api/slots/classic', methods=['POST'])
def slots_classic():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id'])
        bet = float(data['bet'])
        if bet <= 0:
            return jsonify({'error': 'Ставка должна быть больше 0'}), 400
        conn = sqlite3.connect('casino.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        if not row or row[0] < bet:
            conn.close()
            return jsonify({'error': 'Недостаточно средств'}), 400
        symbols = ['🍒', '🍋', '🍊', '🍇', '💎', '7️⃣']
        weights = [30, 25, 20, 15, 8, 2]
        reels = []
        for _ in range(3):
            r = random.randint(1, 100)
            acc = 0
            for sym, w in zip(symbols, weights):
                acc += w
                if r <= acc:
                    reels.append(sym)
                    break
        if reels[0] == reels[1] == reels[2]:
            mult = 50 if reels[0] == '7️⃣' else 20 if reels[0] == '💎' else 10 if reels[0] == '🍇' else 5
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
            mult = 2 if ('💎' in reels or '7️⃣' in reels) else 1.5
        else:
            mult = 0
        win = round(bet * mult, 2)
        new_bal = round(row[0] - bet + win, 2)
        c.execute("UPDATE users SET balance=?, total_rounds = total_rounds + 1 WHERE id=?", (new_bal, user_id))
        if win > 0:
            c.execute("UPDATE users SET best_result = MAX(best_result, ?) WHERE id=?", (win, user_id))
        conn.commit()
        conn.close()
        return jsonify({'reels': reels, 'multiplier': mult, 'win': win, 'balance': new_bal})
    except Exception as e:
        return jsonify({'error': 'Ошибка сервера: ' + str(e)}), 500


@app.route('/api/slots/lucky7', methods=['POST'])
def slots_lucky7():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id'])
        bet = float(data['bet'])
        if bet <= 0:
            return jsonify({'error': 'Ставка должна быть больше 0'}), 400
        conn = sqlite3.connect('casino.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        if not row or row[0] < bet:
            conn.close()
            return jsonify({'error': 'Недостаточно средств'}), 400
        symbols = ['🍒', '🍋', '7️⃣']
        weights = [45, 40, 15]
        reels = []
        for _ in range(3):
            r = random.randint(1, 100)
            acc = 0
            for sym, w in zip(symbols, weights):
                acc += w
                if r <= acc:
                    reels.append(sym)
                    break
        sevens = reels.count('7️⃣')
        if sevens == 3: mult = 30
        elif sevens == 2: mult = 5
        elif sevens == 1: mult = 1.5
        elif reels[0] == reels[1] == reels[2]: mult = 3
        else: mult = 0
        win = round(bet * mult, 2)
        new_bal = round(row[0] - bet + win, 2)
        c.execute("UPDATE users SET balance=?, total_rounds = total_rounds + 1 WHERE id=?", (new_bal, user_id))
        if win > 0:
            c.execute("UPDATE users SET best_result = MAX(best_result, ?) WHERE id=?", (win, user_id))
        conn.commit()
        conn.close()
        return jsonify({'reels': reels, 'multiplier': mult, 'win': win, 'balance': new_bal})
    except Exception as e:
        return jsonify({'error': 'Ошибка сервера: ' + str(e)}), 500


# ============ BACCARAT ============
BACC_SUITS = ['♠', '♥', '♦', '♣']
BACC_VALUES = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']


def bacc_new_deck():
    d = [{'v': v, 's': s} for s in BACC_SUITS for v in BACC_VALUES]
    random.shuffle(d)
    return d


def bacc_value(card):
    v = card['v']
    if v == 'A': return 1
    if v in ('10', 'J', 'Q', 'K'): return 0
    return int(v)


def bacc_score(hand):
    return sum(bacc_value(c) for c in hand) % 10


def bacc_play():
    deck = bacc_new_deck()
    player = [deck.pop(), deck.pop()]
    banker = [deck.pop(), deck.pop()]
    ps = bacc_score(player)
    bs = bacc_score(banker)
    if ps < 8 and bs < 8:
        p3 = None
        if ps <= 5:
            p3 = deck.pop()
            player.append(p3)
            ps = bacc_score(player)
        if p3 is None:
            if bs <= 5:
                banker.append(deck.pop())
                bs = bacc_score(banker)
        else:
            p3v = bacc_value(p3)
            draw = False
            if bs <= 2: draw = True
            elif bs == 3: draw = (p3v != 8)
            elif bs == 4: draw = (p3v in (2, 3, 4, 5, 6, 7))
            elif bs == 5: draw = (p3v in (4, 5, 6, 7))
            elif bs == 6: draw = (p3v in (6, 7))
            if draw:
                banker.append(deck.pop())
                bs = bacc_score(banker)
    if ps > bs: winner = 'player'
    elif bs > ps: winner = 'banker'
    else: winner = 'tie'
    return player, banker, ps, bs, winner


@app.route('/api/baccarat/play', methods=['POST'])
def baccarat_play():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id'])
        bet = float(data['bet'])
        bet_on = data.get('bet_on', 'player')
        if bet <= 0:
            return jsonify({'error': 'Ставка должна быть больше 0'}), 400
        if bet_on not in ('player', 'banker', 'tie'):
            return jsonify({'error': 'Неверный тип ставки'}), 400
        conn = sqlite3.connect('casino.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        if not row or row[0] < bet:
            conn.close()
            return jsonify({'error': 'Недостаточно средств'}), 400
        player, banker, ps, bs, winner = bacc_play()
        profit = 0
        if bet_on == 'player':
            if winner == 'player': profit = bet
            elif winner == 'tie': profit = 0
            else: profit = -bet
        elif bet_on == 'banker':
            if winner == 'banker': profit = round(bet * 0.95, 2)
            elif winner == 'tie': profit = 0
            else: profit = -bet
        elif bet_on == 'tie':
            if winner == 'tie': profit = round(bet * 8, 2)
            else: profit = -bet
        new_bal = round(row[0] + profit, 2)
        c.execute("UPDATE users SET balance=?, total_rounds = total_rounds + 1 WHERE id=?", (new_bal, user_id))
        if profit > 0:
            c.execute("UPDATE users SET best_result = MAX(best_result, ?) WHERE id=?", (profit, user_id))
        conn.commit()
        conn.close()
        return jsonify({
            'player_hand': player, 'banker_hand': banker,
            'player_score': ps, 'banker_score': bs,
            'winner': winner, 'bet_on': bet_on,
            'profit': profit, 'balance': new_bal
        })
    except Exception as e:
        return jsonify({'error': 'Ошибка сервера: ' + str(e)}), 500


# ============ MINES ============
def mines_multiplier(mines_count, picks):
    if picks == 0: return 1.0
    prob = 1.0
    for i in range(picks):
        prob *= (25 - mines_count - i) / (25 - i)
    if prob <= 0: return 0
    return round(0.97 / prob, 2)


@app.route('/api/mines/start', methods=['POST'])
def mines_start():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id'])
        bet = float(data['bet'])
        mines_count = int(data['mines'])
        if bet <= 0 or mines_count < 1 or mines_count > 24:
            return jsonify({'error': 'Неверные параметры'}), 400
        conn = sqlite3.connect('casino.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        if not row or row[0] < bet:
            conn.close()
            return jsonify({'error': 'Недостаточно средств'}), 400
        new_bal = round(row[0] - bet, 2)
        c.execute("UPDATE users SET balance=?, total_rounds = total_rounds + 1 WHERE id=?", (new_bal, user_id))
        conn.commit()
        conn.close()
        mine_positions = random.sample(range(25), mines_count)
        round_id = secrets.token_hex(8)
        MINES_ROUNDS[round_id] = {
            'user_id': user_id, 'bet': bet, 'mines': set(mine_positions),
            'revealed': set(), 'done': False
        }
        return jsonify({'round_id': round_id, 'balance': new_bal, 'multiplier': 1.0})
    except Exception as e:
        return jsonify({'error': 'Ошибка сервера: ' + str(e)}), 500


@app.route('/api/mines/reveal', methods=['POST'])
def mines_reveal():
    try:
        data = request.get_json(silent=True) or {}
        round_id = data.get('round_id')
        cell = int(data['cell'])
        user_id = int(data['id'])
        rd = MINES_ROUNDS.get(round_id)
        if not rd or rd['user_id'] != user_id or rd['done']:
            return jsonify({'error': 'Раунд не найден'}), 404
        if cell < 0 or cell > 24 or cell in rd['revealed']:
            return jsonify({'error': 'Неверная клетка'}), 400
        if cell in rd['mines']:
            rd['done'] = True
            return jsonify({
                'mine': True,
                'mines': list(rd['mines']),
                'revealed': list(rd['revealed']) + [cell]
            })
        rd['revealed'].add(cell)
        picks = len(rd['revealed'])
        mult = mines_multiplier(len(rd['mines']), picks)
        return jsonify({
            'mine': False, 'cell': cell,
            'multiplier': mult, 'picks': picks,
            'potential_win': round(rd['bet'] * mult, 2)
        })
    except Exception as e:
        return jsonify({'error': 'Ошибка сервера: ' + str(e)}), 500


@app.route('/api/mines/cashout', methods=['POST'])
def mines_cashout():
    try:
        data = request.get_json(silent=True) or {}
        round_id = data.get('round_id')
        user_id = int(data['id'])
        rd = MINES_ROUNDS.get(round_id)
        if not rd or rd['user_id'] != user_id or rd['done']:
            return jsonify({'error': 'Раунд не найден'}), 404
        if len(rd['revealed']) == 0:
            return jsonify({'error': 'Откройте хотя бы одну клетку'}), 400
        rd['done'] = True
        picks = len(rd['revealed'])
        mult = mines_multiplier(len(rd['mines']), picks)
        win = round(rd['bet'] * mult, 2)
        conn = sqlite3.connect('casino.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        new_bal = round(row[0] + win, 2)
        c.execute("UPDATE users SET balance=?, best_result = MAX(best_result, ?) WHERE id=?", (new_bal, win, user_id))
        conn.commit()
        conn.close()
        return jsonify({
            'win': win, 'multiplier': mult, 'balance': new_bal,
            'mines': list(rd['mines'])
        })
    except Exception as e:
        return jsonify({'error': 'Ошибка сервера: ' + str(e)}), 500


# ============ ROULETTE ============
@app.route('/api/roulette/spin', methods=['POST'])
def roulette_spin():
    try:
        data = request.get_json(silent=True) or {}
        user_id = int(data['id'])
        bet = float(data['bet'])
        bet_type = data.get('bet_type')
        bet_value = data.get('bet_value')
        if bet <= 0:
            return jsonify({'error': 'Ставка должна быть больше 0'}), 400
        valid_types = ['red', 'black', 'odd', 'even', 'low', 'high',
                       'dozen1', 'dozen2', 'dozen3',
                       'column1', 'column2', 'column3', 'number']
        if bet_type not in valid_types:
            return jsonify({'error': 'Неверный тип ставки'}), 400
        if bet_type == 'number':
            if bet_value is None:
                return jsonify({'error': 'Не указано число'}), 400
            bet_value = int(bet_value)
            if bet_value < 0 or bet_value > 36:
                return jsonify({'error': 'Неверное число'}), 400
        conn = sqlite3.connect('casino.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        if not row or row[0] < bet:
            conn.close()
            return jsonify({'error': 'Недостаточно средств'}), 400
        result = random.randint(0, 36)
        color = 'green' if result == 0 else ('red' if result in RED_ROULETTE_NUMBERS else 'black')
        profit = -bet
        if bet_type == 'number' and result == bet_value:
            profit = bet * 35
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
        new_bal = round(row[0] + profit, 2)
        c.execute("UPDATE users SET balance=?, total_rounds = total_rounds + 1 WHERE id=?", (new_bal, user_id))
        if profit > 0:
            c.execute("UPDATE users SET best_result = MAX(best_result, ?) WHERE id=?", (profit, user_id))
        conn.commit()
        conn.close()
        return jsonify({
            'number': result, 'color': color,
            'profit': profit, 'balance': new_bal,
            'bet_type': bet_type, 'bet_value': bet_value
        })
    except Exception as e:
        return jsonify({'error': 'Ошибка сервера: ' + str(e)}), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'not_found', 'path': request.path}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'server_error'}), 500


@app.route('/')
def index():
    return send_file('index.html')


@bot.message_handler(commands=['start'])
def start(message):
    markup = types.InlineKeyboardMarkup()
    web_app = types.WebAppInfo(url=WEBAPP_URL)
    markup.add(types.InlineKeyboardButton("💎 Открыть MADGEK CASINO", web_app=web_app))
    bot.send_message(message.chat.id, "Добро пожаловать в MADGEK CASINO!", reply_markup=markup)


if __name__ == "__main__":
    import threading
    threading.Thread(target=bot.infinity_polling).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
