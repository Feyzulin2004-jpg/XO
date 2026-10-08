"""HTTP API + раздача Mini App."""
from __future__ import annotations

import json
import random
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from game import (
    O,
    X,
    bot_move_easy,
    bot_move_hard,
    game_over,
    is_draw,
    make_move,
    new_board,
    winner,
)

ROOT = Path(__file__).resolve().parent
WEBAPP = ROOT / "webapp"
STATS_PATH = ROOT / "stats.json"

CONGRATS = (
    "🎉 Поздравляем! Ты победил!",
    "🏆 Красава! Победа твоя!",
    "🔥 Поздравляем с победой!",
    "✨ Легенда! Ты выиграл!",
)
LOSER = ("💀 лох!", "🤡 лох!", "📉 проиграл — лох!", "🗑️ лох!")
DRAWS = ("🤝 Ничья! Оба норм.", "😐 Ничья.", "⚖️ Ничья — честный бой.")

app = FastAPI(title="XO Arena Mini App")
app.mount("/static", StaticFiles(directory=str(WEBAPP)), name="static")

presence: Dict[int, float] = {}
queue: Dict[int, dict] = {}
games: Dict[str, dict] = {}
stats: Dict[str, dict] = {}


def load_stats() -> None:
    global stats
    if STATS_PATH.exists():
        try:
            stats = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except Exception:
            stats = {}


def save_stats() -> None:
    STATS_PATH.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )


load_stats()


def touch(uid: int) -> None:
    presence[uid] = time.time()


def online_count(sec: int = 180) -> int:
    now = time.time()
    return sum(1 for t in presence.values() if now - t <= sec)


def ensure_stat(uid: int, name: str) -> dict:
    key = str(uid)
    row = stats.get(key)
    if not row:
        row = {
            "name": name,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "streak": 0,
            "best_streak": 0,
        }
        stats[key] = row
    else:
        row["name"] = name
    return row


def bump(uid: Optional[int], name: str, result: str) -> None:
    if uid is None:
        return
    row = ensure_stat(uid, name)
    if result == "win":
        row["wins"] += 1
        row["streak"] += 1
        row["best_streak"] = max(row["best_streak"], row["streak"])
    elif result == "loss":
        row["losses"] += 1
        row["streak"] = 0
    else:
        row["draws"] += 1
        row["streak"] = 0


def apply_stats(g: dict) -> None:
    if g.get("stats_applied") or not g.get("finished"):
        return
    g["stats_applied"] = True
    w = winner(g["board"])
    draw = is_draw(g["board"])
    if g["mode"].startswith("bot"):
        if draw:
            bump(g["player_x"], g["name_x"], "draw")
        elif w == X:
            bump(g["player_x"], g["name_x"], "win")
        else:
            bump(g["player_x"], g["name_x"], "loss")
    else:
        if draw:
            bump(g["player_x"], g["name_x"], "draw")
            bump(g["player_o"], g["name_o"], "draw")
        elif w == X:
            bump(g["player_x"], g["name_x"], "win")
            bump(g["player_o"], g["name_o"], "loss")
        elif w == O:
            bump(g["player_o"], g["name_o"], "win")
            bump(g["player_x"], g["name_x"], "loss")
    save_stats()


def identity(
    x_user_id: Optional[str],
    x_user_name: Optional[str],
) -> Tuple[int, str]:
    if not x_user_id:
        raise HTTPException(400, "Нет user id")
    try:
        uid = int(x_user_id)
    except ValueError:
        raise HTTPException(400, "Плохой user id")
    from urllib.parse import unquote

    name = unquote(x_user_name or f"игрок {uid}")
    touch(uid)
    ensure_stat(uid, name)
    return uid, name


def public_game(g: dict, viewer: int) -> dict:
    apply_stats(g)
    w = winner(g["board"])
    result = None
    message = None
    if g.get("finished"):
        if is_draw(g["board"]):
            result = "draw"
            message = random.choice(DRAWS)
        else:
            win_id = g["player_x"] if w == X else g["player_o"]
            if g["mode"].startswith("bot"):
                if w == X:
                    result, message = "win", random.choice(CONGRATS)
                else:
                    result, message = "lose", random.choice(LOSER) + "\nТебя обыграл бот"
            else:
                if viewer == win_id:
                    result, message = "win", random.choice(CONGRATS)
                else:
                    result, message = "lose", random.choice(LOSER)

    return {
        "id": g["id"],
        "board": g["board"],
        "mode": g["mode"],
        "turn": g["turn"],
        "finished": g["finished"],
        "player_x": g["player_x"],
        "player_o": g["player_o"],
        "name_x": g["name_x"],
        "name_o": g["name_o"],
        "result_for_you": result,
        "message": message,
    }


class BotBody(BaseModel):
    mode: str = Field(default="bot_hard")


class MoveBody(BaseModel):
    index: int


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEBAPP / "index.html")


@app.get("/api/presence")
async def api_presence(
    x_user_id: Optional[str] = Header(default=None),
    x_user_name: Optional[str] = Header(default=None),
) -> dict:
    if x_user_id:
        identity(x_user_id, x_user_name)
    return {"online": online_count(), "queue": len(queue)}


@app.post("/api/game/bot")
async def api_bot(
    body: BotBody,
    x_user_id: Optional[str] = Header(default=None),
    x_user_name: Optional[str] = Header(default=None),
) -> dict:
    uid, name = identity(x_user_id, x_user_name)
    mode = body.mode if body.mode in ("bot_easy", "bot_hard") else "bot_hard"
    gid = uuid.uuid4().hex[:10]
    g = {
        "id": gid,
        "board": new_board(),
        "mode": mode,
        "turn": X,
        "player_x": uid,
        "player_o": None,
        "name_x": name,
        "name_o": "Бот 🤖",
        "finished": False,
        "stats_applied": False,
    }
    games[gid] = g
    queue.pop(uid, None)
    return public_game(g, uid)


@app.post("/api/queue/join")
async def queue_join(
    x_user_id: Optional[str] = Header(default=None),
    x_user_name: Optional[str] = Header(default=None),
) -> dict:
    uid, name = identity(x_user_id, x_user_name)
    queue.pop(uid, None)

    opponent = None
    for oid in list(queue.keys()):
        if oid != uid:
            opponent = oid
            break

    if opponent is None:
        queue[uid] = {"name": name, "since": time.time()}
        return {"queued": True, "queue_size": len(queue), "game": None}

    opp = queue.pop(opponent)
    queue.pop(uid, None)
    g = _make_online(opponent, opp["name"], uid, name)
    return {"queued": False, "queue_size": len(queue), "game": public_game(g, uid)}


def _make_online(a_id: int, a_name: str, b_id: int, b_name: str) -> dict:
    if random.random() < 0.5:
        px, nx, po, no = a_id, a_name, b_id, b_name
    else:
        px, nx, po, no = b_id, b_name, a_id, a_name
    gid = uuid.uuid4().hex[:10]
    g = {
        "id": gid,
        "board": new_board(),
        "mode": "online",
        "turn": X,
        "player_x": px,
        "player_o": po,
        "name_x": nx,
        "name_o": no,
        "finished": False,
        "stats_applied": False,
    }
    games[gid] = g
    return g


@app.get("/api/queue/status")
async def queue_status(
    x_user_id: Optional[str] = Header(default=None),
    x_user_name: Optional[str] = Header(default=None),
) -> dict:
    uid, _ = identity(x_user_id, x_user_name)
    # уже в игре?
    for g in games.values():
        if g.get("finished"):
            continue
        if uid in (g.get("player_x"), g.get("player_o")) and g["mode"] == "online":
            return {"queue_size": len(queue), "game": public_game(g, uid)}
    return {"queue_size": len(queue), "game": None, "in_queue": uid in queue}


@app.post("/api/queue/leave")
async def queue_leave(
    x_user_id: Optional[str] = Header(default=None),
    x_user_name: Optional[str] = Header(default=None),
) -> dict:
    uid, _ = identity(x_user_id, x_user_name)
    queue.pop(uid, None)
    return {"ok": True}


@app.get("/api/game/{game_id}")
async def get_game(
    game_id: str,
    x_user_id: Optional[str] = Header(default=None),
    x_user_name: Optional[str] = Header(default=None),
) -> dict:
    uid, _ = identity(x_user_id, x_user_name)
    g = games.get(game_id)
    if not g:
        raise HTTPException(404, "Игра не найдена")
    if uid not in (g["player_x"], g["player_o"]):
        raise HTTPException(403, "Это не твоя партия")
    return public_game(g, uid)


@app.post("/api/game/{game_id}/move")
async def post_move(
    game_id: str,
    body: MoveBody,
    x_user_id: Optional[str] = Header(default=None),
    x_user_name: Optional[str] = Header(default=None),
) -> dict:
    uid, _ = identity(x_user_id, x_user_name)
    g = games.get(game_id)
    if not g:
        raise HTTPException(404, "Игра не найдена")
    if g["finished"]:
        raise HTTPException(400, "Партия окончена")
    if uid not in (g["player_x"], g["player_o"]):
        raise HTTPException(403, "Это не твоя партия")

    if g["mode"].startswith("bot"):
        if uid != g["player_x"] or g["turn"] != X:
            raise HTTPException(400, "Сейчас не твой ход")
        mark = X
    else:
        expected = g["player_x"] if g["turn"] == X else g["player_o"]
        if uid != expected:
            raise HTTPException(400, "Сейчас не твой ход")
        mark = g["turn"]

    if not make_move(g["board"], body.index, mark):
        raise HTTPException(400, "Клетка занята")

    if game_over(g["board"]):
        g["finished"] = True
    else:
        g["turn"] = O if g["turn"] == X else X
        if g["mode"].startswith("bot") and g["turn"] == O:
            bi = (
                bot_move_easy(g["board"])
                if g["mode"] == "bot_easy"
                else bot_move_hard(g["board"])
            )
            make_move(g["board"], bi, O)
            if game_over(g["board"]):
                g["finished"] = True
            else:
                g["turn"] = X

    return public_game(g, uid)


@app.post("/api/game/{game_id}/again")
async def post_again(
    game_id: str,
    x_user_id: Optional[str] = Header(default=None),
    x_user_name: Optional[str] = Header(default=None),
) -> dict:
    uid, _ = identity(x_user_id, x_user_name)
    g = games.get(game_id)
    if not g:
        raise HTTPException(404, "Игра не найдена")
    if uid not in (g["player_x"], g["player_o"]):
        raise HTTPException(403, "Это не твоя партия")

    g["board"] = new_board()
    g["turn"] = X
    g["finished"] = False
    g["stats_applied"] = False
    if g["mode"] == "online" and g["player_x"] and g["player_o"] and random.random() < 0.5:
        g["player_x"], g["player_o"] = g["player_o"], g["player_x"]
        g["name_x"], g["name_o"] = g["name_o"], g["name_x"]
    return public_game(g, uid)


@app.get("/api/stats/me")
async def stats_me(
    x_user_id: Optional[str] = Header(default=None),
    x_user_name: Optional[str] = Header(default=None),
) -> dict:
    uid, name = identity(x_user_id, x_user_name)
    row = ensure_stat(uid, name)
    top_rows = sorted(
        stats.values(),
        key=lambda r: (r.get("wins", 0), r.get("best_streak", 0)),
        reverse=True,
    )[:5]
    top_lines = ["🏆 Топ:"]
    for i, r in enumerate(top_rows, 1):
        top_lines.append(f"{i}. {r.get('name')} — {r.get('wins', 0)} побед")
    return {**row, "top": "\n".join(top_lines)}
