"""Telegram-бот: крестики-нолики."""
from __future__ import annotations

import json
import logging
import os
import random
import secrets
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    User,
    WebAppInfo,
)
from dotenv import load_dotenv

from game import (
    EMPTY,
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

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN or BOT_TOKEN.startswith("your_token"):
    raise SystemExit("Укажи BOT_TOKEN в файле .env")

# HTTPS-URL Mini App (ngrok / cloudflare tunnel / хостинг)
WEBAPP_URL = (os.getenv("WEBAPP_URL") or "").strip().rstrip("/")

STATS_PATH = Path(__file__).with_name("stats.json")

CONGRATS = (
    "🎉 Поздравляем! Ты победил!",
    "🏆 Красава! Победа твоя!",
    "🔥 Поздравляем с победой!",
    "✨ Легенда! Ты выиграл!",
    "👑 Поздравляем, чемпион!",
)
LOSER_LINES = (
    "💀 лох!",
    "🤡 лох!",
    "📉 проиграл — лох!",
    "🫠 лох! в следующий раз повезёт… или нет",
    "🗑️ лох!",
)
DRAW_LINES = (
    "🤝 Ничья! Оба норм.",
    "😐 Ничья. Ни победы, ни «лох».",
    "⚖️ Ничья — честный бой.",
)


@dataclass
class Game:
    board: List[str] = field(default_factory=new_board)
    mode: str = "bot_hard"  # bot_easy | bot_hard | pvp | online
    turn: str = X
    player_x: Optional[int] = None
    player_o: Optional[int] = None
    name_x: str = "Игрок ❌"
    name_o: str = "Игрок 🟢"
    finished: bool = False
    invite_code: Optional[str] = None
    boards: Dict[int, Tuple[int, int]] = field(default_factory=dict)
    stats_applied: bool = False


games: Dict[str, Game] = {}
user_game: Dict[int, str] = {}
invites: Dict[str, str] = {}

# очередь онлайна: user_id -> {name, chat_id, message_id, since}
online_queue: Dict[int, dict] = {}
# кто сейчас «онлайн» в боте (последняя активность)
presence: Dict[int, float] = {}

# stats: user_id(str) -> {name, wins, losses, draws, streak, best_streak}
stats: Dict[str, dict] = {}


def load_stats() -> None:
    global stats
    if STATS_PATH.exists():
        try:
            stats = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except Exception:
            stats = {}
    else:
        stats = {}


def save_stats() -> None:
    STATS_PATH.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def touch(user_id: int) -> None:
    presence[user_id] = time.time()


def online_count(seconds: int = 180) -> int:
    now = time.time()
    return sum(1 for t in presence.values() if now - t <= seconds)


def display_name(user: User) -> str:
    if user.username:
        return f"@{user.username}"
    full = (user.full_name or "").strip()
    return full or f"игрок {user.id}"


def ensure_stat(user_id: int, name: str) -> dict:
    key = str(user_id)
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


def apply_result(game: Game) -> None:
    if game.stats_applied or not game.finished:
        return
    game.stats_applied = True

    w = winner(game.board)
    draw = is_draw(game.board)

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

    if game.mode.startswith("bot"):
        # против бота: только человек
        if draw:
            bump(game.player_x, game.name_x, "draw")
        elif w == X:
            bump(game.player_x, game.name_x, "win")
        else:
            bump(game.player_x, game.name_x, "loss")
    else:
        if draw:
            bump(game.player_x, game.name_x, "draw")
            bump(game.player_o, game.name_o, "draw")
        elif w == X:
            bump(game.player_x, game.name_x, "win")
            bump(game.player_o, game.name_o, "loss")
        elif w == O:
            bump(game.player_o, game.name_o, "win")
            bump(game.player_x, game.name_x, "loss")

    save_stats()


def my_stats_text(user_id: int, name: str) -> str:
    row = ensure_stat(user_id, name)
    return (
        f"📊 Твоя статистика ({row['name']})\n"
        f"🏆 Побед: {row['wins']}\n"
        f"💀 Поражений: {row['losses']}\n"
        f"🤝 Ничьих: {row['draws']}\n"
        f"🔥 Серия: {row['streak']} (рекорд {row['best_streak']})"
    )


def top_text(limit: int = 10) -> str:
    rows = sorted(
        stats.values(),
        key=lambda r: (r.get("wins", 0), r.get("best_streak", 0)),
        reverse=True,
    )[:limit]
    if not rows:
        return "🏆 Топ пока пуст — сыграй первую партию!"
    lines = ["🏆 Топ игроков"]
    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(rows):
        mark = medals[i] if i < 3 else f"{i + 1}."
        lines.append(
            f"{mark} {r.get('name', '?')} — {r.get('wins', 0)} побед "
            f"(серия {r.get('best_streak', 0)})"
        )
    return "\n".join(lines)


def main_menu_kb() -> InlineKeyboardMarkup:
    waiting = len(online_queue)
    online = online_count()
    find_label = "🔎 Найти соперника"
    if waiting:
        find_label = f"🔎 Найти соперника ({waiting} в очереди)"

    rows = []
    if WEBAPP_URL:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🎮 Открыть игру (окно)",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text=find_label, callback_data="online:find")],
            [
                InlineKeyboardButton(
                    text="🤖 Бот (лёгкий)", callback_data="new:bot_easy"
                ),
                InlineKeyboardButton(
                    text="🤖 Бот (сложный)", callback_data="new:bot_hard"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Пригласить друга", callback_data="new:pvp"
                )
            ],
            [
                InlineKeyboardButton(text="📊 Моя стата", callback_data="stats:me"),
                InlineKeyboardButton(text="🏆 Топ", callback_data="stats:top"),
            ],
            [
                InlineKeyboardButton(
                    text=f"🟢 Онлайн сейчас: ~{online}", callback_data="stats:online"
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def setup_webapp_button() -> None:
    """Кнопка меню слева внизу чата — открывает Mini App."""
    if not WEBAPP_URL:
        logger.warning(
            "WEBAPP_URL не задан — Mini App кнопка выключена. "
            "Подними HTTPS-туннель и пропиши URL в .env"
        )
        return
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Играть",
                web_app=WebAppInfo(url=WEBAPP_URL),
            )
        )
        logger.info("MenuButton WebApp: %s", WEBAPP_URL)
    except Exception as e:
        logger.warning("Не удалось поставить MenuButton: %s", e)


def queue_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Выйти из очереди", callback_data="online:cancel"
                )
            ],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")],
        ]
    )


def board_kb(game: Game, game_id: str) -> InlineKeyboardMarkup:
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            cell = game.board[i]
            label = cell if cell != EMPTY else "⬜"
            data = (
                f"noop:{game_id}"
                if game.finished or cell != EMPTY
                else f"m:{game_id}:{i}"
            )
            row.append(InlineKeyboardButton(text=label, callback_data=data))
        rows.append(row)

    bottom = []
    if game.finished:
        if game.mode in ("pvp", "online") and game.player_o:
            bottom.append(
                InlineKeyboardButton(
                    text="🔄 Реванш", callback_data=f"again:{game_id}"
                )
            )
        else:
            bottom.append(
                InlineKeyboardButton(
                    text="🔄 Ещё раз", callback_data=f"again:{game_id}"
                )
            )
        if game.mode == "online":
            bottom.append(
                InlineKeyboardButton(
                    text="🔎 Новый соперник", callback_data="online:find"
                )
            )
    bottom.append(InlineKeyboardButton(text="🏠 Меню", callback_data="menu"))
    rows.append(bottom)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def personal_ending(game: Game, viewer_id: int) -> str:
    w = winner(game.board)
    if is_draw(game.board):
        return random.choice(DRAW_LINES)

    if not w:
        return ""

    if game.mode.startswith("bot"):
        if w == X:
            return random.choice(CONGRATS) + "\nБот в шоке."
        return random.choice(LOSER_LINES) + "\nТебя обыграл бот 🤖"

    winner_id = game.player_x if w == X else game.player_o
    loser_id = game.player_o if w == X else game.player_x
    winner_name = game.name_x if w == X else game.name_o

    if viewer_id == winner_id:
        return random.choice(CONGRATS)
    if viewer_id == loser_id:
        return random.choice(LOSER_LINES) + f"\nПобедитель: {winner_name}"
    return f"Победил {winner_name}"


def render_text(game: Game, viewer_id: Optional[int] = None) -> str:
    mode_line = {
        "bot_easy": "Режим: против бота (лёгкий) 🍼",
        "bot_hard": "Режим: против бота (сложный) 😈",
        "pvp": "Режим: по приглашению 🔗",
        "online": "Режим: онлайн-матч 🌐",
    }.get(game.mode, "")

    lines = [
        f"{X} {O} Крестики-нолики",
        mode_line,
        "",
    ]

    if game.mode in ("pvp", "online"):
        lines.append(f"{X} {game.name_x}")
        lines.append(f"{O} {game.name_o if game.player_o else '…ожидание'}")
        lines.append("")

    if game.finished:
        ending = personal_ending(game, viewer_id or 0)
        if ending:
            lines.append(ending)
        if viewer_id:
            row = stats.get(str(viewer_id))
            if row and row.get("streak", 0) >= 2:
                lines.append(f"🔥 Серия побед: {row['streak']}")
    else:
        whose = game.turn
        if viewer_id:
            my_mark = None
            if viewer_id == game.player_x:
                my_mark = X
            elif viewer_id == game.player_o:
                my_mark = O
            if my_mark and whose == my_mark:
                lines.append(f"👉 Твой ход ({whose})")
            elif my_mark:
                lines.append(f"⏳ Ход соперника ({whose})")
            else:
                lines.append(f"Ход: {whose}")
        else:
            lines.append(f"Ход: {whose}")

    return "\n".join(lines)


def leave_queue(user_id: int) -> None:
    online_queue.pop(user_id, None)


def bind_user(user_id: int, game_id: str) -> None:
    leave_queue(user_id)
    user_game[user_id] = game_id


def create_bot_game(user_id: int, name: str, mode: str) -> str:
    game_id = uuid.uuid4().hex[:10]
    g = Game(mode=mode, player_x=user_id, name_x=name, turn=X)
    games[game_id] = g
    bind_user(user_id, game_id)
    return game_id


def create_pvp_game(user_id: int, name: str) -> str:
    game_id = uuid.uuid4().hex[:10]
    invite = secrets.token_urlsafe(6)
    g = Game(
        mode="pvp",
        player_x=user_id,
        name_x=name,
        turn=X,
        invite_code=invite,
    )
    games[game_id] = g
    invites[invite] = game_id
    bind_user(user_id, game_id)
    return game_id


def create_online_match(
    a_id: int, a_name: str, b_id: int, b_name: str
) -> str:
    game_id = uuid.uuid4().hex[:10]
    # случайно кто крестики
    if random.random() < 0.5:
        px, nx, po, no = a_id, a_name, b_id, b_name
    else:
        px, nx, po, no = b_id, b_name, a_id, a_name

    g = Game(
        mode="online",
        player_x=px,
        player_o=po,
        name_x=nx,
        name_o=no,
        turn=X,
    )
    games[game_id] = g
    bind_user(px, game_id)
    bind_user(po, game_id)
    return game_id


def restart_game(game: Game) -> None:
    game.board = new_board()
    game.turn = X
    game.finished = False
    game.stats_applied = False


def can_control(game: Game, user_id: int) -> bool:
    if game.mode.startswith("bot"):
        return user_id == game.player_x
    return user_id in (game.player_x, game.player_o)


async def sync_boards(game: Game, game_id: str) -> None:
    if game.finished:
        apply_result(game)

    kb = board_kb(game, game_id)
    players = [p for p in (game.player_x, game.player_o) if p]

    for uid in players:
        text = render_text(game, uid)
        prev = game.boards.get(uid)
        if prev:
            chat_id, message_id = prev
            try:
                await bot.edit_message_text(
                    text, chat_id=chat_id, message_id=message_id, reply_markup=kb
                )
                continue
            except Exception:
                pass
        try:
            sent = await bot.send_message(uid, text, reply_markup=kb)
            game.boards[uid] = (sent.chat.id, sent.message_id)
        except Exception as e:
            logger.warning("Не удалось отправить поле %s: %s", uid, e)


dp = Dispatcher()
bot = Bot(token=BOT_TOKEN)
load_stats()


@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    touch(message.from_user.id)
    ensure_stat(message.from_user.id, display_name(message.from_user))
    save_stats()

    args = (command.args or "").strip()
    if args.startswith("join_"):
        await join_by_invite(message, args[5:])
        return

    extra = (
        "\n\n🎮 «Открыть игру» — отдельное окно, как у Hamster."
        if WEBAPP_URL
        else "\n\n(Мини-приложение пока без HTTPS-ссылки — играй кнопками в чате.)"
    )
    await message.answer(
        f"Привет, {display_name(message.from_user)}!\n"
        f"{X} крестики и {O} зелёные нолики.\n\n"
        "Жми «Найти соперника» — подберём игрока из онлайна.\n"
        "Или бей бота / зови друга по ссылке."
        f"{extra}",
        reply_markup=main_menu_kb(),
    )


@dp.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    touch(message.from_user.id)
    await message.answer("Меню:", reply_markup=main_menu_kb())


@dp.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    touch(message.from_user.id)
    name = display_name(message.from_user)
    await message.answer(
        my_stats_text(message.from_user.id, name) + "\n\n" + top_text(5),
        reply_markup=main_menu_kb(),
    )


async def join_by_invite(message: Message, invite: str) -> None:
    touch(message.from_user.id)
    game_id = invites.get(invite)
    if not game_id or game_id not in games:
        await message.answer(
            "Приглашение недействительно или партия уже началась.",
            reply_markup=main_menu_kb(),
        )
        return

    g = games[game_id]
    user_id = message.from_user.id
    name = display_name(message.from_user)

    if g.player_x == user_id:
        await message.answer(f"Ты уже в этой игре как крестики {X}.")
        return

    if g.player_o is not None:
        await message.answer(
            "Место второго игрока уже занято.", reply_markup=main_menu_kb()
        )
        return

    g.player_o = user_id
    g.name_o = name
    bind_user(user_id, game_id)
    invites.pop(invite, None)

    await message.answer(f"Ты играешь ноликами {O}. Удачи!")
    try:
        await bot.send_message(
            g.player_x,
            f"Второй игрок {name} подключился! Ходи крестиками {X}.",
        )
    except Exception:
        pass

    await sync_boards(g, game_id)


@dp.callback_query(F.data == "menu")
async def on_menu(callback: CallbackQuery) -> None:
    touch(callback.from_user.id)
    leave_queue(callback.from_user.id)
    await callback.message.edit_text("Меню:", reply_markup=main_menu_kb())
    await callback.answer()


@dp.callback_query(F.data == "stats:me")
async def on_stats_me(callback: CallbackQuery) -> None:
    touch(callback.from_user.id)
    name = display_name(callback.from_user)
    await callback.message.edit_text(
        my_stats_text(callback.from_user.id, name),
        reply_markup=main_menu_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "stats:top")
async def on_stats_top(callback: CallbackQuery) -> None:
    touch(callback.from_user.id)
    await callback.message.edit_text(top_text(), reply_markup=main_menu_kb())
    await callback.answer()


@dp.callback_query(F.data == "stats:online")
async def on_stats_online(callback: CallbackQuery) -> None:
    touch(callback.from_user.id)
    await callback.answer(
        f"Сейчас онлайн ~{online_count()}, в очереди {len(online_queue)}",
        show_alert=True,
    )


@dp.callback_query(F.data == "online:cancel")
async def on_online_cancel(callback: CallbackQuery) -> None:
    touch(callback.from_user.id)
    leave_queue(callback.from_user.id)
    await callback.message.edit_text(
        "Ок, вышел из очереди.", reply_markup=main_menu_kb()
    )
    await callback.answer("Очередь отменена")


@dp.callback_query(F.data == "online:find")
async def on_online_find(callback: CallbackQuery) -> None:
    touch(callback.from_user.id)
    user_id = callback.from_user.id
    name = display_name(callback.from_user)

    # если уже в активной незавершённой игре — не пускаем
    gid = user_game.get(user_id)
    if gid and gid in games and not games[gid].finished:
        g = games[gid]
        if can_control(g, user_id) and not (
            g.mode in ("pvp", "online") and g.player_o is None and g.player_x == user_id
        ):
            # для invite-ожидания можно искать онлайн
            if not (g.mode == "pvp" and g.player_o is None):
                await callback.answer("Сначала доиграй текущую партию", show_alert=True)
                return

    leave_queue(user_id)

    # ищем пару
    opponent_id = None
    for oid in list(online_queue.keys()):
        if oid != user_id:
            opponent_id = oid
            break

    if opponent_id is None:
        online_queue[user_id] = {
            "name": name,
            "chat_id": callback.message.chat.id,
            "message_id": callback.message.message_id,
            "since": time.time(),
        }
        await callback.message.edit_text(
            "🔎 Ищем соперника…\n"
            f"В очереди: {len(online_queue)}\n"
            f"Онлайн сейчас: ~{online_count()}\n\n"
            "Как только кто-то нажмёт «Найти соперника» — начнём.",
            reply_markup=queue_kb(),
        )
        await callback.answer("В очереди")
        return

    opp = online_queue.pop(opponent_id)
    leave_queue(user_id)

    game_id = create_online_match(opponent_id, opp["name"], user_id, name)
    g = games[game_id]

    # запомним сообщения, чтобы править их
    g.boards[opponent_id] = (opp["chat_id"], opp["message_id"])
    g.boards[user_id] = (
        callback.message.chat.id,
        callback.message.message_id,
    )

    await sync_boards(g, game_id)
    await callback.answer("Соперник найден!")

    # короткое уведомление обоим
    try:
        await bot.send_message(
            opponent_id,
            f"⚔️ Матч найден! Соперник: {name}",
        )
    except Exception:
        pass
    try:
        await callback.message.answer(f"⚔️ Матч найден! Соперник: {opp['name']}")
    except Exception:
        pass


@dp.callback_query(F.data.startswith("new:"))
async def on_new_game(callback: CallbackQuery) -> None:
    touch(callback.from_user.id)
    leave_queue(callback.from_user.id)
    mode = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    name = display_name(callback.from_user)

    if mode == "pvp":
        game_id = create_pvp_game(user_id, name)
        g = games[game_id]
        me = await bot.get_me()
        link = f"https://t.me/{me.username}?start=join_{g.invite_code}"
        await callback.message.edit_text(
            f"Игра по ссылке создана.\nТы — крестики {X}.\n\n"
            "Отправь другу:\n"
            f"{link}\n\n"
            "Или жми «Найти соперника» в меню — без ссылок.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔎 Лучше найти в онлайне",
                            callback_data="online:find",
                        )
                    ],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="menu")],
                ]
            ),
        )
        await callback.answer()
        return

    game_id = create_bot_game(user_id, name, mode)
    g = games[game_id]
    text = render_text(g, user_id)
    kb = board_kb(g, game_id)
    await callback.message.edit_text(text, reply_markup=kb)
    g.boards[user_id] = (callback.message.chat.id, callback.message.message_id)
    await callback.answer("Удачи!")


@dp.callback_query(F.data.startswith("again:"))
async def on_again(callback: CallbackQuery) -> None:
    touch(callback.from_user.id)
    game_id = callback.data.split(":", 1)[1]
    g = games.get(game_id)
    if not g:
        await callback.answer("Игра не найдена", show_alert=True)
        return

    if not can_control(g, callback.from_user.id):
        await callback.answer("Это не твоя партия", show_alert=True)
        return

    if g.mode in ("pvp", "online") and g.player_o is None:
        await callback.answer("Ждём второго игрока", show_alert=True)
        return

    restart_game(g)
    # в онлайне/pvp на реванше можно поменять стороны
    if g.mode in ("pvp", "online") and g.player_x and g.player_o:
        if random.random() < 0.5:
            g.player_x, g.player_o = g.player_o, g.player_x
            g.name_x, g.name_o = g.name_o, g.name_x

    g.boards[callback.from_user.id] = (
        callback.message.chat.id,
        callback.message.message_id,
    )
    await sync_boards(g, game_id)
    await callback.answer("Реванш!")


@dp.callback_query(F.data.startswith("noop:"))
async def on_noop(callback: CallbackQuery) -> None:
    touch(callback.from_user.id)
    await callback.answer()


@dp.callback_query(F.data.startswith("m:"))
async def on_move(callback: CallbackQuery) -> None:
    touch(callback.from_user.id)
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    _, game_id, idx_s = parts
    try:
        index = int(idx_s)
    except ValueError:
        await callback.answer()
        return

    g = games.get(game_id)
    if not g:
        await callback.answer("Игра устарела. Нажми Меню.", show_alert=True)
        return

    if g.finished:
        await callback.answer("Партия уже окончена")
        return

    user_id = callback.from_user.id

    if g.mode in ("pvp", "online") and g.player_o is None:
        await callback.answer("Ждём второго игрока", show_alert=True)
        return

    if g.mode.startswith("bot"):
        if user_id != g.player_x:
            await callback.answer("Это не твоя партия", show_alert=True)
            return
        if g.turn != X:
            await callback.answer("Сейчас ход бота")
            return
        mark = X
    else:
        expected = g.player_x if g.turn == X else g.player_o
        if user_id != expected:
            await callback.answer("Сейчас не твой ход", show_alert=True)
            return
        mark = g.turn

    if not make_move(g.board, index, mark):
        await callback.answer("Клетка занята")
        return

    if game_over(g.board):
        g.finished = True
    else:
        g.turn = O if g.turn == X else X

        if g.mode.startswith("bot") and not g.finished and g.turn == O:
            bi = (
                bot_move_easy(g.board)
                if g.mode == "bot_easy"
                else bot_move_hard(g.board)
            )
            make_move(g.board, bi, O)
            if game_over(g.board):
                g.finished = True
            else:
                g.turn = X

    g.boards[user_id] = (callback.message.chat.id, callback.message.message_id)
    await sync_boards(g, game_id)

    # колбэк-ответ с эмоцией
    if g.finished:
        w = winner(g.board)
        if is_draw(g.board):
            await callback.answer("Ничья!")
        else:
            win_id = g.player_x if w == X else g.player_o
            if g.mode.startswith("bot"):
                await callback.answer(
                    "Поздравляем!" if w == X else "лох!"
                )
            else:
                await callback.answer(
                    "Поздравляем!" if user_id == win_id else "лох!"
                )
    else:
        await callback.answer("Ход принят")


async def main() -> None:
    await setup_webapp_button()
    me = await bot.get_me()
    logger.info("Бот запущен: @%s", me.username)
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
