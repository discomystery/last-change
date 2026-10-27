// @ts-nocheck
// Written-down plays: a play is a list of where everyone is, moment by moment, and this file plays it back on a rink
// from any viewpoint. Players glide through their spots on a smooth curve; the puck moves in straight lines, as passes
// and shots do. Beats are the captions that go with each moment; events fire once when the clock passes them.
import { el, fit, drawRink, figure, puck, sortDepth, motion } from './rink';

// Where a player is at time t: Catmull-Rom through the keys [t, x, y], held still before the first and after the last.
function glide(keys, t) {
  if (t <= keys[0][0]) return [keys[0][1], keys[0][2]];
  const n = keys.length - 1;
  if (t >= keys[n][0]) return [keys[n][1], keys[n][2]];
  let i = 0;
  while (keys[i + 1][0] < t) i++;
  const p0 = keys[Math.max(0, i - 1)], p1 = keys[i], p2 = keys[i + 1], p3 = keys[Math.min(n, i + 2)];
  const u = (t - p1[0]) / (p2[0] - p1[0]);
  // A key repeated in place (a player waiting) stays still: no curve overshoot around a stop.
  const still = p1[1] === p2[1] && p1[2] === p2[2];
  if (still) return [p1[1], p1[2]];
  const cr = (a, b, c, d) => 0.5 * (2 * b + (-a + c) * u + (2 * a - 5 * b + 4 * c - d) * u * u + (-a + 3 * b - 3 * c + d) * u * u * u);
  const a0 = p0 === p1 || (p0[1] === p1[1] && p0[2] === p1[2]) ? p1 : p0;
  const a3 = p3 === p2 || (p3[1] === p2[1] && p3[2] === p2[2]) ? p2 : p3;
  return [cr(a0[1], p1[1], p2[1], a3[1]), cr(a0[2], p1[2], p2[2], a3[2])];
}
function straight(keys, t) {
  if (t <= keys[0][0]) return [keys[0][1], keys[0][2]];
  const n = keys.length - 1;
  if (t >= keys[n][0]) return [keys[n][1], keys[n][2]];
  let i = 0;
  while (keys[i + 1][0] < t) i++;
  const a = keys[i], b = keys[i + 1], u = (t - a[0]) / (b[0] - a[0]);
  return [a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u];
}

// Mount a play on an svg. def: { length, area?, pad?, actors: [{ id, kind, goalie?, tag?, keys }], puck: keys,
// beats: [{ t, h, p }], events: [{ t, on(t, api), off?(api) }] }. Returns controls; ui (optional) wires buttons.
export function mountPlay(svg, def, V, { onBeat, onTick, onPick } = {}) {
  svg.replaceChildren();
  // On a phone a seat's view of the whole rink is a thin strip, so a play can name a closer area for narrow screens.
  const narrow = matchMedia('(max-width: 600px)').matches && def.narrow;
  fit(svg, V, narrow ? def.narrow : def.area ?? [0, 0, 200, 85], def.pad ?? [2, 2, 2, 2]);
  drawRink(svg, V, def.rink ?? {});
  const fx = el(svg, 'g', { class: 'fx' });
  const g = el(svg, 'g', { class: 'people' });
  const actors = def.actors.map((a) => ({ ...a, f: figure(g, V, a.kind, { goalie: a.goalie, tag: a.tag, band: a.band }) }));
  const pk = puck(g, V);
  const api = { svg, fx, V, actors, puck: pk };
  for (const a of actors) {
    a.f.g.addEventListener('click', () => onPick && onPick(a));
    a.f.g.setAttribute('tabindex', '0');
    a.f.g.setAttribute('role', 'button');
    a.f.g.setAttribute('aria-label', a.label ?? a.kind);
    a.f.g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick && onPick(a); } });
  }
  let t = 0, raf = 0, last = 0, playing = false, beat = -1;
  const fired = new Set();
  const evs = (def.events ?? []).slice().sort((a, b) => a.t - b.t);
  function draw(at) {
    t = Math.max(0, Math.min(def.length, at));
    for (const a of actors) a.f.set(...glide(a.keys, t));
    pk.set(...straight(def.puck, t));
    sortDepth(V, g, [...actors.map((a) => a.f), pk]);
    // Undo events we have gone back past (latest first), then fire the ones we have reached (in order).
    for (let i = evs.length - 1; i >= 0; i--) if (t < evs[i].t && fired.has(i)) { fired.delete(i); evs[i].off && evs[i].off(api); }
    evs.forEach((ev, i) => { if (t >= ev.t && !fired.has(i)) { fired.add(i); ev.on(api); } });
    let b = -1;
    def.beats.forEach((x, i) => { if (t >= x.t) b = i; });
    if (b !== beat) { beat = b; onBeat && onBeat(b, def.beats[b]); }
    onTick && onTick(t);
  }
  function frame(now) {
    if (!playing) return;
    t += (now - last) / 1000; last = now;
    draw(t);
    if (t >= def.length) { playing = false; ctl.onstop && ctl.onstop(); return; }
    raf = requestAnimationFrame(frame);
  }
  const ctl = {
    get t() { return t; }, get playing() { return playing; }, length: def.length,
    play() { if (t >= def.length) draw(0); playing = true; last = performance.now(); cancelAnimationFrame(raf); raf = requestAnimationFrame(frame); },
    pause() { playing = false; cancelAnimationFrame(raf); },
    seek(at) { draw(at); },
    stop() { playing = false; cancelAnimationFrame(raf); },
    api,
  };
  draw(motion() ? 0 : def.length);
  return ctl;
}

// The usual controls under a play: play/pause, a scrubber and a caption. root holds [data-play], [data-scrub],
// [data-step], [data-h], [data-p], [data-pick]. makeView(name) rebuilds for a new viewpoint, keeping the moment.
export function playerUI(root, svg, def, viewOf, picked, { onTick } = {}) {
  const btn = root.querySelector('[data-play]'), scrub = root.querySelector('[data-scrub]');
  const stepEl = root.querySelector('[data-step]'), h = root.querySelector('[data-h]'), p = root.querySelector('[data-p]');
  const pick = root.querySelector('[data-pick]');
  scrub.max = String(def.length); scrub.step = '0.01';
  let ctl;
  const label = () => { btn.textContent = ctl.playing ? 'Pause' : ctl.t >= def.length ? 'Watch again' : 'Play'; };
  const build = () => {
    const at = ctl ? ctl.t : null, was = ctl?.playing;
    ctl?.stop();
    ctl = mountPlay(svg, def, viewOf(), {
      onBeat: (i, b) => {
        stepEl.textContent = i < 0 ? '' : `${i + 1} of ${def.beats.length}`;
        h.textContent = b ? b.h : ''; p.textContent = b ? b.p : '';
      },
      onTick: (t) => { scrub.value = String(t); onTick && onTick(t, ctl); },
      onPick: (a) => { ctl.pause(); label(); const say = picked(a); if (pick && say) { pick.hidden = false; pick.textContent = say; } },
    });
    ctl.onstop = label;
    if (at != null) ctl.seek(at);
    if (was) ctl.play();
    label();
  };
  build();
  btn.addEventListener('click', () => { if (pick) pick.hidden = true; ctl.playing ? ctl.pause() : ctl.play(); label(); });
  scrub.addEventListener('input', () => { ctl.pause(); ctl.seek(+scrub.value); label(); });
  return { rebuild: build, play() { ctl.play(); label(); }, get ctl() { return ctl; } };
}

// Light up some players (by id) from t0 until t1, or a class on the whole drawing (ids = null). Two events, so
// scrubbing back and forth turns it on and off again.
export function lit(ids, t0, t1 = Infinity, cls = 'on') {
  const each = (api, fn) => (ids ? api.actors.filter((a) => ids.includes(a.id)).map((a) => a.f.g) : [api.svg]).forEach((g) => fn(g.classList));
  const add = (api) => each(api, (c) => c.add(cls)), drop = (api) => each(api, (c) => c.remove(cls));
  const evs = [{ t: t0, on: add, off: drop }];
  if (t1 !== Infinity) evs.push({ t: t1, on: drop, off: add });
  return evs;
}
