from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import time
import uuid

app = Flask(__name__)
app.config['SECRET_KEY'] = 'kakegurui_secret'
socketio = SocketIO(app, cors_allowed_origins="*")

# 🃏 遊戲常數定義
SUITS = ['♠', '♥', '♦', '♣']
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
VALUES = {'2':2, '3':3, '4':4, '5':5, '6':6, '7':7, '8':8, '9':9, '10':10, 'J':10, 'Q':10, 'K':10, 'A':11}


# ==========================================
# 📊 股市與賽馬 全域變數
# ==========================================
# 賽馬當局下注池 { sid: {'horse': 馬號, 'amount': 金額, 'token': 玩家token} }
horse_bets = {}

# 股市預設價格 (A:穩定, B:成長, C:潛力, D:投機, E:妖股)
stocks = {
    'A': {'name': '學生會公債', 'price': 1000},
    'B': {'name': '百花王建設', 'price': 1500},
    'C': {'name': '家畜互助會', 'price': 500},
    'D': {'name': '地下賭場集團', 'price': 2000},
    'E': {'name': '夢子概念股', 'price': 100}
}
# 🌍 全域玩家錢包系統
db_players = {}   
sid_map = {}      

# 🎰 全域老虎機彩金池
jackpot_pool = 10000000 
# 🚪 房間隔離系統與遊戲狀態
games = {
    'blackjack': { 'type': 'blackjack', 'phase': 'WAITING', 'round': 1, 'players': {}, 'dealer_hand': [], 'deck': [], 'pending_swaps': {} },
    'tax': { 'type': 'tax', 'phase': 'WAITING', 'round': 1, 'players': {} },
    'auction': { 
        'type': 'auction', 'phase': 'WAITING', 'round': 1, 'players': {}, 
        'dealer_sid': None, 'dealer_bids_left': 5, 
        'current_bids': {}, 'highest_bid': 0
    }
}
# ==========================================
# 💰 借貸系統
# ==========================================
@socketio.on('request_loan')
def handle_loan(data):
    sid = request.sid
    tok = sid_map.get(sid, {}).get('token')
    p = db_players.get(tok)

    # 🚨 防呆：如果身分遺失，不再靜默死機，直接把錯誤傳到前端聊天室！
    if not p:
        print(f"❌ [錯誤] 找不到玩家資料！SID: {sid}, Token: {tok}")
        emit('chat_msg', {'name': '系統', 'msg': '❌ 身分驗證失效，請重新整理網頁再登入一次！'})
        return

    # 讀取金額
    try:
        amt = int(data.get('amount', 1000000))
    except:
        amt = 1000000

    # 錢包入帳
    p['chips'] += amt
    p['debt'] += amt
    
    print(f"✅ [系統] {p['name']} 成功借款 {amt}，最新餘額: {p['chips']}")
    
    # 更新畫面與廣播
    emit('login_success', {'token': tok, 'name': p['name'], 'chips': p['chips'], 'debt': p['debt']})
    socketio.emit('chat_msg', {'name': '💸 債務通知', 'msg': f"{p['name']} 簽下了 ¥{amt:,} 的借據！"}, broadcast=True)
# ==========================================
# 🎰 老虎機核心邏輯 (對接 db_players)
# ==========================================

@socketio.on('slot_spin')
def handle_slot_spin():
    global jackpot_pool
    sid = request.sid
    tok = sid_map.get(sid, {}).get('token')
    p = db_players.get(tok)
    
    if not p: return
    
    cost = 1000000 # 100 萬一抽
    if p.get('chips', 0) < cost:
        emit('chat_msg', {'name': '系統', 'msg': '❌ 籌碼不足 100 萬！'})
        return

    # 💸 扣錢並注入 10% 到彩金池
    p['chips'] -= cost
    jackpot_pool += int(cost * 0.1)
    
    # 🎲 決定結果 (0-5)
    r = [random.randint(0, 5) for _ in range(3)]
    is_win = (r[0] == r[1] == r[2])
    win_amt = 0
    
    if is_win:
        win_amt = int(jackpot_pool * 0.8) # 贏走 80%
        p['chips'] += win_amt
        jackpot_pool -= win_amt
        msg = f"🎊 大獎！{p['name']} 贏得了 ¥{win_amt:,}！"
        socketio.emit('chat_msg', {'name': '🎰 廣播', 'msg': msg}, broadcast=True)
    else:
        msg = "沒中獎，加油！"

    # 📢 1. 更新前端的個人錢包 (讓你立刻看到錢被扣)
    emit('login_success', {'token': tok, 'name': p['name'], 'chips': p['chips'], 'debt': p['debt']})
    
    # 📢 2. 更新前端的老虎機畫面與最新獎池
    emit('slot_result', {'reels': r, 'win': is_win, 'msg': msg})
    
    # 📢 3. 全服廣播最新獎池 (統一使用 'jackpot' 作為 key)
    socketio.emit('update_jackpot', {'jackpot': jackpot_pool}, broadcast=True)
    emit('login_success', {'token': tok, 'name': p['name'], 'chips': p['chips'], 'debt': p['debt']
    })

def calculate_hand(hand):
    val = sum(VALUES[card[1:]] for card in hand)
    aces = sum(1 for card in hand if card.endswith('A'))
    while val > 21 and aces: val -= 10; aces -= 1
    return val

def save_wallet(sid):
    room = sid_map.get(sid, {}).get('room')
    tok = sid_map.get(sid, {}).get('token')
    if room and tok and room in games:
        p = games[room]['players'].get(sid)
        if p:
            db_players[tok]['chips'] = p['chips']
            db_players[tok]['debt'] = p['debt']
            db_players[tok]['recharge'] = p['recharge']

def broadcast_state(room):
    socketio.emit('game_update', games[room], room=room)

@app.route('/')
def index():
    return render_template('index.html')

# ==========================================
# 🏫 大廳系統
# ==========================================
import uuid

import uuid

# ==========================================
# 🚪 登入與身分綁定系統
# ==========================================
@socketio.on('login')
def handle_login(data):
    sid = request.sid
    name = data.get('name', '無名氏')
    token = data.get('token')

    print(f"📥 [系統] 收到登入請求 - 名字: {name}, Token: {token}")

    # 1. 確認是老玩家還是新玩家
    if token and token in db_players:
        p = db_players[token]
        p['name'] = name
        print(f"✅ [系統] 老玩家回歸: {name}")
    else:
        token = str(uuid.uuid4())
        db_players[token] = {
            'name': name,
            'chips': 1000000,  # 🎁 新手送 100 萬
            'debt': 0
        }
        p = db_players[token]
        print(f"✨ [系統] 新玩家加入: {name}")

    # 🔴 2. 絕對不能漏掉的一行：綁定連線 ID 與身分證！
    sid_map[sid] = {'token': token, 'room': None}
    
    # 3. 發送最新資料給前端
    emit('login_success', {'token': token, 'name': p['name'], 'chips': p['chips'], 'debt': p['debt']})
    socketio.emit('chat_msg', {'name': '📢 系統', 'msg': f"{p['name']} 進入了賭場..."}, broadcast=True)

@socketio.on('join_game_room')
def join_game_room(data):
    sid = request.sid
    room_id = data.get('room')
    tok = sid_map.get(sid, {}).get('token')
    
    if not tok or room_id not in games: return
    
    leave_room('lobby')
    join_room(room_id)
    sid_map[sid]['room'] = room_id
    
    wallet = db_players[tok]
    game_state = games[room_id]
    
    existing_p = None
    for old_sid, p in list(game_state['players'].items()):
        if p.get('token') == tok:
            existing_p = p
            del game_state['players'][old_sid]
            break
            
    if existing_p:
        existing_p['is_online'] = True
        existing_p['name'] = wallet['name']
        game_state['players'][sid] = existing_p
        game_state['players'][sid]['id'] = sid[:4]
        socketio.emit('chat_msg', {'msg': f"📢 {wallet['name']} 無縫重連回到了房間。"}, room=room_id)
    else:
        p_status = 'ACTIVE' if game_state['phase'] in ['WAITING', 'VOTING', 'GAMEOVER'] else 'OBSERVING' 
        p_data = {
            'id': sid[:4], 'name': wallet['name'], 'token': tok, 'is_online': True,
            'chips': wallet['chips'], 'debt': wallet['debt'], 'recharge': wallet['recharge'], 
            'status': p_status, 'spec_target': None, 'spec_bet': 0
        }
        
        if room_id == 'blackjack':
            p_data.update({'bet': 0, 'hand': [], 'ally': None, 'secret': None, 'cheats': 3, 'skill_used': False, 'has_reported': False, 'fake_used': False, 'vote': None, 'split_hand': [], 'split_bet': 0, 'main_status': 'WAITING', 'split_status': 'WAITING', 'active_hand': 'main'})
        elif room_id == 'tax':
            p_data.update({'tax_paid': -1, 'exile_vote': None})
        elif room_id == 'auction':
            p_data.update({'bid': 0, 'total_spent': 0, 'total_won': 0}) # 拍賣會專用欄位
            
        game_state['players'][sid] = p_data
        msg = "進入了房間。" if p_status == 'ACTIVE' else "進入了房間 (觀戰中)。"
        socketio.emit('chat_msg', {'msg': f"📢 {wallet['name']} {msg}"}, room=room_id)
    
    emit('joined_room', {'room': room_id})
    broadcast_state(room_id)

@socketio.on('return_to_lobby')
def return_to_lobby():
    sid = request.sid
    room_id = sid_map.get(sid, {}).get('room')
    tok = sid_map.get(sid, {}).get('token')
    
    if room_id and room_id != 'lobby':
        save_wallet(sid) 
        p = games[room_id]['players'].get(sid)
        if p:
            p['status'] = 'LEFT'
            socketio.emit('chat_msg', {'msg': f"🏃 {p['name']} 退回了大廳。"}, room=room_id)
        
        leave_room(room_id)
        sid_map[sid]['room'] = 'lobby'
        join_room('lobby')
        
        emit('login_success', {
            'token': tok, 'name': db_players[tok]['name'],
            'chips': db_players[tok]['chips'], 'debt': db_players[tok]['debt']
        })
        
        if room_id == 'blackjack':
            ps = [player for player in games[room_id]['players'].values() if player['status'] not in ['ELIMINATED', 'LEFT', 'OBSERVING']]
            if len(ps) < 2 and games[room_id]['phase'] not in ['WAITING', 'GAMEOVER', 'VOTING']: 
                socketio.start_background_task(final_settlement)
            else: check_phase_complete()
        elif room_id == 'auction':
            ps = [player for player in games[room_id]['players'].values() if player['status'] not in ['ELIMINATED', 'LEFT', 'OBSERVING']]
            if len(ps) < 2 and games[room_id]['phase'] not in ['WAITING', 'GAMEOVER', 'VOTING']:
                socketio.start_background_task(resolve_auction_gameover)
                
        broadcast_state(room_id)

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    room_id = sid_map.get(sid, {}).get('room')
    
    if room_id and room_id in games:
        save_wallet(sid)
        p = games[room_id]['players'].get(sid)
        if p:
            p['is_online'] = False
            socketio.emit('chat_msg', {'msg': f"🔌 {p['name']} 暫時斷線了。"}, room=room_id)
            broadcast_state(room_id)

# ==========================================
# 🔨 【100票拍賣會 遊戲邏輯】
# ==========================================
@socketio.on('start_auction_game')
def start_auction_game():
    game = games['auction']
    # 檢查資金門檻 (至少要有 100萬 也就是 10票)
    valid_ps = []
    for sid, p in game['players'].items():
        if p['status'] not in ['LEFT', 'OBSERVING'] and p.get('is_online', True):
            if p['chips'] >= 1000000:
                valid_ps.append((sid, p))
            else:
                p['status'] = 'OBSERVING'
                socketio.emit('chat_msg', {'msg': f"❌ {p['name']} 資金不足 10 票 (¥1,000,000)，失去競標資格，轉為觀戰。"}, room='auction')

    if len(valid_ps) < 2:
        emit('chat_msg', {'msg': '❌ 至少需要 2 名擁有 ¥1,000,000 以上的玩家才能開局！'}, room='auction')
        return

    # 尋找首富擔任莊家
    richest_sid = None
    max_net = -float('inf')
    for sid, p in valid_ps:
        net = p['chips'] - p['debt']
        if net > max_net:
            max_net = net
            richest_sid = sid

    game['dealer_sid'] = richest_sid
    game['dealer_bids_left'] = 5
    game['round'] = 1
    game['phase'] = 'BIDDING'
    game['current_bids'] = {}
    game['highest_bid'] = 0

    dealer_name = game['players'][richest_sid]['name']
    
    for sid, p in valid_ps:
        p['status'] = 'ACTIVE'
        p['bid'] = 0
        p['total_spent'] = 0
        p['total_won'] = 0

    socketio.emit('chat_msg', {'msg': f"⚖️ 【100票拍賣會】正式開始！<br>👑 系統已任命全場首富 <strong>{dealer_name}</strong> 為本次拍賣的莊家！"}, room='auction')
    socketio.emit('chat_msg', {'msg': f"🔨 第 1 局競標開始！拍賣品：100 票 (價值 ¥10,000,000)"}, room='auction')
    broadcast_state('auction')

@socketio.on('submit_bid')
def submit_bid(data):
    sid = request.sid
    game = games['auction']
    p = game['players'].get(sid)
    
    if not p or game['phase'] != 'BIDDING' or p['status'] != 'ACTIVE': return
    
    try: tickets = int(data.get('tickets', 0))
    except: return
    
    cost = tickets * 100000  # 1 票 = 10 萬
    
    if cost > p['chips']:
        emit('chat_msg', {'msg': '❌ 你的籌碼不足以支付此出價！'})
        return
        
    if sid == game['dealer_sid']:
        if game['dealer_bids_left'] <= 0:
            emit('chat_msg', {'msg': '❌ 莊家參賽次數已耗盡 (限 5 次)！'})
            return
        if tickets > 0:
            game['dealer_bids_left'] -= 1
            socketio.emit('chat_msg', {'msg': f"🤫 (莊家暗中消耗了 1 次參賽權...)"}, room=sid)

    # 記錄出價並扣除籌碼 (全付費制)
    if tickets >= 0:
        p['bid'] = cost
        p['total_spent'] += cost
        p['chips'] -= cost
        game['current_bids'][sid] = cost
        save_wallet(sid)
        
        socketio.emit('chat_msg', {'msg': f"🔒 你已出價 {tickets} 票。"}, room=sid)
        
        # 匿名更新最高出價
        if cost > game['highest_bid']:
            game['highest_bid'] = cost
            socketio.emit('play_sound', {'sound': 'sfx-deal'}, room='auction')
            socketio.emit('chat_msg', {'msg': f"📈 價格更新！目前最高出價：{int(game['highest_bid']/100000)} 票！"}, room='auction')

        # 檢查是否所有人都出價完畢
        active_ps = [psid for psid, player in game['players'].items() if player['status'] == 'ACTIVE']
        if len(game['current_bids']) >= len(active_ps):
            socketio.start_background_task(resolve_auction_round)
        else:
            broadcast_state('auction')

def resolve_auction_round():
    game = games['auction']
    game['phase'] = 'RESOLVING'
    broadcast_state('auction')
    
    socketio.emit('chat_msg', {'msg': "⏳ 競標結束，正在結算..."}, room='auction')
    time.sleep(2)
    
    dealer_p = game['players'].get(game['dealer_sid'])
    bids = game['current_bids']
    
    if not bids or game['highest_bid'] == 0:
        socketio.emit('chat_msg', {'msg': "💨 本局無人出價，商品流局！"}, room='auction')
    else:
        # 找出最高出價者
        max_bid = game['highest_bid']
        winners = [s for s, b in bids.items() if b == max_bid]
        
        # 所有人的出價金，全部歸莊家所有 (全付費拍賣核心)
        total_pool = sum(bids.values())
        if dealer_p:
            dealer_p['chips'] += total_pool
            save_wallet(game['dealer_sid'])
        
        if len(winners) > 1:
            socketio.emit('chat_msg', {'msg': f"💥 最高出價平手 ({int(max_bid/100000)} 票)！商品流局，但所有人的出價已被莊家沒收！"}, room='auction')
            socketio.emit('play_sound', {'sound': 'sfx-lose'}, room='auction')
        else:
            winner_sid = winners[0]
            winner_p = game['players'].get(winner_sid)
            
            if winner_p:
                winner_p['chips'] += 10000000  # 贏得 100 票 (1000萬)
                winner_p['total_won'] += 10000000
                save_wallet(winner_sid)
                
                socketio.emit('chat_msg', {'msg': f"🎉 恭喜得標！{winner_p['name']} 以 {int(max_bid/100000)} 票贏得了 100 票！<br>🩸 (其餘玩家的出價已全數歸莊家所有)"}, room='auction')
                socketio.emit('play_sound', {'sound': 'sfx-win'}, room=winner_sid)

    time.sleep(4)
    game['round'] += 1
    
    if game['round'] > 10:
        socketio.start_background_task(resolve_auction_gameover)
    else:
        game['phase'] = 'BIDDING'
        game['current_bids'] = {}
        game['highest_bid'] = 0
        for p in game['players'].values(): p['bid'] = 0
        socketio.emit('chat_msg', {'msg': f"🔨 第 {game['round']} 局競標開始！拍賣品：100 票 (價值 ¥10,000,000)"}, room='auction')
        broadcast_state('auction')

def resolve_auction_gameover():
    game = games['auction']
    game['phase'] = 'GAMEOVER'
    broadcast_state('auction')
    
    results = []
    for sid, p in game['players'].items():
        if p['status'] != 'LEFT':
            if sid == game['dealer_sid']:
                net = p['chips'] - (10000000 if p.get('total_spent') == 0 else p['chips'] - p.get('total_won',0) + p.get('total_spent',0)) # 莊家收益粗略估算顯示
                results.append({'sid': sid, 'name': f"👑[莊家] {p['name']}", 'net': p['chips'] - db_players[p['token']]['debt'], 'chips': p['chips']})
            else:
                net = p.get('total_won', 0) - p.get('total_spent', 0)
                results.append({'sid': sid, 'name': p['name'], 'net': net, 'chips': p['chips']})
            
    results.sort(key=lambda x: x['net'], reverse=True)
    
    if len(results) > 0:
        max_net = results[0]['net']
        for r in results:
            if r['net'] == max_net and r['net'] > 0: socketio.emit('play_sound', {'sound': 'sfx-win'}, room=r['sid'])
            else: socketio.emit('play_sound', {'sound': 'sfx-lose'}, room=r['sid'])
    
    msg = "<br>========================<br>🏁 【拍賣會 最終清算】<br>========================<br>"
    for r in results:
        if r['net'] >= 0: msg += f"📈 <strong>{r['name']}</strong>: 本場淨利 ¥{r['net']:,} (餘額:¥{r['chips']:,})<br>"
        else: msg += f"🩸 <strong style='color:red;'>{r['name']}</strong>: 沉沒虧損 ¥{r['net']:,} (餘額:¥{r['chips']:,})<br>"
            
    socketio.emit('chat_msg', {'msg': msg}, room='auction')
    time.sleep(5)
    
    game['phase'] = 'VOTING'
    socketio.emit('chat_msg', {'msg': '即將進入【結算階段】...請選擇退回大廳或重新開始。'}, room='auction')
    for p in game['players'].values(): 
        p['vote'] = None
        if p['status'] == 'OBSERVING': p['status'] = 'ACTIVE'
    broadcast_state('auction')


# ==========================================
# 💰 【稅金遊戲 遊戲邏輯】
# ==========================================
@socketio.on('start_tax_game')
def start_tax_game():
    game = games['tax']
    ps = [p for p in game['players'].values() if p['status'] not in ['LEFT', 'OBSERVING'] and p.get('is_online', True)]
    if len(ps) < 2:
        emit('chat_msg', {'msg': '❌ 至少需要 2 人在線才能開局 (建議3人以上)'})
        return
    game['round'] = 1
    game['phase'] = 'TAXING'
    for p in game['players'].values():
        p['tax_paid'] = -1
        p['exile_vote'] = None
        if p['status'] == 'OBSERVING': p['status'] = 'ACTIVE'
    socketio.emit('chat_msg', {'msg': '⚖️ 稅金遊戲開始！第 1 局補助金 ¥5,000,000 已下發。'}, room='tax')
    broadcast_state('tax')

@socketio.on('submit_tax')
def submit_tax(data):
    sid = request.sid
    game = games['tax']
    p = game['players'].get(sid)
    if not p or game['phase'] != 'TAXING' or p['status'] != 'ACTIVE': return
    
    try: amount = int(data.get('amount', 0))
    except: return
    
    if 0 <= amount <= 5000000:
        p['tax_paid'] = amount
        socketio.emit('chat_msg', {'msg': f"🔒 {p['name']} 已完成申報。"}, room='tax')
        
        active_ps = [player for player in game['players'].values() if player['status'] == 'ACTIVE']
        if all(player['tax_paid'] >= 0 for player in active_ps):
            socketio.start_background_task(resolve_tax_round)
        else:
            broadcast_state('tax')
    else:
        emit('chat_msg', {'msg': '❌ 申報金額不合法！'})

def resolve_tax_round():
    game = games['tax']
    active_ps = [p for p in game['players'].values() if p['status'] == 'ACTIVE']
    
    total_tax = sum(p['tax_paid'] for p in active_ps)
    multiplier = 2
    reward_pool = total_tax * multiplier
    payout_per_player = int(reward_pool / len(active_ps))
    
    for sid, p in game['players'].items():
        if p['status'] == 'ACTIVE':
            private_stash = 5000000 - p['tax_paid']
            round_profit = private_stash + payout_per_player
            p['chips'] += round_profit
            save_wallet(sid)
            
    socketio.emit('chat_msg', {'msg': f"<br>================<br>💰 第 {game['round']} 局結算：<br>全國總稅金：¥{total_tax:,}<br>翻倍總預算：¥{reward_pool:,}<br>每人獲得配給：¥{payout_per_player:,}<br>================"}, room='tax')
    socketio.emit('play_sound', {'sound': 'sfx-deal'}, room='tax')
    
    time.sleep(3)
    game['round'] += 1
    
    if game['round'] > 5:
        game['phase'] = 'VOTING_EXILE'
        socketio.emit('chat_msg', {'msg': '🔥 5局結束！請投票流放你認為最貪婪的逃稅犯！'}, room='tax')
    else:
        game['phase'] = 'TAXING'
        for p in active_ps: p['tax_paid'] = -1
        socketio.emit('chat_msg', {'msg': f"⚖️ 第 {game['round']} 局開始！補助金 ¥5,000,000 已下發。"}, room='tax')
        
    broadcast_state('tax')

@socketio.on('submit_exile_vote')
def submit_exile_vote(data):
    sid = request.sid
    game = games['tax']
    p = game['players'].get(sid)
    target = data.get('target')
    
    if p and game['phase'] == 'VOTING_EXILE' and p['status'] == 'ACTIVE':
        p['exile_vote'] = target
        socketio.emit('chat_msg', {'msg': f"🗳️ {p['name']} 已投出流放票。"}, room='tax')
        
        active_ps = [player for player in game['players'].values() if player['status'] == 'ACTIVE']
        if all(player['exile_vote'] is not None for player in active_ps):
            socketio.start_background_task(resolve_exile)
        else:
            broadcast_state('tax')

def resolve_exile():
    game = games['tax']
    active_ps = [p for p in game['players'].values() if p['status'] == 'ACTIVE']
    
    votes = {}
    for p in active_ps:
        v = p['exile_vote']
        if v in votes: votes[v] += 1
        else: votes[v] = 1
        
    max_v = max(votes.values())
    exiled_sids = [s for s, v in votes.items() if v == max_v]
    
    if len(exiled_sids) == len(active_ps):
         socketio.emit('chat_msg', {'msg': "⚖️ 所有玩家平票！無人被流放，大家都是共犯。"}, room='tax')
    else:
        penalty = 20000000
        for esid in exiled_sids:
            ep = game['players'].get(esid)
            if ep:
                ep['chips'] -= penalty
                socketio.emit('chat_msg', {'msg': f"🔥 【流放天罰】<strong style='color:red;'>{ep['name']}</strong> 獲得最多票數，被強制沒收財產 ¥{penalty:,}！"}, room='tax')
                socketio.emit('play_sound', {'sound': 'sfx-lose'}, room=esid)
                save_wallet(esid)
                
    time.sleep(3)
    game['phase'] = 'WAITING'
    for p in game['players'].values():
        p['tax_paid'] = -1
        p['exile_vote'] = None
    socketio.emit('chat_msg', {'msg': "🔄 遊戲結束，等待重新開局或退回大廳。"}, room='tax')
    broadcast_state('tax')


# ==========================================
# 🃏 【絕望 21 點 遊戲邏輯】
# ==========================================

@socketio.on('start_game')
def start_game():
    game = games['blackjack']
    ps = [player for player in game['players'].values() if player['status'] not in ['ELIMINATED', 'LEFT', 'OBSERVING'] and player.get('is_online', True)]
    if len(ps) < 2: emit('chat_msg', {'msg': '❌ 至少需要 2 人在線才能開局'}, room='blackjack'); return
    game['round'] = 1; game['phase'] = 'ANTE'
    socketio.emit('chat_msg', {'msg': '🎲 遊戲正式開始！進入第一階段：【支付盲注】'}, room='blackjack')
    broadcast_state('blackjack')

@socketio.on('pay_ante')
def pay_ante():
    sid = request.sid; game = games['blackjack']; p = game['players'].get(sid); 
    if not p: return
    req_ante = game['round'] * 1000000
    if game['phase'] == 'ANTE' and p['status'] == 'ACTIVE':
        if p['chips'] >= req_ante:
            p['bet'] = req_ante; p['status'] = 'READY_ANTE'; save_wallet(sid)
            emit('chat_msg', {'msg': f"✅ 已支付盲注 ¥{req_ante:,}"})
            check_phase_complete()
        else: emit('chat_msg', {'msg': f"❌ 籌碼不足 ¥{req_ante:,}，請向莊家借貸！"})

@socketio.on('place_side_bet')
def place_side_bet(data):
    sid = request.sid; game = games['blackjack']; p = game['players'].get(sid)
    if not p or p['status'] not in ['ELIMINATED', 'LEFT', 'OBSERVING']: return
    if game['phase'] in ['WAITING', 'GAMEOVER', 'VOTING', 'SETTLEMENT']: return
    target_id = data.get('target')
    try: amt = int(data.get('amount', 0))
    except: return
    if amt < 1000000 or amt > p['chips']: emit('chat_msg', {'msg': '❌ 場外下注失敗：金額不足或超過餘額！'}); return
    p['spec_target'] = target_id; p['spec_bet'] = amt; p['chips'] -= amt; save_wallet(sid)
    target_p = game['players'].get(target_id); t_name = target_p['name'] if target_p else "某人"
    socketio.emit('chat_msg', {'msg': f"🎫 【場外插花】觀戰的 {p['name']} 押注了 ¥{amt:,} 賭 {t_name} 會贏！"}, room='blackjack')
    broadcast_state('blackjack')

@socketio.on('recharge')
def handle_recharge(data):
    sid = request.sid; room = sid_map.get(sid, {}).get('room'); 
    if not room or room not in games: return
    game = games[room]; p = game['players'].get(sid)
    try: amt = int(data.get('amount', 0))
    except: emit('chat_msg', {'msg': "❌ 無效的借款金額！"}); return

    if p and p['recharge'] > 0:
        if amt < 1000000 or amt % 1000000 != 0: emit('chat_msg', {'msg': "❌ 借貸金額必須大於等於 100 萬，且以百萬為單位！"}); return
        p['chips'] += amt; p['debt'] += amt; p['recharge'] -= 1; save_wallet(sid)
        if p['status'] == 'ELIMINATED': p['status'] = 'OBSERVING'; socketio.emit('chat_msg', {'msg': f"💸 【死者甦醒】{p['name']} 簽下高利貸借了 ¥{amt:,}！將重返牌桌！"}, room=room)
        else: socketio.emit('chat_msg', {'msg': f"💸 {p['name']} 向莊家借貸了 ¥{amt:,} 補充籌碼！"}, room=room)
        broadcast_state(room)
    else: emit('chat_msg', {'msg': "❌ 借貸次數已耗盡，銀行拒絕了你的申請！"})

@socketio.on('choose_ally')
def choose_ally(data):
    sid = request.sid; game = games['blackjack']; p = game['players'].get(sid)
    p['ally'] = data.get('target') or None; p['status'] = 'READY_ALLY'
    emit('chat_msg', {'msg': "🤝 結盟抉擇已鎖定"})
    check_phase_complete()

@socketio.on('secret_action')
def secret_action(data):
    sid = request.sid; game = games['blackjack']; p = game['players'].get(sid)
    choice = data.get('choice')
    if choice == '3' and p['fake_used']: emit('chat_msg', {'msg': '❌ 假牌每場限用一次！'}); return
    if p['ally'] is None and choice in ['1', '2']: emit('chat_msg', {'msg': '❌ 孤狼玩家無法選擇合作或背叛！'}); return
    p['secret'] = choice; p['status'] = 'READY_RIGGING'
    emit('chat_msg', {'msg': "🤫 秘密抉擇已記錄。"})
    check_phase_complete()

def check_phase_complete():
    game = games['blackjack']; ps = [p for p in game['players'].values() if p['status'] not in ['ELIMINATED', 'LEFT', 'OBSERVING']]
    if len(ps) < 2 and game['phase'] not in ['WAITING', 'GAMEOVER', 'VOTING']: socketio.start_background_task(final_settlement); return

    if game['phase'] == 'ANTE' and all(p['status'] == 'READY_ANTE' for p in ps):
        game['phase'] = 'ALLIANCE'; socketio.emit('chat_msg', {'msg': '🤝 進入第二階段：【公開結盟】。請選擇你的共犯'}, room='blackjack')
    elif game['phase'] == 'ALLIANCE' and all(p['status'] == 'READY_ALLY' for p in ps):
        game['phase'] = 'RIGGING'; socketio.emit('chat_msg', {'msg': '🤫 進入第三階段：【秘密做牌】。決定你的忠誠或背叛'}, room='blackjack')
    elif game['phase'] == 'RIGGING' and all(p['status'] == 'READY_RIGGING' for p in ps): socketio.start_background_task(process_inspection)
    elif game['phase'] == 'RAISE_BET' and all(p['status'] in ['ACTION', 'BUSTED', 'STAY'] for p in ps):
        game['phase'] = 'ACTION'; socketio.emit('chat_msg', {'msg': '🃏 進入第六階段：【局中對決】。開始操作！'}, room='blackjack')
        if all(p['status'] in ['BUSTED', 'STAY'] for p in ps): resolve_round()
    broadcast_state('blackjack')

def process_inspection():
    game = games['blackjack']; game['phase'] = 'INSPECTION'; broadcast_state('blackjack')
    socketio.emit('chat_msg', {'msg': '👁️ 莊家發牌，並掃描千術中...'}, room='blackjack'); time.sleep(2)
    game['deck'] = [s + r for s in SUITS for r in RANKS] * 6; random.shuffle(game['deck'])

    for sid, p in game['players'].items():
        if p['status'] in ['ELIMINATED', 'LEFT', 'OBSERVING']: continue
        is_caught = False
        if p['secret'] == '1' and random.randint(1, 100) <= 30: is_caught = True
        elif p['secret'] == '2' and random.randint(1, 100) <= 40: is_caught = True
        elif p['secret'] == '3' and random.randint(1, 100) <= 55: is_caught = True
        
        if is_caught:
            penalty = p['bet'] * 6; p['chips'] -= penalty; p['status'] = 'BUSTED'; save_wallet(sid)
            socketio.emit('play_sound', {'sound': 'sfx-lose'}, room=sid)
            socketio.emit('chat_msg', {'msg': f"🚨 莊家抓包！{p['name']} 出千，慘賠 ¥{penalty:,}！"}, room='blackjack')
        else:
            if p['secret'] == '3': p['hand'] = [random.choice(SUITS)+'A', random.choice(SUITS)+'K']; p['fake_used'] = True
            elif p['secret'] == '1': p['hand'] = [random.choice(SUITS)+'10', random.choice(SUITS)+'Q']
            else: p['hand'] = [game['deck'].pop(), game['deck'].pop()]
            p['status'] = 'WAITING_RAISE'; p['main_status'] = 'ACTION'; p['split_status'] = 'WAITING'; p['active_hand'] = 'main'

    game['dealer_hand'] = [game['deck'].pop(), game['deck'].pop()]; game['phase'] = 'RAISE_BET'
    socketio.emit('chat_msg', {'msg': '📈 進入第五階段：【看牌加注】。請根據手牌決定是否加注！'}, room='blackjack')
    broadcast_state('blackjack')
    ps = [p for p in game['players'].values() if p['status'] not in ['ELIMINATED', 'LEFT', 'OBSERVING']]
    if all(p['status'] == 'BUSTED' for p in ps): socketio.start_background_task(resolve_round)

@socketio.on('raise_bet')
def raise_bet(data):
    sid = request.sid; game = games['blackjack']; p = game['players'].get(sid)
    if game['phase'] == 'RAISE_BET' and p['status'] == 'WAITING_RAISE':
        try:
            amt = int(data.get('amount', 0))
            if amt < 0 or (p['bet'] + amt) > 20000000 or amt > (p['chips'] - p['bet']): return
            p['bet'] += amt; p['status'] = 'ACTION'; save_wallet(sid)
            if amt > 0: socketio.emit('chat_msg', {'msg': f"💰 {p['name']} 加注了 ¥{amt:,} (總注: ¥{p['bet']:,})"}, room='blackjack')
            else: socketio.emit('chat_msg', {'msg': f"✊ {p['name']} 過牌 (總注: ¥{p['bet']:,})"}, room='blackjack')
            check_phase_complete()
        except: pass

@socketio.on('request_swap')
def request_swap(data):
    sid = request.sid; game = games['blackjack']; p = game['players'].get(sid)
    if not p or p['cheats'] < 1 or not p['ally']: return
    ally_sid = p['ally']; ally_p = game['players'].get(ally_sid); 
    if not ally_p: return
    my_target, my_idx = data.get('my_choice', 'main_0').split('_'); ally_target, ally_idx = data.get('ally_choice', 'main_0').split('_')
    my_idx, ally_idx = int(my_idx), int(ally_idx)
    my_list = p['hand'] if my_target == 'main' else p['split_hand']; ally_list = ally_p['hand'] if ally_target == 'main' else ally_p['split_hand']
    
    if my_idx < len(my_list) and ally_idx < len(ally_list):
        game['pending_swaps'][ally_sid] = {'from_sid': sid, 'my_target': my_target, 'my_idx': my_idx, 'ally_target': ally_target, 'ally_idx': ally_idx}
        emit('incoming_swap', {'from_name': p['name'], 'offer_card': my_list[my_idx], 'want_card': ally_list[ally_idx]}, room=ally_sid)

@socketio.on('answer_swap')
def answer_swap(data):
    sid = request.sid; game = games['blackjack']; accept = data.get('accept', False)
    swap_info = game['pending_swaps'].pop(sid, None)
    if not swap_info: return
    from_sid = swap_info['from_sid']; p_from = game['players'].get(from_sid); p_to = game['players'].get(sid)
    
    if accept and p_from and p_to and p_from['cheats'] >= 1:
        m_target, m_idx = swap_info['my_target'], swap_info['my_idx']; a_target, a_idx = swap_info['ally_target'], swap_info['ally_idx']
        m_list = p_from['hand'] if m_target == 'main' else p_from['split_hand']; a_list = p_to['hand'] if a_target == 'main' else p_to['split_hand']
        if m_idx < len(m_list) and a_idx < len(a_list):
            m_list[m_idx], a_list[a_idx] = a_list[a_idx], m_list[m_idx]; p_from['cheats'] -= 1; p_from['skill_used'] = True
            socketio.emit('chat_msg', {'msg': f"🤫 你們的暗中換牌已成功執行。"}, room=from_sid)
            socketio.emit('chat_msg', {'msg': f"🤫 你們的暗中換牌已成功執行。"}, room=sid)
            broadcast_state('blackjack')
    else:
        if p_from: socketio.emit('chat_msg', {'msg': f"❌ 你的盟友拒絕了這筆交易！"}, room=from_sid)

@socketio.on('game_action')
def game_action(data):
    sid = request.sid; game = games['blackjack']; p = game['players'].get(sid)
    if p['status'] != 'ACTION': return
    act = data.get('act')
    active_h = p['hand'] if p['active_hand'] == 'main' else p['split_hand']; active_b = p['bet'] if p['active_hand'] == 'main' else p['split_bet']; current_total_bet = p['bet'] + p['split_bet']

    if act == 'split':
        if len(p['hand']) == 2 and p['hand'][0][1:] == p['hand'][1][1:] and not p['split_hand']:
            if (p['chips'] - current_total_bet) >= p['bet']:
                p['split_bet'] = p['bet']; p['split_hand'] = [p['hand'].pop()]; p['hand'].append(game['deck'].pop()); p['split_hand'].append(game['deck'].pop()); p['split_status'] = 'ACTION'; save_wallet(sid)
                socketio.emit('chat_msg', {'msg': f"✂️ {p['name']} 發動了【拆牌】！賭注翻倍，雙線操作！"}, room='blackjack')
            else: emit('chat_msg', {'msg': '❌ 籌碼不足以支付拆牌的額外底金！'})
        return

    elif act == 'h':
        active_h.append(game['deck'].pop())
        if calculate_hand(active_h) > 21:
            h_name = '主牌' if p['active_hand'] == 'main' else '拆牌'; socketio.emit('play_sound', {'sound': 'sfx-lose'}, room=sid); socketio.emit('chat_msg', {'msg': f"💥 {p['name']} 的【{h_name}】爆牌了！"}, room='blackjack')
            if p['active_hand'] == 'main':
                p['main_status'] = 'BUSTED'
                if p['split_hand']: p['active_hand'] = 'split'
                else: p['status'] = 'BUSTED'
            else: p['split_status'] = 'BUSTED'; p['status'] = 'STAY' 

    elif act == 'h_raise':
        try:
            amt = int(data.get('amount', 0))
            if amt > 0 and (active_b + amt) <= 20000000 and amt <= (p['chips'] - current_total_bet):
                if p['active_hand'] == 'main': p['bet'] += amt
                else: p['split_bet'] += amt
                save_wallet(sid); active_h.append(game['deck'].pop()); h_name = '主牌' if p['active_hand'] == 'main' else '拆牌'; socketio.emit('chat_msg', {'msg': f"💸 {p['name']} 對【{h_name}】瘋狂追注 ¥{amt:,} 並抽牌！"}, room='blackjack')
                if calculate_hand(active_h) > 21:
                    socketio.emit('play_sound', {'sound': 'sfx-lose'}, room=sid); socketio.emit('chat_msg', {'msg': f"💥 {p['name']} 貪心爆牌了！"}, room='blackjack')
                    if p['active_hand'] == 'main':
                        p['main_status'] = 'BUSTED'
                        if p['split_hand']: p['active_hand'] = 'split'
                        else: p['status'] = 'BUSTED'
                    else: p['split_status'] = 'BUSTED'; p['status'] = 'STAY'
            else: emit('chat_msg', {'msg': '❌ 追注不合法（不可超過餘額或單局20M上限）'}); return
        except: return

    elif act == 's': 
        if p['active_hand'] == 'main':
            p['main_status'] = 'STAY'
            if p['split_hand']: p['active_hand'] = 'split'
            else: p['status'] = 'STAY'
        else: p['split_status'] = 'STAY'; p['status'] = 'STAY'
            
    elif act == '1':
        if p['cheats'] >= 1: p['cheats'] -= 1; emit('chat_msg', {'msg': f"👁️ 莊家底牌是: {game['dealer_hand'][1]}"})
        else: emit('chat_msg', {'msg': '❌ 透視失敗：千術點數不足！'})
        
    elif act == '3':
        if p['cheats'] >= 2: p['cheats'] -= 2; p['skill_used'] = True; active_h[-1] = game['deck'].pop(); emit('chat_msg', {'msg': "✨ 魔術手發動，替換了最後一張牌！"}); socketio.emit('chat_msg', {'msg': f"⚠️ {p['name']} 的手牌發生了不自然的變動..."}, room='blackjack')
        else: emit('chat_msg', {'msg': '❌ 魔術手失敗：千術點數不足 (需2點)！'})
        
    elif act == 'report':
        target_sid = data.get('target'); target_p = game['players'].get(target_sid)
        if not target_p or p['has_reported'] or target_p['status'] in ['BUSTED', 'ELIMINATED', 'LEFT', 'OBSERVING']: return
        p['has_reported'] = True; is_cheater = (target_p['secret'] != '0') or target_p['skill_used']
        if is_cheater:
            penalty = target_p['bet'] * 3; target_p['chips'] -= penalty; p['chips'] += penalty; target_p['status'] = 'BUSTED'; save_wallet(sid); save_wallet(target_sid); socketio.emit('play_sound', {'sound': 'sfx-lose'}, room=target_sid) 
            socketio.emit('chat_msg', {'msg': f"🚨 【檢舉成功】{p['name']} 揭穿了 {target_p['name']} 的千術！奪取賠償金 ¥{penalty:,}！"}, room='blackjack')
        else:
            penalty = p['bet'] * 3; p['chips'] -= penalty; target_p['chips'] += penalty; save_wallet(sid); save_wallet(target_sid); socketio.emit('play_sound', {'sound': 'sfx-lose'}, room=sid) 
            socketio.emit('chat_msg', {'msg': f"🤡 【誣告天罰】{p['name']} 企圖抹黑清白的 {target_p['name']}！慘賠精神損失 ¥{penalty:,}！"}, room='blackjack')
    
    ps = [p for p in game['players'].values() if p['status'] not in ['ELIMINATED', 'LEFT', 'OBSERVING']]
    if all(p['status'] in ['STAY', 'BUSTED'] for p in ps): resolve_round()
    else: broadcast_state('blackjack')

def resolve_round():
    game = games['blackjack']; game['phase'] = 'SETTLEMENT'; game['pending_swaps'] = {} 
    while calculate_hand(game['dealer_hand']) < 17: game['dealer_hand'].append(game['deck'].pop())
    d_val = calculate_hand(game['dealer_hand']); socketio.emit('chat_msg', {'msg': f"🏁 莊家最終點數: {d_val} {game['dealer_hand']}"}, room='blackjack')
    winners_set = set()

    for sid, p in game['players'].items():
        if p['status'] in ['ELIMINATED', 'LEFT', 'OBSERVING']: continue
        hands_info = [(p['hand'], p['bet'], p['main_status'], '主牌')]
        if p['split_hand']: hands_info.append((p['split_hand'], p['split_bet'], p['split_status'], '拆牌'))
        
        survived_hands = 0
        for h, b, s, h_name in hands_info:
            if s == 'BUSTED':
                p['chips'] -= b; socketio.emit('chat_msg', {'msg': f"💸 {p['name']} 【{h_name}】爆牌輸了 ¥{b:,}"}, room='blackjack'); continue
            p_val = calculate_hand(h)
            if p_val <= 21 and (d_val > 21 or p_val > d_val): p['chips'] += b; socketio.emit('chat_msg', {'msg': f"🎉 {p['name']} 【{h_name}】贏了 ¥{b:,}"}, room='blackjack'); survived_hands += 1; winners_set.add(sid)
            elif p_val > 21 or p_val < d_val: p['chips'] -= b; socketio.emit('chat_msg', {'msg': f"💸 {p['name']} 【{h_name}】輸了 ¥{b:,}"}, room='blackjack')
            else: socketio.emit('chat_msg', {'msg': f"🤝 {p['name']} 【{h_name}】與莊家平手。"}, room='blackjack'); survived_hands += 1
        
        if p['secret'] == '2' and p['ally']:
            ally_p = game['players'].get(p['ally'])
            if ally_p and ally_p['secret'] == '1':
                if survived_hands > 0:
                    steal = int((p['bet'] + p['split_bet']) * 1.5); p['chips'] += steal; ally_p['chips'] -= steal
                    socketio.emit('chat_msg', {'msg': f"⚡ {p['name']} 背叛成功！奪取 {ally_p['name']} ¥{steal:,}"}, room='blackjack')
                else: socketio.emit('chat_msg', {'msg': f"🤡 {p['name']} 企圖背叛，但因全盤爆牌導致搶奪失敗！"}, room='blackjack')

    for sid, p in game['players'].items():
        if p['status'] in ['ELIMINATED', 'LEFT', 'OBSERVING'] and p.get('spec_target'):
            if p['spec_target'] in winners_set: win_amt = p['spec_bet'] * 2; p['chips'] += win_amt; socketio.emit('chat_msg', {'msg': f"🎰 【插花大賺】{p['name']} 押中贏家，暴賺 ¥{win_amt:,}！"}, room='blackjack')
            else: socketio.emit('chat_msg', {'msg': f"💸 【插花慘賠】{p['name']} 押錯人，白白輸掉了 ¥{p['spec_bet']:,}！"}, room='blackjack')
            p['spec_target'] = None; p['spec_bet'] = 0

    for sid, p in game['players'].items():
        if p['debt'] > 0:
            interest = int(p['debt'] * 0.1); p['debt'] += interest
            if p['status'] != 'LEFT': socketio.emit('chat_msg', {'msg': f"📈 【高利貸】{p['name']} 產生 10% 循環利息 (¥{interest:,})，總負債達 ¥{p['debt']:,}！"}, room='blackjack')
        save_wallet(sid)

    broadcast_state('blackjack'); time.sleep(5)
    
    active_count = 0
    for sid, p in game['players'].items():
        if p['status'] in ['LEFT', 'OBSERVING']: continue
        req_next_ante = (game['round'] + 1) * 1000000
        if p['chips'] < req_next_ante and p['recharge'] == 0: p['status'] = 'ELIMINATED'; socketio.emit('chat_msg', {'msg': f"☠️ 【淘汰】玩家 {p['name']} 籌碼不足以支付下局盲注，黯然退場。"}, room='blackjack')
        if p['status'] not in ['ELIMINATED', 'LEFT', 'OBSERVING']: active_count += 1

    game['round'] += 1
    if game['round'] > 3 or active_count < 2: socketio.start_background_task(final_settlement)
    else:
        game['phase'] = 'ANTE'
        for p in game['players'].values():
            if p['status'] == 'OBSERVING': p['status'] = 'ACTIVE'
            if p['status'] not in ['ELIMINATED', 'LEFT']: p['status'] = 'ACTIVE'; p['hand'] = []; p['bet'] = 0; p['ally'] = None; p['secret'] = None; p['skill_used'] = False; p['has_reported'] = False; p['split_hand'] = []; p['split_bet'] = 0; p['active_hand'] = 'main'; p['spec_target'] = None; p['spec_bet'] = 0
        socketio.emit('chat_msg', {'msg': f"🎲 第 {game['round']} 局開始！本局盲注為 ¥{game['round']*1000000:,}"}, room='blackjack')
        broadcast_state('blackjack')

def final_settlement():
    game = games['blackjack']; game['phase'] = 'GAMEOVER'; game['pending_swaps'] = {}; broadcast_state('blackjack')
    results = []
    for sid, p in game['players'].items():
        if p['status'] != 'LEFT': net_worth = p['chips'] - p['debt']; results.append({'sid': sid, 'name': p['name'], 'net': net_worth, 'chips': p['chips'], 'debt': p['debt']})
    results.sort(key=lambda x: x['net'], reverse=True)
    
    if len(results) > 0:
        max_net = results[0]['net']
        for r in results:
            if r['net'] == max_net: socketio.emit('play_sound', {'sound': 'sfx-win'}, room=r['sid'])
            else: socketio.emit('play_sound', {'sound': 'sfx-lose'}, room=r['sid'])
    
    msg = "<br>========================<br>🏁 【本輪最終清算】<br>公式：淨資產 = 現有籌碼 - 借貸總額<br>========================<br>"
    for r in results:
        if r['net'] >= 0: msg += f"👑 <strong>{r['name']}</strong>: 淨利 ¥{r['net']:,}<br>   <span style='font-size:12px;color:#ccc;'>(手邊:¥{r['chips']:,} | 欠債:¥{r['debt']:,})</span><br>"
        else: msg += f"💀 <strong style='color:red;'>{r['name']}</strong>: 嚴重虧損 ¥{r['net']:,} <br>"
            
    socketio.emit('chat_msg', {'msg': msg}, room='blackjack'); time.sleep(5)
    game['phase'] = 'VOTING'; socketio.emit('chat_msg', {'msg': '即將進入【結算與投票階段】...'}, room='blackjack')
    for p in game['players'].values(): 
        p['vote'] = None
        if p['status'] == 'OBSERVING': p['status'] = 'ACTIVE'
    broadcast_state('blackjack')

@socketio.on('submit_vote')
def submit_vote(data):
    sid = request.sid; room = sid_map.get(sid, {}).get('room'); 
    if not room or room not in games: return
    game = games[room]; p = game['players'].get(sid)
    if not p or game['phase'] != 'VOTING': return
    choice = data.get('choice')
    if choice == 'leave': p['status'] = 'LEFT'; save_wallet(sid); socketio.emit('chat_msg', {'msg': f"🚪 {p['name']} 放棄了賭局，離開牌桌。"}, room=room)
    else: p['vote'] = choice; c_str = "同意重置籌碼" if choice == 'reset' else "保留現狀繼續下一局"; socketio.emit('chat_msg', {'msg': f"🗳️ {p['name']} 已投票: {c_str}"}, room=room)
    
    if room == 'blackjack': check_voting_complete()
    elif room == 'auction': check_auction_voting()

def check_voting_complete():
    game = games['blackjack']; active_ps = [p for p in game['players'].values() if p['status'] not in ['ELIMINATED', 'LEFT', 'OBSERVING']]
    if len(active_ps) < 2:
        game['phase'] = 'WAITING'
        for p in game['players'].values(): p['vote'] = None; p['spec_target'] = None; p['spec_bet'] = 0
        socketio.emit('chat_msg', {'msg': '⏸️ 存活人數不足 2 人，遊戲進入待機狀態。'}, room='blackjack'); broadcast_state('blackjack'); return

    if all(p['vote'] is not None for p in active_ps):
        reset_votes = sum(1 for p in active_ps if p['vote'] == 'reset')
        game['round'] = 1; game['dealer_hand'] = []
        if reset_votes == len(active_ps):
            socketio.emit('chat_msg', {'msg': '🔄 全員達成共識！籌碼洗白，債務清零，回到初始 ¥10,000,000！'}, room='blackjack')
            for sid, p in game['players'].items():
                if p['status'] != 'LEFT': p['chips'] = 10000000; p['debt'] = 0; p['recharge'] = 3; p['cheats'] = 3; p['status'] = 'ACTIVE'; p['hand'] = []; p['bet'] = 0; p['ally'] = None; p['secret'] = None; p['skill_used'] = False; p['has_reported'] = False; p['fake_used'] = False; p['split_hand'] = []; p['split_bet'] = 0; p['active_hand'] = 'main'; p['spec_target'] = None; p['spec_bet'] = 0; save_wallet(sid)
        else:
            socketio.emit('chat_msg', {'msg': '▶️ 未達成全體重置共識，大家將帶著現在的籌碼與負債繼續廝殺！'}, room='blackjack')
            for p in game['players'].values():
                if p['status'] == 'OBSERVING': p['status'] = 'ACTIVE'
                if p['status'] not in ['LEFT', 'ELIMINATED']: p['status'] = 'ACTIVE'; p['hand'] = []; p['bet'] = 0; p['ally'] = None; p['secret'] = None; p['skill_used'] = False; p['has_reported'] = False; p['split_hand'] = []; p['split_bet'] = 0; p['active_hand'] = 'main'; p['spec_target'] = None; p['spec_bet'] = 0
        game['phase'] = 'ANTE'; socketio.emit('chat_msg', {'msg': f"🎲 第 {game['round']} 局開始！本局盲注為 ¥{game['round']*1000000:,}"}, room='blackjack'); broadcast_state('blackjack')

def check_auction_voting():
    game = games['auction']; active_ps = [p for p in game['players'].values() if p['status'] not in ['ELIMINATED', 'LEFT', 'OBSERVING']]
    if len(active_ps) < 2:
        game['phase'] = 'WAITING'
        for p in game['players'].values(): p['vote'] = None
        socketio.emit('chat_msg', {'msg': '⏸️ 存活人數不足 2 人，遊戲進入待機狀態。'}, room='auction'); broadcast_state('auction'); return

    if all(p['vote'] is not None for p in active_ps):
        socketio.emit('chat_msg', {'msg': '▶️ 拍賣會即將重新開始，將重新選出首富擔任莊家。'}, room='auction')
        for p in game['players'].values():
            if p['status'] == 'OBSERVING': p['status'] = 'ACTIVE'
        socketio.start_background_task(start_auction_game)

# 1. 賽馬背景迴圈
def horse_racing_loop():
    while True:
        socketio.sleep(600) # 每 10 分鐘 (600秒)
        
        # 🎲 決定哪匹馬贏 (1-10號)
        # 這裡可以根據你想要的機率權重來骰
        winner = random.choices(range(1, 11), weights=[30, 20, 15, 10, 8, 7, 5, 3, 1.5, 0.5])[0]
        
        # 💰 結算邏輯 (示意)
        # 假設我們計算出本局所有人押錯的總額為 lost_bets
        lost_bets = 5000000 # 這邊之後要串接實際投注資料
        
        global jackpot_pool
        jackpot_pool += lost_bets # 💸 輸掉的錢注入老虎機！
        
        socketio.emit('chat_msg', {
            'name': '🏇 賽馬結果', 
            'msg': f"第 {winner} 號馬奪冠！未中獎金額 ¥{lost_bets:,} 已匯入老虎機彩金池！"
        }, broadcast=True)
        socketio.emit('update_jackpot', {'pool': jackpot_pool}, broadcast=True)

# 2. 股市背景迴圈
def stock_market_loop():
    while True:
        socketio.sleep(1800) # 每 30 分鐘 (1800秒)
        
        # 📈 根據你給的機率 (80%, 65%, 20%, 10%, 1%) 更新 5 支股票
        # 更新完後廣播給全服玩家
        socketio.emit('chat_msg', {
            'name': '📈 股市開盤', 
            'msg': "地下股市已更新！請前往交易所查看最新報價。"
        }, broadcast=True)

# ==========================================
# 🐎 系統一：百花王虛擬賽馬 (含下注與背景結算)
# ==========================================
@socketio.on('place_horse_bet')
def place_horse_bet(data):
    sid = request.sid
    tok = sid_map.get(sid, {}).get('token')
    p = db_players.get(tok)
    if not p: return

    try:
        horse_num = int(data.get('horse'))
        amount = int(data.get('amount'))
    except: return

    if amount <= 0 or p['chips'] < amount:
        emit('chat_msg', {'name': '系統', 'msg': '❌ 籌碼不足或金額無效！'})
        return

    # 扣錢並記錄下注
    p['chips'] -= amount
    horse_bets[sid] = {'horse': horse_num, 'amount': amount, 'token': tok}
    emit('chat_msg', {'name': '系統', 'msg': f'🎫 成功下注 {horse_num} 號馬 ¥{amount:,}！'})
    emit('login_success', {'token': tok, 'name': p['name'], 'chips': p['chips'], 'debt': p['debt']})

def horse_racing_loop():
    global jackpot_pool, horse_bets
    while True:
        socketio.sleep(600) # 等待 10 分鐘 (600秒)
        
        if not horse_bets:
            socketio.emit('chat_msg', {'name': '🏇 賽馬場', 'msg': '本期賽馬無人下注，比賽流局。'}, broadcast=True)
            continue

        # 🎲 決定哪匹馬贏 (依照勝率權重)
        # 1號(30%), 2-4號(15%), 5-9號(4%), 10號(1%)
        winner = random.choices(range(1, 11), weights=[30, 15, 15, 15, 4, 4, 4, 4, 4, 1])[0]
        
        # 賠率設定
        odds = {1: 2, 2: 5, 3: 5, 4: 5, 5: 20, 6: 20, 7: 20, 8: 20, 9: 20, 10: 100}
        
        lost_bets_total = 0
        for sid, bet in horse_bets.items():
            tok = bet['token']
            p = db_players.get(tok)
            if not p: continue
            
            if bet['horse'] == winner:
                win_amt = bet['amount'] * odds[winner]
                p['chips'] += win_amt
                socketio.emit('chat_msg', {'name': '🏇 賽馬場', 'msg': f"🎉 恭喜 {p['name']} 押中 {winner} 號馬，贏得 ¥{win_amt:,}！"}, room=sid)
            else:
                lost_bets_total += bet['amount'] # 沒中的錢收集起來

        # 💸 輸掉的錢注入老虎機彩金池
        if lost_bets_total > 0:
            jackpot_pool += lost_bets_total
            socketio.emit('update_jackpot', {'pool': jackpot_pool}, broadcast=True)

        socketio.emit('chat_msg', {'name': '🏇 賽馬結果', 'msg': f"第 {winner} 號馬奪冠！未中獎賭金 ¥{lost_bets_total:,} 已全數匯入老虎機彩金池！"}, broadcast=True)
        horse_bets.clear() # 清空本局下注

# ==========================================
# 📈 系統二：學園地下股市 (含買賣與背景波動)
# ==========================================
@socketio.on('trade_stock')
def trade_stock(data):
    sid = request.sid
    tok = sid_map.get(sid, {}).get('token')
    p = db_players.get(tok)
    if not p: return

    action = data.get('action') # 'buy' 或 'sell'
    stock_id = data.get('stock_id') # 'A', 'B', 'C', 'D', 'E'
    amount = int(data.get('amount', 1)) # 買賣幾股
    
    if stock_id not in stocks: return
    current_price = stocks[stock_id]['price']
    total_cost = current_price * amount

    # 初始化玩家的股票背包
    if 'portfolio' not in p: p['portfolio'] = {'A':0, 'B':0, 'C':0, 'D':0, 'E':0}

    if action == 'buy':
        if p['chips'] >= total_cost:
            p['chips'] -= total_cost
            p['portfolio'][stock_id] += amount
            emit('chat_msg', {'name': '系統', 'msg': f'📈 成功買入 {amount} 股 {stocks[stock_id]["name"]}。'})
        else:
            emit('chat_msg', {'name': '系統', 'msg': '❌ 籌碼不足！'})
    
    elif action == 'sell':
        if p['portfolio'][stock_id] >= amount:
            p['portfolio'][stock_id] -= amount
            p['chips'] += total_cost
            emit('chat_msg', {'name': '系統', 'msg': f'📉 成功賣出 {amount} 股 {stocks[stock_id]["name"]}，獲得 ¥{total_cost:,}。'})
        else:
            emit('chat_msg', {'name': '系統', 'msg': '❌ 持股不足！'})
            
    emit('login_success', {'token': tok, 'name': p['name'], 'chips': p['chips'], 'debt': p['debt']})
    # 🟢 ：每次買賣完，立刻更新玩家的個人戶頭
    emit('update_portfolio', p['portfolio'])
   
@socketio.on('get_stock_info')
def get_stock_info():
    sid = request.sid
    tok = sid_map.get(sid, {}).get('token')
    p = db_players.get(tok)
    
    emit('update_stocks', stocks)
    
    if p and 'portfolio' in p:
        emit('update_portfolio', p['portfolio']) 
def stock_market_loop():
    global stocks
    while True:
        socketio.sleep(1800) # 每 30 分鐘 (1800秒)
        
        # A股 (80%漲5%, 20%跌1%)
        if random.random() < 0.80: stocks['A']['price'] = int(stocks['A']['price'] * 1.05)
        else: stocks['A']['price'] = int(stocks['A']['price'] * 0.99)
        
        # B股 (65%漲10%, 35%跌3%)
        if random.random() < 0.65: stocks['B']['price'] = int(stocks['B']['price'] * 1.10)
        else: stocks['B']['price'] = int(stocks['B']['price'] * 0.97)

        # C股 (20%漲50%, 80%平盤)
        if random.random() < 0.20: stocks['C']['price'] = int(stocks['C']['price'] * 1.50)
        
        # D股 (10%漲200%, 90%跌5%)
        if random.random() < 0.10: stocks['D']['price'] = int(stocks['D']['price'] * 3.00)
        else: stocks['D']['price'] = int(stocks['D']['price'] * 0.95)

        # E股 (1%漲1000%, 99%跌1%)
        if random.random() < 0.01: stocks['E']['price'] = int(stocks['E']['price'] * 11.00)
        else: stocks['E']['price'] = int(stocks['E']['price'] * 0.99)

        # 確保股價不會歸零 (最低 10 元)
        for s in stocks.values(): s['price'] = max(10, s['price'])

        socketio.emit('chat_msg', {'name': '📈 股市開盤', 'msg': "地下股市價格已更新！請隨時注意資產變化。"}, broadcast=True)
        # 🟢 請加上這行：將最新股價推播給全服
        socketio.emit('update_stocks', stocks, broadcast=True)
# ==========================================
# 🚀 啟動背景任務 (放在 app.py 最底部)
# ==========================================
if __name__ == '__main__':
    # 🐎 啟動賽馬計時器 (獨立執行緒)
    socketio.start_background_task(horse_racing_loop)
    
    # 📈 啟動股市開市計時器 (獨立執行緒)
    socketio.start_background_task(stock_market_loop)
    
    # 啟動伺服器
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
