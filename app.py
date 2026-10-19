import telebot
from telebot import types
import random
import os
from flask import Flask
import threading

TOKEN = os.environ.get('TELEGRAM_TOKEN', '8879662302:AAGUpOTng02XIMIRZe7xf154qKaJVXakj3o')
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

users = {}
vip_levels = {0: "Новичок", 1000: "Бронза", 5000: "Серебро", 10000: "Золото", 50000: "Платина"}

def get_user(uid):
    if uid not in users:
        users[uid] = {"balance": 100.0, "total_bet": 0.0}
    return users[uid]

def get_vip(uid):
    total = get_user(uid)["total_bet"]
    current_vip = "Новичок"
    for threshold, name in sorted(vip_levels.items()):
        if total >= threshold:
            current_vip = name
    return current_vip

def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🎰 Слоты", "🎲 Рулетка")
    markup.row("💵 Депозит $1000", "💸 Вывод средств")
    markup.row("💎 VIP Кабинет", "❓ Поддержка / FAQ")
    return markup

@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    user = get_user(uid)
    bot.send_message(message.chat.id, 
        f"👋 Добро пожаловать в Демо-Казино!\n\n"
        f"💰 Ваш баланс: ${user['balance']:.2f}\n"
        f"💎 Ваш VIP-статус: {get_vip(uid)}\n\n"
        f"Выберите действие в меню ниже:",
        reply_markup=main_menu())

@bot.message_handler(content_types=['text'])
def handle_text(message):
    uid = message.from_user.id
    user = get_user(uid)
    text = message.text

    if text == "💵 Депозит $1000":
        user["balance"] += 1000.0
        bot.send_message(message.chat.id, f"✅ Тестовый депозит на $1000 успешно зачислен!\nТекущий баланс: ${user['balance']:.2f}")
    elif text == "💸 Вывод средств":
        bot.send_message(message.chat.id, f"📝 Заявка на вывод создана!\n(В демо-версии реальные деньги не выводятся, баланс остался ${user['balance']:.2f})")
    elif text == "💎 VIP Кабинет":
        total = user["total_bet"]
        next_vip = 10000 - total if total < 10000 else 0
        bot.send_message(message.chat.id, 
            f"💎 VIP Статус: {get_vip(uid)}\n"
            f"📊 Всего ставок сделано: ${total:.2f}\n"
            f"📈 До Золота: ${next_vip:.2f}")
    elif text == "❓ Поддержка / FAQ":
        bot.send_message(message.chat.id, 
            f"❓ Частые вопросы:\n"
            f"1. Как вывести деньги? — В демо-версии вывод недоступен.\n"
            f"2. Как получить бонус? — Нажмите «Депозит $1000».\n\n"
            f"👨‍💻 Поддержка: @admin_demo")
    elif text == "🎰 Слоты":
        if user["balance"] < 10:
            bot.send_message(message.chat.id, "❌ Недостаточно средств! Сделайте депозит.")
            return
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("Ставка $10", callback_data="slot_10"))
        markup.add(types.InlineKeyboardButton("Ставка $50", callback_data="slot_50"))
        markup.add(types.InlineKeyboardButton("Ставка $100", callback_data="slot_100"))
        bot.send_message(message.chat.id, f"🎰 Выберите ставку (Баланс: ${user['balance']:.2f}):", reply_markup=markup)
    elif text == "🎲 Рулетка":
        if user["balance"] < 10:
            bot.send_message(message.chat.id, "❌ Недостаточно средств! Сделайте депозит.")
            return
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔴 На КРАСНОЕ ($10)", callback_data="roul_red"))
        markup.add(types.InlineKeyboardButton("⚫️ На ЧЕРНОЕ ($10)", callback_data="roul_black"))
        bot.send_message(message.chat.id, f"🎲 Выберите цвет (Баланс: ${user['balance']:.2f}):", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    uid = call.from_user.id
    user = get_user(uid)
    
    if call.data.startswith("slot_"):
        bet = float(call.data.split("_")[1])
        if user["balance"] < bet:
            bot.answer_callback_query(call.id, "Недостаточно средств!")
            return
        user["balance"] -= bet
        user["total_bet"] += bet
        symbols = ['🍒', '🍋', '🔔', '💎', '7️⃣']
        result = [random.choice(symbols) for _ in range(3)]
        res_text = " | ".join(result)
        
        if result[0] == result[1] == result[2]:
            win = bet * 5
            user["balance"] += win
            msg = f"🎰 {res_text}\n\n🎉 ДЖЕКПОТ! +${win:.2f}!"
        elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
            win = bet * 2
            user["balance"] += win
            msg = f"🎰 {res_text}\n\n✨ Пара! +${win:.2f}!"
        else:
            msg = f"🎰 {res_text}\n\n😢 Проигрыш."
        msg += f"\n\n💰 Баланс: ${user['balance']:.2f}\n💎 VIP: {get_vip(uid)}"
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=msg)

    elif call.data.startswith("roul_"):
        bet = 10.0
        if user["balance"] < bet:
            bot.answer_callback_query(call.id, "Недостаточно средств!")
            return
        user["balance"] -= bet
        user["total_bet"] += bet
        choice = call.data.split("_")[1]
        num = random.randint(0, 36)
        is_red = num in [1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36]
        is_black = num != 0 and not is_red
        color = "🔴 Красное" if is_red else ("⚫️ Черное" if is_black else "🟢 Зеро")
        
        if (choice == "red" and is_red) or (choice == "black" and is_black):
            win = bet * 2
            user["balance"] += win
            msg = f"🎲 Выпало {num} ({color})\n\n🎉 Выигрыш ${win:.2f}!"
        else:
            msg = f"🎲 Выпало {num} ({color})\n\n😢 Проигрыш."
        msg += f"\n\n💰 Баланс: ${user['balance']:.2f}"
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=msg)

@app.route('/')
def home():
    return "Bot is running"

def run_bot():
    bot.infinity_polling()

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
