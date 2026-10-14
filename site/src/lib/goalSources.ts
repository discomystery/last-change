// Goal sources (pipeline/metrics/goal_sources.py), worded once for the team, preview and League pages.
// Each goal gets one source, checked in the pipeline's order; this list is the order the tables show them in.
export const goalSources: { key: string; label: string; phrase: string; tip: string }[] = [
  { key: 'other', label: 'Everyday even-strength play', phrase: 'everyday even-strength play', tip: 'Even-strength goals that were not a rebound and did not come within five seconds of a turnover or an offensive-zone faceoff win.' },
  { key: 'pp', label: 'Power play', phrase: 'the power play', tip: 'Goals scored with more skaters on the ice than the opponent.' },
  { key: 'rebound', label: 'Rebound', phrase: 'rebounds', tip: 'Goals scored within three seconds of a teammate’s shot on goal.' },
  { key: 'en', label: 'Empty net', phrase: 'empty nets', tip: 'Goals into a net with no goalie in it.' },
  { key: 'turnover', label: 'Off a turnover', phrase: 'turnovers', tip: 'Even-strength goals within five seconds of winning the puck (a takeaway, or the opponent’s giveaway) with no stoppage in between.' },
  { key: 'ea', label: 'Goalie pulled', phrase: 'play with the goalie pulled', tip: 'Goals scored with their own goalie off for an extra skater.' },
  { key: 'sh', label: 'Shorthanded', phrase: 'shorthanded play', tip: 'Goals scored with fewer skaters on the ice than the opponent.' },
  { key: 'faceoff', label: 'Off a faceoff win', phrase: 'faceoff wins', tip: 'Even-strength goals within five seconds of winning a faceoff in the offensive zone.' },
];
export const srcInfo: [string, string, string][] = goalSources.map((s) => [s.key, s.label, s.tip]);
export const srcPhrase: Record<string, string> = Object.fromEntries(goalSources.map((s) => [s.key, s.phrase]));
