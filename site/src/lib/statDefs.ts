// Stat names, tips, formats and ranking rules, with no data, so the Leaders page's browser script can share them.
import { clock, ordinal } from './format';

export type Row = Record<string, any>;

// ---- Formats ----
const pct1 = (v: number | null) => (v == null ? '–' : `${(100 * v).toFixed(1)}%`);
const three = (v: number | null) => (v == null ? '–' : v.toFixed(3).replace(/^0/, ''));
const signed = (v: number | null) => (v == null ? '–' : v > 0 ? `+${v}` : v < 0 ? `−${-v}` : '0');
const num = (v: number | null) => (v == null ? '–' : String(v));
const dec = (n: number) => (v: number | null) => (v == null ? '–' : v.toFixed(n));
const time = (v: number | null) => (v == null ? '–' : clock(v));

/** A stat: short heading, full name, plain-English tip, format, and whether a lower number is better. `q` names
 *  what a player needs before he is ranked on a rate (shooting %, save %...), so one shot at one goal doesn't lead. */
type Raw = { key: string; short: string; name: string; tip: string; f: Fmt; low?: boolean; q?: Qual };
export type Stat = Raw & { fmt: (v: any) => string };
type Fmt = keyof typeof FMT;
type Qual = keyof typeof QUAL;
// Formats and qualifying rules by name, so the Leaders page's script can use the same ones (stats.ts is not shipped to it).
export const FMT = { num, pct1, three, signed, time, dec1: dec(1), dec2: dec(2), mins: (v: number | null) => (v == null ? '–' : `${Math.round(v / 60)}`) };
/** What a player needs before he is ranked on a rate, given the most games any club has played (`gp`). */
export const QUAL = {
  shots: (r: Row, gp: number) => r.s >= Math.min(50, gp),
  fo: (r: Row, gp: number) => r.fow + r.fol >= Math.min(100, 2 * gp),
  gs: (r: Row, gp: number) => r.gs >= Math.min(10, Math.ceil(gp / 3)),
  games: (r: Row, gp: number) => r.gp >= Math.min(20, Math.ceil(gp / 2)),
};
const build = (list: Raw[]): Record<string, Stat> => Object.fromEntries(list.map((s) => [s.key, { ...s, fmt: FMT[s.f] }]));

export const SKATER: Record<string, Stat> = build([
  { key: 'gp', short: 'GP', name: 'Games played', tip: 'Games he dressed for.', f: 'num' },
  { key: 'g', short: 'G', name: 'Goals', tip: 'Goals scored, all situations.', f: 'num' },
  { key: 'a', short: 'A', name: 'Assists', tip: 'Assists, first and second.', f: 'num' },
  { key: 'p', short: 'P', name: 'Points', tip: 'Goals plus assists.', f: 'num' },
  { key: 'pm', short: '+/−', name: 'Plus-minus', tip: 'Even-strength and shorthanded goals for his team while he was on the ice, minus goals against. Power-play goals don’t count.', f: 'signed' },
  { key: 'pim', short: 'PIM', name: 'Penalty minutes', tip: 'Minutes of penalties he was given.', f: 'num', low: true },
  { key: 'evg', short: 'EVG', name: 'Even-strength goals', tip: 'Goals with the same number of skaters on each side (5-on-5, 4-on-4, 3-on-3).', f: 'num' },
  { key: 'evp', short: 'EVP', name: 'Even-strength points', tip: 'Goals and assists at even strength.', f: 'num' },
  { key: 'ppg', short: 'PPG', name: 'Power-play goals', tip: 'Goals while his team had a man advantage from a penalty.', f: 'num' },
  { key: 'ppp', short: 'PPP', name: 'Power-play points', tip: 'Goals and assists on the power play.', f: 'num' },
  { key: 'shg', short: 'SHG', name: 'Shorthanded goals', tip: 'Goals while his team was killing a penalty.', f: 'num' },
  { key: 'shp', short: 'SHP', name: 'Shorthanded points', tip: 'Goals and assists while killing a penalty.', f: 'num' },
  { key: 'gwg', short: 'GWG', name: 'Game-winning goals', tip: 'The goal that put his team one ahead of the other team’s final total.', f: 'num' },
  { key: 'otg', short: 'OTG', name: 'Overtime goals', tip: 'Goals in overtime (not shootouts).', f: 'num' },
  { key: 's', short: 'S', name: 'Shots on goal', tip: 'Shots that reached the net: saves plus goals.', f: 'num' },
  { key: 'spct', short: 'S%', name: 'Shooting percentage', tip: 'Goals per shot on goal. Ranked only with 50 shots on goal (or one a game for a season under way).', f: 'pct1', q: 'shots' },
  { key: 'toi', short: 'TOI', name: 'Ice time a game', tip: 'Average minutes a game, all situations.', f: 'time' },
  { key: 'evtoi', short: 'EV TOI', name: 'Even-strength time a game', tip: 'Average minutes a game with the same number of skaters on each side (5-on-5, 4-on-4, 3-on-3).', f: 'time' },
  { key: 'pptoi', short: 'PP TOI', name: 'Power-play time a game', tip: 'Average power-play minutes a game.', f: 'time' },
  { key: 'shtoi', short: 'SH TOI', name: 'Shorthanded time a game', tip: 'Average penalty-killing minutes a game.', f: 'time' },
  { key: 'shifts', short: 'SHFT', name: 'Shifts a game', tip: 'How many times a game he went over the boards, on average.', f: 'dec1' },
  { key: 'tps', short: 'TOI/SHFT', name: 'Average shift', tip: 'How long he stays on the ice each time he goes out: his ice time divided by his shifts. Most skaters average between 0:40 and 0:55; defensemen stay out a little longer than forwards. Longer isn’t better or worse: it follows his role and his coach. Ranked only with 20 games (or half his team’s games for a season under way).', f: 'time', q: 'games' },
  { key: 'fopct', short: 'FO%', name: 'Faceoff win %', tip: 'Share of his faceoffs won. Ranked only with 100 faceoffs (or two a game for a season under way).', f: 'pct1', q: 'fo' },
  { key: 'hit', short: 'HIT', name: 'Hits', tip: 'Hits credited to him, as recorded by each arena’s scorer.', f: 'num' },
  { key: 'blk', short: 'BLK', name: 'Blocked shots', tip: 'Shots he blocked, as recorded.', f: 'num' },
  { key: 'tk', short: 'TK', name: 'Takeaways', tip: 'Times he took the puck from an opponent, as recorded.', f: 'num' },
  { key: 'gv', short: 'GV', name: 'Giveaways', tip: 'Times he gave the puck away under no pressure, as recorded.', f: 'num', low: true },
]);

export const GOALIE: Record<string, Stat> = build([
  { key: 'gp', short: 'GP', name: 'Games played', tip: 'Games he played in, starting or in relief.', f: 'num' },
  { key: 'gs', short: 'GS', name: 'Games started', tip: 'Games he started.', f: 'num' },
  { key: 'w', short: 'W', name: 'Wins', tip: 'Games his team won with him in net for the winning goal.', f: 'num' },
  { key: 'l', short: 'L', name: 'Losses', tip: 'Regulation losses.', f: 'num', low: true },
  { key: 'otl', short: 'OTL', name: 'Overtime losses', tip: 'Losses in overtime or a shootout.', f: 'num', low: true },
  { key: 'sa', short: 'SA', name: 'Shots against', tip: 'Shots on goal he faced.', f: 'num' },
  { key: 'sv', short: 'SV', name: 'Saves', tip: 'Shots on goal he stopped.', f: 'num' },
  { key: 'ga', short: 'GA', name: 'Goals against', tip: 'Goals scored on him.', f: 'num', low: true },
  { key: 'svpct', short: 'SV%', name: 'Save percentage', tip: 'Share of shots on goal he stopped. Ranked only with a start in a third of the games played so far (10 at most).', f: 'three', q: 'gs' },
  { key: 'gaa', short: 'GAA', name: 'Goals-against average', tip: 'Goals against per 60 minutes in net. Ranked like save percentage.', f: 'dec2', low: true, q: 'gs' },
  { key: 'so', short: 'SO', name: 'Shutouts', tip: 'Games he played start to finish without giving up a goal.', f: 'num' },
  { key: 'toi', short: 'TOI', name: 'Time in net', tip: 'Total minutes in net.', f: 'mins' },
]);

export const TEAM: Record<string, Stat> = build([
  { key: 'gp', short: 'GP', name: 'Games played', tip: 'Games played.', f: 'num' },
  { key: 'w', short: 'W', name: 'Wins', tip: 'Wins, including overtime and shootouts.', f: 'num' },
  { key: 'l', short: 'L', name: 'Losses', tip: 'Regulation losses.', f: 'num', low: true },
  { key: 'otl', short: 'OTL', name: 'Overtime losses', tip: 'Losses in overtime or a shootout (one point each).', f: 'num', low: true },
  { key: 'pts', short: 'PTS', name: 'Points', tip: 'Two for a win, one for an overtime or shootout loss.', f: 'num' },
  { key: 'ptspct', short: 'P%', name: 'Points %', tip: 'Points as a share of the most they could have had. Fairer than points while teams have played different numbers of games.', f: 'three' },
  { key: 'rw', short: 'RW', name: 'Regulation wins', tip: 'Wins in 60 minutes, the first tiebreaker in the standings.', f: 'num' },
  { key: 'gf', short: 'GF', name: 'Goals for', tip: 'Goals scored (shootout winners don’t count).', f: 'num' },
  { key: 'ga', short: 'GA', name: 'Goals against', tip: 'Goals allowed (shootout winners don’t count).', f: 'num', low: true },
  { key: 'gfpg', short: 'GF/GP', name: 'Goals for a game', tip: 'Goals scored per game.', f: 'dec2' },
  { key: 'gapg', short: 'GA/GP', name: 'Goals against a game', tip: 'Goals allowed per game.', f: 'dec2', low: true },
  { key: 'diff', short: 'DIFF', name: 'Goal difference', tip: 'Goals for minus goals against.', f: 'signed' },
  { key: 'pppct', short: 'PP%', name: 'Power play %', tip: 'Power-play goals per power-play chance.', f: 'pct1' },
  { key: 'ppg', short: 'PPG', name: 'Power-play goals', tip: 'Goals scored on the power play.', f: 'num' },
  { key: 'ppo', short: 'PPO', name: 'Power-play chances', tip: 'Times they went on the power play.', f: 'num' },
  { key: 'pkpct', short: 'PK%', name: 'Penalty kill %', tip: 'Share of the other team’s power plays killed without a goal.', f: 'pct1' },
  { key: 'ppga', short: 'PPGA', name: 'Power-play goals against', tip: 'Goals allowed while killing a penalty.', f: 'num', low: true },
  { key: 'tsh', short: 'TSH', name: 'Times shorthanded', tip: 'Times they had to kill a penalty.', f: 'num', low: true },
  { key: 'shgf', short: 'SHGF', name: 'Shorthanded goals', tip: 'Goals scored while killing a penalty.', f: 'num' },
  { key: 'shga', short: 'SHGA', name: 'Shorthanded goals against', tip: 'Goals allowed on their own power play.', f: 'num', low: true },
  { key: 'sfpg', short: 'SF/GP', name: 'Shots for a game', tip: 'Shots on goal a game.', f: 'dec1' },
  { key: 'sapg', short: 'SA/GP', name: 'Shots against a game', tip: 'Shots on goal allowed a game.', f: 'dec1', low: true },
  { key: 'fopct', short: 'FO%', name: 'Faceoff win %', tip: 'Share of faceoffs won.', f: 'pct1' },
  { key: 'hit', short: 'HIT', name: 'Hits', tip: 'Hits, as recorded by each arena’s scorer (some arenas count far more than others).', f: 'num' },
  { key: 'blk', short: 'BLK', name: 'Blocked shots', tip: 'Shots blocked, as recorded.', f: 'num' },
  { key: 'tk', short: 'TK', name: 'Takeaways', tip: 'Takeaways, as recorded.', f: 'num' },
  { key: 'gv', short: 'GV', name: 'Giveaways', tip: 'Giveaways, as recorded.', f: 'num', low: true },
]);

/** Rank of `value` among `all` (1 = best), with ties sharing a rank: "T-4th" when tied. */
export function rankOf(value: number | null, all: (number | null)[], low = false) {
  if (value == null) return null;
  const vals = all.filter((v): v is number => v != null);
  const better = vals.filter((v) => (low ? v < value : v > value)).length;
  const tied = vals.filter((v) => v === value).length > 1;
  return { rank: better + 1, tied, of: vals.length, word: `${tied ? 'T-' : ''}${ordinal(better + 1)}` };
}
