// Names, teams and positions for the skater rankings page, shared by every rating.
import { roster } from '../../../lib/leaders';

export function GET() {
  return new Response(JSON.stringify(roster()), { headers: { 'Content-Type': 'application/json' } });
}
