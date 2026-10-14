// Share cards: 1080 x 1350 images made in the visitor's browser from the page's own data, for the Share links on
// post-game, preview, team and player pages. The card is drawn as ordinary HTML off screen, turned into a PNG (modern-screenshot), shown in the
// share sheet, then handed to the phone's share sheet or downloaded. Nothing is made on the server.
import { domToBlob } from 'modern-screenshot';
import LOGO from '../assets/logo/full.svg?raw';
import { signed } from './format';

type Two = { light: string; dark: string };
type Lane = { team: string | null; ends: [string, string] | null; avg: [number, ...unknown[]] | null; usual: [number, string]; now: [number, string]; ref?: [number, string] | null };
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
export type { Theme } from './shareSheet';
import type { Theme } from './shareSheet';

const esc = (s: unknown) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const ordinal = (n: number) => `${n}${n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th'}`;
const posWord: Record<string, string> = { C: 'Center', L: 'Left wing', R: 'Right wing', D: 'Defense', G: 'Goalie' };
const pillWord: Record<string, string> = { huge: '★ Huge night', big: 'Big night', did: 'Did his job', mixed: 'Mixed night', didnt: 'Off night', rough: 'Rough night' };
const mark = { held: '✓', partly: '≈', missed: '✕', na: '–' } as const;
const verdictPill = { held: '✓ Called it', partly: '≈ Close', missed: '✕ Missed' } as const;
const group = (pos: string) => (pos === 'D' ? 'defensemen’s' : pos === 'G' ? 'goalies’' : 'forwards’');
// "How each Hurricane swung the game", or "each Lightning player" where the nickname has no singular.
const one = (nick: string) => (/[^s]s$/.test(nick) && nick !== 'Blues' ? nick.slice(0, -1) : `${nick} player`);

// The same sentence the report card shows when the whole game and his usual jobs disagree.
const nightNote = (c: Card) => {
  const quiet = c.summary === 'mixed' || c.summary === 'didnt';
  if (c.night === 'huge' && c.pill === 'big') return '. Huge on the scoresheet, but he came up short on his usual responsibilities.';
  if ((c.night === 'huge' || c.night === 'big') && c.pill === 'mixed') return '. A good scoresheet, but he came up short on his usual responsibilities.';
  if ((c.night === 'huge' || c.night === 'big') && quiet) return '. A strong game, even if he didn’t handle all of his usual responsibilities.';
  if (c.night === 'good') return c.pill === 'big' ? ', on top of handling his usual responsibilities.' : ', so it counts for more.';
  if (c.night === 'rough' && c.summary === 'did') return ', though he still handled his usual responsibilities.';
  return '.';
};

const foot = (note: string) => `<div class="sc-foot"><div class="sc-brand"><span class="sc-logo">${LOGO}</span><span class="sc-beta">Beta</span></div><div>${esc(note)}</div></div>`;
const shell = (theme: Theme, vars: string, body: string, cls = '', note = 'sparelinechange.com') =>
  `<div class="shc ${theme}" style="${vars}"><div class="sc-sheet ${cls}">${body}</div>${foot(note)}</div>`;

// A graded call's lane: end words either side, average tick, the usual as a ring and tonight as a dot.
function lane(L: Lane, col: string) {
  const pos = (x: number) => `left:${Math.min(Math.max(x, 8), 92)}%`;
  const below: [number, string][] = [[L.usual[0], L.usual[1]]];
  if (L.ref && Math.abs(L.ref[0] - L.usual[0]) > 14) below.push([L.ref[0], L.ref[1]]);
  return `<div class="sc-lane"><span class="sc-end l">${esc(L.ends?.[0])}</span><div class="sc-mid">
<div class="sc-lb top"><span class="nw" style="${pos(L.now[0])}">${esc(L.now[1])}</span></div>
<div class="sc-tr">${L.avg ? `<i class="avg" style="left:${L.avg[0]}%"></i>` : ''}${L.ref ? `<i class="ref" style="left:${L.ref[0]}%"></i>` : ''}<i class="us" style="left:${L.usual[0]}%;border-color:${col}"></i><i class="now" style="left:${L.now[0]}%;background:${col}"></i></div>
<div class="sc-lb">${below.map(([x, t]) => `<span style="${pos(x)}">${esc(t)}</span>`).join('')}</div></div><span class="sc-end r">${esc(L.ends?.[1])}</span></div>`;
}

// "Replay this game's chances 100 times and Carolina wins 52 of them." The
// game's winner, whatever the replays say; the counts row above gives the rest.
function replaySay(g: GameData, col: (t: string) => string) {
  const rp = g.replays!;
  const win = g.score[g.home] > g.score[g.away] ? g.home : g.away;
  const n = win === g.home ? rp.home : rp.away;
  return `Replay this game\u2019s chances 100 times and <b style="color:${col(win)}">${esc(g.teams[win].place)}</b> wins <b style="color:${col(win)}">${n}</b> of them. Expected goals: ${esc(g.teams[g.away].place)} ${rp.xg[g.away].toFixed(1)}, ${esc(g.teams[g.home].place)} ${rp.xg[g.home].toFixed(1)}.`;
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
<div><div class="sc-kick" style="margin-bottom:12px">Run it back</div><div class="sc-disp sc-fit" style="font-size:34px;line-height:1.15">${esc(rp.head)}</div>
<div class="sc-rpc"><span><b style="color:${col(g.away)}">${rp.away}</b>${esc(g.teams[g.away].place)}</span><span><b style="color:var(--ink3)">${rp.tie}</b>even</span><span><b style="color:${col(g.home)}">${rp.home}</b>${esc(g.teams[g.home].place)}</span></div>
<div class="sc-rps">${replaySay(g, col)}</div></div></div>` : '';
  // Two calls keep their lanes; three or four are told in a line each.
  const roomy = g.calls.length <= 2;
  const calls = g.calls.length ? `<div class="sc-calls">${g.calls.map((c) => `<div class="sc-call"><div class="sc-said">We said ${esc(c.said)}</div>
<div class="sc-h${roomy ? '' : ' sm'}"><span class="sc-vp ${c.verdict}">${verdictPill[c.verdict]}</span><span class="sc-ht">${esc(c.title)}</span></div>${roomy && c.lane ? `<div data-opt="2">${lane(c.lane, (c.lane.team && g.teams[c.lane.team] ? col(c.lane.team) : null) ?? 'var(--ink)')}</div>` : ''}</div>`).join('')}</div>` : '';
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

// ---------- Cards for team, player, goalie and preview pages: their data is worked out when the page is built. ----------
type Mode = 'blend' | 'season';
const modeWord = { blend: 'Blended', season: 'This season' } as const;
/** A scale lane: where he or they sit (percentile), the likely range, the ends' words and the rank in words. */
export type ScaleLane = { label: string; rank: string; pct: number; lo?: number | null; hi?: number | null; ends: [string, string] };
const clampPct = (v: number) => Math.max(0, Math.min(100, v));
function scale(l: ScaleLane, opt?: number) {
  const wh = l.lo != null && l.hi != null ? `<i class="wh" style="left:${clampPct(l.lo)}%;width:${Math.max(clampPct(l.hi) - clampPct(l.lo), 1.2)}%"></i>` : '';
  return `<div class="sc-ln"${opt ? ` data-opt="${opt}"` : ''}><div class="top"><span>${esc(l.label)}</span><em>${esc(l.rank)}</em></div>
<div class="sc-trk">${wh}<i class="avg"></i><i class="pt" style="left:${clampPct(l.pct)}%"></i></div>
<div class="sc-lends"><span>${esc(l.ends[0])}</span><span>${esc(l.ends[1])}</span></div></div>`;
}
const statRow = (cells: [string | number, string][]) => `<div class="sc-stats">${cells.map(([v, k]) => `<div><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join('')}</div>`;
const nameBlock = (kick: string, name: string, size: number, sub: string, number: number | null) =>
  `${number != null ? `<div class="sc-num">${number}</div>` : ''}<div class="sc-kick">${esc(kick)}</div>
<div class="sc-disp sc-fit" style="font-size:${size}px;margin-top:28px">${esc(name)}</div>
<div class="sc-sub"><span class="sc-dot"></span>${esc(sub)}</div>`;

export type TeamCardData = { abbr: string; name: string; place: string; own: Two; line: string; asOf: string; modes: Record<Mode, ScaleLane[]> };
/** Team style: their most distinctive traits, the strongest leanings first, with the three biggest as a headline. */
export function teamCard(d: TeamCardData, mode: Mode, theme: Theme) {
  const lanes = d.modes[mode] ?? d.modes.blend;
  const far = (l: ScaleLane) => Math.abs(l.pct - 50);
  const high = lanes.filter((l) => l.pct >= 50), low = lanes.filter((l) => l.pct < 50);
  // The headline is their three strongest leanings toward the right-hand word, the left-hand ones only to make up three.
  const byFar = (a: ScaleLane, b: ScaleLane) => far(b) - far(a);
  const words = [...[...high].sort(byFar), ...[...low].sort(byFar)].slice(0, 3).map((l) => (l.pct >= 50 ? l.ends[1] : l.ends[0]));
  // The least distinctive traits go first if the sheet runs out of room.
  const order = [...lanes].sort((a, b) => far(a) - far(b));
  const opt = (l: ScaleLane) => (lanes.length > 4 && order.indexOf(l) < lanes.length - 4 ? order.indexOf(l) + 1 : undefined);
  const body = `<div class="sc-kick">Team style · ${modeWord[mode]}</div>
<div class="sc-disp sc-fit" style="font-size:84px;margin-top:26px">${esc(d.name)}</div>
<div class="sc-sub"><span class="sc-dot"></span>${esc(d.line)}</div>
${words.length ? `<div class="sc-disp sc-fit" style="font-size:52px;margin-top:30px;color:var(--team)">${words.map((w) => `${esc(w)}.`).join(' ')}</div>` : ''}
<div style="margin-top:40px">${high.map((l) => scale(l, opt(l))).join('')}${high.length && low.length ? '<div class="sc-rule"></div>' : ''}${low.map((l) => scale(l, opt(l))).join('')}</div>`;
  return shell(theme, `--team:${d.own[theme]}`, body, '', `sparelinechange.com · ${d.asOf}`);
}

export type Tile = { label: string; v: number; sub: string; tier: string | null; cls: string; mates: [number, boolean][] };
export type PlayerCardData = {
  id: number; name: string; last: string; number: number | null; sub: string; place: string; own: Two; asOf: string;
  stats: [string | number, string][]; chips: string[]; modes: Record<Mode, { lanes: ScaleLane[]; tiles: Tile[] }>;
};
const tierCls = (c: string) => (c === 'v4' || c === 'v5' ? 'quiet' : 'team');
/** Player card: season line, what his club counts on him for, what he does better than most, and his share of the scoring. */
export function playerCard(d: PlayerCardData, mode: Mode, theme: Theme) {
  const m = d.modes[mode] ?? d.modes.blend;
  const tiles = m.tiles.slice(0, 2);
  const tile = (t: Tile) => `<div><div class="sc-clab">${esc(t.label)}</div>
<div class="sc-cbig"><span class="sc-disp">${Math.round(t.v)}%</span>${t.tier ? `<span class="sc-tier ${tierCls(t.cls)}">${esc(t.tier)}</span>` : ''}</div>
<div class="sc-csub">${esc(t.sub)}</div><div class="sc-ddot"><span></span>${t.mates.map(([x, me]) => (me ? `<b style="left:${x}%"></b>` : `<i style="left:${x}%"></i>`)).join('')}</div></div>`;
  const body = `${nameBlock(`Player card · ${modeWord[mode]}`, d.name, 100, d.sub, d.number)}
${d.stats.length ? statRow(d.stats) : ''}
${d.chips.length ? `<div class="sc-sec" data-opt="2"><div class="sc-kick">${esc(d.place)} count on him for</div><div class="sc-chips">${d.chips.map((c) => `<span>${esc(c)}</span>`).join('')}</div></div>` : ''}
${m.lanes.length ? `<div class="sc-sec" data-opt="3"><div class="sc-kick">Better than most at</div>${m.lanes.slice(0, 2).map((l) => scale(l)).join('')}</div>` : ''}
${tiles.length ? `<div class="sc-sec" data-opt="1"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px"><div class="sc-kick">His share of ${esc(d.place)}’s scoring</div><div class="sc-dleg"><i class="me"></i>${esc(d.last)}<i class="tm"></i>Teammates</div></div>
<div class="sc-ctiles">${tiles.map(tile).join('')}</div></div>` : ''}`;
  return shell(theme, `--team:${d.own[theme]}`, body, '', `sparelinechange.com · ${d.asOf}`);
}

export type GoalieCardData = {
  name: string; number: number | null; sub: string; own: Two; asOf: string; stats: [string | number, string][];
  quad: { mid: [number, number]; league: [number, number][]; mates: [number, number, string][]; me: [number, number, string] } | null;
  sv: { label: string; val: string; rank: string; dots: number[]; mates: number[]; me: number }[];
  work: ScaleLane[]; reb: ScaleLane | null; night: [number, number, number] | null; starts: number;
};
// "Helping, or hung out to dry?": every goalie's goals saved above expected against what his team allows, as on team pages.
function quadrant(q: NonNullable<GoalieCardData['quad']>, W: number, H: number) {
  const X = (v: number) => (v / 100) * W, Y = (v: number) => (v / 100) * H;
  const cs = 'font:600 15px Hanken Grotesk;letter-spacing:.07em;text-transform:uppercase;fill:var(--ink3)';
  const corner = (x: number, y: number, a: string, l1: string, l2: string) => `<text x="${x}" y="${y}" text-anchor="${a}" style="${cs}"><tspan x="${x}">${l1}</tspan><tspan x="${x}" dy="19">${l2}</tspan></text>`;
  const label = (x: number, y: number, name: string, me: boolean) => {
    const right = x < W - 130;
    return `<text x="${(right ? x + 18 : x - 18).toFixed(1)}" y="${(y + 7).toFixed(1)}" text-anchor="${right ? 'start' : 'end'}" style="font:${me ? 700 : 500} 20px Hanken Grotesk;fill:var(--ink);paint-order:stroke;stroke:var(--ice);stroke-width:5px">${esc(name)}</text>`;
  };
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" style="display:block"><rect width="${W}" height="${H}" rx="18" fill="var(--ice)"/>
<line x1="0" x2="${W}" y1="${Y(q.mid[1]).toFixed(1)}" y2="${Y(q.mid[1]).toFixed(1)}" stroke="var(--ink3)" stroke-width="2" opacity=".45"/><line x1="${X(q.mid[0]).toFixed(1)}" x2="${X(q.mid[0]).toFixed(1)}" y1="0" y2="${H}" stroke="var(--ink3)" stroke-width="2" opacity=".45"/>
${corner(16, 28, 'start', 'System and goalie', 'both good')}${corner(W - 16, 28, 'end', 'Goalie bailing', 'them out')}${corner(16, H - 31, 'start', 'Goalie', 'giving back')}${corner(W - 16, H - 31, 'end', 'Trouble', 'everywhere')}
${q.league.map(([x, y]) => `<circle cx="${X(x).toFixed(1)}" cy="${Y(y).toFixed(1)}" r="5" fill="var(--ink3)" opacity=".3"/>`).join('')}
${q.mates.map(([x, y, n]) => `<circle cx="${X(x).toFixed(1)}" cy="${Y(y).toFixed(1)}" r="9" fill="var(--sheet)" stroke="var(--team)" stroke-width="4"/>${label(X(x), Y(y), n, false)}`).join('')}
<circle cx="${X(q.me[0]).toFixed(1)}" cy="${Y(q.me[1]).toFixed(1)}" r="11" fill="var(--team)" stroke="var(--ice)" stroke-width="3"/>${label(X(q.me[0]), Y(q.me[1]), q.me[2], true)}</svg>`;
}
/** Goalie card: his line, the league chart, save % by kind of shot against every other goalie, what's in front of him and his nights. */
export function goalieCard(d: GoalieCardData, theme: Theme) {
  const sv = d.sv.map((l) => `<div class="sc-ln"><div class="top"><span>${esc(l.label)}</span><em><b>${esc(l.val)}</b> · ${esc(l.rank)}</em></div>
<div class="sc-trk"><i class="avg"></i>${l.dots.map((x) => `<i class="gd" style="left:${x}%"></i>`).join('')}${l.mates.map((x) => `<i class="gm" style="left:${x}%"></i>`).join('')}<i class="pt" style="left:${l.me}%"></i></div>
<div class="sc-lends"><span>Sieve</span><span>Wall</span></div></div>`).join('');
  const n = d.night;
  const night = n ? `<div class="bot"><div class="sc-kick k2">Night to night</div><p class="k2s">Goals saved above expected, ${d.starts} starts</p>
<div class="sc-nsb">${n[0] ? `<i class="r" style="flex:${n[0]}"></i>` : ''}${n[1] ? `<i class="e" style="flex:${n[1]}"></i>` : ''}${n[2] ? `<i class="s" style="flex:${n[2]}"></i>` : ''}</div>
<p class="sc-nsl"><span><b>${n[0]}</b> rough</span><span><b>${n[1]}</b> about expected</span><span><b>${n[2]}</b> strong</span></p></div>` : '';
  const body = `${nameBlock('Goalie card · Blended', d.name, 88, d.sub, d.number)}
${statRow(d.stats)}
<div class="sc-g3"><div class="col">${d.quad ? `<div class="sc-kick k2">Helping, or hung out to dry?</div>${quadrant(d.quad, 408, 421)}` : ''}
${d.work.length ? `<div class="bot"><div class="sc-kick k2">In front of him</div>${d.work.map((l) => scale(l)).join('')}</div>` : ''}</div>
<div class="col"><div class="sc-kick k2">Save % against other goalies</div><div class="svl">${sv}${d.reb ? scale(d.reb) : ''}</div>${night}</div></div>`;
  return shell(theme, `--team:${d.own[theme]}`, body, 'gc', `sparelinechange.com · ${d.asOf}`);
}

export type PreviewCardData = {
  id: number; date: string; away: string; home: string; teams: Record<string, { nick: string; rec: string; match: Two }>;
  head: string; lede: string | null; calls: string[]; items: { tag: string; text: string }[];
};
/** Preview: the matchup, the story's headline, every "What to watch" call, then "Who to watch" as room allows. */
export function previewCard(d: PreviewCardData, theme: Theme) {
  const side = (t: string, right: boolean) => `<div class="tm"${right ? ' style="text-align:right"' : ''}><div class="sc-bar" style="background:${d.teams[t].match[theme]}${right ? ';margin-left:auto' : ''}"></div><div class="ab">${t}</div><div class="rec">${esc(d.teams[t].nick)} · ${esc(d.teams[t].rec)}</div></div>`;
  const calls = d.calls.length ? `<div class="sc-sec" style="margin-top:28px"><div class="sc-kick">What to watch</div><div class="sc-pcalls">${d.calls.map((c, i) => `<div class="call"><div class="n">${i + 1}</div><div class="h${d.calls.length > 2 ? ' sm' : ''}">${esc(c)}</div></div>`).join('')}</div></div>` : '';
  // Who to watch fills whatever room the calls leave, last item first to go.
  const items = d.items.length ? `<div class="sc-sec" style="margin-top:26px" data-opt="${d.items.length + 1}" data-needs=".sc-watch"><div class="sc-kick">Who to watch</div>${d.items.map((x, i) => `<div class="sc-watch sm" data-opt="${d.items.length - i}"><span class="sc-tag">${esc(x.tag)}</span>${esc(x.text)}</div>`).join('')}</div>` : '';
  const body = `<div class="sc-kick">Game preview · ${esc(d.date)}</div>
<div class="sc-vs">${side(d.away, false)}<div class="at">at</div>${side(d.home, true)}</div>
<div class="sc-disp sc-fit" style="font-size:60px;margin-top:36px">${esc(d.head)}</div>
${d.lede ? `<div class="sc-serif sc-plede" data-opt="${d.items.length + 2}">${esc(d.lede)}</div>` : ''}${calls}${items}`;
  return shell(theme, '', body, '', 'Graded after the game · sparelinechange.com');
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
  // Chips that wrap onto a second row get smaller until they fit on one (down to 22px), before anything is dropped.
  for (const row of card.querySelectorAll<HTMLElement>('.sc-chips')) {
    const chips = [...row.children] as HTMLElement[];
    const wraps = () => chips.some((c) => c.offsetTop > chips[0].offsetTop + 2);
    let size = chips.length ? parseFloat(getComputedStyle(chips[0]).fontSize) : 0;
    while (chips.length && wraps() && size > 22) { size -= 1; chips.forEach((c) => { c.style.fontSize = `${size}px`; c.style.paddingInline = `${Math.round(size * 0.75)}px`; }); }
  }
  // Pin wrapped lines where the page broke them: the image is drawn a hair narrower than the layout,
  // so a title right at the edge would otherwise come out on one line with a gap under it.
  for (const el of card.querySelectorAll<HTMLElement>('.sc-ht')) {
    const words = (el.textContent ?? '').split(' ');
    el.innerHTML = words.map((w) => `<span>${esc(w)}</span>`).join(' ');
    const lines: string[][] = [];
    let top = -1;
    for (const sp of el.querySelectorAll('span')) {
      if (sp.offsetTop !== top) { lines.push([]); top = sp.offsetTop; }
      lines[lines.length - 1].push(sp.textContent ?? '');
    }
    // No last line of one or two words: borrow from the line above while it keeps three.
    for (let i = lines.length - 1; i > 0; i--) while (lines[i].length < 3 && lines[i - 1].length > 3) lines[i].unshift(lines[i - 1].pop()!);
    el.innerHTML = lines.map((l) => `<span style="white-space:nowrap">${esc(l.join(' '))}</span>`).join('<br>');
    // The pill lines up with the first line of the title, not the whole block.
    const pill = el.previousElementSibling as HTMLElement | null;
    if (pill) {
      const half = (pill.offsetHeight - parseFloat(getComputedStyle(el).lineHeight)) / 2;
      if (half > 0) el.style.paddingTop = `${half}px`; else pill.style.marginTop = `${-half}px`;
    }
  }
  const sheet = card.querySelector<HTMLElement>('.sc-sheet')!;
  const opts = [...card.querySelectorAll<HTMLElement>('[data-opt]')].sort((a, b) => Number(a.dataset.opt) - Number(b.dataset.opt));
  while (sheet.scrollHeight > sheet.clientHeight + 1 && opts.length) opts.shift()!.remove();
  // A section whose every entry was dropped goes too, rather than leaving its heading alone.
  card.querySelectorAll<HTMLElement>('[data-needs]').forEach((el) => { if (!el.querySelector(el.dataset.needs!)) el.remove(); });
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
