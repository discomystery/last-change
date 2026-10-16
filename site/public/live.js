// Live scores (Sora, 2026-10-16). The NHL's own feed can't be read from a browser on another site, so the visitor's
// browser reads ESPN's public scoreboard (it allows that) every 30 s, only while a game the page shows is between
// 20 minutes before puck drop and 5 hours after it, and only while the tab is in front. Pages that show a live card
// ask for a game's goals too (`want`): ESPN's game summary, fetched only when the score changes. Scores, the clock,
// shots and goals only: nothing else from ESPN is shown or saved, and the nightly data still owns every final.
//
// Markup for a plain matchup line: a container with data-live="{game id}" holding .lv-clock (its text becomes
// "2nd 9:16", "Final (OT)"...), .lv-sc[data-side=away|home] (the scores), .lv-at ("at", hidden once live) and
// .lv-dash ("–", shown once live). The container gets .live-on while the game is on and .live-done once ESPN calls it
// final. `card(g, opts)` builds the full live card (score, shots, goal timeline, last goal), which redraws itself.
window.lcLive = (() => {
  const API = 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl';
  const AB = { TB: 'TBL', NJ: 'NJD', SJ: 'SJS', LA: 'LAK', UTAH: 'UTA' };
  const EVERY = 30e3, BEFORE = 20 * 60e3, AFTER = 5 * 3600e3;
  const state = new Map();
  const detail = new Map();
  const wanted = new Set();
  const games = new Map();
  const cards = new Set();
  const espnId = new Map();
  let timer = null, busy = false;
  const get = (url) => fetch(url, { cache: 'no-store', credentials: 'omit' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const ord = (n) => ['1st', '2nd', '3rd'][n - 1] ?? `${n}th`;
  const period = (n) => (n <= 3 ? ord(n) : n === 4 ? 'OT' : `${n - 3}OT`);
  const clockOf = (s, playoff) => {
    const t = s.type ?? {};
    if (t.state === 'post') return s.period > 3 ? `Final (${s.period === 5 && !playoff ? 'SO' : 'OT'})` : 'Final';
    if (t.state !== 'in') return /POSTPONED|CANCELED|SUSPENDED|DELAYED/.test(t.name ?? '') ? t.description : null;
    if (s.period === 5 && !playoff) return 'Shootout';
    if (t.name === 'STATUS_END_PERIOD' || (s.clock === 0 && t.name !== 'STATUS_IN_PROGRESS')) return `End of ${period(s.period)}`;
    return `${period(s.period)} ${s.displayClock}`;
  };
  const abbr = (x) => AB[x?.team?.abbreviation] ?? x?.team?.abbreviation;
  const due = () => {
    const now = Date.now();
    return [...games.values()].filter((g) => {
      const t = +new Date(g.start);
      return !g.final && state.get(g.id)?.st !== 'post' && now >= t - BEFORE && now <= t + AFTER;
    });
  };

  const paint = (root = document) => {
    const nodes = root.matches?.('[data-live]') ? [root] : [...root.querySelectorAll('[data-live]')];
    nodes.forEach((box) => {
      if (box.classList.contains('lcard')) return;
      const s = state.get(Number(box.dataset.live));
      const on = !!s && (s.st === 'in' || s.st === 'post');
      box.classList.toggle('live-on', on && s.st === 'in');
      box.classList.toggle('live-done', on && s.st === 'post');
      box.querySelectorAll('.lv-sc').forEach((n) => { n.textContent = on ? String(s[n.dataset.side === 'home' ? 'hs' : 'as']) : ''; });
      box.querySelectorAll('.lv-clock').forEach((n) => {
        if (!('orig' in n.dataset)) n.dataset.orig = n.textContent;
        n.textContent = s?.clock && (on || s.st === 'off') ? s.clock : n.dataset.orig;
      });
      if (on) box.setAttribute('aria-label', `${s.away} ${s.as}, ${s.home} ${s.hs}, ${s.clock}`);
    });
  };

  // One game's goals from ESPN's summary: period, seconds into it, side, scorer and assists, strength.
  const goalsOf = (sum) => {
    const side = Object.fromEntries((sum?.header?.competitions?.[0]?.competitors ?? []).map((c) => [c.team?.id ?? c.id, c.homeAway]));
    return (sum?.plays ?? []).filter((p) => p.scoringPlay && p.period?.number <= 4).map((p) => {
      const [m, s] = String(p.clock?.displayValue ?? '0:00').split(':').map(Number);
      const who = (p.participants ?? []).map((x) => ({ name: x.athlete?.displayName, type: x.type }));
      return {
        p: p.period.number, t: (m || 0) * 60 + (s || 0), side: side[p.team?.id] === 'home' ? 'home' : 'away',
        scorer: who.find((x) => x.type === 'scorer')?.name ?? null,
        assists: who.filter((x) => x.type === 'assister').map((x) => x.name),
        str: p.strength?.abbreviation === 'power-play' ? 'pp' : p.strength?.abbreviation === 'short-handed' ? 'sh' : null,
      };
    });
  };

  const poll = async () => {
    clearTimeout(timer);
    timer = null;
    const want = due();
    if (busy || document.hidden) return;
    if (!want.length) { plan(); return; }
    busy = true;
    const days = [...new Set(want.map((g) => g.date))];
    const got = await Promise.all(days.map((d) => get(`${API}/scoreboard?dates=${d.replaceAll('-', '')}`)));
    got.forEach((data, i) => (data?.events ?? []).forEach((e) => {
      const c = e.competitions?.[0];
      const side = (k) => c?.competitors?.find((x) => x.homeAway === k);
      const [a, h] = [side('away'), side('home')];
      if (!a || !h) return;
      const g = [...games.values()].find((x) => x.date === days[i] && x.away === abbr(a) && x.home === abbr(h));
      if (!g) return;
      espnId.set(g.id, e.id);
      const s = e.status ?? c.status ?? {};
      const st = s.type?.state;
      const clock = clockOf(s, g.playoff);
      // Goals by period (shootout left out), and shots on goal: the other side's saves plus this side's goals.
      const lines = (x) => (x.linescores ?? []).filter((l) => (l.period ?? 0) <= 4).map((l) => Number(l.value ?? 0));
      const [ap, hp] = [lines(a), lines(h)];
      const saves = (x) => Number((x.statistics ?? []).find((y) => y.name === 'saves')?.displayValue);
      const sum = (xs) => xs.reduce((p, q) => p + q, 0);
      const sa = Number.isFinite(saves(h)) ? saves(h) + sum(ap) : null;
      const sh = Number.isFinite(saves(a)) ? saves(a) + sum(hp) : null;
      const left = Number(s.clock ?? 0);
      const per = Number(s.period ?? 0);
      const len = per >= 4 && !g.playoff ? 3900 : 3600;
      const done = st === 'post' ? 1 : st === 'in' ? Math.min(1, (Math.min(per, 4) - 1) * 1200 / len + (per >= 4 && !g.playoff ? 300 - left : 1200 - left) / len) : 0;
      state.set(g.id, { st: clock && st === 'pre' ? 'off' : st, as: Number(a.score ?? 0), hs: Number(h.score ?? 0), clock, away: g.away, home: g.home, per, ap, hp, sa, sh, len, done });
    }));
    // Goals for the cards on show: fetched once a game starts and again only when the score changes.
    const need = [...wanted].filter((id) => {
      const s = state.get(id);
      return s && (s.st === 'in' || s.st === 'post') && espnId.has(id) && detail.get(id)?.key !== `${s.as}-${s.hs}-${s.st}`;
    });
    await Promise.all(need.map(async (id) => {
      const sum = await get(`${API}/summary?event=${espnId.get(id)}`);
      const s = state.get(id);
      if (sum) detail.set(id, { key: `${s.as}-${s.hs}-${s.st}`, goals: goalsOf(sum) });
    }));
    busy = false;
    paint();
    document.dispatchEvent(new CustomEvent('live'));
    plan();
  };
  // Every 30 s while a game is on; otherwise wake when the next game we know about is 20 minutes out, if that's soon.
  const plan = () => {
    if (timer || busy) return;
    if (due().length) { timer = setTimeout(poll, EVERY); return; }
    const now = Date.now();
    const next = Math.min(...[...games.values()].filter((g) => !g.final).map((g) => +new Date(g.start) - BEFORE).filter((t) => t > now));
    if (next - now < 12 * 3600e3) timer = setTimeout(poll, next - now + 1000);
  };
  // Pages hand over the games they show (schedule rows: id, start, date, away, home, final).
  const watch = (list) => {
    list.forEach((g) => { if (g?.id && g.start && !games.has(g.id)) games.set(g.id, g); });
    if (due().length && !busy) poll(); else plan();
  };
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !busy) poll(); });

  // The middle of a matchup line: "at" before the game, "3 – 2" once it's on. cls names each page's own styles.
  const middle = ({ at = 'tk-at', sc = 'tk-sc', dash = 'tk-dash' } = {}) => {
    const sp = (cls, text, sideName) => { const n = document.createElement('span'); n.className = cls; if (text) n.textContent = text; if (sideName) n.dataset.side = sideName; return n; };
    return [sp(`${at} lv-at`, 'at'), sp(`${sc} lv-sc`, '', 'away'), sp(`${dash} lv-dash`, '–'), sp(`${sc} lv-sc`, '', 'home')];
  };

  // Player names the way the site spells them (ESPN drops accents), with a link when he has a page.
  // Pages pass a lookup built from players/index.json: (espn name, team) -> { name, last, href } or null.
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const mmss = (t) => `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
  const lastGoal = (g, s, goals, o) => {
    const x = goals.at(-1);
    if (!x) return s.st === 'in' ? 'No goals yet.' : null;
    const team = x.side === 'home' ? g.home : g.away;
    const who = o.player?.(x.scorer, team) ?? { name: x.scorer ?? 'Someone', last: x.scorer };
    const p = el('p', 'lc-last');
    const n = who.href ? el('a', 'plink', who.name) : el('span', null, who.name);
    if (who.href) n.href = who.href;
    const how = x.str === 'pp' ? ' on the power play' : x.str === 'sh' ? ' shorthanded' : '';
    const when = x.p >= 4 ? `${mmss(x.t)} into overtime` : `${mmss(x.t)} into the ${ord(x.p)}`;
    const helpers = x.assists.map((a) => (o.player?.(a, team)?.last ?? a.split(' ').slice(1).join(' ')));
    const from = helpers.length ? `, from ${helpers.length > 1 ? `${helpers.slice(0, -1).join(', ')} and ${helpers.at(-1)}` : helpers[0]}.` : ', unassisted.';
    p.append(n, ` scored${how} ${when}${from}`);
    return p;
  };
  const draw = ({ el: box, g, o }) => {
    const s = state.get(g.id);
    if (!s || (s.st !== 'in' && s.st !== 'post')) return;
    const goals = detail.get(g.id)?.goals ?? [];
    box.classList.toggle('live-on', s.st === 'in');
    box.classList.toggle('live-done', s.st === 'post');
    const top = el('div', 'lc-top');
    top.append(el('span', 'lv-clock lc-clock', s.clock), el('span', 'lc-where', g.venue ?? ''));
    const row = (side) => {
      const a = side === 'home' ? g.home : g.away;
      const r = el('p', 'lc-row');
      const ab = el('span', 'lc-ab', a);
      const c = o.colour?.(a, g);
      if (c) ab.style.setProperty('--t', c);
      const mine = side === 'home' ? s.hs : s.as, theirs = side === 'home' ? s.as : s.hs;
      const shots = side === 'home' ? s.sh : s.sa;
      r.append(ab, el('span', 'lc-nick', o.nick?.(a) ?? ''), el('span', 'lc-shots', shots == null ? '' : `${shots} shots`), el('span', `lc-sc${mine < theirs ? ' l' : ''}`, String(mine)));
      return r;
    };
    // Goal timeline: the game clock left to right, visitors' goals above the line, the home team's below.
    const line = el('div', 'lc-line');
    line.setAttribute('role', 'img');
    line.setAttribute('aria-label', goals.length ? `Goals: ${goals.map((x) => `${x.side === 'home' ? g.home : g.away} ${x.p >= 4 ? 'OT' : ord(x.p)} ${mmss(x.t)}`).join(', ')}` : 'No goals yet');
    const pct = (sec) => `${((sec / s.len) * 100).toFixed(2)}%`;
    const played = el('span', 'lc-played');
    played.style.width = `${(s.done * 100).toFixed(2)}%`;
    line.append(el('span', 'lc-track'), played);
    for (let b = 1200; b < s.len; b += 1200) { const t = el('span', 'lc-tick'); t.style.left = pct(b); line.append(t); }
    if (s.st === 'in') { const now = el('span', 'lc-now'); now.style.left = pct(s.done * s.len); line.append(now); }
    goals.forEach((x) => {
      const team = x.side === 'home' ? g.home : g.away;
      const d = el('span', `lc-goal ${x.side}${x.str === 'pp' ? ' pp' : ''}`);
      d.style.left = pct((Math.min(x.p, 4) - 1) * 1200 + x.t);
      const c = o.colour?.(team, g);
      if (c) d.style.setProperty('--t', c);
      d.dataset.tip = `${o.player?.(x.scorer, team)?.name ?? x.scorer ?? 'Goal'}, ${team}, ${x.p >= 4 ? 'overtime' : ord(x.p)} ${mmss(x.t)}${x.str === 'pp' ? ' (power play)' : x.str === 'sh' ? ' (shorthanded)' : ''}`;
      line.append(d);
    });
    const pers = el('div', 'lc-pers');
    ['1st', '2nd', '3rd', ...(s.len > 3600 ? ['OT'] : [])].forEach((t) => { const p = el('span', null, t); p.style.width = pct(t === 'OT' ? 300 : 1200); pers.append(p); });
    const foot = el('div', 'lc-foot');
    foot.append(el('span', null, goals.some((x) => x.str === 'pp') ? 'Hollow dots: power-play goals' : ''));
    if (o.href) { const a = el('a', null, o.label ?? 'Preview ›'); a.href = o.href; foot.append(a); }
    const kids = [top, row('away'), row('home'), line, pers];
    const last = lastGoal(g, s, goals, o);
    if (last) kids.push(last);
    kids.push(foot);
    box.replaceChildren(...kids);
    box.setAttribute('aria-label', `${g.away} ${s.as}, ${g.home} ${s.hs}, ${s.clock}`);
  };
  const redraw = () => cards.forEach((c) => (c.el.isConnected ? draw(c) : cards.delete(c)));
  document.addEventListener('live', redraw);
  // A live card for one game. o: colour(abbr, g), nick(abbr), player(espnName, abbr), href, label.
  const card = (g, o = {}) => {
    const box = el('article', 'card lcard');
    box.dataset.live = g.id;
    wanted.add(g.id);
    const c = { el: box, g, o };
    cards.add(c);
    draw(c);
    if (!detail.has(g.id) && state.has(g.id) && !busy) poll();
    return box;
  };
  const want = (ids) => ids.forEach((id) => wanted.add(id));
  // A name lookup for cards from players/index.json: ESPN's spelling (no accents) and club to ours, with his page.
  const fold = (x) => String(x ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/ø/gi, 'o').replace(/æ/gi, 'ae').replace(/ß/g, 'ss').replace(/[^a-z ]/gi, '').toLowerCase().trim();
  const players = (index, base) => {
    const by = new Map();
    (index ?? []).forEach((p) => { const k = fold(p.name); by.set(`${k}|${p.team}`, p); if (!by.has(k)) by.set(k, p); else by.set(k, null); });
    return (name, team) => {
      if (!name) return null;
      const p = by.get(`${fold(name)}|${team}`) ?? by.get(fold(name));
      return p ? { name: p.name, last: p.last ?? p.name.split(' ').slice(1).join(' '), href: `${base}player/${p.id}/` } : null;
    };
  };
  return { watch, paint, middle, card, want, players, get: (id) => state.get(id) ?? null, goals: (id) => detail.get(id)?.goals ?? null };
})();
