(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#12352c");
      tg.setBackgroundColor("#0b1f1a");
    } catch (_) {}
  }

  const state = {
    userId: null,
    name: "Игрок",
    gameId: null,
    pollTimer: null,
    queueTimer: null,
    myMark: null,
  };

  const $ = (id) => document.getElementById(id);
  const screens = {
    home: $("screen-home"),
    queue: $("screen-queue"),
    game: $("screen-game"),
    stats: $("screen-stats"),
  };

  function show(name) {
    Object.values(screens).forEach((el) => el.classList.remove("active"));
    screens[name].classList.add("active");
  }

  function toast(text) {
    const el = $("toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => {
      el.hidden = true;
    }, 2200);
  }

  function authHeaders() {
    const initData = (tg && tg.initData) || "";
    return {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": initData,
      "X-User-Id": String(state.userId || ""),
      "X-User-Name": encodeURIComponent(state.name || "Игрок"),
    };
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: { ...authHeaders(), ...(options.headers || {}) },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || "Ошибка сервера");
    return data;
  }

  function resolveUser() {
    const u = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;
    if (u) {
      state.userId = u.id;
      state.name = u.username ? `@${u.username}` : [u.first_name, u.last_name].filter(Boolean).join(" ") || `игрок ${u.id}`;
      return;
    }
    // локальный просмотр в браузере
    let id = localStorage.getItem("xo_uid");
    if (!id) {
      id = String(Math.floor(1e9 + Math.random() * 1e9));
      localStorage.setItem("xo_uid", id);
    }
    state.userId = Number(id);
    state.name = localStorage.getItem("xo_name") || `Гость ${id.slice(-4)}`;
  }

  function stopPolls() {
    clearInterval(state.pollTimer);
    clearInterval(state.queueTimer);
    state.pollTimer = null;
    state.queueTimer = null;
  }

  function renderBoard(game) {
    const board = $("board");
    board.innerHTML = "";
    const finished = !!game.finished;
    const myTurn =
      !finished &&
      ((game.turn === "❌" && game.player_x === state.userId) ||
        (game.turn === "🟢" && game.player_o === state.userId));

    game.board.forEach((cell, i) => {
      const btn = document.createElement("button");
      btn.className = "cell";
      btn.type = "button";
      if (cell === "❌") {
        btn.textContent = "❌";
        btn.classList.add("mark-x");
      } else if (cell === "🟢") {
        btn.textContent = "🟢";
        btn.classList.add("mark-o");
      } else {
        btn.classList.add("empty");
        btn.textContent = "";
      }
      const locked = finished || cell !== "·" || !myTurn;
      btn.disabled = locked;
      btn.addEventListener("click", () => onMove(i));
      board.appendChild(btn);
    });
  }

  function renderGame(game) {
    state.gameId = game.id;
    state.myMark =
      game.player_x === state.userId ? "❌" : game.player_o === state.userId ? "🟢" : null;

    $("vsLine").textContent = `${game.name_x} ❌  vs  🟢 ${game.name_o || "…"}`;

    const status = $("statusLine");
    status.className = "status";
    if (game.finished) {
      if (game.result_for_you === "win") {
        status.textContent = game.message || "🎉 Поздравляем!";
        status.classList.add("win");
      } else if (game.result_for_you === "lose") {
        status.textContent = game.message || "💀 лох!";
        status.classList.add("lose");
      } else {
        status.textContent = game.message || "Ничья";
        status.classList.add("draw");
      }
      $("btnAgain").hidden = false;
    } else if (state.myMark && game.turn === state.myMark) {
      status.textContent = "👉 Твой ход";
      $("btnAgain").hidden = true;
    } else {
      status.textContent = "⏳ Ход соперника";
      $("btnAgain").hidden = true;
    }

    renderBoard(game);
    show("game");
  }

  async function refreshOnline() {
    try {
      const data = await api("/api/presence");
      $("onlinePill").textContent = `онлайн ${data.online} · очередь ${data.queue}`;
    } catch (_) {}
  }

  async function startBot(mode) {
    stopPolls();
    const game = await api("/api/game/bot", {
      method: "POST",
      body: JSON.stringify({ mode }),
    });
    renderGame(game);
  }

  async function findMatch() {
    stopPolls();
    show("queue");
    const data = await api("/api/queue/join", { method: "POST", body: "{}" });
    if (data.game) {
      renderGame(data.game);
      startGamePoll();
      return;
    }
    $("queueInfo").textContent = `В очереди: ${data.queue_size}`;
    state.queueTimer = setInterval(async () => {
      try {
        const st = await api("/api/queue/status");
        $("queueInfo").textContent = `В очереди: ${st.queue_size}`;
        if (st.game) {
          stopPolls();
          renderGame(st.game);
          startGamePoll();
          toast("Соперник найден!");
        }
      } catch (e) {
        toast(e.message);
      }
    }, 900);
  }

  async function cancelQueue() {
    stopPolls();
    try {
      await api("/api/queue/leave", { method: "POST", body: "{}" });
    } catch (_) {}
    show("home");
    refreshOnline();
  }

  function startGamePoll() {
    clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
      if (!state.gameId) return;
      try {
        const game = await api(`/api/game/${state.gameId}`);
        renderGame(game);
        if (game.finished) stopPolls();
      } catch (_) {}
    }, 1000);
  }

  async function onMove(index) {
    if (!state.gameId) return;
    try {
      const game = await api(`/api/game/${state.gameId}/move`, {
        method: "POST",
        body: JSON.stringify({ index }),
      });
      const cells = document.querySelectorAll(".cell");
      if (cells[index]) cells[index].classList.add("pop");
      renderGame(game);
      if (!game.finished && game.mode.startsWith("bot")) {
        // бот уже сходил на сервере
      } else if (!game.finished) {
        startGamePoll();
      } else {
        stopPolls();
      }
    } catch (e) {
      toast(e.message);
    }
  }

  async function again() {
    if (!state.gameId) return;
    const game = await api(`/api/game/${state.gameId}/again`, {
      method: "POST",
      body: "{}",
    });
    renderGame(game);
    if (game.mode === "online") startGamePoll();
  }

  async function showStats() {
    const data = await api("/api/stats/me");
    $("statsBox").textContent =
      `🏆 Побед: ${data.wins}\n` +
      `💀 Поражений: ${data.losses}\n` +
      `🤝 Ничьих: ${data.draws}\n` +
      `🔥 Серия: ${data.streak} (рекорд ${data.best_streak})\n\n` +
      (data.top || "");
    show("stats");
  }

  $("btnFind").onclick = () => findMatch().catch((e) => toast(e.message));
  $("btnBotEasy").onclick = () => startBot("bot_easy").catch((e) => toast(e.message));
  $("btnBotHard").onclick = () => startBot("bot_hard").catch((e) => toast(e.message));
  $("btnStats").onclick = () => showStats().catch((e) => toast(e.message));
  $("btnStatsBack").onclick = () => show("home");
  $("btnCancelQueue").onclick = () => cancelQueue();
  $("btnHome").onclick = () => {
    stopPolls();
    show("home");
    refreshOnline();
  };
  $("btnAgain").onclick = () => again().catch((e) => toast(e.message));

  resolveUser();
  refreshOnline();
  setInterval(refreshOnline, 5000);
})();
