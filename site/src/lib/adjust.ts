// Which adjustment each score-style stat uses, so the settings menu's Custom choice can switch them one kind at a time:
// arena = scorers who count hits, blocks, giveaways and takeaways generously (and home teams' extra);
// score = shot rates weighted for the score and home ice; competition = who a player is up against (faceoffs, so far).
// Team fingerprint traits and player ratings share one list; a stat missing from it has no recorded twin.
export type AdjKind = 'arena' | 'score' | 'competition';
export const ADJ_KIND: Record<string, AdjKind> = {
  physical: 'arena', forecheck: 'arena', hits: 'arena', blocks: 'arena', takeaways: 'arena',
  volume: 'score', suppression: 'score', pace: 'score',
  faceoffs: 'competition',
};
export const adjKind = (key: string): AdjKind | undefined => ADJ_KIND[key];
