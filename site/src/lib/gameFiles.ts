// Post-game report cards (data/jobs) and Swing (data/scores), indexed once per build by game id. Every post-game page
// and several of its components look up their game's files; scanning every file each time grew with the square of
// the season.
const jobFiles = import.meta.glob<{ default: any }>('../../public/data/jobs/2*.json', { eager: true });
const scoreFiles = import.meta.glob<{ default: any }>('../../public/data/scores/2*.json', { eager: true });
const byGame = (files: Record<string, { default: any }>) => new Map(Object.values(files).map((f) => [f.default.game_id as number, f.default]));
let jobs: Map<number, any> | null = null;
let scores: Map<number, any> | null = null;

/** A finished game's report cards (jobs/{id}.json), or undefined before they are written. */
export const jobsFor = <T = any>(gameId: number): T | undefined => (jobs ??= byGame(jobFiles)).get(gameId);
/** A finished game's Swing (scores/{id}.json), or undefined before it is written. */
export const scoresFor = <T = any>(gameId: number): T | undefined => (scores ??= byGame(scoreFiles)).get(gameId);
