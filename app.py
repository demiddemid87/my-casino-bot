import telebot
from telebot import types
import os
from flask import Flask, send_file

TOKEN = os.environ.get('TELEGRAM_TOKEN', '8879662302:AAGUpOTng02XIMIRZe7xf154qKaJVXakj3o')
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Отдаем наш HTML файл
@app.route('/')
def index():
    return send_file('index.html')

# Главное меню бота
@bot.message_handler(commands=['start'])
def start(message):
    markup = types.InlineKeyboardMarkup()
    
    # Твоя ссылка на Render
    web_app = types.WebAppInfo(url="https://my-casino-bot-2.onrender.com")
    btn_app = types.InlineKeyboardButton("🎰 Открыть Казино", web_app=web_app)
    
    markup.add(btn_app)
    
    bot.send_message(message.chat.id, 
        "👋 Добро пожаловать в Демо-Казино!\n\nНажми на кнопку ниже, чтобы открыть полноценное приложение.", 
        reply_markup=markup)

if __name__ == "__main__":
    import threading
    def run_bot():
        bot.infinity_polling()
    
    threading.Thread(target=run_bot).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
