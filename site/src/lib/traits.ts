// Every team style trait and goalie scale: label, tooltip, group, the words at each end of its scale and the stat in
// plain words. Shared by team, preview, League, Methods and share cards. (Moved here from the Phase 1 mock data.)
import fingerprints from '../../public/data/fingerprints.json';
import { ordinal } from './format';

export { ordinal };

type Trait = { key: string; label: string; tip: string; neutral?: boolean };
const TRAITS: Trait[] = [
  { key: 'volume', label: 'Shot volume', tip: 'How many shot attempts a team takes at 5-on-5, as a score where 100 is league average: 120 means 20% more than an average team. Teams that are behind shoot more, so each attempt is weighted for the score and for home ice first. That’s why it’s a score and not a count.' },
  { key: 'quality', label: 'Shot quality', tip: 'How dangerous the average shot is: its chance of going in, by expected goals.' },
  { key: 'suppression', label: 'Shot suppression', tip: 'How many shot attempts a team allows at 5-on-5, as a shots-against score where 100 is league average. Lower is better: 75 means a quarter fewer than an average team. Each attempt is weighted for the score and for home ice, so a team that sat on a lead isn’t punished for it. Further right means fewer allowed.' },
  { key: 'qualityAllowed', label: 'Dangerous shots allowed', tip: 'How dangerous the average shot against is. Further right means safer shots against.' },
  { key: 'pace', label: 'Pace', tip: 'How many shots both teams get at 5-on-5 combined (blocked shots left out), as a score where 100 is league average. Each shot is weighted for the score and for home ice, so a game that turned into a chase doesn’t look faster than it was.', neutral: true },
  { key: 'rebounds', label: 'Second chances', tip: 'Share of scoring chances that come from rebounds.', neutral: true },
  { key: 'turnover', label: 'Off-turnover offense', tip: 'Share of scoring chances within five seconds of forcing a turnover.', neutral: true },
  { key: 'dShots', label: 'Shots from defensemen', tip: 'Share of 5-on-5 shot attempts taken by defensemen. Some systems send defensemen deep into the offensive zone; others leave the shooting to the forwards.', neutral: true },
  { key: 'inClose', label: 'Shots from in close', tip: 'Share of 5-on-5 shots taken within 20 feet of the net (blocked shots left out, since they rarely get that far).', neutral: true },
  { key: 'shotsThrough', label: 'Getting shots through', tip: 'Share of 5-on-5 shot attempts that get past the other team’s shot blockers, compared with what the league manages in the same score (teams ahead block a little more). Further right means more get through. A habit that holds from one half of the season to the other.' },
  { key: 'forecheck', label: 'Forecheck pressure', tip: 'Hits, takeaways and forced giveaways in the offensive zone. A rough stand-in for forechecking, adjusted for each arena’s scorer.' },
  { key: 'physical', label: 'Physicality', tip: 'Hits per 60 in close games, adjusted for each arena’s scorer.', neutral: true },
  { key: 'rough', label: 'Rough stuff', tip: 'Roughing, boarding, elbowing and other body-contact penalties the team takes per 60 minutes (fights not counted). Most of these come with a call on the other team at the same whistle, so it says how chippy their games get. A style, not better or worse. It’s the one kind of penalty that really is a team habit: which stick fouls a team takes mostly comes down to luck.', neutral: true },
  { key: 'breakdowns', label: 'Breakdowns avoided', tip: 'How rarely the team gives up a dangerous chance within seconds of losing the puck. Further right means fewer breakdowns.' },
  { key: 'longShifts', label: 'Caught on long shifts', tip: 'Share of 5-on-5 shots against that come while the team’s skaters are 45 seconds or more into their shift, usually stuck in their own end and unable to change. Further right means it happens less. Teams that get caught out stay caught out from one half of the season to the next.' },
  { key: 'blocking', label: 'Blocking shots', tip: 'Share of the other team’s 5-on-5 shot attempts this team blocks, compared with what the league blocks in the same score (teams ahead block a little more), and adjusted for arenas that record more or fewer blocks. Further right means more blocked.' },
  { key: 'pointAllowed', label: 'Point shots allowed', tip: 'Share of the other team’s 5-on-5 shot attempts taken by their defensemen. Teams that collapse around their net leave the point open; teams that push out take it away. A style, not better or worse.', neutral: true },
  { key: 'icing', label: 'Icing the puck', tip: 'Icings per 60 minutes of 5-on-5 play, compared with what the league does in the same score (teams protecting a lead ice it more). Usually a sign of being stuck in their own end. Further right means fewer icings. Teams that ice it a lot tend to give up more chances.' },
  { key: 'pp', label: 'Power play', tip: 'Expected goals per 60 with the man advantage.' },
  { key: 'pk', label: 'Penalty kill', tip: 'Expected goals allowed per 60 shorthanded. Further right means stingier.' },
  { key: 'powerKill', label: 'Shorthanded threat', tip: 'How dangerous the team is while shorthanded.' },
  { key: 'discipline', label: 'Discipline', tip: 'Penalties drawn minus penalties taken, per 60.' },
  { key: 'goalie', label: 'Goaltending', tip: 'Goals saved above expected per 60, all goalies combined.' },
  { key: 'depth', label: 'Depth', tip: 'How much of the forwards’ 5-on-5 ice time goes to the six least-used forwards each night. Higher means the coach spreads minutes more evenly.' },
];

export type FpMeta = { group: string; lo: string; hi: string; say: (v: number) => string };
const f1 = (v: number) => v.toFixed(1);
// A chance with its article: "a 6.3%", "an 8.1%".
const aPct = (v: number) => { const s = `${f1(100 * v)}%`; return (s[0] === '8' || /^1[18]\./.test(s) ? 'an ' : 'a ') + s; };
// Traits shown as a score (100 is league average) rather than a rate: the arena-adjusted ones, and the shot rates weighted
// for score and venue (Sora, 2026-10-12). Read from the data: the pipeline exports a recorded twin (`raw`) for exactly
// these (team_style.INDEXED), so a new score trait needs no change here. Each carries its rate as recorded, in these words.
const someTeam = Object.values((fingerprints as any).teams)[0] as { dims: Record<string, { blend: Record<string, unknown> }> } | undefined;
export const scoreTraits = new Set(Object.entries(someTeam?.dims ?? {}).filter(([, d]) => d.blend && 'raw' in d.blend).map(([k]) => k));
export const rawWords: Record<string, string> = {
  volume: 'shot attempts per 60', suppression: 'shot attempts allowed per 60', pace: 'shots per 60, both teams combined',
  physical: 'hits recorded per 60', forecheck: 'forecheck plays recorded per 60',
};
export const fpGroups = ['Offense', 'Defense', 'Tempo and edge', 'Special teams'];
const fpMeta: Record<string, FpMeta> = {
  volume: { group: 'Offense', lo: 'Selective', hi: 'Relentless', say: (v) => `shot volume score ${Math.round(v)}` },
  quality: { group: 'Offense', lo: 'Hopeful', hi: 'Dangerous', say: (v) => `${aPct(v)} chance of scoring on the average shot` },
  rebounds: { group: 'Offense', lo: 'One-and-done', hi: 'Crashers', say: (v) => `${f1(v)}% of chances come from rebounds` },
  turnover: { group: 'Offense', lo: 'Patient', hi: 'Opportunist', say: (v) => `${f1(v)}% of chances come right after a turnover` },
  dShots: { group: 'Offense', lo: 'Forward-led', hi: 'Active D', say: (v) => `${f1(v)}% of shot attempts by defensemen` },
  inClose: { group: 'Offense', lo: 'From distance', hi: 'In close', say: (v) => `${f1(v)}% of shots come from within 20 feet` },
  shotsThrough: { group: 'Offense', lo: 'Blocked often', hi: 'Gets it through', say: (v) => `${f1(v)}% of shot attempts get past the blockers` },
  suppression: { group: 'Defense', lo: 'Porous', hi: 'Stingy', say: (v) => `shots-against score ${Math.round(v)}` },
  qualityAllowed: { group: 'Defense', lo: 'Exposed', hi: 'Sheltered', say: (v) => `${aPct(v)} chance of scoring per shot against` },
  breakdowns: { group: 'Defense', lo: 'Fire drill', hi: 'Composed', say: (v) => `${v.toFixed(2)} defensive breakdowns per 60` },
  longShifts: { group: 'Defense', lo: 'Pinned', hi: 'Fresh legs', say: (v) => `${f1(v)}% of shots against come on long shifts` },
  blocking: { group: 'Defense', lo: 'Rarely block', hi: 'Shot blockers', say: (v) => `${f1(v)}% of shot attempts against blocked` },
  pointAllowed: { group: 'Defense', lo: 'Point covered', hi: 'Guard the net', say: (v) => `${f1(v)}% of attempts against by defensemen` },
  icing: { group: 'Defense', lo: 'Ices often', hi: 'Rarely ices', say: (v) => `${f1(v)} icings per 60 at 5-on-5` },
  goalie: { group: 'Defense', lo: 'Sieve', hi: 'Wall', say: (v) => `${Math.abs(v) < 0.005 ? '' : v > 0 ? '+' : '−'}${Math.abs(v).toFixed(2)} goals saved above expected per 60` },
  pace: { group: 'Tempo and edge', lo: 'Slog', hi: 'Track meet', say: (v) => `pace score ${Math.round(v)}` },
  forecheck: { group: 'Tempo and edge', lo: 'Passive', hi: 'Hounding', say: (v) => `forecheck score ${Math.round(v)}` },
  physical: { group: 'Tempo and edge', lo: 'Finesse', hi: 'Bruising', say: (v) => `hit score ${Math.round(v)}` },
  rough: { group: 'Tempo and edge', lo: 'Calm', hi: 'Chippy', say: (v) => `${v.toFixed(2)} body-contact penalties per 60` },
  depth: { group: 'Tempo and edge', lo: 'Top-heavy', hi: 'Deep', say: (v) => `bottom six get ${f1(v)}% of forwards’ 5-on-5 time` },
  pp: { group: 'Special teams', lo: 'Harmless', hi: 'Lethal', say: (v) => `${f1(v)} expected goals per 60 on the power play` },
  pk: { group: 'Special teams', lo: 'Leaky', hi: 'Airtight', say: (v) => `${f1(v)} expected goals against per 60 on the kill` },
  powerKill: { group: 'Special teams', lo: 'Bunkered', hi: 'Predatory', say: (v) => `${f1(v)} expected goals created per 60 on the kill` },
  discipline: { group: 'Special teams', lo: 'Reckless', hi: 'Clean', say: (v) => `${Math.abs(v).toFixed(2)} ${v >= 0 ? 'more penalties drawn than taken' : 'more penalties taken than drawn'} per 60` },
};

/** Labels, tooltips, scale ends and plain-language wording for each fingerprint trait. */
export const fpInfo = Object.fromEntries(TRAITS.map((d) => [d.key, { label: d.label, tip: d.tip, ...fpMeta[d.key] }])) as Record<string, { label: string; tip: string } & FpMeta>;

/** Labels, tooltips and scale ends for goalie traits. */
export const goalieTraitInfo = [
  { key: 'gsax', label: 'Saving more than expected', tip: 'Goals saved above expected: goals prevented compared with an average goalie facing the same shots.', lo: 'Sieve', hi: 'Wall' },
  { key: 'rebounds', label: 'Rebound control', tip: 'How often his saves lead to another shot within three seconds, compared with what saves on the same kind of shot from the same spot usually give up.', lo: 'Loose', hi: 'Smothers it' },
  { key: 'busy', label: 'How busy he is', tip: 'Shots on goal he faces per 60.', lo: 'Quiet', hi: 'Under siege' },
  { key: 'danger', label: 'How dangerous the shots are', tip: 'How dangerous the shots against him are, on average.', lo: 'Sheltered', hi: 'Exposed' },
  { key: 'breakdowns', label: 'Breakdowns in front of him', tip: 'Dangerous chances he faces per 60 within seconds of his team losing the puck. A stand-in for chances faced alone.', lo: 'Rare', hi: 'Constant' },
  { key: 'gaps', label: 'Waiting between shots', tip: 'The typical wait, in game-clock time, between one shot and the next.', lo: 'Steady work', hi: 'Long waits' },
  { key: 'steady', label: 'Night-to-night steadiness', tip: 'How often he gives his team a fair chance to win.', lo: 'Streaky', hi: 'Steady' },
];
