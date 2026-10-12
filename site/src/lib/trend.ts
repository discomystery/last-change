// Season trend written by pipeline/export/trend.py. Read through a glob so a build without it still works.
const files = import.meta.glob('../../public/data/trend.json', { eager: true, import: 'default' }) as Record<string, any>;
const data = Object.values(files)[0] as { season: number; window: number; band: [number, number]; teams: Record<string, any> } | undefined;

export const trendFor = (abbr: string) => (data?.teams?.[abbr] ? { t: data.teams[abbr], window: data.window, band: data.band, season: data.season } : null);
