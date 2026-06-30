#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Elijah - 合并策略（选币+策略2）
选币逻辑：每15分钟扫描一次，精确筛选盘中最高涨幅>27%的合约
策略2：只做多，过滤实体涨幅>5%的买入点
10倍杠杆 | 合约数据
"""
import ccxt, requests, datetime, json, sys, os, time
sys.stdout.reconfigure(encoding='utf-8')

# ==================== 全局配置 ====================
PROXY = 'http://127.0.0.1:7897'
LEV = 10  # 杠杆（改10x）
TOTAL_FUNDS = 10000  # 总资金（USDT）
MAX_POS_PCT = 0.2  # 每个合约最大仓位（20%）
GAIN_THRESH = 27  # 选股门槛：盘中最高涨幅>27%
ROUGH_THRESH = 5  # 快速过滤门槛：24h振幅>5%（用范围替代净涨幅，避免漏先跌后涨）
TP_PCT = 0.13  # 止盈百分比
SL_HARD = 0.20  # 硬止损百分比（20%价格反向 = 200%杠杆亏损）
MAX_BODY_PCT = 5  # 实体涨幅>5%不追高
CACHE_FILE = 'cache.json'
PROCESSED_CACHE_FILE = 'processed.json'  # 已处理K线记录（去重用）
SCAN_INTERVAL = 15 * 60  # 选币扫描间隔（15分钟，单位：秒）
STRATEGY_INTERVAL = 5 * 60  # 策略执行间隔（5分钟，匹配5m K线周期）

# ==================== 初始化交易所 ====================
import os
# 设置代理环境变量（让requests/ccxt都走代理）
os.environ['http_proxy'] = PROXY
os.environ['https_proxy'] = PROXY

# 用requests测试代理是否通
try:
    r = requests.get('https://fapi.binance.com/fapi/v1/ping', timeout=10, proxies={'http': PROXY, 'https': PROXY})
    print(f'  代理测试通过: {r.status_code}')
except Exception as e:
    print(f'  代理测试失败: {e}')
    exit(1)

# 初始化交易所（带代理）
ex = ccxt.binanceusdm({
    'enableRateLimit': True,
    'proxies': {'http': PROXY, 'https': PROXY},
    'timeout': 30000,
})
try:
    ex.load_markets()
    print(f'  交易所初始化完成，加载 {len(ex.markets)} 个市场')
except Exception as e:
    print(f'  交易所初始化失败: {e}')
    exit(1)

# ==================== 选币模块 ====================
def select_coins():
    """精确选币（每15分钟运行一次）
    1. 用24h接口快速过滤出涨幅>15%的币
    2. 拉取这些币的5m K线，精确计算盘中最高涨幅
    3. 筛选>27%的写入缓存
    """
    now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now_str}] 开始选币...")
    
    # ---------- 第1步：快速过滤（24h接口） ----------
    url = 'https://fapi.binance.com/fapi/v1/ticker/24hr'
    try:
        r = requests.get(url, timeout=15, proxies={'http': PROXY, 'https': PROXY})
        data = r.json()
        rough = []
        for d in data:
            # 只保留USDT永续合约
            if not d['symbol'].endswith('USDT'):
                continue
            try:
                high = float(d['highPrice'])
                low = float(d['lowPrice'])
                # 用24h波动幅度替代净涨幅，避免漏掉"先跌后涨"的币
                range_pct = (high - low) / low * 100
                if range_pct > ROUGH_THRESH:
                    rough.append({'symbol': d['symbol'], 'range_24h': range_pct})
            except:
                continue
        rough.sort(key=lambda x: -x['range_24h'])
        print(f"  快速过滤: {len(rough)}个币24h振幅>{ROUGH_THRESH}%")
    except Exception as e:
        print(f"24h接口错误: {e}")
        return []
    
    # ---------- 第2步：精确计算（拉5m K线） ----------
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today_utc = int(now_utc.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    
    qualified = []
    for i, r in enumerate(rough):
        symbol = r['symbol']
        sys.stdout.write(f"\r  精确检查 {i+1}/{len(rough)} {symbol}...    ")
        sys.stdout.flush()
        
        try:
            # 拉取今日5m K线
            bars = ex.fetch_ohlcv(symbol, '5m', today_utc, 300)
            if not bars or len(bars) < 2:
                continue
            
            # 计算盘中最高涨幅
            day_open = bars[0][1]  # 今日开盘价（第一根5m K线开盘价）
            max_high = max(b[2] for b in bars)  # 今日最高价
            gain = (max_high - day_open) / day_open * 100
            
            if gain > GAIN_THRESH:
                # 转换成ccxt格式（TACUSDT → TAC/USDT:USDT）
                symbol_ccxt = symbol.replace('USDT', '/USDT:USDT')
                qualified.append({'symbol': symbol_ccxt, 'gain': gain})
                print(f"\n  ✅ {symbol_ccxt}: 盘中最高涨幅={gain:.2f}%")
        except Exception as e:
            print(f"\n  ❌ {symbol}: 错误 {e}")
        
        time.sleep(0.05)  # 限速，避免触发交易所风控
    
    print()
    
    # ---------- 第3步：写入缓存 ----------
    cache = {
        'date': datetime.date.today().isoformat(),
        'update_time': now_str,
        'qualified': qualified
    }
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    
    print(f"  合格: {len(qualified)}个, 已写入 {CACHE_FILE}")
    for i, q in enumerate(qualified):
        print(f"  {i+1}. {q['symbol']}  盘中最高涨幅: {q['gain']:.2f}%")
    
    return qualified

# ==================== 策略2模块 ====================
def analyze_symbol2(data, name):
    """策略2：均线交叉做多做空
    - 做多：SMA4上穿SMA13 + 阳线
    - 做空：SMA4下穿SMA13 + 阴线
    - 过滤实体>5%避免追高/追低
    """
    if len(data) < 20:
        return {'name': name, 'status': '数据不足'}
    
    # ---------- 先检查选股条件（盘中最高涨幅>27%） ----------
    day_open = data[0]['o']
    max_high = max(b['h'] for b in data)
    gain = (max_high - day_open) / day_open * 100
    if gain <= GAIN_THRESH:
        return {
            'name': name,
            'status': '未达标',
            'gain': gain,
            'msg': f'涨幅{gain:.2f}% <= {GAIN_THRESH}%'
        }
    
    print(f"  {name} 达标! 涨幅={gain:.2f}%")
    
    # ---------- 计算SMA4和SMA13 ----------
    closes = [b['c'] for b in data]
    sma4 = []
    for i in range(len(closes)):
        if i >= 3:
            sma4.append(sum(closes[i-3:i+1]) / 4)
        else:
            sma4.append(None)
    
    sma13 = []
    for i in range(len(closes)):
        if i >= 12:
            sma13.append(sum(closes[i-12:i+1]) / 13)
        else:
            sma13.append(None)
    
    # ---------- 策略逻辑 ----------
    trades = []
    in_pos = False
    entry_price = 0
    entry_idx = 0
    entry_bar = None
    tp1_hit = False
    deferred = None  # 'long'/'short' 延迟入场等待确认
    deferred_reverse = None  # 'long'/'short' 延迟反转等待确认
    
    for i in range(1, len(data)):
        # 跳过SMA为None的K线
        if sma4[i] is None or sma13[i] is None or sma4[i-1] is None or sma13[i-1] is None:
            continue
        if i-2 >= 0 and (sma4[i-2] is None or sma13[i-2] is None):
            continue
        
        cur = data[i]
        prev = data[i-1]
        
        # 检测金叉和死叉
        gc = sma4[i-1] > sma13[i-1] and sma4[i-2] <= sma13[i-2]
        dc = sma4[i-1] < sma13[i-1] and sma4[i-2] >= sma13[i-2]
        
        # ---------- 延迟反转确认 ----------
        if deferred_reverse is not None:
            if not in_pos:
                deferred_reverse = None
            elif deferred_reverse == 'short' and cur['c'] < cur['o']:
                cur_body = abs(cur['c'] - cur['o']) / cur['o'] * 100
                if cur_body <= MAX_BODY_PCT and i + 1 < len(data):
                    exit_price = cur['c']
                    pnl = (exit_price - entry_price) / entry_price * LEV * 100
                    trades[-1]['exit'] = exit_price
                    trades[-1]['exit_time'] = cur['dt']
                    trades[-1]['pnl_pct'] = round(pnl, 2)
                    trades[-1]['reason'] = '多转空(延)'
                    print(f"    出场 多转空(延) idx={i} at {cur['dt']} 价={exit_price:.8f} pnl={pnl:+.2f}%")
                    in_pos = False
                    if i + 1 < len(data):
                        entry_price = data[i+1]['o']
                        entry_idx = i + 1; entry_bar = cur
                        in_pos = True; pos_type = 'short'; tp1_hit = False
                        trades.append({'type':'s2_rev_d_short','entry':entry_price,'exit':None,
                            'entry_time':data[i+1]['dt'],'exit_time':None,'pnl_pct':0,'reason':None})
                    deferred_reverse = None
                    continue
            elif deferred_reverse == 'long' and cur['c'] >= cur['o']:
                cur_body = (cur['c'] - cur['o']) / cur['o'] * 100
                if cur_body <= MAX_BODY_PCT and i + 1 < len(data):
                    exit_price = cur['c']
                    pnl = (entry_price - exit_price) / entry_price * LEV * 100
                    trades[-1]['exit'] = exit_price
                    trades[-1]['exit_time'] = cur['dt']
                    trades[-1]['pnl_pct'] = round(pnl, 2)
                    trades[-1]['reason'] = '空转多(延)'
                    print(f"    出场 空转多(延) idx={i} at {cur['dt']} 价={exit_price:.8f} pnl={pnl:+.2f}%")
                    in_pos = False
                    if i + 1 < len(data):
                        entry_price = data[i+1]['o']
                        entry_idx = i + 1; entry_bar = cur
                        in_pos = True; pos_type = 'long'; tp1_hit = False
                        trades.append({'type':'s2_rev_d_long','entry':entry_price,'exit':None,
                            'entry_time':data[i+1]['dt'],'exit_time':None,'pnl_pct':0,'reason':None})
                    deferred_reverse = None
                    continue
            deferred_reverse = None
        
        # ---------- 未持仓：检查入场信号 ----------
        if not in_pos:
            # ---- 延迟入场确认 ----
            if deferred is not None:
                if deferred == 'long' and cur['c'] >= cur['o']:
                    cur_body = (cur['c'] - cur['o']) / cur['o'] * 100
                    if cur_body > MAX_BODY_PCT:
                        deferred = None; continue
                    if i + 1 < len(data):
                        entry_price = data[i+1]['o']
                        entry_idx = i + 1
                        entry_bar = cur
                        in_pos = True; pos_type = 'long'; tp1_hit = False
                        trades.append({'type':'s2_long_d','entry':entry_price,'exit':None,
                            'entry_time':data[i+1]['dt'],'exit_time':None,'pnl_pct':0,'reason':None})
                        deferred = None; continue
                elif deferred == 'short' and cur['c'] < cur['o']:
                    cur_body = abs(cur['c'] - cur['o']) / cur['o'] * 100
                    if cur_body > MAX_BODY_PCT:
                        deferred = None; continue
                    if i + 1 < len(data):
                        entry_price = data[i+1]['o']
                        entry_idx = i + 1
                        entry_bar = cur
                        in_pos = True; pos_type = 'short'; tp1_hit = False
                        trades.append({'type':'s2_short_d','entry':entry_price,'exit':None,
                            'entry_time':data[i+1]['dt'],'exit_time':None,'pnl_pct':0,'reason':None})
                        deferred = None; continue
                deferred = None
            
            # ---- 做多：金叉+阳线 ----
            if gc:
                body_gain = 0
                if prev['c'] >= prev['o']:
                    body_gain = (prev['c'] - prev['o']) / prev['o'] * 100
                if body_gain > MAX_BODY_PCT:
                    continue
                if body_gain <= 0.2:
                    deferred = 'long'
                elif prev['c'] >= prev['o']:
                    if i + 1 < len(data):
                        entry_price = data[i+1]['o']
                        entry_idx = i + 1; entry_bar = prev
                        in_pos = True; pos_type = 'long'; tp1_hit = False
                        trades.append({'type':'s2_long','entry':entry_price,'exit':None,
                            'entry_time':data[i+1]['dt'],'exit_time':None,'pnl_pct':0,'reason':None})
                        continue
            
            # ---- 做空：死叉+阴线 ----
            if dc:
                body_drop = 0
                if prev['c'] < prev['o']:
                    body_drop = abs(prev['c'] - prev['o']) / prev['o'] * 100
                if body_drop > MAX_BODY_PCT:
                    continue
                if body_drop <= 0.2:
                    deferred = 'short'
                elif prev['c'] < prev['o']:
                    if i + 1 < len(data):
                        entry_price = data[i+1]['o']
                        entry_idx = i + 1; entry_bar = prev
                        in_pos = True; pos_type = 'short'; tp1_hit = False
                        trades.append({'type':'s2_short','entry':entry_price,'exit':None,
                            'entry_time':data[i+1]['dt'],'exit_time':None,'pnl_pct':0,'reason':None})
                        continue
        
        # ---------- 已持仓：检查出场信号（含反转）----------
        if in_pos:
            exit_reason = None; exit_price = None; reverse_type = None
            
            # === 反转逻辑：出现反向交叉信号时平仓反手 ===
            if pos_type == 'long' and dc:
                if prev['c'] < prev['o']:
                    body = abs(prev['c'] - prev['o']) / prev['o'] * 100
                else:
                    body = 0  # 反向K线（阳线），同小实体延迟逻辑
                if body > MAX_BODY_PCT:
                    pass
                elif body > 0.2:
                    exit_reason = '多转空'; reverse_type = 'short'
                    exit_price = prev['c']
                else:
                    deferred_reverse = 'short'
            elif pos_type == 'short' and gc:
                if prev['c'] >= prev['o']:
                    body = (prev['c'] - prev['o']) / prev['o'] * 100
                else:
                    body = 0
                if body > MAX_BODY_PCT:
                    pass
                elif body > 0.2:
                    exit_reason = '空转多'; reverse_type = 'long'
                    exit_price = prev['c']
                else:
                    deferred_reverse = 'long'
            
            if exit_reason:
                if pos_type == 'long':
                    pnl = (exit_price - entry_price) / entry_price * LEV * 100
                else:
                    pnl = (entry_price - exit_price) / entry_price * LEV * 100
                trades[-1]['exit'] = exit_price
                trades[-1]['exit_time'] = prev['dt']
                trades[-1]['pnl_pct'] = round(pnl, 2)
                trades[-1]['reason'] = exit_reason
                print(f"    出场 {exit_reason} idx={i} at {prev['dt']} 价={exit_price:.8f} pnl={pnl:+.2f}%")
                in_pos = False
                if i + 1 < len(data):
                    entry_price = data[i+1]['o']
                    entry_idx = i + 1; entry_bar = prev
                    in_pos = True; pos_type = reverse_type; tp1_hit = False
                    trades.append({'type':'s2_rev_'+reverse_type,'entry':entry_price,'exit':None,
                        'entry_time':data[i+1]['dt'],'exit_time':None,'pnl_pct':0,'reason':None})
                    continue
                continue
            
            # === 原有的出场逻辑（无反转信号时）===
            exit_reason = None; exit_price = None
            
            if pos_type == 'long':
                # 硬止损（-20%）
                if (cur['l'] - entry_price) / entry_price <= -SL_HARD:
                    exit_reason = '200%止损'
                    exit_price = entry_price * (1 - SL_HARD)
                # 止盈（+13%）（做多）
                tp_price = 0
                if exit_reason is None:
                    bar_gain = (cur['h'] - entry_price) / entry_price * 100
                    if bar_gain > TP_PCT * 100:
                        tp_price = entry_price * (1 + TP_PCT)
                if tp_price > 0:
                    if not tp1_hit:
                        pnl = (tp_price - entry_price) / entry_price * LEV * 100
                        trades[-1]['exit'] = tp_price
                        trades[-1]['exit_time'] = cur['dt']
                        trades[-1]['pnl_pct'] = round(pnl, 2)
                        trades[-1]['reason'] = 'tp1(+13%)'
                        print(f"    多止盈1 idx={i} at {cur['dt']} 价={tp_price:.8f} pnl={pnl:+.2f}%")
                        if i + 1 < len(data):
                            entry_price = data[i+1]['o']
                            entry_idx = i + 1; tp1_hit = True
                            trades.append({'type':'s2_long_re','entry':entry_price,'exit':None,
                                'entry_time':data[i+1]['dt'],'exit_time':None,'pnl_pct':0,'reason':None})
                            continue
                    else:
                        exit_reason = 'tp2清仓'
                        exit_price = tp_price
                # 反包止损
                if exit_reason is None and i - entry_idx == 1 and entry_bar:
                    if cur['c'] < cur['o']:
                        if cur['o'] >= entry_bar['c'] and cur['c'] <= entry_bar['o']:
                            exit_reason = '反包止损'
                            exit_price = cur['c']
            
            else:  # pos_type == 'short'
                # 硬止损（反向+20%）
                if (cur['h'] - entry_price) / entry_price >= SL_HARD:
                    exit_reason = '200%止损'
                    exit_price = entry_price * (1 + SL_HARD)
                # 止盈（-13%）（做空）
                tp_price = 0
                if exit_reason is None:
                    bar_drop = (cur['l'] - entry_price) / entry_price * 100
                    if bar_drop < -TP_PCT * 100:
                        tp_price = entry_price * (1 - TP_PCT)
                if tp_price > 0:
                    if not tp1_hit:
                        pnl = (entry_price - tp_price) / entry_price * LEV * 100
                        trades[-1]['exit'] = tp_price
                        trades[-1]['exit_time'] = cur['dt']
                        trades[-1]['pnl_pct'] = round(pnl, 2)
                        trades[-1]['reason'] = 'tp1(-13%)'
                        print(f"    空止盈1 idx={i} at {cur['dt']} 价={tp_price:.8f} pnl={pnl:+.2f}%")
                        if i + 1 < len(data):
                            entry_price = data[i+1]['o']
                            entry_idx = i + 1; tp1_hit = True
                            trades.append({'type':'s2_short_re','entry':entry_price,'exit':None,
                                'entry_time':data[i+1]['dt'],'exit_time':None,'pnl_pct':0,'reason':None})
                            continue
                    else:
                        exit_reason = 'tp2清仓'
                        exit_price = tp_price
                # 反包止损（做空：被阳线包了）
                if exit_reason is None and i - entry_idx == 1 and entry_bar:
                    if cur['c'] > cur['o']:
                        if cur['o'] <= entry_bar['c'] and cur['c'] >= entry_bar['o']:
                            exit_reason = '反包止损'
                            exit_price = cur['c']
            
            if exit_reason is not None:
                if pos_type == 'long':
                    pnl = (exit_price - entry_price) / entry_price * LEV * 100
                else:
                    pnl = (entry_price - exit_price) / entry_price * LEV * 100
                trades[-1]['exit'] = exit_price
                trades[-1]['exit_time'] = cur['dt']
                trades[-1]['pnl_pct'] = round(pnl, 2)
                trades[-1]['reason'] = exit_reason
                print(f"    出场 {exit_reason} idx={i} at {cur['dt']} 价={exit_price:.8f} pnl={pnl:+.2f}%")
                in_pos = False
        
        # 延迟入场：小K线等待确认
        if not in_pos:
            if gc and prev['c'] >= prev['o']:
                body_gain = (prev['c'] - prev['o']) / prev['o'] * 100
                if body_gain <= 0.2:
                    deferred = 'long'
            if dc and prev['c'] < prev['o']:
                body_drop = abs(prev['c'] - prev['o']) / prev['o'] * 100
                if body_drop <= 0.2:
                    deferred = 'short'
    
    # ---------- 持仓未平：收盘强平 ----------
    if in_pos and trades:
        last = data[-1]
        if pos_type == 'long':
            pnl = (last['c'] - entry_price) / entry_price * LEV * 100
        else:
            pnl = (entry_price - last['c']) / entry_price * LEV * 100
        trades[-1]['exit'] = last['c']
        trades[-1]['exit_time'] = last['dt']
        trades[-1]['pnl_pct'] = round(pnl, 2)
        trades[-1]['reason'] = '收盘强平'
        print(f"    强平 idx={len(data)-1} at {last['dt']} 价={last['c']:.8f} pnl={pnl:+.2f}%")
    
    total_pnl = sum(t['pnl_pct'] for t in trades)
    return {
        'name': name,
        'status': '已交易',
        'trades': trades,
        'total_pnl': round(total_pnl, 2),
        'gain': gain
    }

# ==================== 策略运行模块 ====================
def load_processed():
    """加载已处理的K线记录"""
    if os.path.exists(PROCESSED_CACHE_FILE):
        try:
            with open(PROCESSED_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_processed(processed):
    """保存已处理的K线记录"""
    with open(PROCESSED_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(processed, f, indent=2)


def run_strategy():
    """运行策略2：读取缓存中的合格币，逐个运行策略"""
    if not os.path.exists(CACHE_FILE):
        print(f"缓存文件 {CACHE_FILE} 不存在，请先运行选币")
        return []
    
    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
        cache = json.load(f)
    
    qualified = cache.get('qualified', [])
    if not qualified:
        print("缓存中无合格合约")
        return []
    
    print(f"从缓存读取 {len(qualified)} 个合格合约，开始跑策略...")
    
    # 分仓：每个合约分配20%总资金（最多5个合约）
    n_contracts = len(qualified)
    alloc_pct = min(MAX_POS_PCT, 1.0 / n_contracts)  # 每个合约分配百分比
    alloc_usdt = TOTAL_FUNDS * alloc_pct  # 每个合约分配USDT
    print(f"  总资金: {TOTAL_FUNDS} USDT")
    print(f"  合格合约数: {n_contracts}，每个分配: {alloc_pct*100:.1f}% = {alloc_usdt:.0f} USDT")
    
    # 获取今日UTC时间戳（用于拉取今日K线）
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today_utc = int(now_utc.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
    
    # 加载已处理记录，避免重复分析相同K线
    processed = load_processed()
    
    all_trades = []
    portfolio_pnl = 0  # 组合总收益（百分比）
    for q in qualified:
        symbol = q['symbol']
        name = symbol.replace('/USDT:USDT', '')
        print(f"\n{'─'*50}")
        print(f"  {name} ({symbol}) | 分配: {alloc_usdt:.0f} USDT ({alloc_pct*100:.1f}%)")
        print(f"{'─'*50}")
        
        # 拉取今日5m K线
        try:
            bars = ex.fetch_ohlcv(symbol, '5m', today_utc, 300)
            data = []
            for b in bars:
                ts, o, h, l, c, v = b
                dt = ex.iso8601(ts)[:19]
                data.append({'ts': ts, 'dt': dt, 'o': o, 'h': h, 'l': l, 'c': c, 'v': v})
        except Exception as e:
            print(f"  拉取数据失败: {e}")
            continue
        
        if not data or len(data) < 20:
            print(f"  数据不足: {len(data)}根K线")
            continue
        
        # 去重：检查是否有新K线，无新K线则跳过
        last_bar_ts = bars[-1][0] if bars else 0
        prev_ts = processed.get(symbol, 0)
        if last_bar_ts <= prev_ts:
            print(f"  无新K线（上次处理到 {prev_ts}），跳过")
            continue
        processed[symbol] = last_bar_ts
        
        # 运行策略2
        r = analyze_symbol2(data, name)
        print(f"  结果: {r['status']}")
        
        if r['status'] == '已交易':
            # 计算加权收益和绝对收益
            weighted_pnl = alloc_pct * r['total_pnl']
            portfolio_pnl += weighted_pnl
            contract_abs_pnl = alloc_usdt * (r['total_pnl'] / 100)  # 合约绝对收益（USDT）
            
            for t in r['trades']:
                side = '多'
                trade_abs_pnl = alloc_usdt * (t['pnl_pct'] / 100)
                print(f"    {t['reason']:12s} {side} entry={t['entry']:.8f} exit={t['exit']:.8f} pnl={t['pnl_pct']:+.2f}% | {trade_abs_pnl:+.2f} USDT")
            print(f"    >>> 合约收益: {r['total_pnl']:+.2f}% | {contract_abs_pnl:+.2f} USDT")
            print(f"    >>> 加权贡献: {weighted_pnl:+.2f}%")
            all_trades.extend(r['trades'])
        elif r['status'] == '未达标':
            print(f"    {r['msg']}")
    
    # 保存已处理记录
    save_processed(processed)
    
    # 输出组合总收益
    print(f"\n{'='*60}")
    print(f"  组合总收益: {portfolio_pnl:+.2f}%")
    print(f"  总资金变化: {TOTAL_FUNDS * (1 + portfolio_pnl/100):.0f} USDT")
    print(f"{'='*60}")
    
    return all_trades

# ==================== 主流程 ====================
def main():
    """主循环：选币（15分钟间隔）+ 策略运行（5分钟间隔，匹配5m K线）"""
    print(f"{'='*60}")
    print(f"  Elijah 合并策略 | {datetime.date.today()}")
    print(f"  选币间隔: {SCAN_INTERVAL//60}分钟 | 策略间隔: {STRATEGY_INTERVAL//60}分钟 | 杠杆: {LEV}x | 选股门槛: {GAIN_THRESH}%")
    print(f"{'='*60}")
    
    last_scan_time = 0
    while True:
        try:
            now = time.time()
            
            # 1. 选币（每SCAN_INTERVAL秒一次，不是每次循环都跑）
            if now - last_scan_time >= SCAN_INTERVAL:
                select_coins()
                last_scan_time = now
            
            # 2. 运行策略（每STRATEGY_INTERVAL秒一次，匹配5m K线周期）
            now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
            print(f"\n{'='*60}")
            print(f"  [{now_str}] 开始运行策略2")
            print(f"{'='*60}")
            run_strategy()
            
            # 3. 等待STRATEGY_INTERVAL秒
            print(f"\n{'='*60}")
            print(f"  等待{STRATEGY_INTERVAL//60}分钟后下一次策略运行...")
            print(f"{'='*60}")
            time.sleep(STRATEGY_INTERVAL)
        except KeyboardInterrupt:
            print("\n程序手动停止")
            break
        except Exception as e:
            print(f"\n程序出错: {e}")
            time.sleep(60)  # 出错后等待1分钟再重试

if __name__ == '__main__':
    main()
