// Share cards: 1080 x 1350 images made in the visitor's browser from the page's own data, for the post-game page's
// "Share" links. The card is drawn as ordinary HTML off screen, turned into a PNG (modern-screenshot), shown in the
// share sheet, then handed to the phone's share sheet or downloaded. Nothing is made on the server.
import { domToBlob } from 'modern-screenshot';
import LOGO from '../assets/logo/full.svg?raw';

type Two = { light: string; dark: string };
type Lane = { team: string; ends: [string, string]; avg: [number, ...unknown[]]; usual: [number, string]; now: [number, string]; ref?: [number, string] | null };
export type GameData = {
  id: number; base: string; away: string; home: string; date: string; venue: string; end: string;
  score: Record<string, number>; head: string; lede: string | null;
  teams: Record<string, { place: string; name: string; nick: string; own: Two; match: Two }>;
  replays: { away: number; tie: number; home: number; head: string; xg: Record<string, number> } | null;
  calls: { said: string; title: string; verdict: 'held' | 'partly' | 'missed'; lane: Lane | null }[];
  best: string | null;
  people: Record<string, [string, number | null]>;
};
type Score = { id: number; name: string; team: string; pos: string; score: number; pct: number | null; parts: Record<string, number> };
type Scores = { players: Score[]; units?: Record<string, { kind: string; n: number; ids: number[] }[]> };
type Job = { key: string; label: string; verdict: 'held' | 'partly' | 'missed' | 'na'; unit: string; tonight?: string; usual?: string; why: string };
type Card = { id: number; name: string; team: string; pos: string; toi: string; box: Record<string, number | string | null>; jobs: Job[]; summary: string | null; night: string | null; night_why: string | null; pill: string };
type Jobs = { players: Card[] };
export type Theme = 'light' | 'dark';

const esc = (s: unknown) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const ordinal = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'}`;
const signed = (v: number) => { const r = Math.round(v * 10) / 10; return r > 0 ? `+${r.toFixed(1)}` : r < 0 ? `−${(-r).toFixed(1)}` : '0.0'; };
const posWord: Record<string, string> = { C: 'Center', L: 'Left wing', R: 'Right wing', D: 'Defense', G: 'Goalie' };
const pillWord: Record<string, string> = { huge: '★ Huge night', big: 'Big night', did: 'Did his job', mixed: 'Mixed night', didnt: 'Off night', rough: 'Rough night' };
const mark = { held: '✓', partly: '≈', missed: '✕', na: '–' } as const;
const verdictPill = { held: '✓ Held up', partly: '≈ Partly', missed: '✕ Didn’t happen' } as const;
const group = (pos: string) => (pos === 'D' ? 'defensemen’s' : pos === 'G' ? 'goalies’' : 'forwards’');
// "How each Hurricane swung the game", or "each Lightning player" where the nickname has no singular.
const one = (nick: string) => (/[^s]s$/.test(nick) && nick !== 'Blues' ? nick.slice(0, -1) : `${nick} player`);

// The same sentence the report card shows when the whole game and his usual jobs disagree.
const nightNote = (c: Card) => {
  const quiet = c.summary === 'mixed' || c.summary === 'didnt';
  if (c.night === 'huge' && c.pill === 'big') return '. Huge on the scoresheet, but his usual jobs went quiet.';
  if ((c.night === 'huge' || c.night === 'big') && c.pill === 'mixed') return '. A good scoresheet, but his usual jobs went quiet.';
  if ((c.night === 'huge' || c.night === 'big') && quiet) return '. A strong game, even though his usual jobs were quieter.';
  if (c.night === 'good') return c.pill === 'big' ? ', on top of doing his usual jobs.' : ', so it counts for more.';
  if (c.night === 'rough' && c.summary === 'did') return ', though he still did his usual jobs.';
  return '.';
};

const foot = (note: string) => `<div class="sc-foot"><div class="sc-brand"><span class="sc-logo">${LOGO}</span><span class="sc-beta">Beta</span></div><div>${esc(note)}</div></div>`;
const shell = (theme: Theme, vars: string, body: string, cls = '') =>
  `<div class="shc ${theme}" style="${vars}"><div class="sc-sheet ${cls}">${body}</div>${foot('sparelinechange.com')}</div>`;

// A graded call's lane: end words either side, average tick, the usual as a ring and tonight as a dot.
function lane(L: Lane, col: string) {
  const pos = (x: number) => `left:${Math.min(Math.max(x, 8), 92)}%`;
  const below: [number, string][] = [[L.usual[0], L.usual[1]]];
  if (L.ref && Math.abs(L.ref[0] - L.usual[0]) > 14) below.push([L.ref[0], L.ref[1]]);
  return `<div class="sc-lane"><span class="sc-end l">${esc(L.ends[0])}</span><div class="sc-mid">
<div class="sc-lb top"><span class="nw" style="${pos(L.now[0])}">${esc(L.now[1])}</span></div>
<div class="sc-tr"><i class="avg" style="left:${L.avg[0]}%"></i>${L.ref ? `<i class="ref" style="left:${L.ref[0]}%"></i>` : ''}<i class="us" style="left:${L.usual[0]}%;border-color:${col}"></i><i class="now" style="left:${L.now[0]}%;background:${col}"></i></div>
<div class="sc-lb">${below.map(([x, t]) => `<span style="${pos(x)}">${esc(t)}</span>`).join('')}</div></div><span class="sc-end r">${esc(L.ends[1])}</span></div>`;
}

export function gameCard(g: GameData, theme: Theme) {
  const col = (t: string) => g.teams[t].match[theme];
  const end = g.end === 'OT' ? 'Final · OT' : g.end === 'SO' ? 'Final · SO' : 'Final';
  const side = (t: string, first: boolean) => {
    const ab = `<div><div class="sc-bar" style="background:${col(t)}${first ? '' : ';margin-left:auto'}"></div><div class="sc-ab">${t}</div></div>`;
    const sc = `<div class="sc-n">${g.score[t]}</div>`;
    return `<div class="sc-side">${first ? ab + sc : sc + ab}</div>`;
  };
  const rp = g.replays;
  const dots = rp ? [...Array(rp.away).fill(col(g.away)), ...Array(rp.tie).fill('var(--track)'), ...Array(rp.home).fill(col(g.home))] : [];
  const run = rp ? `<div class="sc-run" data-opt="3"><div class="sc-dots">${dots.map((c) => `<i style="background:${c}"></i>`).join('')}</div>
<div><div class="sc-kick" style="margin-bottom:12px">Run it back</div><div class="sc-disp sc-fit" style="font-size:40px;line-height:1.15">${esc(rp.head)}</div>
<div class="sc-rpc"><span><b style="color:${col(g.away)}">${rp.away}</b>${esc(g.teams[g.away].place)}</span><span><b style="color:var(--ink3)">${rp.tie}</b>even</span><span><b style="color:${col(g.home)}">${rp.home}</b>${esc(g.teams[g.home].place)}</span></div>
<div class="sc-serif sc-fit" style="font-size:22px;color:var(--ink2);margin-top:14px">Expected goals: ${esc(g.teams[g.away].place)} ${rp.xg[g.away].toFixed(1)}, ${esc(g.teams[g.home].place)} ${rp.xg[g.home].toFixed(1)}.</div></div></div>` : '';
  // Two calls keep their lanes; three or four are told in a line each.
  const roomy = g.calls.length <= 2;
  const calls = g.calls.length ? `<div class="sc-calls">${g.calls.map((c) => `<div class="sc-call"><div class="sc-said">We said: ${esc(c.said)}</div>
<div class="sc-h${roomy ? '' : ' sm'}"><span class="sc-vp ${c.verdict}">${verdictPill[c.verdict]}</span>${esc(c.title)}</div>${roomy && c.lane ? `<div data-opt="2">${lane(c.lane, col(c.lane.team) ?? 'var(--ink)')}</div>` : ''}</div>`).join('')}</div>` : '';
  const lede = !g.calls.length && g.lede ? `<div class="sc-serif sc-lede" data-opt="4">${esc(g.lede)}</div>` : '';
  const best = g.best ? `<div class="sc-watch" data-opt="1"><span class="sc-tag">Best night</span>${esc(g.best)}</div>` : '';
  return shell(theme, '', `<div class="sc-kick">${end} · ${esc(g.date)} · ${esc(g.venue)}</div>
<div class="sc-score">${side(g.away, true)}<div class="sc-midw">${g.end === 'OT' ? 'OT' : g.end === 'SO' ? 'SO' : 'FINAL'}</div>${side(g.home, false)}</div>
<div class="sc-disp sc-fit" style="font-size:50px;margin-top:14px">${esc(g.head)}</div>${run}${lede}${calls}${best}`);
}

// Swing bars on one scale for the whole game, drawn from the skaters (goalies clip at the edges).
function swingScale(s: Scores) {
  const pieces = (p: Score) => (p.parts.net != null ? [p.parts.net] : [p.parts.sheet ?? 0, (p.parts.play ?? 0) + (p.parts.usage ?? 0), p.parts.special ?? 0]);
  const sk = s.players.filter((p) => p.pos !== 'G').map(pieces);
  const lo = Math.min(-0.5, ...sk.map((ps) => ps.filter((v) => v < 0).reduce((a, b) => a + b, 0)));
  const hi = Math.max(0.5, ...sk.map((ps) => ps.filter((v) => v > 0).reduce((a, b) => a + b, 0)));
  const at = (v: number) => Math.max(0, Math.min(100, (100 * (v - lo)) / (hi - lo)));
  const bar = (p: Score, cls = '') => {
    let up = 0, down = 0, out = '';
    pieces(p).forEach((v, k) => {
      if (Math.abs(v) < 0.03) return;
      const from = v > 0 ? up : down + v;
      if (v > 0) up += v; else down += v;
      const l = at(from), w = at(from + Math.abs(v)) - l;
      if (w > 0.05) out += `<i class="s${k + 1}" style="left:${l.toFixed(1)}%;width:${w.toFixed(1)}%"></i>`;
    });
    return `<div class="sc-sb ${cls}">${out}<span class="z" style="left:${at(0).toFixed(1)}%"></span></div>`;
  };
  return { bar };
}

export function swingCard(g: GameData, s: Scores, team: string, theme: Theme) {
  const t = g.teams[team], other = team === g.away ? g.home : g.away;
  const { bar } = swingScale(s);
  const tag: Record<number, string> = {};
  for (const u of s.units?.[team] ?? []) for (const id of u.ids) tag[id] ??= `${u.kind === 'D' ? 'P' : 'L'}${u.n}`;
  const rows = s.players.filter((p) => p.team === team);
  const h = Math.min(38, Math.floor(800 / Math.max(rows.length, 1)));
  const body = `<div class="sc-kick">Swing · ${esc(g.date)} · ${esc(g.venue)}</div>
<div class="sc-disp sc-fit" style="font-size:64px;margin-top:22px">${esc(t.nick)} ${g.score[team]}, ${esc(g.teams[other].nick)} ${g.score[other]}</div>
<div class="sc-sub">How each ${esc(one(t.nick))} swung the game</div>
<div class="sc-kick" style="margin-top:20px;display:flex;justify-content:flex-end"><span class="sc-leg"><i class="s1"></i>Scoresheet<i class="s2"></i>5-on-5<i class="s3"></i>Special teams</span></div>
<div style="margin-top:14px">${rows.map((p) => `<div class="sc-swr" style="height:${h}px"><span class="nm"><em>${p.pos === 'G' ? 'G' : tag[p.id] ?? ''}</em>${esc(p.name)}</span>${bar(p)}<span class="val">${signed(p.score)}</span></div>`).join('')}</div>`;
  return shell(theme, `--team:${t.own[theme]}`, body);
}

export function nightCard(g: GameData, s: Scores | null, j: Jobs, id: number, theme: Theme) {
  const c = j.players.find((p) => p.id === id)!;
  const t = g.teams[c.team], other = c.team === g.away ? g.home : g.away;
  const [full, number] = g.people[id] ?? [c.name, null];
  const b = c.box as Record<string, number>;
  const goalie = c.pos === 'G';
  const share = b.xgf + b.xga > 0 ? ` · ${Math.round((100 * b.xgf) / (b.xgf + b.xga))}% of the 5-on-5 chances` : '';
  const sv = b.sv != null ? Number(b.sv).toFixed(3).replace(/^0/, '') : null;
  // A goalie's sentence already gives his saves, so the line under it doesn't repeat them.
  const line = goalie
    ? (c.night_why ? [sv && `${sv} save percentage`, `${c.toi} in net`] : [`${b.sa - b.ga} saves on ${b.sa} shots`, sv, `${c.toi} in net`]).filter(Boolean).join(' · ')
    : `${b.g} G · ${b.a} A · ${b.sog} shot${b.sog === 1 ? '' : 's'} · ${c.toi} TOI${share}`;
  const said = c.night_why ? `<div class="sc-serif sc-say" data-opt="2">${esc(c.night_why[0].toUpperCase() + c.night_why.slice(1) + nightNote(c))}</div>` : '';
  const me = s?.players.find((p) => p.id === id);
  let swing = '';
  if (me && s) {
    const peers = s.players.filter((p) => (p.pos === 'G') === goalie).sort((x, y) => y.score - x.score);
    const rank = peers.findIndex((p) => p.id === id) + 1;
    const where = goalie ? (peers.length > 1 ? (rank === 1 ? 'The better of the two goalies tonight' : 'The second of two goalies tonight') : '') : `${ordinal(rank)} of ${peers.length} skaters tonight`;
    const pct = me.pct != null ? `Better than ${me.pct}% of ${group(c.pos)} nights last season` : '';
    const parts = me.parts.net != null ? [] : [['Scoresheet', me.parts.sheet ?? 0], ['5-on-5', (me.parts.play ?? 0) + (me.parts.usage ?? 0)], ['Special teams', me.parts.special ?? 0]];
    swing = `<div class="sc-sec" data-opt="3"><div class="sc-kick">Swing</div>
<div style="display:flex;align-items:last baseline;gap:22px"><span class="sc-disp" style="font-size:84px">${signed(me.score)}</span><span style="font-size:27px;color:var(--ink2);line-height:1.3">${[where, pct].filter(Boolean).map(esc).join('<br>')}</span></div>
${swingScale(s).bar(me, 'big')}${parts.length ? `<div class="sc-ends">${parts.map(([k, v]) => `<span>${k} ${signed(v as number)}</span>`).join('')}</div>` : ''}</div>`;
  }
  const unit = (u: string) => u.replace(/^his team's/, `${t.place}’s`).replace(/^his team’s/, `${t.place}’s`);
  const jobs = c.jobs.length ? `<div class="sc-sec" data-opt="1"><div class="sc-kick">Did he do his job?</div><div class="sc-jobs">${c.jobs.map((x) => `<div class="sc-job ${x.verdict}"><span class="v">${mark[x.verdict]}</span><span class="l">${esc(x.label)}<span class="u">${esc(x.verdict === 'na' ? (x.why === 'core' ? 'Too few shots to judge' : 'Too little time to judge') : unit(x.unit))}</span></span>${x.verdict === 'na' ? '<span></span>' : `<span class="n"><b>${esc(x.tonight)}</b>${x.key === 'high' || x.key === 'routine' ? 'expected to save' : 'expected'} ${esc(x.usual)}</span>`}</div>`).join('')}</div></div>` : '';
  const filled = ['huge', 'big', 'did'].includes(c.pill);
  const body = `${number != null ? `<div class="sc-num">${number}</div>` : ''}
<div class="sc-kick">His night · ${c.team} ${g.score[c.team]}, ${other} ${g.score[other]} · ${esc(g.date)}</div>
<div class="sc-disp sc-fit" style="font-size:100px;margin-top:30px">${esc(full)}</div>
<div class="sc-sub"><span class="sc-dot"></span>${number != null ? `#${number} · ` : ''}${posWord[c.pos] ?? c.pos} · ${esc(t.name)}</div>
<div style="margin-top:36px"><span class="sc-vp big ${filled ? 'fill' : 'line'}">${pillWord[c.pill] ?? ''}</span></div>
${said}<div class="sc-line">${esc(line)}</div>${swing}${jobs}`;
  return shell(theme, `--team:${t.own[theme]}`, body, 'tc');
}

// Fonts go into the image as data, since the picture is drawn away from the page: the page's own Google Fonts, Latin
// letters only (with accents), fetched once per visit.
const FONTS = 'https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght,SOFT@9..144,300..800,100&family=Hanken+Grotesk:wght@400;500;600;700&family=Literata:ital,opsz,wght@0,7..72,400..700;1,7..72,400..700&display=swap';
let fontCss: Promise<string> | null = null;
const dataUrl = (blob: Blob) => new Promise<string>((ok, no) => { const r = new FileReader(); r.onload = () => ok(String(r.result)); r.onerror = no; r.readAsDataURL(blob); });
function fonts() {
  fontCss ??= (async () => {
    const css = await (await fetch(FONTS)).text();
    const faces = [...css.matchAll(/\/\*\s*(latin(?:-ext)?)\s*\*\/\s*(@font-face\s*{[^}]*})/g)].map((m) => m[2]).filter((f) => !/font-style:\s*italic/.test(f));
    const out = await Promise.all(faces.map(async (f) => {
      const url = f.match(/url\(['"]?(https?:[^)'"]+)['"]?\)/)?.[1];
      if (!url) return f;
      return f.replace(url, await dataUrl(await (await fetch(url)).blob()));
    }));
    return out.join('\n');
  })().catch((e) => { fontCss = null; throw e; });
  return fontCss;
}

// Shrink one-line titles until they fit, then drop optional parts (lowest number first) until the sheet fits.
function fit(card: HTMLElement) {
  for (const el of card.querySelectorAll<HTMLElement>('.sc-fit')) {
    let size = parseFloat(getComputedStyle(el).fontSize);
    while (el.scrollWidth > el.clientWidth + 1 && size > 16) { size -= 2; el.style.fontSize = `${size}px`; }
  }
  const sheet = card.querySelector<HTMLElement>('.sc-sheet')!;
  const opts = [...card.querySelectorAll<HTMLElement>('[data-opt]')].sort((a, b) => Number(a.dataset.opt) - Number(b.dataset.opt));
  while (sheet.scrollHeight > sheet.clientHeight + 1 && opts.length) opts.shift()!.remove();
}

/** Draws one card's HTML off screen and returns it as a PNG. */
export async function render(html: string): Promise<Blob> {
  const cssText = await fonts().catch(() => '');
  const host = document.createElement('div');
  host.className = 'shc-host';
  host.setAttribute('aria-hidden', 'true');
  host.innerHTML = html;
  document.body.appendChild(host);
  try {
    await document.fonts.ready;
    const card = host.firstElementChild as HTMLElement;
    fit(card);
    const blob = await domToBlob(card, { width: 1080, height: 1350, scale: 1, type: 'image/png', font: cssText ? { cssText } : undefined });
    if (!blob) throw new Error('No image');
    return blob;
  } finally {
    host.remove();
  }
}

const cache = new Map<string, Promise<unknown>>();
/** The game's Swing and report-card files, read once from the site's public data. */
export function load<T>(g: GameData, kind: 'scores' | 'jobs'): Promise<T> {
  const url = `${g.base}data/${kind}/${g.id}.json`;
  if (!cache.has(url)) cache.set(url, fetch(url).then((r) => (r.ok ? r.json() : Promise.reject(new Error(url)))));
  return cache.get(url) as Promise<T>;
}
