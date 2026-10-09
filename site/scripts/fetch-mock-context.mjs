// One-off helper for the Phase 1 mock: pulls real names, records and game info so the
// placeholder page shows the right people. Every statistic on the mock is still invented.
import { writeFileSync } from 'node:fs';

const API = 'https://api-web.nhle.com';
const UA = { 'User-Agent': 'last-change-hockey-site (fan project; mock context fetch)' };
const GAME_ID = 2026020163;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const get = async (path) => {
  await sleep(500);
  const res = await fetch(API + path, { headers: UA });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
};

const lastFinal = async (abbr) => {
  const sched = await get(`/v1/club-schedule-season/${abbr}/now`);
  const done = sched.games.filter((g) => g.gameType === 2 && ['OFF', 'FINAL'].includes(g.gameState));
  return done[done.length - 1];
};

const lineup = async (abbr) => {
  const game = await lastFinal(abbr);
  const box = await get(`/v1/gamecenter/${game.id}/boxscore`);
  const side = box.awayTeam.abbrev === abbr ? 'awayTeam' : 'homeTeam';
  const stats = box.playerByGameStats[side];
  const pick = (p) => ({ id: p.playerId, name: p.name.default, pos: p.position, num: p.sweaterNumber, toi: p.toi });
  let coach = null;
  try {
    const rail = await get(`/v1/gamecenter/${game.id}/right-rail`);
    coach = rail.gameInfo?.[side]?.headCoach?.default ?? null;
  } catch {}
  return {
    abbr,
    name: box[side].commonName?.default ?? abbr,
    place: box[side].placeName?.default ?? null,
    fromGame: { id: game.id, date: game.gameDate },
    coach,
    forwards: stats.forwards.map(pick),
    defense: stats.defense.map(pick),
    goalies: stats.goalies.map((g) => ({ ...pick(g), starter: g.starter ?? null })),
  };
};

const standings = await get('/v1/standings/now');
const record = (abbr) => {
  const t = standings.standings.find((s) => s.teamAbbrev.default === abbr);
  return t ? { w: t.wins, l: t.losses, otl: t.otLosses, gp: t.gamesPlayed } : null;
};

const sched = await get('/v1/club-schedule-season/CAR/now');
const g = sched.games.find((x) => x.id === GAME_ID);
const out = {
  fetchedAt: new Date().toISOString(),
  game: { id: GAME_ID, date: g.gameDate, startTimeUTC: g.startTimeUTC, venue: g.venue?.default, away: g.awayTeam.abbrev, home: g.homeTeam.abbrev },
  teams: { CAR: { ...(await lineup('CAR')), record: record('CAR') }, EDM: { ...(await lineup('EDM')), record: record('EDM') } },
};
writeFileSync(new URL('../src/data/mock-context.json', import.meta.url), JSON.stringify(out, null, 2));
for (const t of Object.values(out.teams)) console.log(t.abbr, t.name, t.place, t.coach, t.record, t.fromGame, '\nF:', t.forwards.map((p) => `${p.name} ${p.pos} ${p.toi}`).join(' | '), '\nD:', t.defense.map((p) => `${p.name} ${p.toi}`).join(' | '), '\nG:', t.goalies.map((p) => `${p.name} ${p.toi} ${p.starter}`).join(' | '));
console.log(out.game);
