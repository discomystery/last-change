// Packs cards into two columns so they end close to flush: each card goes to the shorter column so far,
// in reading order. `size` is a card's height in rough units (its rows, plus a little for its heading).
// Returns the columns, each card carrying its original position so one-column layouts can keep reading order.
export function twoColumns<T>(items: T[], size: (item: T) => number): { item: T; order: number }[][] {
  const cols: { item: T; order: number }[][] = [[], []];
  const height = [0, 0];
  items.forEach((item, order) => {
    const c = height[1] < height[0] ? 1 : 0;
    cols[c].push({ item, order });
    height[c] += size(item);
  });
  return cols.filter((c) => c.length);
}
