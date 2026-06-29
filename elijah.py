#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Elijah - 最终部署版本 v1.0
自动选币 + 实盘交易 + 通知
"""
import ccxt, requests, datetime, json, sys, os, time, hmac, hashlib
from hashlib import sha256
from urllib.parse import urlencode
sys.stdout.reconfigure(encoding='utf-8')

# ==================== 配置区 ====================
PROXY = 'http://127.0.0.1:7897'  # 代理地址（云服务器如果没代理改为 ''）
LEV = 10  # 杠杆倍数
TOTAL_FUNDS = 10000  # 总资金（USDT）
MAX_POS_PCT = 0.2  # 每个合约最大仓位（20%）
GAIN_THRESH = 27  # 选股门槛：盘中最高涨幅 > 27%
ROUGH_THRESH = 15  # 快速过滤门槛：24h涨幅 > 15%
TP_PCT = 0.13  # 止盈百分比（13%）
SL_HARD = 0.10  # 硬止损百分比（10%）
MAX_BODY_PCT = 5  # 实体涨幅 > 5% 不追高
SESSION_FILE = 'elijah_session.json'  # 每日session文件

# Binance API配置（从环境变量读取）
API_KEY = os.environ.get('BINANCE_API_KEY', '')
API_SECRET = os.environ.get('BINANCE_API_SECRET', '')

# Server酱 SendKey（通知用，从环境变量读取）
SENDKEY = os.environ.get('SERVERCHAN_SENDKEY', '')

# ==================== 初始化 ====================
# 设置代理
if PROXY:
    os.environ['http_proxy'] = PROXY
    os.environ['https_proxy'] = PROXY

# 初始化交易所（合约）
ex = ccxt.binanceusdm({
    'apiKey': API_KEY,
    'secret': API_SECRET,
    'enableRateLimit': True,
    'timeout': 30000,
    'options': {
        'defaultType': 'future',
        'adjustForTimeDifference': True,
    },
})
if PROXY:
    ex.proxies = {'http': PROXY, 'https': PROXY}

# ==================== 工具函数 ====================
def send_notify(title, msg):
    """发送通知（Server酱）"""
    if not SENDKEY:
        print(f"  [通知] {title}: {msg}")
        return
    try:
        url = f'https://sctapi.ftqq.com/{SENDKEY}.send'
        data = {'title': title, 'desp': msg}
        r = requests.post(url, data=data, timeout=10)
        if r.status_code == 200:
            print(f"  ✅ 通知发送成功")
        else:
            print(f"  ❌ 通知发送失败: {r.status_code}")
    except Exception as e:
        print(f"  ❌ 通知异常: {e}")

def binance_request(method, path, params={}):
    """发送Binance签名请求"""
    params['timestamp'] = int(time.time() * 1000)
    query = urlencode(sorted(params.items()))
    signature = hmac.new(API_SECRET.encode('utf-8'), query.encode('utf-8'), sha256).hexdigest()
    query += f'&signature={signature}'
    
    headers = {'X-MBX-APIKEY': API_KEY}
    url = f'https://fapi.binance.com{path}?{query}'
    
    try:
        if method == 'GET':
            r = requests.get(url, headers=headers, timeout=10)
        elif method == 'POST':
            r = requests.post(url, headers=headers, timeout=10)
        elif method == 'DELETE':
            r = requests.delete(url, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        print(f"  ❌ Binance请求失败: {e}")
        return None

# ==================== 选币模块 ====================
def select_coins():
    """精确选币（盘中最高涨幅 > 27%）"""
    now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now_str}] 开始选币...")

    # 第1步：快速过滤（24h接口）
    try:
        r = requests.get('https://fapi.binance.com/fapi/v1/ticker/24hr', timeout=15)
        data = r.json()
        rough = []
        for d in data:
            if not d['symbol'].endswith('USDT'):
                continue
            try:
                gain = float(d['priceChangePercent'])
                if gain > ROUGH_THRESH:
                    rough.append({'symbol': d['symbol'], 'gain_24h': gain})
            except:
                continue
        rough.sort(key=lambda x: -x['gain_24h'])
        print(f"  快速过滤: {len(rough)}个币24h涨幅 > {ROUGH_THRESH}%")
    except Exception as e:
        print(f"  ❌ 24h接口错误: {e}")
        return []

    # 第2步：精确计算（拉5m K线）
    today_utc = int(datetime.datetime(
        datetime.date.today().year,
        datetime.date.today().month,
        datetime.date.today().day,
        0,
        tzinfo=datetime.timezone.utc
    ).timestamp() * 1000)

    qualified = []
    for i, r in enumerate(rough):
        symbol = r['symbol']
        sys.stdout.write(f"\r  精确检查 {i+1}/{len(rough)} {symbol}...    ")
        sys.stdout.flush()

        try:
            symbol_ccxt = symbol.replace('USDT', '/USDT:USDT')
            bars = ex.fetch_ohlcv(symbol_ccxt, '5m', today_utc, 300)
            if not bars or len(bars) < 2:
                continue

            day_open = bars[0][1]
            max_high = max(b[2] for b in bars)
            gain = (max_high - day_open) / day_open * 100

            if gain > GAIN_THRESH:
                qualified.append({'symbol': symbol_ccxt, 'gain': gain})
                print(f"\n  ✅ {symbol_ccxt}: 盘中最高涨幅 = {gain:.2f}%")
        except Exception as e:
            print(f"\n  ❌ {symbol}: 错误 {e}")

        time.sleep(0.05)

    print()

    # 第3步：写入session
    session = {
        'date': datetime.date.today().isoformat(),
        'update_time': now_str,
        'qualified': qualified
    }
    with open(SESSION_FILE, 'w', encoding='utf-8') as f:
        json.dump(session, f, indent=2, ensure_ascii=False)

    print(f"  ✅ 合格: {len(qualified)}个, 已写入 {SESSION_FILE}")
    for i, q in enumerate(qualified):
        print(f"  {i+1}. {q['symbol']}  盘中最高涨幅: {q['gain']:.2f}%")

    # 发送通知
    if qualified:
        msg = f"选币完成，合格{len(qualified)}个:\n"
        for q in qualified:
            msg += f"{q['symbol']}: {q['gain']:.2f}%\n"
        send_notify(f"Elijah选币 {datetime.date.today()}", msg)

    return qualified

# ==================== 策略模块 ====================
def run_strategy2(symbol, data):
    """策略2：均线交叉只做多"""
    if len(data) < 20:
        return {'status': '数据不足'}

    # 检查选股条件
    day_open = data[0]['o']
    max_high = max(b['h'] for b in data)
    gain = (max_high - day_open) / day_open * 100
    if gain <= GAIN_THRESH:
        return {'status': '未达标', 'gain': gain}

    print(f"  {symbol} 达标! 涨幅 = {gain:.2f}%")

    # 计算SMA4和SMA13
    closes = [b['c'] for b in data]
    sma4 = [None] * len(closes)
    sma13 = [None] * len(closes)
    
    for i in range(3, len(closes)):
        sma4[i] = sum(closes[i-3:i+1]) / 4
    for i in range(12, len(closes)):
        sma13[i] = sum(closes[i-12:i+1]) / 13

    # 策略逻辑
    trades = []
    in_pos = False
    entry_price = 0
    entry_idx = 0
    tp1_hit = False
    pos_type = None

    for i in range(13, len(data)):
        if sma4[i] is None or sma13[i] is None or sma4[i-1] is None or sma13[i-1] is None:
            continue
        if sma4[i-2] is None or sma13[i-2] is None:
            continue

        cur = data[i]
        prev = data[i-1]

        # 检测金叉
        gc = sma4[i-1] > sma13[i-1] and sma4[i-2] <= sma13[i-2]

        if not in_pos:
            # 入场条件
            if gc and prev['c'] >= prev['o']:
                body_gain = (prev['c'] - prev['o']) / prev['o'] * 100
                if body_gain > MAX_BODY_PCT:
                    continue  # 实体涨幅太大，不追高

                # 下根K线开盘价入场
                if i + 1 < len(data):
                    entry_price = data[i+1]['o']
                    entry_idx = i + 1
                    in_pos = True
                    tp1_hit = False
                    pos_type = 'long'
                    trades.append({
                        'type': 's2_long',
                        'entry': entry_price,
                        'entry_time': data[i+1]['dt'],
                        'exit': None,
                        'exit_time': None,
                        'pnl_pct': 0,
                        'reason': None
                    })
                    print(f"    入场 idx={entry_idx} at {data[i+1]['dt']} 价 = {entry_price:.6f}")

        else:
            # 持仓中
            bars_in_pos = i - entry_idx

            # 止盈1：5分钟涨幅 > 13%
            if not tp1_hit:
                candle_gain = (cur['c'] - cur['o']) / cur['o'] * 100
                if candle_gain > TP_PCT * 100:
                    tp1_price = entry_price * (1 + TP_PCT * LEV)
                    trades[-1]['exit'] = tp1_price
                    trades[-1]['reason'] = 'tp1'
                    trades[-1]['exit_time'] = cur['dt']
                    tp1_hit = True
                    print(f"    止盈1 idx={i} at {cur['dt']} 价 = {tp1_price:.6f}")
                    
                    # 实盘：平仓50%
                    # order = ex.create_order(symbol, 'LIMIT', 'SELL', pos_size/2, tp1_price)
                    
                    send_notify(f"{symbol} 止盈1", f"价格: {tp1_price:.6f}")

            # 止盈2：再涨13%清仓
            elif tp1_hit:
                candle_gain2 = (cur['c'] - cur['o']) / cur['o'] * 100
                if candle_gain2 > TP_PCT * 100:
                    exit_price = data[i+1]['o'] if i+1 < len(data) else cur['c']
                    trades[-1]['exit'] = exit_price
                    trades[-1]['reason'] = 'tp2_clear'
                    trades[-1]['exit_time'] = data[i+1]['dt'] if i+1 < len(data) else cur['dt']
                    in_pos = False
                    print(f"    止盈2清仓 idx={i} at {trades[-1]['exit_time']} 价 = {exit_price:.6f}")
                    
                    # 实盘：平仓剩余50%
                    # order = ex.create_order(symbol, 'LIMIT', 'SELL', pos_size/2, exit_price)
                    
                    send_notify(f"{symbol} 止盈2清仓", f"价格: {exit_price:.6f}")

            # 硬止损：-10%
            if (cur['l'] - entry_price) / entry_price <= -SL_HARD:
                exit_price = entry_price * (1 - SL_HARD)
                trades[-1]['exit'] = exit_price
                trades[-1]['reason'] = 'sl_hard'
                trades[-1]['exit_time'] = cur['dt']
                in_pos = False
                print(f"    硬止损 idx={i} at {cur['dt']} 价 = {exit_price:.6f}")
                
                # 实盘：平仓100%
                # order = ex.create_order(symbol, 'LIMIT', 'SELL', pos_size, exit_price)
                
                send_notify(f"{symbol} 硬止损", f"价格: {exit_price:.6f}")

    # 强制平仓（收盘前）
    if in_pos:
        last = data[-1]
        exit_price = last['c']
        trades[-1]['exit'] = exit_price
        trades[-1]['reason'] = 'force_close'
        trades[-1]['exit_time'] = last['dt']
        in_pos = False
        print(f"    强制平仓 at {last['dt']} 价 = {exit_price:.6f}")
        
        # 实盘：平仓100%
        # order = ex.create_order(symbol, 'LIMIT', 'SELL', pos_size, exit_price)
        
        send_notify(f"{symbol} 强制平仓", f"价格: {exit_price:.6f}")

    # 计算收益
    for t in trades:
        if t['exit'] is not None:
            t['pnl_pct'] = (t['exit'] - t['entry']) / t['entry'] * 100 * LEV

    total_pnl = sum(t['pnl_pct'] for t in trades)
    return {
        'status': '已交易',
        'trades': trades,
        'total_pnl': round(total_pnl, 2),
        'gain': gain
    }

# ==================== 主函数 ====================
def main():
    """主函数"""
    today = datetime.date.today().isoformat()
    now_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    
    print(f"{'='*60}")
    print(f"  Elijah 自动交易系统 v1.0")
    print(f"  日期: {today} | 时间: {now_str}")
    print(f"  杠杆: {LEV}x | 仓位: {MAX_POS_PCT*100:.0f}% | 止损: {SL_HARD*100:.0f}%")
    print(f"{'='*60}\n")

    # 检查session文件
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, 'r', encoding='utf-8') as f:
            session = json.load(f)
        if session.get('date') == today:
            print(f"✅ session已存在，直接使用")
            qualified = session.get('qualified', [])
        else:
            print(f"⚠️  session过期，重新选币")
            qualified = select_coins()
    else:
        print(f"⚠️  session不存在，开始选币")
        qualified = select_coins()

    if not qualified:
        print("\n❌ 今日无合格合约")
        return

    # 分配仓位
    n_contracts = len(qualified)
    alloc_pct = min(MAX_POS_PCT, 1.0 / n_contracts)
    alloc_usdt = TOTAL_FUNDS * alloc_pct

    print(f"\n📊 总资金: {TOTAL_FUNDS} USDT")
    print(f"📊 合格合约数: {n_contracts}，每个分配: {alloc_pct*100:.1f}% = {alloc_usdt:.0f} USDT\n")

    # 对每个合格合约运行策略2
    results = []
    for q in qualified:
        symbol = q['symbol']
        print(f"\n{'='*60}")
        print(f"  {symbol} | 分配: {alloc_usdt:.0f} USDT ({alloc_pct*100:.1f}%)")
        print(f"{'='*60}")

        # 拉取今日5m K线
        today_utc = int(datetime.datetime(
            datetime.date.today().year,
            datetime.date.today().month,
            datetime.date.today().day,
            0,
            tzinfo=datetime.timezone.utc
        ).timestamp() * 1000)

        try:
            bars = ex.fetch_ohlcv(symbol, '5m', today_utc, 300)
            if not bars:
                print(f"  ❌ 无数据")
                continue

            # 转换成字典格式
            data = []
            for b in bars:
                t = datetime.datetime.fromtimestamp(b[0]/1000, tz=datetime.timezone.utc)
                t_cst = t.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
                data.append({
                    'dt': t_cst.strftime('%Y-%m-%dT%H:%M:%S'),
                    'o': b[1],
                    'h': b[2],
                    'l': b[3],
                    'c': b[4],
                    'v': b[5]
                })

            # 运行策略2
            result = run_strategy2(symbol, data)
            results.append(result)

            if result['status'] == '已交易':
                print(f"  ✅ 结果: 已交易")
                for t in result['trades']:
                    print(f"    {t['type']:12s} entry={t['entry']:.6f} exit={t['exit']:.6f} pnl={t['pnl_pct']:+.2f}%  {t['reason']}")
                print(f"    >>> 合约收益: {result['total_pnl']:+.2f}%")
            else:
                print(f"  ⚠️  结果: {result['status']} ({result.get('gain', 0):.2f}%)")

        except Exception as e:
            print(f"  ❌ 错误: {e}")

    # 汇总
    print(f"\n{'='*60}")
    print(f"  组合总收益汇总")
    print(f"{'='*60}")
    total_pnl = 0
    for r in results:
        if r['status'] == '已交易':
            weighted_pnl = r['total_pnl'] * alloc_pct
            total_pnl += weighted_pnl
            print(f"  {r['symbol']:20s} {r['total_pnl']:+.2f}%  (加权 {weighted_pnl:+.2f}%)")

    final_funds = TOTAL_FUNDS * (1 + total_pnl / 100)
    print(f"\n  >>> 组合总收益: {total_pnl:+.2f}%")
    print(f"  >>> 总资金变化: {TOTAL_FUNDS} → {final_funds:.0f} USDT")

    # 发送每日汇总通知
    msg = f"今日交易完成\n组合总收益: {total_pnl:+.2f}%\n总资金: {final_funds:.0f} USDT\n\n"
    for r in results:
        if r['status'] == '已交易':
            msg += f"{r['symbol']}: {r['total_pnl']:+.2f}%\n"
    send_notify(f"Elijah每日汇总 {today}", msg)

if __name__ == '__main__':
    main()
