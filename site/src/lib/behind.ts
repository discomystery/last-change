// What's behind each team's record, written by pipeline/export/behind.py. Read through a glob so a build without it
// still works: team pages and Standings then leave the section out.
const files = import.meta.glob('../../public/data/behind.json', { eager: true, import: 'default' }) as Record<string, any>;

export type Check = { kind: string; text: string };
export type Parts = { ev: number; st: number; fin: number; gk: number; en: number; gd: number };
export type BehindTeam = {
  gp: number; pts: number; pp: number; play: number; gap: number; parts: Parts;
  verdict?: 'ahead' | 'behind' | 'little_ahead' | 'little_behind' | 'level'; head?: string; why?: string[]; tags?: string[]; checks?: Check[];
  names?: { id: number; name: string }[];
};
export type Behind = { season: number; min_games: number; gap: number; fit: { base: number; per_goal: number }; teams: Record<string, BehindTeam> };

export const behind = Object.values(files)[0] as Behind | undefined;
export const behindFor = (abbr: string) => { const t = behind?.teams?.[abbr]; return t && t.checks ? t : null; };
// The five parts of a goal difference, in the order the card lists them.
export const PARTS: [keyof Parts, string, string][] = [
  ['ev', 'Chances at 5-on-5', 'Expected goals for minus against at 5-on-5: the goal difference their chances would usually bring.'],
  ['st', 'Chances on special teams', 'Expected goals for minus against on the power play, the penalty kill and other uneven strengths.'],
  ['fin', 'Shooting', 'Goals scored minus the expected goals of their shots, empty nets left out. Above zero, they have scored more than their chances usually bring.'],
  ['gk', 'Goaltending', 'Expected goals against minus goals allowed, empty nets left out. Above zero, their goalies have stopped more than the chances usually allow.'],
  ['en', 'Empty-net goals', 'Goals scored into an empty net minus goals allowed into theirs.'],
];
export const pp3 = (x: number) => x.toFixed(3).replace(/^0/, '').replace(/^-0/, '-');
export const signed = (x: number, dp = 1) => (Math.abs(x) < 0.05 ? '0' : `${x > 0 ? '+' : '−'}${Math.abs(x).toFixed(dp)}`);
