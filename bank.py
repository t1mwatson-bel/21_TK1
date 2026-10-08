import os
import json

from datetime import datetime

from config import (
    BANK_FILE,
    START_BALANCE,
    START_BET,
    MOSCOW_TZ,
)

from coefs import get_dealer_cf, get_player_cf


# =====================================================================
# КОНСТАНТЫ ЛОГИКИ СТАВОК
# =====================================================================

DOGON_STEP = 25.0
CYCLE_STEP = 100.0
DOGONS_PER_CYCLE = 4

# ВАЖНО: 2 ставки (игрок + дилер)
BETS_PER_PREDICTION = 2


# =====================================================================
# СОСТОЯНИЕ БАНКА
# =====================================================================

bank_state = {
    "balance": START_BALANCE,
    "current_bet": START_BET,
    "started_at": None,
    "history": [],
}


# =====================================================================
# ЗАГРУЗКА / СОХРАНЕНИЕ
# =====================================================================

def load_bank():
    global bank_state

    try:
        if not os.path.exists(BANK_FILE):
            bank_state = {
                "balance": START_BALANCE,
                "current_bet": START_BET,
                "started_at": datetime.now(MOSCOW_TZ).isoformat(),
                "history": [],
            }
            save_bank()
            return

        with open(BANK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        bank_state = {
            "balance": float(data.get("balance", START_BALANCE)),
            "current_bet": float(data.get("current_bet", START_BET)),
            "started_at": data.get("started_at"),
            "history": data.get("history", []),
        }

    except Exception as e:
        print(f"⚠️ Ошибка чтения банка: {e}", flush=True)
        bank_state = {
            "balance": START_BALANCE,
            "current_bet": START_BET,
            "started_at": datetime.now(MOSCOW_TZ).isoformat(),
            "history": [],
        }


def save_bank():
    try:
        tmp = BANK_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(bank_state, f, ensure_ascii=False, indent=2)

        os.replace(tmp, BANK_FILE)

    except Exception as e:
        print(f"⚠️ Ошибка сохранения банка: {e}", flush=True)


# =====================================================================
# ГЕТТЕРЫ
# =====================================================================

def get_balance():
    return float(bank_state["balance"])


def get_current_bet():
    return float(bank_state["current_bet"])


# =====================================================================
# ЛОГИКА СТАВОК
# =====================================================================

def bet_for_dogon(base_bet, dogon_index):
    """
    Считает сумму 2 ставок для конкретного догона.

    dogon_index: 0 = Д0, 1 = Д1, 2 = Д2, 3 = Д3.

    Пример при base_bet = 50:
        Д0: 2 × 50 = 100
        Д1: 2 × 75 = 150
        Д2: 2 × 100 = 200
        Д3: 2 × 125 = 250
    """

    one_bet = base_bet + dogon_index * DOGON_STEP

    return round(one_bet * BETS_PER_PREDICTION, 2)


def next_cycle_base(current_base):
    return round(current_base + CYCLE_STEP, 2)


# =====================================================================
# СПИСАНИЕ СТАВКИ
# =====================================================================

def apply_dogon_bet(prediction, dogon_index):

    base_bet = prediction.get(
        "base_bet",
        bank_state["current_bet"],
    )

    amount = bet_for_dogon(base_bet, dogon_index)

    bank_state["balance"] = round(
        bank_state["balance"] - amount,
        2,
    )

    record = {
        "type": "bet",
        "game_number": prediction.get("target_number"),
        "dogon": dogon_index,
        "amount": amount,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"💸 BET Д{dogon_index}: списано {amount} ₽ | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


# =====================================================================
# РЕЗУЛЬТАТЫ
# =====================================================================

def apply_win(prediction, dogon_index, found_card):
    """
    Прогноз выиграл.

    found_card — словарь:
        {"card": "K♦️", "where": "player"} или
        {"card": "K♦️", "where": "dealer"}
    """

    base_bet = prediction.get(
        "base_bet",
        bank_state["current_bet"],
    )

    one_bet = base_bet + dogon_index * DOGON_STEP

    card = found_card["card"]
    where = found_card["where"]

    if where == "dealer":
        cf = get_dealer_cf(card)
    elif where == "player":
        cf = get_player_cf(card)
    else:
        cf = 1.0

    payout = round(one_bet * cf, 2)

    bank_state["balance"] = round(
        bank_state["balance"] + payout,
        2,
    )

    bank_state["current_bet"] = START_BET

    record = {
        "type": "win",
        "game_number": prediction.get("target_number"),
        "predicted_card": prediction.get("predicted_card"),
        "dogon": dogon_index,
        "card": card,
        "where": where,
        "cf": cf,
        "bet": one_bet,
        "payout": payout,
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"💰 WIN: {card} × {cf} → выплата {payout} ₽ | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


def apply_lose(prediction, dogon_index):

    base_bet = prediction.get(
        "base_bet",
        bank_state["current_bet"],
    )

    if dogon_index < DOGONS_PER_CYCLE - 1:

        next_index = dogon_index + 1
        apply_dogon_bet(prediction, next_index)

        record = {
            "type": "lose",
            "game_number": prediction.get("target_number"),
            "dogon": dogon_index,
            "next_dogon": next_index,
            "balance_after": bank_state["balance"],
            "at": datetime.now(MOSCOW_TZ).isoformat(),
        }

    else:

        new_base = next_cycle_base(base_bet)
        bank_state["current_bet"] = new_base

        record = {
            "type": "lose",
            "game_number": prediction.get("target_number"),
            "dogon": dogon_index,
            "cycle_lost": True,
            "next_base": new_base,
            "balance_after": bank_state["balance"],
            "at": datetime.now(MOSCOW_TZ).isoformat(),
        }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"❌ LOSE Д{dogon_index} | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


def apply_return(prediction):

    bank_state["current_bet"] = START_BET

    record = {
        "type": "return",
        "game_number": prediction.get("target_number"),
        "predicted_card": prediction.get("predicted_card"),
        "balance_after": bank_state["balance"],
        "at": datetime.now(MOSCOW_TZ).isoformat(),
    }

    bank_state["history"].append(record)
    save_bank()

    print(
        f"♻️ ВОЗВРАТ | "
        f"баланс {bank_state['balance']} ₽",
        flush=True,
    )

    return record


# =====================================================================
# СТАТИСТИКА ДЛЯ САЙТА
# =====================================================================

def get_bank_summary():

    balance = bank_state["balance"]
    profit = round(balance - START_BALANCE, 2)
    roi = round((profit / START_BALANCE) * 100, 2) if START_BALANCE else 0.0

    return {
        "balance": balance,
        "start_balance": START_BALANCE,
        "profit": profit,
        "roi": roi,
        "current_bet": bank_state["current_bet"],
        "started_at": bank_state.get("started_at"),
    }


def get_bank_history(limit=100):
    history = bank_state.get("history", [])
    return history[-limit:]
