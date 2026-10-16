// With 11 forwards dressed, one forward takes shifts on two lines: a line card says so ("N. Kucherov also plays on L2").
type U = { label: string; players: string[]; ids?: number[] };
export const doublesNote = (units: U[], u: U): string | null => {
  if (u.label[0] !== 'L' || !u.ids) return null;
  const id = u.ids.find((i) => units.filter((v) => v.label[0] === 'L' && v.ids?.includes(i)).length > 1);
  if (id === undefined) return null;
  const other = units.find((v) => v.label !== u.label && v.label[0] === 'L' && v.ids?.includes(id));
  return other ? `${u.players[u.ids.indexOf(id)]} also plays on ${other.label}.` : null;
};
