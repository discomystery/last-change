// The Academy's lessons, in the order they are taught (outline doc "Spare Line Change Academy: outline"). A lesson may
// only use ideas an earlier lesson has taught. `ready` lessons have a page at academy/{slug}/; the rest are listed as
// coming. `words` are the terms a lesson introduces: they make its "Words you'll hear" box and the glossary.

export type Word = { word: string; means: string };
export type Lesson = { slug: string; title: string; blurb: string; minutes?: number; extra?: boolean; ready?: boolean; words?: Word[] };
export type Track = { id: string; title: string; blurb: string; lessons: Lesson[] };

export const TRACKS: Track[] = [
  {
    id: 'before',
    title: 'Before your first game',
    blurb: 'The rink, the players, how a game runs and the few rules you’ll see every few minutes. Enough to follow along.',
    lessons: [
      {
        slug: 'the-game-in-a-minute', title: 'The game in a minute', minutes: 3, ready: true,
        blurb: 'Two teams, one puck, and a net at each end. The whole idea before any rules.',
        words: [
          { word: 'Puck', means: 'The hard rubber disc players pass and shoot. It’s three inches across and one inch thick.' },
          { word: 'Stick', means: 'What every player uses to move the puck. Goalies have a wider one.' },
          { word: 'Net', means: 'The goal at each end of the rink, six feet wide and four feet high.' },
          { word: 'Goal', means: 'When the puck goes into the net. Most goals wins.' },
          { word: 'Goalie', means: 'The player who guards the net. The only one allowed to hold on to the puck.' },
          { word: 'Skater', means: 'Any player who isn’t a goalie. Each team has five on the ice.' },
        ],
      },
      {
        slug: 'the-rink', title: 'The rink', minutes: 5, ready: true,
        blurb: 'Every line painted on the ice means something. Learn them once and the game stops looking random.',
        words: [
          { word: 'Boards', means: 'The low wall around the rink. The puck bounces off it and play goes on.' },
          { word: 'Glass', means: 'The clear panels on top of the boards that keep the puck in and let you see through.' },
          { word: 'Goal line', means: 'The thin red line at each end, through the net. A goal counts once the whole puck is over it.' },
          { word: 'Crease', means: 'The light blue half circle in front of each net: the goalie’s space.' },
          { word: 'Blue lines', means: 'The two thick blue lines that split the rink into three zones.' },
          { word: 'Defending zone', means: 'The end where a team’s own goalie is. Also called their own end.' },
          { word: 'Neutral zone', means: 'The middle of the rink, between the two blue lines.' },
          { word: 'Attacking zone', means: 'The end where a team is trying to score.' },
          { word: 'Center red line', means: 'The thick red line across the middle of the rink.' },
          { word: 'Faceoff dots', means: 'The nine dots where play starts again after a stop.' },
          { word: 'Bench', means: 'Where each team’s players wait to go on. The two benches are side by side.' },
          { word: 'Penalty box', means: 'Where a player sits for a while after breaking a rule, across the ice from the benches.' },
        ],
      },
      { slug: 'whos-on-the-ice', title: 'Who’s on the ice', blurb: 'Six players a side, each with a job.' },
      { slug: 'officials', title: 'Who’s blowing the whistle', blurb: 'The four officials, the whistle and the faceoff.' },
      { slug: 'changing-on-the-fly', title: 'Changing on the fly', blurb: 'Why players come off every minute or so, often while play goes on.' },
      { slug: 'how-a-game-is-built', title: 'How a game is built', blurb: 'Periods, the clock, the scoreboard, overtime and the shootout.' },
      { slug: 'goals-assists-points', title: 'Goals, assists and points', blurb: 'When a goal counts, own goals included, and who gets credit.' },
      { slug: 'offside-and-icing', title: 'Offside and icing', blurb: 'The two whistles you’ll hear most, and what the lines on the ice have to do with them.' },
      { slug: 'penalties', title: 'Penalties: the basics', blurb: 'The penalty box, the power play and the penalty kill.' },
      { slug: 'other-sports', title: 'If you’re used to other sports', blurb: 'What’s like soccer, basketball and football, and what isn’t.' },
      { slug: 'where-to-look', title: 'Where to look', blurb: 'How fans keep track of a small, fast puck.' },
      { slug: 'the-league', title: 'The league in one picture', extra: true, blurb: '32 teams, how they’re grouped, and how a season ends with the Stanley Cup.' },
      { slug: 'going-to-a-game', title: 'Going to a game', extra: true, blurb: 'What you’ll see at the arena that TV doesn’t show.' },
    ],
  },
  {
    id: 'during',
    title: 'During your first game',
    blurb: 'The questions a friend asks you mid-game, answered in the first two sentences.',
    lessons: [
      { slug: 'why-the-whistle', title: 'Why was there a whistle?', blurb: '' },
      { slug: 'why-that-dot', title: 'Why are they facing off there?', blurb: '' },
      { slug: 'offside-icing-fine-print', title: 'Offside and icing: the fine print', blurb: '' },
      { slug: 'why-a-penalty', title: 'Why was that a penalty?', blurb: '' },
      { slug: 'power-plays', title: 'Power plays up close', blurb: '' },
      { slug: 'where-did-the-goalie-go', title: 'Where did the goalie go?', blurb: '' },
      { slug: 'goal-taken-back', title: 'Why did they take that goal back?', blurb: '' },
      { slug: 'fights', title: 'Why are they fighting?', blurb: '' },
      { slug: 'overtime', title: 'How does overtime work?', blurb: '' },
      { slug: 'after-the-horn', title: 'What happens after the horn?', blurb: '' },
    ],
  },
  {
    id: 'why',
    title: 'Why teams do what they do',
    blurb: 'The strategy behind what you’re seeing, once the rules make sense.',
    lessons: [
      { slug: 'lines-and-last-change', title: 'Lines and last change', blurb: '' },
      { slug: 'up-the-ice', title: 'Getting the puck up the ice', blurb: '' },
      { slug: 'forecheck-and-trap', title: 'The forecheck and the trap', blurb: '' },
      { slug: 'where-goals-come-from', title: 'Where goals come from', blurb: '' },
      { slug: 'goalies-craft', title: 'The goalie’s craft', blurb: '' },
      { slug: 'special-teams', title: 'Special teams tactics', blurb: '' },
      { slug: 'playing-with-a-lead', title: 'Playing with a lead', blurb: '' },
      { slug: 'hits-and-blocks', title: 'Hits and blocked shots', blurb: '' },
    ],
  },
  {
    id: 'numbers',
    title: 'What our pages are showing you',
    blurb: 'The numbers on Spare Line Change, from shots to expected goals, and how to read a preview.',
    lessons: [
      { slug: 'shots', title: 'Shots, misses and blocks', blurb: '' },
      { slug: 'expected-goals', title: 'Chances and expected goals', blurb: '' },
      { slug: 'fair-comparisons', title: 'Fair comparisons', blurb: '' },
      { slug: 'luck', title: 'Luck and small samples', blurb: '' },
      { slug: 'reading-a-preview', title: 'Reading a preview', blurb: '' },
      { slug: 'reading-a-post-game', title: 'Reading a post-game page', blurb: '' },
    ],
  },
];

// Where a lesson sits: its track, its number in the track ("1.2"), and its neighbours among ready lessons.
export function lessonAt(slug: string) {
  for (const [ti, tr] of TRACKS.entries()) {
    const i = tr.lessons.findIndex((l) => l.slug === slug);
    if (i < 0) continue;
    return { track: tr, trackNo: ti + 1, lesson: tr.lessons[i], no: `${ti + 1}.${i + 1}`, index: i, next: tr.lessons[i + 1] ?? TRACKS[ti + 1]?.lessons[0] };
  }
  throw new Error(`No Academy lesson ${slug}`);
}
