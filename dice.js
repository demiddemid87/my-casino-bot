// ==========================================
// ИГРА КОСТИ (Автономный модуль)
// ==========================================
(function() {
    // Ждем, пока загрузится основной интерфейс
    setTimeout(() => {
        // 1. Добавляем кнопку в меню игр
        const grid = document.getElementById('gamesGrid');
        if (grid) {
            // Используем MutationObserver, чтобы кнопка не пропадала при смене вкладок
            const observer = new MutationObserver(() => {
                if (!document.getElementById('card-dice')) {
                    const card = document.createElement('div');
                    card.id = 'card-dice';
                    card.className = 'game-card';
                    card.innerHTML = `<span class="icon">🎲</span><h3>Кости</h3><p>Угадай число</p>`;
                    card.onclick = openDiceGame;
                    grid.appendChild(card);
                }
            });
            observer.observe(grid, { childList: true });
        }

        // 2. Создаем модальное окно для игры
        const modal = document.createElement('div');
        modal.id = 'diceModal';
        modal.className = 'modal-overlay';
        modal.innerHTML = `
            <div class="modal-header">
                <h2>🎲 Кости</h2>
                <button class="close-btn" onclick="document.getElementById('diceModal').classList.remove('open')">×</button>
            </div>
            <div class="modal-content">
                <div style="width:100%; max-width:400px; background: #0a0a0f; border: 2px solid rgba(255,215,0,0.3); border-radius: 20px; padding: 25px; margin-bottom: 20px; box-shadow: inset 0 0 30px rgba(0,0,0,0.8);">
                    <div id="diceRes" style="font-size: 64px; font-weight: 900; color: #FFD700; text-align: center; font-family: 'Cinzel', serif; margin-bottom: 20px; text-shadow: 0 0 20px rgba(255,215,0,0.5); height: 80px; display: flex; justify-content: center; align-items: center;">50.00</div>
                    
                    <div style="position: relative; margin-bottom: 25px; padding: 0 10px;">
                        <input type="range" id="diceSlider" min="2" max="98" value="50" style="-webkit-appearance: none; width: 100%; height: 12px; border-radius: 10px; background: linear-gradient(to right, #00E676 0%, #00E676 50%, #E53935 50%, #E53935 100%); outline: none; margin-bottom: 10px;" oninput="window.updateDice()">
                        <div style="display: flex; justify-content: space-between; font-weight: 800; font-size: 14px; color: #fff;"><span>2</span><span>50</span><span>98</span></div>
                    </div>
                    
                    <div style="display: flex; gap: 10px; margin-bottom: 20px;">
                        <button id="btnUnder" onclick="window.setDiceCond('under')" style="flex: 1; padding: 12px; border-radius: 12px; font-weight: 900; border: 2px solid #00E676; color: #fff; background: rgba(0,76,25,0.4); font-size: 14px;">МЕНЬШЕ</button>
                        <button id="btnOver" onclick="window.setDiceCond('over')" style="flex: 1; padding: 12px; border-radius: 12px; font-weight: 900; border: 2px solid rgba(255,215,0,0.3); color: #8b8b93; background: rgba(0,0,0,0.5); font-size: 14px;">БОЛЬШЕ</button>
                    </div>
                    
                    <div style="display: flex; justify-content: space-between; background: rgba(255,255,255,0.05); padding: 15px; border-radius: 12px; margin-bottom: 20px; border: 1px solid rgba(255,255,255,0.1);">
                        <div style="text-align: center;"><div style="font-size: 11px; color: #8b8b93; font-weight: 800;">МНОЖИТЕЛЬ</div><div id="diceMult" style="font-size: 20px; font-weight: 900; color: #FFD700;">1.98x</div></div>
                        <div style="text-align: center;"><div style="font-size: 11px; color: #8b8b93; font-weight: 800;">ШАНС</div><div id="diceChance" style="font-size: 20px; font-weight: 900; color: #FFD700;">49.5%</div></div>
                    </div>

                    <div style="display: flex; gap: 8px; justify-content: center; margin-bottom: 15px; flex-wrap: wrap;" id="diceChips"></div>
                    
                    <button id="dicePlayBtn" onclick="window.playDice()" style="width: 100%; padding: 18px; border-radius: 15px; border: none; font-weight: 900; font-size: 16px; cursor: pointer; background: linear-gradient(135deg, #FFD700, #B8860B); color: #000; box-shadow: 0 5px 20px rgba(255,215,0,0.4);">БРОСИТЬ</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        // Стили для модалки (на случай если их нет в старом html)
        const style = document.createElement('style');
        style.innerHTML = `
            .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.85); backdrop-filter: blur(8px); display: none; flex-direction: column; z-index: 9999; overflow-y: auto; }
            .modal-overlay.open { display: flex; animation: fadeIn 0.3s ease; }
            .modal-header { display: flex; justify-content: space-between; align-items: center; padding: 20px; background: rgba(10,10,15,0.95); position: sticky; top: 0; z-index: 10; border-bottom: 1px solid rgba(255,215,0,0.3); }
            .modal-header h2 { font-family: 'Cinzel', serif; font-size: 22px; color: #FFD700; margin: 0; }
            .close-btn { background: none; border: none; color: #fff; font-size: 36px; cursor: pointer; line-height: 1; }
            .modal-content { padding: 20px; flex: 1; display: flex; flex-direction: column; align-items: center; }
            @keyframes fadeIn { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); } }
            /* Конфетти */
            .dice-confetti { position: fixed; width: 10px; height: 10px; top: -20px; pointer-events: none; z-index: 10000; animation: confFall linear forwards; }
            @keyframes confFall { to { transform: translateY(105vh) rotate(720deg); opacity: 0; } }
        `;
        document.head.appendChild(style);

        // Инициализация переменных
        window.diceTarget = 50;
        window.diceCondition = 'under';
        window.diceBet = 100;
        window.updateDice();
        window.renderDiceChips();
    }, 1000); // Ждем секунду, чтобы основной HTML точно загрузился

    // --- ЛОГИКА ИГРЫ ---
    window.openDiceGame = function() {
        document.getElementById('diceModal').classList.add('open');
        window.renderDiceChips();
    };

    window.renderDiceChips = function() {
        const el = document.getElementById('diceChips');
        if(!el) return;
        el.innerHTML = [10, 50, 100, 500, 1000].map(val => 
            `<div style="background: ${window.diceBet === val ? '#FFD700' : 'rgba(0,0,0,0.5)'}; color: ${window.diceBet === val ? '#000' : '#fff'}; border: 1px solid rgba(255,215,0,0.3); padding: 8px 15px; border-radius: 20px; font-weight: 800; cursor: pointer; transition: 0.2s;" onclick="window.diceBet = ${val}; window.renderDiceChips();">$${val}</div>`
        ).join('');
    };

    window.updateDice = function() {
        const slider = document.getElementById('diceSlider');
        if(!slider) return;
        window.diceTarget = parseFloat(slider.value);
        const chance = window.diceCondition === 'over' ? (100 - window.diceTarget) : window.diceTarget;
        const mult = (99 / chance).toFixed(2);
        
        document.getElementById('diceMult').innerText = mult + 'x';
        document.getElementById('diceChance').innerText = chance.toFixed(1) + '%';
        
        if (window.diceCondition === 'over') {
            slider.style.background = `linear-gradient(to right, #00E676 0%, #00E676 ${window.diceTarget}%, #E53935 ${window.diceTarget}%, #E53935 100%)`;
        } else {
            slider.style.background = `linear-gradient(to right, #E53935 0%, #E53935 ${window.diceTarget}%, #00E676 ${window.diceTarget}%, #00E676 100%)`;
        }
    };

    window.setDiceCond = function(cond) {
        window.diceCondition = cond;
        const btnU = document.getElementById('btnUnder');
        const btnO = document.getElementById('btnOver');
        if(cond === 'under') {
            btnU.style.border = '2px solid #00E676'; btnU.style.color = '#fff'; btnU.style.background = 'rgba(0,76,25,0.4)';
            btnO.style.border = '2px solid rgba(255,215,0,0.3)'; btnO.style.color = '#8b8b93'; btnO.style.background = 'rgba(0,0,0,0.5)';
        } else {
            btnO.style.border = '2px solid #E53935'; btnO.style.color = '#fff'; btnO.style.background = 'rgba(229,57,53,0.2)';
            btnU.style.border = '2px solid rgba(255,215,0,0.3)'; btnU.style.color = '#8b8b93'; btnU.style.background = 'rgba(0,0,0,0.5)';
        }
        window.updateDice();
    };

    window.playDice = async function() {
        // Берем ID пользователя из Telegram
        const tg = window.Telegram?.WebApp;
        const userId = tg?.initDataUnsafe?.user?.id || 0;
        
        // Получаем текущий баланс (запрашиваем у сервера)
        try {
            const btn = document.getElementById('dicePlayBtn');
            btn.disabled = true; btn.innerText = '🎲 КРУТИМ...';
            
            const resUser = await fetch('/api/user?id=' + userId);
            const userData = await resUser.json();
            
            if (userData.balance < window.diceBet) {
                btn.disabled = false; btn.innerText = 'БРОСИТЬ';
                return tg.showAlert('Недостаточно средств!');
            }

            const resEl = document.getElementById('diceRes');
            resEl.style.color = '#8b8b93';
            let anim = setInterval(() => { resEl.innerText = (Math.random() * 100).toFixed(2); }, 50);

            setTimeout(async () => {
                clearInterval(anim);
                
                // Логика выигрыша
                const result = parseFloat((Math.random() * 99.99).toFixed(2));
                const chance = window.diceCondition === 'over' ? (100 - window.diceTarget) : window.diceTarget;
                const mult = 99 / chance;
                let win = 0;
                
                if (window.diceCondition === 'over' && result > window.diceTarget) win = window.diceBet * mult;
                else if (window.diceCondition === 'under' && result < window.diceTarget) win = window.diceBet * mult;

                const newBal = userData.balance - window.diceBet + win;
                
                // Сохраняем на сервере (используем твой старый метод сохранения)
                await fetch('/api/save', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ 
                        id: userId, 
                        balance: newBal, 
                        total_rounds: userData.total_rounds + 1, 
                        best_result: Math.max(userData.best_result, win) 
                    })
                });

                // Показываем результат
                resEl.innerText = result.toFixed(2);
                
                if (win > 0) {
                    resEl.style.color = '#00E676';
                    resEl.style.textShadow = '0 0 30px #00E676';
                    window.diceEffect(true);
                    tg.showAlert(`🎉 ВЫИГРЫШ $${win.toFixed(2)}!`);
                } else {
                    resEl.style.color = '#E53935';
                    resEl.style.textShadow = '0 0 30px #E53935';
                    window.diceEffect(false);
                    tg.showAlert(`😔 Проигрыш`);
                }
                
                btn.disabled = false; btn.innerText = 'БРОСИТЬ';
                
                // Обновляем баланс в шапке сайта, если функция существует
                if (typeof window.loadUser === 'function') window.loadUser(); 
                else if (typeof updateUI === 'function') updateUI();

            }, 800);
        } catch(e) {
            console.error(e);
            document.getElementById('dicePlayBtn').disabled = false;
            document.getElementById('dicePlayBtn').innerText = 'БРОСИТЬ';
        }
    };

    // --- СПЕЦЭФФЕКТЫ ДЛЯ КОСТЕЙ ---
    window.diceEffect = function(isWin) {
        if (isWin) {
            // Конфетти
            for(let i=0; i<50; i++) {
                let c = document.createElement('div');
                c.className = 'dice-confetti';
                c.style.left = Math.random() * 100 + 'vw';
                c.style.background = ['#FFD700', '#00E676', '#29B6F6', '#E53935'][Math.floor(Math.random()*4)];
                c.style.animationDuration = (Math.random() * 1.5 + 1) + 's';
                document.body.appendChild(c);
                setTimeout(() => c.remove(), 3000);
            }
        } else {
            // Тряска экрана
            document.body.style.animation = 'shakeAnim 0.5s';
            setTimeout(() => document.body.style.animation = '', 500);
        }
    };
})();
