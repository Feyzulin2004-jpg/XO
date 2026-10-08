# Крестики-нолики в Telegram

Два режима интерфейса:

1. **Кнопки в чате** — как раньше  
2. **Mini App** — отдельное окно поверх Telegram (как Hamster Combat)

## Запуск

```bash
python -m pip install -r requirements.txt
python run.py
```

`run.py` поднимает и бота, и сайт Mini App на `http://127.0.0.1:8080`.

Только чат-бот без окна:

```bash
python bot.py
```

## Как включить отдельное окно (Mini App)

Telegram открывает Mini App **только по HTTPS**.

1. Запусти `python run.py`
2. Подними туннель к порту 8080, например [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/):

```bash
cloudflared tunnel --url http://127.0.0.1:8080
```

или ngrok:

```bash
ngrok http 8080
```

3. Скопируй выданный `https://....` в `.env`:

```env
WEBAPP_URL=https://твой-адрес.trycloudflare.com
```

4. Перезапусти `python run.py`
5. В боте появится кнопка **«Открыть игру (окно)»** и меню **Играть** внизу чата

В @BotFather можно также: Bot Settings → Menu Button → указать тот же URL.

## Структура

- `run.py` — бот + API вместе
- `bot.py` — чат, кнопки, онлайн в чате
- `api.py` + `webapp/` — Mini App
- `game.py` — логика поля
