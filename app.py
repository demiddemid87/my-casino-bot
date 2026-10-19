import telebot
from telebot import types
import os
import sqlite3
from flask import Flask, send_file, request, jsonify

TOKEN = os.environ.get('TELEGRAM_TOKEN', '8879662302:AAGUpOTng02XIMIRZe7xf154qKaJVXakj3o')
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

ADMIN_ID = '7695713296'

def init_db():
    conn = sqlite3.connect('casino.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, username TEXT, balance REAL, total_rounds INTEGER, best_result REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS transactions 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, method TEXT, amount REAL, status TEXT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()
init_db()

@app.route('/api/user', methods=['GET'])
def get_user():
    user_id = request.args.get('id')
    username = request.args.get('username', 'Игрок')
    if not user_id: return jsonify({'error': 'No ID'}), 400
    conn = sqlite3.connect('casino.db')
    c = conn.cursor()
    c.execute("SELECT balance, total_rounds, best_result FROM users WHERE id=?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT INTO users (id, username, balance, total_rounds, best_result) VALUES (?, ?, ?, ?, ?)", (user_id, username, 10000.0, 0, 0.0))
        conn.commit()
        user = (10000.0, 0, 0.0)
    conn.close()
    return jsonify({'balance': user[0], 'total_rounds': user[1], 'best_result': user[2]})

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
        c.execute("INSERT INTO transactions (user_id, type, method, amount, status) VALUES (?, 'Deposit', ?, ?, 'Pending')", (user_id, method, amount))
        tx_id = c.lastrowid
        conn.commit()
        conn.close()
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅ Одобрить", callback_data=f"app_dep_{tx_id}"), types.InlineKeyboardButton("❌ Отклонить", callback_data=f"rej_dep_{tx_id}"))
        bot.send_message(ADMIN_ID, f"💰 **Заявка на пополнение #{tx_id}**\nID: {user_id}\nСумма: ${amount}\nСеть: {method}", reply_markup=markup, parse_mode='Markdown')
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
        c.execute("SELECT balance FROM users WHERE id=?", (user_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Пользователь не найден'}), 404
            
        bal = row[0]
        if bal >= amount:
            c.execute("UPDATE users SET balance = balance - ? WHERE id=?", (amount, user_id))
            c.execute("INSERT INTO transactions (user_id, type, method, amount, status) VALUES (?, 'Withdrawal', ?, ?, 'Pending')", (user_id, method, amount))
            tx_id = c.lastrowid
            conn.commit()
            conn.close()
            
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✅ Выплачено", callback_data=f"app_wd_{tx_id}"), types.InlineKeyboardButton("❌ Отклонить (Возврат)", callback_data=f"rej_wd_{tx_id}"))
            bot.send_message(ADMIN_ID, f"💸 **Заявка на вывод #{tx_id}**\nID: {user_id}\nСумма: ${amount}\nСеть: {method}", reply_markup=markup, parse_mode='Markdown')
            return jsonify({'status': 'ok'})
        else:
            conn.close()
            return jsonify({'error': f'Недостаточно средств. Ваш баланс: ${bal}'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@bot.callback_query_handler(func=lambda call: call.data.startswith(('app_dep', 'rej_dep', 'app_wd', 'rej_wd')))
def handle_admin_action(call):
    if str(call.from_user.id) != ADMIN_ID: return bot.answer_callback_query(call.id, "Нет прав.")
    action, tx_type, tx_id = call.data.split('_')[0], call.data.split('_')[1], call.data.split('_')[2]
    
    conn = sqlite3.connect('casino.db')
    c = conn.cursor()
    c.execute("SELECT user_id, amount, status FROM transactions WHERE id=?", (tx_id,))
    tx = c.fetchone()
    
    if not tx or tx[2] != 'Pending':
        conn.close()
        return bot.edit_message_text("❌ Заявка уже обработана.", call.message.chat.id, call.message.message_id)
        
    user_id, amount = int(tx[0]), float(tx[1])
    
    if action == 'app' and tx_type == 'dep':
        c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
        c.execute("UPDATE transactions SET status = 'Completed' WHERE id=?", (tx_id,))
        new_text = call.message.text + "\n\n✅ **Пополнение одобрено**"
        try: bot.send_message(user_id, f"✅ Ваш депозит на ${amount} одобрен! Баланс пополнен.")
        except: pass
        
    elif action == 'rej' and tx_type == 'dep':
        c.execute("UPDATE transactions SET status = 'Rejected' WHERE id=?", (tx_id,))
        new_text = call.message.text + "\n\n❌ **Пополнение отклонено**"
        try: bot.send_message(user_id, f"❌ Ваш депозит на ${amount} отклонен.")
        except: pass
        
    elif action == 'app' and tx_type == 'wd':
        c.execute("UPDATE transactions SET status = 'Completed' WHERE id=?", (tx_id,))
        new_text = call.message.text + "\n\n✅ **Вывод выплачен**"
        try: bot.send_message(user_id, f"✅ Ваш вывод на ${amount} успешно выплачен!")
        except: pass
        
    elif action == 'rej' and tx_type == 'wd':
        c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, user_id))
        c.execute("UPDATE transactions SET status = 'Rejected' WHERE id=?", (tx_id,))
        new_text = call.message.text + "\n\n❌ **Вывод отклонен (средства возвращены)**"
        try: bot.send_message(user_id, f"❌ Ваш вывод на ${amount} отклонен. Средства возвращены на баланс.")
        except: pass
        
    conn.commit()
    conn.close()
    bot.edit_message_text(new_text, call.message.chat.id, call.message.message_id, parse_mode='Markdown')

@app.route('/api/support', methods=['POST'])
def support():
    data = request.json
    text = f"📩 **Обращение в поддержку**\nОт: @{data.get('username')} (ID: {data.get('id')})\n\n{data.get('message')}"
    try:
        bot.send_message(ADMIN_ID, text, parse_mode='Markdown')
        return jsonify({'status': 'ok'})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/')
def index():
    return send_file('index.html')

@bot.message_handler(commands=['start'])
def start(message):
    markup = types.InlineKeyboardMarkup()
    web_app = types.WebAppInfo(url="https://my-casino-bot-2.onrender.com")
    markup.add(types.InlineKeyboardButton("💎 Открыть MADGEK CASINO", web_app=web_app))
    bot.send_message(message.chat.id, "Добро пожаловать в MADGEK CASINO!", reply_markup=markup)

if __name__ == "__main__":
    import threading
    threading.Thread(target=bot.infinity_polling).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
