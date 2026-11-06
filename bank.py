import os
import json
from datetime import datetime

from config import (
    BANK_FILE, START_BALANCE, START_BET,
    BET_MULTIPLIER, COEFFICIENT, MOSCOW_TZ,
)

_bank = None

def _default_bank():
    return {
        "balance": START_BALANCE,
        "current_bet": START_BET,
        "lose_streak": 0,
        "wins": 0,
        "loses": 0,
        "returns": 0,
        "updated_at": datetime.now(MOSCOW_TZ).isoformat(),
    }

def load_bank():
    global _bank
    try:
        if os.path.exists(BANK_FILE):
            with open(BANK_FILE, "r", encoding="utf-8") as f:
                _bank = json.load(f)
        else:
            _bank = _default_bank()
    except Exception as e:
        print(f"⚠️ Ошибка чтения {BANK_FILE}: {e}", flush=True)
        _bank = _default_bank()
    return _bank

def save_bank():
    try:
        tmp = BANK_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_bank, f, ensure_ascii=False, indent=2)
        os.replace(tmp, BANK_FILE)
    except Exception as e:
        print(f"🔥 Ошибка сохранения {BANK_FILE}: {e}", flush=True)

def get_current_bet():
    if _bank is None:
        load_bank()
    return float(_bank.get("current_bet", START_BET))

def bet_for_dogon(base_bet, dogon):
    return round(base_bet * (BET_MULTIPLIER ** dogon), 2)

def apply_win(prediction, dogon, bet_amount):
    if _bank is None:
        load_bank()
    profit = round(bet_amount * (COEFFICIENT - 1), 2)
    _bank["balance"] = round(_bank["balance"] + profit, 2)
    _bank["current_bet"] = START_BET
    _bank["lose_streak"] = 0
    _bank["wins"] = _bank.get("wins", 0) + 1
    _bank["updated_at"] = datetime.now(MOSCOW_TZ).isoformat()
    save_bank()
    print(f"💰 WIN Д{dogon}: +{profit} | баланс {_bank['balance']}", flush=True)

def apply_lose(prediction, total_bet):
    """Списывает СУММУ всех догонов (Д0..Д3), а не base_bet."""
    if _bank is None:
        load_bank()
    _bank["balance"] = round(_bank["balance"] - total_bet, 2)
    _bank["lose_streak"] = _bank.get("lose_streak", 0) + 1
    _bank["current_bet"] = round(_bank.get("current_bet", START_BET) * BET_MULTIPLIER, 2)
    _bank["loses"] = _bank.get("loses", 0) + 1
    _bank["updated_at"] = datetime.now(MOSCOW_TZ).isoformat()
    save_bank()
    print(f"💸 LOSE: -{total_bet} | баланс {_bank['balance']}", flush=True)

def apply_return(prediction, bet_amount):
    if _bank is None:
        load_bank()
    _bank["returns"] = _bank.get("returns", 0) + 1
    _bank["updated_at"] = datetime.now(MOSCOW_TZ).isoformat()
    save_bank()
    print(f"♻️ RETURN: ставка {bet_amount} возвращена", flush=True)
