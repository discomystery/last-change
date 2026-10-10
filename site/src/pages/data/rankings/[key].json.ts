// One small file per rating for the skater rankings page: every rated skater, in rank order, for both season modes.
import { RATINGS, ratingRows } from '../../../lib/leaders';

export function getStaticPaths() {
  return RATINGS.map((r) => ({ params: { key: r.key } }));
}
export function GET({ params }: { params: { key: string } }) {
  return new Response(JSON.stringify(ratingRows(params.key)), { headers: { 'Content-Type': 'application/json' } });
}
