// WORKSHOP MOCK-UP ONLY: hand-drafted samples of the plain-English game story, written from real data for four
// games, to show Sora where the blurb sits and how it reads. Not wired to the pipeline.
export type Item = { tag: string; text: string };
export type Story = { kicker: string; head: string; paras: string[]; listHead: string; items: Item[]; bodies?: Record<string, { body: string; nums: string }> };

export const stories: Record<number, Story> = {
  2026020093: {
    kicker: 'Before the game',
    head: 'Round two in Raleigh',
    paras: [
      'The Capitals already came to Raleigh once this season and left with a 5-2 win. The Hurricanes haven’t lost since: three straight wins, the last a 7-2 rout of Vancouver, with Sebastian Aho on a four-game point streak. Carolina have had much the better of the play lately and they’re at home, so this one leans the Canes’ way.',
      'Washington’s way back in is quality. The Caps make their shots count, and the few shots Carolina allow have been the most dangerous in the league. The flip side: no team gives the puck away into trouble like the Caps, and the Canes are as good as anyone at making them pay.',
    ],
    listHead: 'Who to watch',
    items: [
      { tag: 'Haunts them', text: 'Alex Tuch scored twice in the first meeting and has seven goals in ten career games against Carolina, most of them as a Sabre.' },
      { tag: 'Slow start', text: 'Alex Ovechkin is still looking for his first goal of the season.' },
      { tag: 'Still out', text: 'Seth Jarvis hasn’t played yet this season, so William Carrier keeps his spot beside Aho and Svechnikov.' },
    ],
    bodies: {
      '2026020093-1': { body: 'At home, Rod Brind’Amour likes to send Blake’s line out against the other team’s second line rather than its first. Tonight that means Kyrou and the two Protas brothers.', nums: 'At home: 48% of their time against second lines, against about 28% with no line matching · 3 games, 32 minutes' },
      '2026020093-2': { body: 'The Canes are among the best at turning stolen pucks into chances, and the Caps give it away into trouble more than anyone. A sloppy pass or two could decide this.', nums: 'Carolina 2nd for chances off turnovers (7.8% of their chances) · Washington 32nd at avoiding breakdowns' },
      '2026020093-3': { body: 'The Caps make their shots count, and the shots Carolina do allow tend to be dangerous ones. Expect Washington to get some real chances.', nums: 'Washington 6th for shot quality (a 6.7% chance on the average shot) · Carolina 32nd at preventing dangerous shots' },
      '2026020093-4': { body: 'Carolina’s kill is one of the league’s best and Washington’s one of the worst. Penalties should hurt the Caps more than the Canes.', nums: 'Penalty kill: Carolina 3rd, Washington 27th' },
    },
  },
  2026020072: {
    kicker: 'Before the game',
    head: 'McDavid’s favourite opponent, and Nurse’s old team',
    paras: [
      'The Oilers arrive on a three-game winning streak, and Connor McDavid has a point in every game this season. San Jose is a happy sight for him: he has a point in nine straight games against the Sharks. The Sharks are 3-1, but they’ve played the last two without Macklin Celebrini, who had five points in his first two games.',
      'Edmonton have had the better of the play lately, so the Sharks’ best route is pouncing on loose pucks. Keep an eye on the penalty box: the Oilers’ power play is the best in the league and the Sharks’ kill has struggled. And don’t let the low shot counts fool you. Oilers games have produced 38 goals in four nights.',
    ],
    listHead: 'Who to watch',
    items: [
      { tag: 'Old team', text: 'Darnell Nurse faces Edmonton for the first time since leaving, two nights after scoring the overtime winner in St. Louis.' },
      { tag: 'Owns them', text: 'Connor McDavid: a point in nine straight games against San Jose, and 22 points in his last ten against them.' },
      { tag: 'Missing', text: 'Macklin Celebrini has missed the Sharks’ last two games.' },
      { tag: 'Old team', text: 'Jake Walman, Ty Emberson and Shakir Mukhamadullin all played for San Jose before Edmonton.' },
    ],
  },
  2026020061: {
    kicker: 'What happened',
    head: 'Stankoven’s hat trick, one at every strength',
    paras: [
      'The Hurricanes had this one put away early. K’Andre Miller scored 78 seconds in, Andrei Svechnikov added two power-play goals, and it was 3-0 before the first period was half over. Logan Stankoven did the rest with a hat trick: one shorthanded, one on the power play and one at even strength. Vancouver pulled Kevin Lankinen after six goals.',
      'The score was fair. Replay this game’s chances a hundred times and Carolina win almost nine in ten. The Canucks’ bright spot was a line they hadn’t used before: Elias Pettersson between Jake DeBrusk and Jonathan Lekkerimäki, which had a hand in both their goals.',
    ],
    listHead: 'What it means',
    items: [
      { tag: 'Streak', text: 'Three straight wins for Carolina since losing at home to Washington.' },
      { tag: 'Keep an eye on', text: 'Whether Vancouver keeps the Pettersson line together in New Jersey on Saturday. Their lines have changed more than almost anyone’s.' },
      { tag: 'The preview', text: 'Called the Canes’ power play, and then some. Their shot total fell short of the call.' },
    ],
  },
  2026020063: {
    kicker: 'What happened',
    head: 'Nedeljkovic steals one in St. Louis',
    paras: [
      'The Blues had the better of this one. Replay the chances a hundred times and St. Louis win about three in four. But Alex Nedeljkovic stopped everything dangerous, and twice the Sharks answered a Blues lead: Tyler Toffoli tied it after Connor McMichael’s opener, and Collin Graf tied it after Dillon Dube’s shorthanded goal. Darnell Nurse won it 63 seconds into overtime.',
      'It was the slow, low-event game the preview expected, and the Blues got the good looks they were supposed to. They just couldn’t get enough past Nedeljkovic.',
    ],
    listHead: 'What it means',
    items: [
      { tag: 'Form', text: 'Three wins in four for San Jose, and this one without Macklin Celebrini.' },
      { tag: 'Slump', text: 'St. Louis have lost three straight since winning their opener 4-0 in Dallas.' },
      { tag: 'Up next', text: 'Nurse faces his old team, Edmonton, on Saturday.' },
    ],
  },
};
