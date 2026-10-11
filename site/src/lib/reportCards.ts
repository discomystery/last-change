// Every skater's post-game report cards and Swing, indexed once per build by player id (player pages read them).
import schedule from '../../public/data/schedule.json';

export type CardRow = { id: number; date: string; opp: string; home: boolean; us: number; them: number; end: string; card: any; swing: any };
let index: Map<number, CardRow[]> | null = null;

export function cardsFor(pid: number, team: string): CardRow[] {
  if (!index) {
    index = new Map();
    const jobs = import.meta.glob<{ default: any }>('../../public/data/jobs/2*.json', { eager: true });
    const scores = import.meta.glob<{ default: any }>('../../public/data/scores/2*.json', { eager: true });
    const swing = new Map<string, any>();
    for (const f of Object.values(scores)) for (const x of f.default.players) swing.set(`${f.default.game_id}-${x.id}`, x);
    const games = new Map((schedule as any).games.map((g: any) => [g.id, g]));
    for (const f of Object.values(jobs)) {
      const d = f.default;
      const g: any = games.get(d.game_id);
      if (!g) continue;
      const all = (Array.isArray(d.players) ? d.players : Object.values(d.players).flat()) as any[];
      for (const c of all) {
        if (c.pos === 'G') continue;
        const home = c.team === g.home;
        const row = { id: d.game_id, date: g.date, opp: home ? g.away : g.home, home, us: home ? g.hs : g.as, them: home ? g.as : g.hs, end: g.end, card: c, swing: swing.get(`${d.game_id}-${c.id}`) };
        if (!index.has(c.id)) index.set(c.id, []);
        index.get(c.id)!.push(row);
      }
    }
    for (const rows of index.values()) rows.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : b.id - a.id));
  }
  return index.get(pid) ?? [];
}
